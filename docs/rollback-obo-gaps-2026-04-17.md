# Rollback runbook — close-obo-gaps (2026-04-17)

Deploy window covered:

- **Deploy A** (OBO-gap closure change set) — dev + prod on 2026-04-17.
- **Deploy B** (catalog visibility default for Genie Space) — prod on
  2026-04-17, same day, follow-up. Section 4 below covers Deploy B;
  the rest of this doc covers Deploy A.
- **Deploy C** (catalog visibility + access fixes) — prod on 2026-04-17,
  same day, second follow-up. Section 5 covers Deploy C; it addresses
  four user-reported issues (Genie not appearing on /admin/catalog,
  owners denied access, Discover hang, MAS raw endpoint names).

Target workspace for both deploys: `<your-profile>`.

Use this doc if any of the following show up after either deploy:

- `/api/v1/debug/me/scopes` 500s or returns wrong data for everyone.
- Catalog / Genie / tiles regress on prod (compare against "Baseline" below).
- The scope-diff script flags drift that wasn't there before (compare against
  `.apx/last-deployed-scopes.json`).
- Genie Spaces appear or disappear from the catalog in a way that admins
  did not trigger (Deploy B territory; see §4).

> **One-liner rollback is not possible.** This repo is not a git worktree
> (`.git` absent), so we cannot `git revert`. The rollback is a targeted
> undo of five edits plus a redeploy; each step is listed below and each
> is self-contained.

---

## 0. Baseline (post-deploy, verified 2026-04-17)

Capture these so a regression is obvious.

### 0.1 Scope-diff baseline

```
OBO scope drift check
  app.yaml       (5 scopes): ['dashboards.genie', 'iam.access-control:workspace', 'model-serving', 'serving.serving-endpoints', 'sql']
  databricks.yml (3 scopes): ['dashboards.genie', 'serving.serving-endpoints', 'sql']

  accepted drift (only in app.yaml, known-F2): ['iam.access-control:workspace', 'model-serving']
  OK -- files are aligned
```

Snapshot at the time of deploy:

```json
{
  "effective_scopes": [
    "dashboards.genie",
    "serving.serving-endpoints",
    "sql"
  ]
}
```

### 0.2 Effective scopes from Databricks Apps API

`databricks apps get agent-hub --profile <your-profile>` should show:

```json
{
  "user_api_scopes": ["serving.serving-endpoints", "sql", "dashboards.genie"],
  "effective_user_api_scopes": [
    "sql", "iam.current-user:read", "dashboards.genie",
    "serving.serving-endpoints", "iam.access-control:read"
  ]
}
```

### 0.3 `/api/v1/debug/me/scopes` on prod

```json
{
  "ok": true,
  "token_kind": "jwt",
  "declared": ["dashboards.genie", "iam.access-control:read",
               "iam.current-user:read", "serving.serving-endpoints", "sql"],
  "missing_from_token": [],
  "extra_in_token": ["email", "offline_access", "openid", "profile"],
  "app_name": "agent-hub",
  "notes": []
}
```

### 0.4 Catalog counts on prod

- 4 Agent Bricks endpoints (2 MAS named `mas-*-endpoint` + 2 KA named `ka-*-endpoint`).
- 4 `pttor-*` models.
- 5 custom agent endpoints (pm-multi-agent-orchestrator, pttor-nong-luby,
  pttor-nong-luby-genie, pttor-nong-luby-ml,
  agents_aan_demo_workspace_catalog-agents-pttor_supervisor).
- 20 Genie Spaces (confirmed in discovery logs).

### 0.5 Expected log signatures after a discovery run

```
INFO  Found 16 custom endpoints (filtered from 48 total)
WARNING Tiles API lookup via obo failed: ... | required_scope=unknown
INFO  Tiles API lookup OK via sp
INFO  Loaded 0 Agent Bricks tiles
INFO  Listed 20 Genie Space(s)
```

The `required_scope=unknown` is *expected* on prod today — it reflects that
the platform's 403 does not name the missing scope. Tracked in
`docs/es-tickets/tiles-api-scope.md`.

---

## 1. Full rollback (undo everything from 2026-04-17)

Execute in order. Each step is idempotent; you can safely re-run.

### 1.1 Revert file edits

| File | Undo action |
|---|---|
| `app.yaml` | Remove the `BOOTSTRAP_ADMIN_EMAILS` env entry and its 3 preceding comment lines. Leave the other env vars intact. |
| `src/agent_hub/backend/core/auth.py` | Delete `_bootstrap_admin_emails()` helper and the `require_debug_admin` function. Keep `require_role`. |
| `src/agent_hub/backend/router.py` | In the `/debug/me/scopes` route, swap `Depends(require_debug_admin)` back to `Depends(require_role("admin"))`; remove `require_debug_admin` from the import list. |
| `src/agent_hub/backend/services/debug_service.py` | Delete the file (and the `ScopeDebugOut` import in `router.py` + `models.py`). |
| `src/agent_hub/backend/services/catalog_service.py` | Remove `_REQUIRED_SCOPE_RE`, `_extract_required_scope`, and the `required_scope=…` substring in the `Tiles API lookup via %s failed` warning. |
| `src/agent_hub/backend/models.py` | Delete the `ScopeDebugOut` model. |
| `scripts/check_scopes.py` | Delete the file. |
| `README.md` | Remove the "Deploy" section referencing `scripts/check_scopes.py`. |
| `tests/test_obo_scope_helpers.py`, `tests/test_check_scopes.py`, `tests/__init__.py` | Delete. |
| `docs/es-tickets/tiles-api-scope.md` | Delete (or keep; it is docs-only and harmless). |
| `docs/obo-auth-design.md` | Revert to the pre-2026-04-17 copy if you have one. Otherwise the current doc is accurate with `Platform Gap` status for F1/F3 — no need to revert. |
| `.apx/last-deployed-scopes.json` | `rm .apx/last-deployed-scopes.json` |

### 1.2 Redeploy

```bash
# The previous-state bundle had no P2a/P2b/P3 code and no bootstrap-admin env.
databricks bundle deploy --target dev  --profile <your-profile>
databricks bundle run    agent_hub --target dev  --profile <your-profile>
# Smoke-check dev
curl -I https://agent-hub-dev-<workspace-id>.aws.databricksapps.com/api/v1/health/live

databricks bundle deploy --target prod --profile <your-profile>
databricks bundle run    agent_hub --target prod --profile <your-profile>
```

### 1.3 Re-consent (only if scopes changed on the way back)

Not applicable for this change set — `user_api_scopes` is identical before
and after (`serving.serving-endpoints, sql, dashboards.genie`). Consent
carries over. If a future rollback *does* touch scopes,
`scripts/check_scopes.py` will remind you (F5 warning).

---

## 2. Partial rollbacks (keep what works, back out one piece)

Each item below can be reverted in isolation.

### 2.1 Keep debug endpoint, drop bootstrap-admin gate

Rationale: you're about to re-enable Lakebase on dev and don't need the
bypass anymore.

- `src/agent_hub/backend/router.py`: swap back to `require_role("admin")`.
- `app.yaml`: remove `BOOTSTRAP_ADMIN_EMAILS`.
- Redeploy. Existing consent carries over.

### 2.2 Keep P2b logging, drop the debug endpoint

- Delete `/debug/me/scopes` route, `debug_service.py`, `ScopeDebugOut`.
- Keep `_extract_required_scope` — it's the one piece that keeps F3 observable.
- Redeploy.

### 2.3 Keep P3 script, drop everything else

- Keep `scripts/check_scopes.py` + README snippet.
- Undo all code edits per §1.1 except the ones under `scripts/` and `README.md`.
- Redeploy.

---

## 3. Contacts

- ES ticket draft: `docs/es-tickets/tiles-api-scope.md`
- Design doc: `docs/obo-auth-design.md` §11 (Action items) and §14 (Debug runbook).
- Prior deploy logs: `/tmp/bundle-deploy-dev.log`, `/tmp/bundle-deploy-prod.log`
  (overwritten by subsequent runs — capture a fresh copy before rolling back).

---

## 4. Deploy B — Genie Space default visibility (2026-04-17 follow-up)

**Intent.** Promote `AgentType.GENIE_SPACE` from default-hidden to
default-visible, so newly-discovered spaces surface without admin
intervention. Paired with a one-shot data migration that flips already
hidden Genie rows and a small UI note on `/admin/catalog` describing
the new default. See discussion trail in this session + the code
comment in `_default_visible_for`.

**What actually changed on the database.** On `<your-profile>` prod the
migration ran but touched **0 rows** — `list_genie_spaces` is a
direct pass-through to `/api/2.0/genie/spaces` and has never persisted
spaces into `catalog_config`. The migration is defensive: it
runs idempotently and is a no-op today, but protects us if discovery
later starts persisting Genie Spaces.

### 4.1 Files touched in Deploy B

| File | Revert action |
|---|---|
| `src/agent_hub/backend/services/catalog_service.py` | Remove `AgentType.GENIE_SPACE` from the set returned by `_default_visible_for`. Restore the old docstring. |
| `src/agent_hub/backend/core/lakebase.py` | Delete `_DATA_MIGRATIONS`, `_run_data_migrations`, and the `_run_data_migrations(engine)` call inside `_run_migrations_bg`. Keep the existing `_TABLES_DDL` / `_INDEXES_DDL` / `_SEED_DDL` blocks. |
| `src/agent_hub/ui/routes/_sidebar/admin.catalog.tsx` | Remove the new `<p>` explaining visibility defaults (leave the original `Hidden agents stay registered...` paragraph). |
| `tests/test_catalog_visibility.py` | Delete the file. |

### 4.2 Database cleanup (only if rolling back default and you want existing rows hidden again)

```sql
-- Revert Genie Spaces the migration made visible back to hidden. Only
-- run if the product decision is "hide Genie Spaces by default again".
UPDATE catalog_config
   SET visible = false, updated_at = NOW()
 WHERE agent_type = 'GENIE_SPACE';

-- Reset the migration ledger so a re-deploy could re-apply if desired.
DELETE FROM admin_settings
 WHERE key IN (
   'migration_genie_default_visible_2026_04_17',
   'migration_genie_default_visible_2026_04_17_v2'
 );
```

Skip this block if the rollback is code-only and you're fine leaving
already-visible rows as-is.

### 4.3 Redeploy

```bash
databricks bundle deploy --target prod -p <your-profile>
databricks bundle run    agent_hub --target prod -p <your-profile>

# Optional — verify the migration infra was removed from logs:
databricks apps logs agent-hub -p <your-profile> 2>&1 | grep -i "Data migration"
# Expected: no matches.
```

Catalog counts after rollback: same 13 curated catalog entries (MAS / KA /
Custom Agent / Models admins explicitly enabled). Genie Spaces are
unaffected for render (still direct pass-through) unless you also ran §4.2.

### 4.4 Verification baseline (post-Deploy-B)

- `/catalog` with `Genie Space` filter: all **20 spaces** render.
- `/admin/catalog`: subtitle now includes
  "New MAS, Agent, KA, External, and Genie Space entries are visible by default..."
- Logs include:
  ```
  INFO Data migration migration_genie_default_visible_2026_04_17_v2: Promoted Genie Spaces to default-visible (touched 0 rows)
  INFO Database migration completed successfully
  ```

---

## 5. Deploy C — Catalog visibility + access fixes (2026-04-17 second follow-up)

**Intent.** Fix four distinct user-reported issues that surfaced after
Deploy B:

1. Genie Spaces didn't appear on `/admin/catalog` at all (they were
   fetched live from the API and never persisted in `catalog_config`,
   which is the sole data source behind the admin table).
2. Owners were denied access to their own agents when the OBO
   `serving_endpoints.get` probe failed under a stale-consent token
   (the 970-byte token variant we've been tracking).
3. The "Discover agents" action appeared to hang — logs showed
   `Listing serving endpoints...` with no follow-up. `serving_endpoints.list()`
   had no SP fallback.
4. MAS endpoints displayed their raw Agent-Bricks IDs
   (`mas-94fa1c3b-endpoint`) whenever the Tiles API call returned 0
   tiles (F3 territory).

**What actually changed.** Five localized hunks in
`src/agent_hub/backend/services/catalog_service.py`, two hunks in
`src/agent_hub/backend/router.py`, three new test files, and this
rollback section.

### 5.1 Files touched in Deploy C

| File | Revert action |
|---|---|
| `src/agent_hub/backend/services/catalog_service.py` | (a) Remove `_GENIE_ENDPOINT_PREFIX`, `_genie_endpoint_name`, `_owner_has_access`, `_smart_title`, `_derive_display_name`, `_list_serving_endpoints_resilient`, `_upsert_genie_spaces`, `_fetch_genie_spaces_raw`, `_persist_and_fetch_visibility`. (b) Restore `list_genie_spaces` to the single-shot OBO→SP read-through (no session parameter, no persistence). (c) Restore the inline `try: ws.serving_endpoints.list()` block in `discover_from_workspace`. (d) Restore `display_name = (tile.get("name") if tile else None) or endpoint_name` in both `discover_from_workspace` and `reclassify_existing`. (e) Remove the `genie:*` filter from `list_agents` SQL. (f) Drop the `user_email` parameter + owner fallback in `get_agent_detail` and `check_access`. |
| `src/agent_hub/backend/router.py` | (a) Remove the `user_email` / owner fallback in `list_agents`. (b) Revert `get_agent` / `check_agent_access` to not pass `user_email`. (c) Revert `list_genie_spaces` to not open/pass a session. (d) Drop the module-level `logger`. |
| `tests/test_display_name.py` | Delete. |
| `tests/test_owner_access.py` | Delete. |
| `tests/test_genie_persistence.py` | Delete. |

### 5.2 Database cleanup (only if rolling back persistence and you want genie:\* rows gone)

```sql
-- Drop any Genie Space rows persisted by Deploy C. Harmless to keep
-- them (a subsequent Deploy C-style change would re-use them); only
-- run if the product decision is "Genie lives outside catalog_config".
DELETE FROM catalog_config WHERE endpoint_name LIKE 'genie:%';
```

### 5.3 Redeploy

```bash
databricks bundle deploy --target prod -p <your-profile>
databricks bundle run    agent_hub --target prod -p <your-profile>

# Verify discover still works but no longer logs genie upserts:
databricks apps logs agent-hub -p <your-profile> 2>&1 | grep -E 'Discovery complete|Genie Space'
```

### 5.4 Partial rollbacks (keep what works)

Each of the four fixes is independent — you can back out one without
touching the others.

- **Back out Genie persistence only.** Revert the `discover_from_workspace`
  Genie upsert loop, the `list_genie_spaces(session=...)` plumbing in
  `router.py`, and the `list_agents` SQL filter. Run the `DELETE` in §5.2
  so `/admin/catalog` doesn't display stale rows. Leaves owner fallback,
  discover resilience, and display-name derivation in place.
- **Back out owner fallback only.** Remove the `user_email` parameter from
  `get_agent_detail` / `check_access` / the three router routes, and
  remove the `_owner_has_access` calls from the router's `list_agents`
  loop. Also delete `tests/test_owner_access.py`.
- **Back out discover resilience only.** Inline the old
  `try/except` around `ws.serving_endpoints.list()` in
  `discover_from_workspace` and delete `_list_serving_endpoints_resilient`.
- **Back out display-name fallback only.** Restore
  `display_name = (tile.get("name") if tile else None) or endpoint_name`
  in both `discover_from_workspace` and `reclassify_existing`; delete
  `_derive_display_name` and `_smart_title`; delete
  `tests/test_display_name.py`.

### 5.5 Verification baseline (post-Deploy-C)

- `/admin/catalog` lists Genie Spaces alongside endpoints; Hide toggle
  removes them from `/catalog` on next render.
- Owner hitting `/catalog/mas-94fa1c3b-endpoint`: green "Accessible"
  badge, "Start Chat" enabled.
- Logs include `Listed N serving endpoint(s) via obo in Xms` and
  `Found N custom endpoints (filtered from M total)` per discover run.
- MAS endpoint card shows `Mas 94fa1c3b` (prettified) or the UC model
  name (e.g. `Ma Supply Chain Copilot`), never `mas-94fa1c3b-endpoint`.

---

## 6. Deploy D — Streaming + instant sidebar (2026-04-09)

**Scope:** UX-only. **OBO flow unchanged** — the same
`X-Forwarded-Access-Token` is reused as the `Authorization` header on the
streaming HTTP call, so consent, scopes, and permissions are identical.

### 6.1 What shipped

Two independent changes land together:

1. **Real token-by-token streaming.** Replace
   `ws.api_client.do(POST, /invocations, stream=True)` (which buffers
   the whole response before returning) with a raw `httpx.Client` + a
   streaming `POST` that iterates `resp.iter_lines()`. New helpers in
   `src/agent_hub/backend/services/chat_service.py`:
   - `_invocations_url`, `_auth_headers` — build the streaming request.
   - `_post_stream` — open the connection, handle a 400 on `messages` by
     retrying with `input` (preserves MAS `input`-field compatibility).
   - `_close_stream` — close both the response and the owning client.
   - `_iter_sse_lines` — parse upstream `data: {...}` frames into JSON,
     skipping `[DONE]` / keepalive / comment lines.
   - `_emit_streamed` updated to consume `iter_lines()` for SSE or fall
     through to a single `read()` when the server returned JSON instead
     of a stream.
   - `_query_endpoint` retained for the non-streaming fallback and
     insight extraction.
2. **Immediate sidebar + URL flip.** `stream_chat` emits
   `data: {"type":"started","conversation_id":"..."}` the instant the
   conversation row + user message are persisted (before any upstream
   call). `src/agent_hub/ui/hooks/use-chat.ts` handles the event by
   calling `setConversationId` + invalidating `listConversationsKey()`.
   `src/agent_hub/ui/routes/_sidebar/chat.new.tsx` now mounts
   `ConversationSidebar` on first render (previously it only appeared on
   `/chat/$id`) and navigates as soon as `conversationId` is set — the
   old `!isStreaming` gate was removed so the URL flips before any
   tokens arrive. `StreamingMessage` renders a three-dot "thinking"
   placeholder during the gap between `started` and the first `token`.

Events are now `{type, ...}` tagged:

| Event    | Fields                                            |
| -------- | ------------------------------------------------- |
| started  | `type`, `conversation_id`                         |
| token    | `type`, `token`, `done=false`                     |
| done     | `type`, `done=true`, `conversation_id`            |
| error    | `type`, `error`, `done=true`, `conversation_id`   |

Legacy consumers still work — the UI keys on `event.token` / `event.done`
in addition to `event.type`.

### 6.2 Rollback — revert in 3 focused hunks

Each hunk is independent; back out all three for the full revert.

| Hunk | File | Revert to |
| ---- | ---- | --------- |
| **Backend stream** | `src/agent_hub/backend/services/chat_service.py` | Remove `_invocations_url`, `_auth_headers`, `_post_stream`, `_close_stream`, `_iter_sse_lines`, and the httpx import. Restore the single-branch `_query_endpoint` that just does `ws.api_client.do("POST", path, body={"stream": bool(stream), ...})`. Restore the old `_emit_streamed(streamed)` that iterates the SDK-returned iterable. |
| **started event** | `src/agent_hub/backend/services/chat_service.py` | Delete the `yield _sse({"type":"started",...})` line and drop the `type` field from `error`/`done` events. |
| **FE handling** | `src/agent_hub/ui/hooks/use-chat.ts`, `src/agent_hub/ui/routes/_sidebar/chat.new.tsx`, `src/agent_hub/ui/components/chat/streaming-message.tsx`, `src/agent_hub/ui/lib/types.ts` | Remove the `started` branch + `invalidateConversations` calls from `use-chat.ts`. Drop `ConversationSidebar` from `chat.new.tsx` and restore the `!chat.isStreaming` navigate gate. Restore `StreamingMessage`'s `if (!content) return null;`. Drop the `type` field from `SSEEvent`. |

### 6.3 Rollback commands

```bash
# 1. Revert the three backend+frontend hunks via git (or by hand).
git checkout HEAD~1 -- \
  src/agent_hub/backend/services/chat_service.py \
  src/agent_hub/ui/hooks/use-chat.ts \
  src/agent_hub/ui/routes/_sidebar/chat.new.tsx \
  src/agent_hub/ui/components/chat/streaming-message.tsx \
  src/agent_hub/ui/lib/types.ts

# 2. Delete the tests that would otherwise fail against the old
#    non-streaming implementation.
rm tests/test_chat_streaming.py

# 3. Redeploy.
databricks bundle deploy --target prod -p <your-profile>
databricks bundle run    agent_hub --target prod -p <your-profile>
```

### 6.4 No migration, no scope changes

- **No DB migration.** No new columns, no backfill. Conversation and
  message persistence order moved slightly (user message now persists
  before the upstream call, same as before Deploy D — verify via
  `SELECT role, created_at FROM messages ORDER BY created_at LIMIT 4`).
- **No OBO changes.** `ws.config.token` already contains the OBO token
  on request-scoped `WorkspaceClient` instances; we read it and set it
  as the `Authorization: Bearer ...` header on `httpx.Client.send`.
  Scopes in `app.yaml` and `databricks.yml` are unchanged.
- **No dep changes.** `httpx` is already declared in `pyproject.toml`
  (`httpx>=0.27.0`). No `requirements.txt` or `pyproject` edits.

### 6.5 Verification baseline (post-Deploy-D)

- Send a message on `/chat/new?agent=...`. The URL flips to
  `/chat/<uuid>` within ~100 ms and the left sidebar shows the new
  conversation row before the assistant has streamed any text.
- The assistant bubble shows the three-dot thinking placeholder, then
  begins filling in characters incrementally (visible chunks, not one
  big drop).
- After the response completes, the sidebar preview/timestamp updates
  without a manual refresh.
- MAS agent chat still works: logs show `Endpoint <ep> uses 'input'
  field, retrying streaming` exactly once if the agent rejects the
  `messages` body. No `Falling back to non-streaming` entry for
  endpoints that honour `stream:true`.
- `pytest tests/` — all 69 tests green, including the 14 new cases in
  `tests/test_chat_streaming.py`.

### 6.6 Partial rollbacks (keep what works)

- **Back out streaming only.** Revert just hunk 1 in §6.2; keep the
  `started` event + frontend sidebar logic. The client still sees the
  URL flip + sidebar immediately, just without progressive text.
- **Back out the instant sidebar only.** Revert just hunk 3 in §6.2;
  keep raw streaming. Tokens will render progressively but the sidebar
  will only appear after the `done` event.

---

## 7. Deploy E — Genie in-app chat + simulated streaming (2026-04-09)

**Scope:** Two distinct fixes shipped together. **OBO unchanged** — same
`X-Forwarded-Access-Token` is reused; no scope changes; no new consent.

### 7.1 What shipped

1. **Genie Spaces are now first-class chatable agents in-app.** Clicking a
   Genie card on `/catalog` no longer opens the external Databricks UI;
   it routes to `/catalog/genie:<space_id>` and "Start Chat" goes through
   the same SSE pipeline as MAS. `chat_service.stream_chat` detects the
   `genie:` prefix and dispatches to a new `_stream_genie` branch that
   speaks the Genie Conversation API directly:
   - First turn: `POST /api/2.0/genie/spaces/{space_id}/start-conversation`
   - Follow-ups: `POST /api/2.0/genie/spaces/{space_id}/conversations/{cid}/messages`
   - Polls `GET .../messages/{mid}` every 1s (90s deadline), emitting
     `_Generating SQL..._`, `_Running query..._`, etc. as token events.
   - On `COMPLETED`, renders the natural-language answer + a fenced
     ```sql block built from `attachments[].text` / `attachments[].query`.
   - On `FAILED` / `CANCELLED` / timeout, emits a typed SSE `error` event.
   - Persists the Genie `conversation_id` in
     `conversations.metadata_json->>'genie_conversation_id'` so
     subsequent turns thread correctly.
2. **Streaming UX restored end-to-end.**
   - **Backend**: `_post_stream` now sends `Accept: text/event-stream`
     (no JSON fallback in the Accept header for the streaming path)
     to strongly hint upstream MAS endpoints into real SSE. When an
     upstream still returns a single `application/json` blob,
     `_emit_streamed` and `_stream_with_fallback` both run the
     payload through a new word-boundary-aware
     `_simulate_chunked_stream` helper so the user perceives a live
     stream rather than a freeze + dump. Default chunk = 12 chars,
     20ms inter-chunk delay (tunable via `_CHUNK_CHARS_DEFAULT` /
     `_CHUNK_DELAY_S_DEFAULT`).
   - **Frontend**: `chat.new.tsx` no longer calls `navigate()` to flip
     the URL — that was unmounting the route, aborting the live
     `EventSource`, and resetting `chat.messages` to `[]` (which is
     what made streaming "look" broken). Replaced with
     `window.history.replaceState({}, "", target)` so the URL flips in
     place and the `EventSource` survives. Also passes
     `activeId={chat.conversationId}` to `ConversationSidebar` so the
     new row highlights the moment the `started` event arrives.
   - `chat.$conversationId.tsx` now skips re-seeding messages from
     `convDetail` when `chat.isStreaming === true` for the active
     conversation, preventing a sub-second "wipe" if the loader race
     resolves mid-stream.

### 7.2 Files touched in Deploy E

| File | Revert action |
| ---- | ------------- |
| `src/agent_hub/backend/services/chat_service.py` | (a) Delete `_GENIE_ENDPOINT_PREFIX`, `_is_genie`, `_genie_space_id`, `_genie_get_conv_id`, `_genie_set_conv_id`, `_genie_render_attachments`, `_genie_status_label`, `_stream_genie`, `_GENIE_POLL_TIMEOUT_S`, `_GENIE_POLL_INTERVAL_S`, `_simulate_chunked_stream`, `_CHUNK_CHARS_DEFAULT`, `_CHUNK_DELAY_S_DEFAULT`. (b) Remove the `_is_genie(endpoint_name)` dispatch block from `stream_chat` (the Genie branch + early return). (c) Restore `_post_stream` so it does **not** override `headers["Accept"] = "text/event-stream"` (re-uses the JSON-fallback Accept from `_auth_headers`). (d) Restore `_emit_streamed` JSON-fallback to a single token event instead of `_simulate_chunked_stream`. (e) Restore `_stream_with_fallback` non-streaming branch to a single token event instead of `_simulate_chunked_stream`. |
| `src/agent_hub/backend/services/catalog_service.py` | Remove `_genie_has_access` and the Genie branches inside `get_agent_detail` and `check_access`. The `genie:` prefix on rows in `catalog_config` is harmless to leave behind — `list_agents` already filters them out. |
| `src/agent_hub/backend/core/lakebase.py` | Drop the `metadata_json JSONB` column from `_TABLES_DDL` for `conversations` and remove the matching `ALTER TABLE conversations ADD COLUMN IF NOT EXISTS metadata_json JSONB` migration. The DB migration is idempotent — leaving the column in place is also fine; it's NULL for non-Genie conversations. |
| `src/agent_hub/ui/components/catalog/genie-space-card.tsx` | Restore the original external `<a href={...databricks_url}>` wrapper. Drop the `<Link to="/catalog/$agentId">` import and the small "external link" icon button. |
| `src/agent_hub/ui/routes/_sidebar/chat.new.tsx` | Restore the `navigate({ to: "/chat/$conversationId", params: { conversationId } })` call in the `useEffect` and drop the `window.history.replaceState` line. Remove `activeId` from the `ConversationSidebar` props. |
| `src/agent_hub/ui/routes/_sidebar/chat.$conversationId.tsx` | Drop the `chat.isStreaming` gate in the `useEffect` that calls `chat.setMessages(normalized)` (always call). |
| `tests/test_chat_streaming.py` | Delete the 11 new tests added in Deploy E: `test_simulate_chunked_stream_*` (3), `test_post_stream_sets_accept_sse`, `test_is_genie_helpers`, `test_stream_genie_*` (5), `test_stream_chat_dispatches_to_genie`. Restore `test_emit_streamed_single_json_fallback_chunks` to its earlier single-event form (`test_emit_streamed_single_json_fallback`). |

### 7.3 Database cleanup (only if you want the column gone)

```sql
-- Optional. The column is nullable, defaults to NULL for non-Genie rows,
-- and consumes ~0 bytes when unset. Only drop if you're sure no other
-- caller relies on it.
ALTER TABLE conversations DROP COLUMN IF EXISTS metadata_json;
```

### 7.4 Redeploy

```bash
databricks bundle deploy --target prod -p <your-profile>
databricks bundle run    agent_hub --target prod -p <your-profile>

# Smoke-check: a Genie card should open externally again, MAS chat
# should still flow tokens (or single-shot if upstream doesn't stream).
databricks apps logs agent-hub -p <your-profile> 2>&1 | \
  grep -E 'Genie|Streaming|Simulated chunked'
# Expected after rollback: no "Genie ..." entries, no "Simulated
# chunked stream applied" entries.
```

### 7.5 Partial rollbacks (keep what works)

Each item below is independent — back out one without touching the others.

- **Back out Genie chat only.** Revert the `chat_service.py`, `catalog_service.py`, and `genie-space-card.tsx` hunks. Keep the streaming + frontend fixes. Genie cards open externally again; the `metadata_json` column stays (NULL for all rows); MAS streaming UX is preserved.
- **Back out streaming chunker only.** Restore `_emit_streamed` JSON fallback + `_stream_with_fallback` non-streaming branch to single events. Keep the `Accept` header override (harmless) and Genie chat. The single-blob endpoints will look like a freeze + dump again, but Genie + real-streaming MAS are unaffected.
- **Back out the frontend `replaceState` + sidebar gate only.** Restore `navigate(...)` in `chat.new.tsx` and drop the `chat.isStreaming` gate in `chat.$conversationId.tsx`. Streaming will still work end-to-end on the backend, but the route swap will tear down the EventSource on the first message — the UI will look like the response is delayed until completion.

### 7.6 No scope changes, no consent re-prompt

- **No `app.yaml` / `databricks.yml` edits.** `dashboards.genie` was already declared (used by `_load_genie_map` for tiles). The `start-conversation` / `messages` / poll calls reuse it.
- **No new env vars.**
- **No dep changes.** `httpx` was already pinned (>=0.27.0).
- **DB migration is idempotent and trivial** (single `ALTER TABLE … ADD COLUMN IF NOT EXISTS metadata_json JSONB`). Skips if already applied.

### 7.7 Verification baseline (post-Deploy-E)

- `/catalog` Genie filter: cards link **in-app** to `/catalog/genie:<id>`; the small ↗ icon still opens Databricks for deep linking.
- `/catalog/genie:<id>` shows the same access badge model as MAS; "Start Chat" lands on `/chat/new?agent=genie:<id>`.
- Sending a Genie message produces visible status tokens (`_Generating SQL..._` → `_Running query..._`) followed by the natural-language answer + a fenced ```sql block. The conversation sidebar updates immediately on `started`, and the URL flips in place without remounting.
- Sending a MAS message: tokens render progressively even when the upstream returns `application/json` (logs show `Upstream returned application/json (one-shot); applying simulated chunking (chars=N)`). Real-streaming endpoints log `Streaming chat to <ep> (stream=true, content-type=text/event-stream)` as before.
- `pytest tests/` — all 80 tests green, including 11 new Deploy-E cases in `tests/test_chat_streaming.py`:
  ```
  test_simulate_chunked_stream_word_boundaries
  test_simulate_chunked_stream_empty
  test_simulate_chunked_stream_single_short_word
  test_post_stream_sets_accept_sse
  test_is_genie_helpers
  test_stream_genie_first_turn_persists_conv_id
  test_stream_genie_followup_uses_existing_conv_id
  test_stream_genie_completed_emits_answer_and_sql
  test_stream_genie_failed_emits_error
  test_stream_genie_timeout_emits_error
  test_stream_chat_dispatches_to_genie
  ```

---

## 8. Deploy F — UC-tag agent types + catalog discovery (2026-04-27)

**Scope:** Phase 1 of the master roadmap — surfaces Unity-Catalog-tagged
functions and Connections in the catalog under two new `AgentType`
values (`HTTP_CONNECTION`, `MCP_ENDPOINT`). Chat invocation is a
deliberate stub; Phase 2 will deliver `_stream_http_connection` /
`_stream_mcp`. **OBO unchanged** — discovery reads
`system.information_schema.*_tags` through the SP client (admin
warehouse) since OBO cannot see those views; every user-facing read
path still goes through OBO.

See `docs/obo-auth-design.md` §15 for the full design. Quick rollback
matrix below.

### 8.1 What shipped

1. **Enum expansion.** `AgentType` in
   `src/agent_hub/backend/models.py` gains `HTTP_CONNECTION` and
   `MCP_ENDPOINT`. Two new endpoint-name prefixes, `uc:` and `mcp:`,
   are now valid in `catalog_config.endpoint_name`.
2. **Admin tag-config API.** `GET /admin/tag-config` (read, any auth
   user) and `PUT /admin/tag-config` (admin only) persist a
   `UCTagConfig` JSON blob in `admin_settings.uc_tag_config`. Defaults:
   `agent_tag_key="agent_hub_role"`, `agent_tag_value="agent"`,
   `agent_kind_tag_key="agent_hub_kind"`.
3. **UC-tag discovery.** `_discover_uc_tagged` runs inside
   `discover_from_workspace` after the Genie upsert. Uses the SP
   client + admin warehouse (`AGENT_HUB_ADMIN_WAREHOUSE_ID` →
   `DATABRICKS_WAREHOUSE_ID`) to query `function_tags` and
   `connection_tags`, classifies rows via the optional kind tag,
   upserts under `uc:*` / `mcp:*` with `invoke_shape` already populated
   for Phase 2.
4. **Access + chat branches.** `get_agent_detail` / `check_access` add
   `uc:*` / `mcp:*` branches that skip `serving_endpoints.get` and
   return an optimistic access verdict (`OWNER` if the caller matches
   the owner, `CAN_USE_DEFERRED` otherwise). `stream_chat` emits a
   Phase-2 notice SSE for these prefixes instead of attempting the MAS
   pipeline. `_verify_access_best_effort` and `reclassify_existing`
   skip the new prefixes too.
5. **FE affordances.** `agent-type.ts` learns the new labels
   (`HTTP Connection`, `MCP Endpoint`) and badge variants (`uc`,
   `mcp`). `catalog.index.tsx` adds matching filter chips.
   `admin.catalog.tsx` mounts a new `<TagConfigCard />` for editing the
   three tag keys. `chat.new.tsx` and `components/chat/agent-header.tsx`
   derive agent type from the endpoint-name prefix as a fallback, so
   Genie headers (and the new UC/MCP headers) no longer render as
   "Supervisor Agent".
6. **Tests + script.** 16 new cases in `tests/test_catalog_uc_discovery.py`,
   extensions to `tests/test_chat_streaming.py` (UC/MCP stub coverage)
   and `tests/test_catalog_visibility.py`. New
   `scripts/audit_agent_types.py` flags prefix/type mismatches in
   `catalog_config` (read-only; non-zero exit on drift).

### 8.2 Files touched in Deploy F

| File | Revert action |
| ---- | ------------- |
| `src/agent_hub/backend/models.py` | Remove `HTTP_CONNECTION` and `MCP_ENDPOINT` from `AgentType`. Remove `UCTagConfig` and `UCTagConfigUpdate` classes. |
| `src/agent_hub/backend/services/admin_service.py` | Remove `UC_TAG_CONFIG_KEY`, `get_uc_tag_config`, `update_uc_tag_config`, and the `UCTagConfig` / `UCTagConfigUpdate` imports. `admin_settings` row with `key='uc_tag_config'` is safe to leave behind (or delete via SQL below). |
| `src/agent_hub/backend/router.py` | Delete `GET /admin/tag-config` and `PUT /admin/tag-config` routes plus their imports. |
| `src/agent_hub/backend/services/catalog_service.py` | Remove `_UC_ENDPOINT_PREFIX`, `_MCP_ENDPOINT_PREFIX`, `_is_uc_endpoint`, `_is_mcp_endpoint`, `_uc_endpoint_name`, `_mcp_endpoint_name`, `_strip_uc_prefix`, `_admin_warehouse_id`, `_normalize_sql_ident`, `_execute_sp_sql`, `_discover_uc_tagged`, `_upsert_uc_row`. In `discover_from_workspace`, remove the UC-tag discovery block. In `get_agent_detail` / `check_access`, drop the `uc:*` / `mcp:*` branches. In `reclassify_existing`, drop the prefix skip for `uc:*` / `mcp:*`. In `_default_visible_for`, drop the `HTTP_CONNECTION` / `MCP_ENDPOINT` cases (they'll fall through to the safe default). |
| `src/agent_hub/backend/services/chat_service.py` | Remove `_UC_ENDPOINT_PREFIX`, `_MCP_ENDPOINT_PREFIX`, `_is_uc_connection`, `_is_mcp_endpoint`, `_uc_full_name`, `_mcp_full_name`. Remove the `uc:` / `mcp:` stub branch in `stream_chat`. In `_verify_access_best_effort`, drop the UC/MCP skip (leave the `genie:` skip). |
| `src/agent_hub/ui/lib/types.ts` | Remove `"HTTP_CONNECTION"` and `"MCP_ENDPOINT"` from `AgentType`. Remove `UCTagConfig` interface. |
| `src/agent_hub/ui/lib/agent-type.ts` | Remove the two new `agentTypeVariant` / `agentTypeLabel` cases. Remove `agentTypeFromEndpointName` OR restrict it back to `genie:` only (keeping it for genie is safe). |
| `src/agent_hub/ui/lib/api.ts` | Remove `UCTagConfig`, `UCTagConfigUpdate`, `getUCTagConfig`, `useGetUCTagConfig`, `updateUCTagConfig`, `useUpdateUCTagConfig`. |
| `src/agent_hub/ui/components/admin/tag-config-card.tsx` | Delete the file. |
| `src/agent_hub/ui/routes/_sidebar/admin.catalog.tsx` | Remove the `<TagConfigCard />` import + render. Restore the descriptive `<p>` copy to the pre-Deploy-F wording. |
| `src/agent_hub/ui/routes/_sidebar/catalog.index.tsx` | Remove `"HTTP Connection"` and `"MCP Endpoint"` from `FILTER_OPTIONS` and `TYPE_FILTERS`. |
| `src/agent_hub/ui/routes/_sidebar/chat.new.tsx` | Restore the `agentType = agent?.agent_type ?? "MAS"` fallback. |
| `src/agent_hub/ui/components/chat/agent-header.tsx` | Restore the `agentType = "MAS"` default signature (or keep the prefix fallback — it's harmless without the new prefixes). |
| `scripts/audit_agent_types.py` | Delete the file. |
| `tests/test_catalog_uc_discovery.py` | Delete the file. |
| `tests/test_chat_streaming.py` | Delete the three UC/MCP-focused cases: `test_uc_and_mcp_prefix_helpers`, `test_stream_chat_uc_stub_emits_phase2_notice`, `test_stream_chat_mcp_stub_emits_phase2_notice`. |
| `tests/test_catalog_visibility.py` | Restore `test_agent_surfaces_are_visible` to drop the two new enum values from its loop. |

### 8.3 Database cleanup (if you want the rows gone)

Feature-flag rollback does **not** require DB cleanup. Rows stay in
`catalog_config` with `visible=false` once the flag is on (they simply
become invisible to users). If you want them fully removed:

```sql
-- Drop discovered UC/MCP agents. Safe to re-run after a redeploy
-- (the next POST /agents/discover will re-populate).
DELETE FROM catalog_config WHERE endpoint_name LIKE 'uc:%' OR endpoint_name LIKE 'mcp:%';

-- Drop the admin tag-config row (defaults will be re-applied on next read).
DELETE FROM admin_settings WHERE key = 'uc_tag_config';
```

### 8.4 Redeploy

```bash
databricks bundle deploy --target prod -p <your-profile>
databricks bundle run    agent_hub --target prod -p <your-profile>

# Smoke-check
curl -sS "$APP_URL/api/v1/agents" -H "Cookie: ..." | jq '.[] | .endpoint_name' \
  | grep -E '^"(uc|mcp):' || echo "OK: no uc:/mcp: rows visible"
```

### 8.5 Partial rollbacks (keep what works)

Each is independent — back one out without touching the others.

- **Disable discovery only.** Set `AGENT_HUB_DISABLE_UC_MCP_DISCOVERY=1` in
  `app.yaml` env. `_discover_uc_tagged` short-circuits (returns
  `(0,0,0,[])`). Existing rows remain visible; just no new ones land.
  Good for an emergency in a region where `*_tags` views misbehave.
- **Disable the admin editor only.** Remove the `<TagConfigCard />` in
  `admin.catalog.tsx`; the backend route stays, the defaults still
  work.
- **Disable the chat stub only.** Remove the `uc:` / `mcp:` branch from
  `stream_chat`. Users who click a UC/MCP card will see the usual
  "endpoint not found" error instead of the friendly Phase-2 notice —
  not ideal, but harmless. (Phase 2's `AGENT_HUB_DISABLE_UC_MCP_CHAT=1`
  flag re-routes back to the stub after Phase 2 ships.)

### 8.6 No scope changes, no consent re-prompt

- **No `app.yaml` / `databricks.yml` scope edits.** The SP client
  already has metastore-admin reach via the existing bundle config.
- **No new scopes.**
- **One optional env var** — `AGENT_HUB_ADMIN_WAREHOUSE_ID` (if unset, the
  standard SDK `DATABRICKS_WAREHOUSE_ID` is used; if neither is set,
  discovery skips with a visible warning — no crash).
- **No DB migration.** Discovery upserts into the existing
  `catalog_config` table; admin settings land in the existing
  `admin_settings` table.

### 8.7 Verification baseline (post-Deploy-F)

- `POST /agents/discover` returns 200 and logs
  `catalog.uc_discovery fn=... mcp_fn=... conn=... created=... updated=... warehouse=...`.
- `GET /admin/tag-config` returns the defaults (or whatever admins
  have set via `PUT`).
- Catalog page: tagged UC functions/connections appear with the
  `HTTP Connection` or `MCP Endpoint` badge; filter chips filter them
  correctly.
- Opening a UC/MCP agent detail page shows no sub-agents, owner
  fallback works, and access is granted to the owner +
  `CAN_USE_DEFERRED` to everyone else.
- Sending a message to a UC/MCP agent renders the Phase-2 notice with
  the full UC name in backticks, followed by the normal "done" state.
- `uv run python scripts/audit_agent_types.py` exits 0.
- `pytest tests/` — all green, including the 16 new UC-discovery cases
  and the two new stub cases in `tests/test_chat_streaming.py`.

---

## 9. Deploy G — UC HTTP + MCP chat invocation (2026-04-27 follow-up)

Deploy G replaces the Phase-1 stub for `uc:*` and `mcp:*` chat with
real streaming invocation. Phases 1 (discovery, access checks, badge
fix) are untouched. MAS and Genie chat are untouched.

### 9.1 What shipped

1. `src/agent_hub/backend/services/chat_service.py`
   - New helpers: `_stream_http_connection` (UC function + UC HTTP
     Connection via SQL Statements REST), `_stream_mcp` (JSON-RPC
     over streamable-HTTP MCP), plus ~10 private helpers for
     warehouse resolution, privilege probing, SP-driven SQL execute,
     MCP URL/bearer resolution, tool-list caching, tool picking, and
     argument-building.
   - `stream_chat` gains `sp_ws` + `tool_choice` params. The `uc:` /
     `mcp:` branches now dispatch to the new invokers; the old stub
     is behind `AGENT_HUB_DISABLE_UC_MCP_CHAT=1`.
2. `src/agent_hub/backend/models.py` — `ChatRequest.tool_choice`
   (`Optional[str]`).
3. `src/agent_hub/backend/router.py` — `/chat/{endpoint_name}`
   forwards `sp_ws` and `body.tool_choice` into `chat_service`.
4. `pyproject.toml` — adds `mcp>=1.2.0` (kept for future direct
   use; the runtime path uses `httpx` JSON-RPC for portability).
5. Frontend:
   - `lib/types.ts` — new `SSEEventType` members and
     `ChatTimelineEvent` / `McpToolDescriptor` shapes.
   - `stores/chat-store.ts` — `timelineEvents` + `pendingToolChoice`
     state.
   - `hooks/use-chat.ts` — reducer handles `tool_call`,
     `tool_result`, `needs_tool_choice`, and an optional
     `toolChoice` kwarg on `sendMessage`.
   - New components:
     `ui/components/chat/tool-call-block.tsx` and
     `ui/components/chat/tool-picker.tsx`.
   - `chat.new.tsx` + `chat.$conversationId.tsx` render the new
     components inline with messages and re-send on tool pick.
   - `lib/agent-type.ts` gains `emptyStateHint()` for agent-type
     specific first-run copy.
6. Tests: `tests/test_chat_streaming.py` updates — `uc` and `mcp`
   stub-emission tests renamed and re-pinned under the kill-switch,
   plus two new dispatch tests verifying the invoker is called with
   the kill-switch off.
7. Docs: `docs/obo-auth-design.md §16` (see §16.4 for the SSE
   event contract).

### 9.2 Files touched in Deploy G

- **Backend**: `chat_service.py`, `models.py`, `router.py`,
  `pyproject.toml`.
- **Frontend**: `lib/types.ts`, `lib/agent-type.ts`,
  `stores/chat-store.ts`, `hooks/use-chat.ts`, two new files in
  `components/chat/`, `chat.new.tsx`, `chat.$conversationId.tsx`.
- **Docs**: `docs/obo-auth-design.md`, this rollback doc.

### 9.3 Rollback — environment flag (preferred)

Fastest safe revert is flipping one env var and redeploying. No DB
migration to reverse, no frontend rebuild needed (the new components
just never receive events and never render):

```bash
# Set on the app resource via bundle config or Databricks UI
AGENT_HUB_DISABLE_UC_MCP_CHAT=1
```

Then redeploy the app; `uc:*` and `mcp:*` chat falls back to the
Phase-1 notice stub. All other agents keep streaming.

### 9.4 Rollback — code revert

If a full revert is needed:

```bash
# Kill switch first (gives you seconds to decide vs. a redeploy)
databricks apps update agent-hub \
  --user-api-scopes "..."  # unchanged; use the last known-good set

# Then, on a feature branch:
git revert <deploy-G-commits>
databricks bundle deploy --target prod --profile <your-profile>
databricks apps deploy agent-hub --source-code-path ... --profile <your-profile>
```

### 9.5 Partial rollbacks (keep what works)

- Keep HTTP connection invocation, drop MCP: set
  `AGENT_HUB_DISABLE_UC_MCP_CHAT=1` only for `mcp:*` — not currently a
  separate flag, so achieve this by removing the MCP tag (or
  un-tagging the specific UC connection) so discovery stops upserting
  the row.
- Keep tool-call UI, drop tool picker: not supported (picker is
  required when the MCP server exposes >1 tool); in practice the
  picker only fires when there is no conventional `chat` / `ask`
  tool, so a well-behaved MCP server never triggers it.

### 9.6 No scope changes, no consent re-prompt

Deploy G does **not** change `app.yaml` or `databricks.yml`. The
same `sql` + `serving.serving-endpoints` + `dashboards.genie` scopes
that closed the P11 loop are sufficient for Phase 2; EXECUTE is
checked via OBO `has_privilege` probes, and the actual SQL runs
through the SP + admin warehouse.

### 9.7 Verification baseline (post-Deploy-G)

- `chat.uc_http start ...` / `chat.uc_http ok ...` log lines appear
  when a user sends a message to a `uc:` agent. No request or
  response bodies are logged.
- `chat.mcp start ... transport=<streamable_http|sse> url_host=...`
  log lines appear for `mcp:` agents.
- SSE stream emits `started` → (optional `status`/`tool_call`) →
  `token` chunks → `done` for both kinds. For MCP servers with >1
  non-conventional tool, a `needs_tool_choice` event is emitted and
  the UI renders a picker.
- `AGENT_HUB_DISABLE_UC_MCP_CHAT=1` restores the Phase-1 stub verbatim
  (error banner on a `uc:` / `mcp:` agent disappears, friendly notice
  returns).
- `pytest tests/test_chat_streaming.py` — all green (30 cases
  including 4 new dispatch / kill-switch cases).
- Frontend `npm run build` produces a clean bundle; no new runtime
  warnings in the browser console on dev or prod.

---

## 10. Deploy H — "Clarity" iOS dual-theme redesign (2026-04-27 follow-up)

Deploy H is a **UI-only** change. Backend APIs are additive
(`/user/prefs`, `/app/config`), no existing route shape changes, and
no migration is required beyond the one already included in Deploy G
(the `user_prefs` table).

### 10.1 What shipped

- Replaced the "Observatory" Design Context in `.impeccable.md` with
  the "Clarity" direction (warm neutrals, iOS voice, dual theme).
- Rewrote `src/agent_hub/ui/styles/globals.css` with an OKLCH
  dual-theme token set; default `@theme` block seeds Tailwind
  utilities with dark values; `[data-theme="light"]` /
  `[data-theme="dark"]` blocks re-bind the same tokens at runtime.
- Added `ThemeProvider` (`ui/providers/theme-provider.tsx`), the
  `ThemeToggle` segmented control, and `MobileTabBar` for <768px.
- Refactored every primitive (`button`, `card`, `badge`, `tooltip`,
  new `input`) and every chat / catalog / admin surface to the new
  tokens and iOS grouped-list patterns.
- Swapped Satoshi / Switzer (Observatory fonts) for a system-first
  stack with Pretendard Variable fallback; removed the Fontshare CDN
  from `ui/index.html`.
- New backend endpoint `GET /api/v1/app/config` returns
  `{ legacy_ui: bool }`, gated by the `AGENT_HUB_LEGACY_UI` env var.

### 10.2 Rollback

Two independent rollback paths, pick the least invasive that resolves
the incident:

1. **Runtime flip (zero code change).** In the Databricks App UI set
   `AGENT_HUB_LEGACY_UI=1` on both dev and prod environments. The next
   request the frontend makes to `/api/v1/app/config` returns
   `legacy_ui: true`; `ThemeProvider` applies `data-theme="dark"`
   unconditionally, hides the theme toggle, and keeps the Clarity
   tokens — which visually matches the old dark look because the
   previous UI was dark-only. **No database change, no deploy, reverts
   in <=60s** after the frontend refetches config (soft-refresh or
   next route load).

2. **Full CSS revert.** If the Clarity tokens themselves are the
   problem, revert `src/agent_hub/ui/styles/globals.css` to the
   commit immediately preceding Deploy H and redeploy. All component
   classes (`bg-surface`, `text-text-primary`, etc.) continue to work
   against whatever tokens the reverted file defines. No DB revert.

3. **Table revert (optional, only if needed for cleanliness).** The
   `user_prefs` table is harmless to keep — it stores three valid
   enum values and is the source of truth for a single user-scoped
   setting. Only drop it if you're rolling back multiple deploys:

   ```sql
   DROP TABLE IF EXISTS user_prefs;
   ```

### 10.3 Files touched in Deploy H

| File | Role in rollback |
| --- | --- |
| `src/agent_hub/ui/styles/globals.css` | Primary revert target (single-file CSS revert). |
| `src/agent_hub/ui/providers/theme-provider.tsx` | Reads `AGENT_HUB_LEGACY_UI`; revert only if the provider itself misbehaves. |
| `src/agent_hub/ui/components/layout/theme-toggle.tsx` | Hidden automatically when `legacy_ui=true`. |
| `src/agent_hub/ui/components/layout/mobile-tab-bar.tsx` | New; safe to leave if reverting CSS only. |
| `src/agent_hub/ui/components/ui/{button,card,badge,input,tooltip}.tsx` | Primitives still work against legacy tokens after CSS revert. |
| `src/agent_hub/ui/components/chat/{message-bubble,streaming-message,tool-call-block,chat-input,agent-header}.tsx` | Same — class names are token-based. |
| `src/agent_hub/ui/components/catalog/{agent-card,genie-space-card,search-input,empty-catalog,sub-agent-row}.tsx` | Same. |
| `src/agent_hub/ui/routes/_sidebar/{catalog.index,catalog.$agentId,admin.settings,admin.catalog}.tsx` | Same. |
| `src/agent_hub/backend/router.py` | New `/app/config` handler — safe to leave live. |
| `src/agent_hub/backend/models.py` | Adds `AppConfigOut` — additive. |
| `src/agent_hub/ui/lib/app-config.ts` | New client — inert if the endpoint is absent (falls back to `{ legacy_ui: false }`). |
| `app.yaml` | Documents the three Phase-*n* rollback env flags as comments. |
| `scripts/check_contrast.py` | Local dev helper; never runs in CI. |
| `scripts/screenshots.py` | Local dev helper; never runs in CI. |
| `tests/test_app_config.py` | New; guards the flag contract. Keep in place. |

### 10.4 Flag semantics

- `AGENT_HUB_LEGACY_UI=1` (server env var): read at request time by
  `/api/v1/app/config`. The frontend treats truthy = "lock to dark,
  hide theme toggle, render data-legacy-ui=1 on `<html>`".
- The flag is **not** stored in `admin_settings` or `user_prefs` — a
  rollback must be visible to every user, not tied to individual
  records.
- The flag only accepts the exact string `"1"` (after strip). Truthy
  synonyms (`"true"`, `"yes"`) are ignored to prevent accidental
  activation.

### 10.5 Verification baseline (post-Deploy-H)

- `curl -sS <app>/api/v1/app/config` returns `{"legacy_ui": false}`
  by default and `{"legacy_ui": true}` after setting the env var.
- `python scripts/check_contrast.py` exits 0 against the current
  `globals.css` (all WCAG AA pairings pass).
- `pytest tests/test_app_config.py` — all 11 cases green.
- Manual browser verification: both themes render, the theme toggle
  flips `data-theme` on `<html>` in <200ms, and setting
  `AGENT_HUB_LEGACY_UI=1` in a local shell (`export AGENT_HUB_LEGACY_UI=1` then
  restart the server) hides the toggle on next refresh.

---

## 11. Deploy I — MAS names, sub-components, native detail page

Deployed: 2026-04-27 (prod target `<your-profile>`).

### 11.1 What changed

Backend (`src/agent_hub/backend/services/catalog_service.py`):

1. New helper `_load_tile_detail(ws, sp_ws, *, tile_id, endpoint_name)`
   calls `GET /api/2.0/multi-agent-supervisors/{tile_id}` with the same
   OBO-first, SP-fallback pattern as `_load_tiles_map`. Returns a
   normalized dict (`name`, `description`, `sub_agents`, ...) cached per
   endpoint for 60 seconds.
2. New helper `_resolve_tile_id_from_endpoint(ws, sp_ws, endpoint_name)`
   reads `tile_endpoint_metadata.tile_id` from
   `GET /api/2.0/serving-endpoints/{name}`. Used to backfill `tile_id`
   for MAS rows imported via plain serving-endpoints discovery (where
   the tiles list was scope-denied at the time, so `metadata_json`
   never captured the UUID).
3. `_derive_display_name` and new `_derive_description` check
   `name` / `display_name` / `title` / `metadata.name` in priority
   order instead of just `tile["name"]`.
4. New `_sub_agents_from_detail(detail)` maps detail-API children
   (`agent_type = knowledge-assistant | genie-space | unity-catalog-function`)
   into structured `SubAgentInfo` dicts, including `endpoint_ref`
   (KA endpoint / Genie space id / UC fully-qualified function name).
5. `get_agent_detail` refreshes stale MAS rows on demand: if
   `display_name` looks like `Mas 93f96edd` (fallback format),
   description is blank, or sub_agents is empty, we call
   `_load_tile_detail` (backfilling `tile_id` first when missing),
   persist the fresh data with the existing `COALESCE(NULLIF(...))`
   pattern, and return it to the caller.

Frontend — catalog detail page (`catalog.$agentId.tsx`):

1. Replaced the stacked header + metadata grid with a hero block:
   type-tinted agent glyph, 32px display title, single subtitle row
   (`Type · Owner · Access`), and a prominent Start Chat CTA.
2. Dropped the duplicate Type row from Details.
3. Collapsed Endpoint behind a `<details>` disclosure ("Technical
   details") so the page leads with the human story.

Frontend — shared components:

1. New `src/agent_hub/ui/lib/agent-glyph.ts` centralizes icon +
   tint mapping for agent and sub-component types. Keeps the hero,
   sub-agent rows, and card chips visually consistent.
2. `sub-agent-row.tsx` redesigned as a full-row tappable cell: tinted
   icon tile, name + type label on line 1, endpoint_ref in monospace
   on line 2, trailing chevron, press + hover states.
3. `catalog.index.tsx` grid children animate in via a staggered
   `.stagger-child` / `--i` CSS pattern; the filter row switches to a
   horizontally-scrollable chip strip below `sm` (touch targets 44px+).
4. `agent-card.tsx` + chat conversation sidebar: replaced `transition-all`
   with scoped transitions (`transition-[transform,border-color,box-shadow]`
   driven by the new `--ease-out-quart` + `--duration-med` tokens) and
   added `active:scale-[0.985]` press states.

Frontend — motion tokens (`globals.css`):

1. Added `--ease-out-quart`, `--ease-out-quint`, `--ease-out-expo` and
   `--duration-fast/med/slow` tokens.
2. `@keyframes fade-slide-in` + `.stagger-child` utility. The existing
   `prefers-reduced-motion` block continues to zero these out.

### 11.2 Platform constraint + resolved workaround — MAS refresh ACL

`GET /api/2.0/multi-agent-supervisors/{tile_id}` and `GET /api/2.0/tiles`
both require the **`all-apis`** OAuth scope. The OBO token minted for
Databricks Apps does **not** include this scope, and the app-level
`user_authorization.scopes` field in `app.yaml` silently filters
`all-apis` out (it is not an Apps-recognized scope value). OBO
therefore cannot call these two endpoints at all.

The SP fallback **does** have `all-apis`, but the MAS detail endpoint
also enforces a per-tile ACL that is separate from the serving
endpoint ACL. The ACL surface is:

```
/api/2.0/permissions/knowledge-assistants/{tile_id}
```

(`knowledge-assistants` is the object_type for both KA and MAS tiles.)
Only `CAN_MANAGE` on the tile unblocks the detail endpoint — `CAN_QUERY`
on either the tile or the serving endpoint is insufficient and still
returns `"You do not have read access to the agent."`.

#### 11.2.1 Required onboarding step for MAS / KA tiles

After the app is installed in a workspace, the app's Service Principal
must hold `CAN_MANAGE` on every MAS/KA tile we want to render. The
SP id is surfaced in `databricks apps get agent-hub`.

**Preferred path — admin buttons on `/admin/catalog` (Deploy J).** An
admin who manages the target tiles opens `/admin/catalog` and clicks:

1. **Grant catalog access** — iterates every MAS/KA catalog row, runs
   `GET /api/2.0/permissions/knowledge-assistants/{tile_id}` via OBO,
   and `PATCH`es the app SP in with `CAN_MANAGE` for each tile where
   it is missing. Toasts summarise `granted / already_granted /
   unauthorized / failed` per tile. Tiles the admin does **not**
   manage come back as `unauthorized` — a different admin (or the
   tile owner) has to grant those.
2. **Rescan metadata** — hybrid OBO + SP. For every MAS/KA row:
   - ``serving_endpoints.get`` and ``tile_endpoint_metadata.tile_id``
     resolution run under the admin's **OBO** (only needs
     `serving.serving-endpoints`; admin has View on any tile they
     manage). Grant-catalog-access only writes the tile ACL, **not**
     the serving-endpoint ACL, so an SP-only rescan would fail with
     `User does not have permission 'View' on Endpoint …` on every
     newly-granted tile (caught during Deploy J live-verify,
     2026-04-27).
   - The `/api/2.0/multi-agent-supervisors/{tile_id}` call runs under
     the **SP** because that endpoint requires the `all-apis` scope
     which Databricks Apps OBO cannot carry. The SP must already be
     in the tile ACL with `CAN_MANAGE` — run "Grant catalog access"
     (or the curl fallback) first.
   Bypasses the 60-second `_TILE_DETAIL_CACHE` (`force=True`) and
   upserts `display_name` / `description` / `agent_type` /
   `metadata_json.sub_agents` into `catalog_config`. Toast reports
   `refreshed / unchanged / failed / skipped`. Query invalidation
   pushes the fresh values to `/catalog` and each detail page on the
   next render.

Grant-then-Rescan is the expected flow, and the Grant toast offers a
"Rescan now" inline action when `granted > 0`. Both actions are
idempotent — clicking them a second time returns `already_granted`
/ `unchanged` rather than mutating.

> **Platform gap (2026-04-27, Deploy J live-verify).** The Grant
> button currently returns `unauthorized` for every tile, even for a
> workspace admin / tile owner. The permissions API rejects OBO with
> `Provided OAuth token does not have required scopes:
> access-management`, and Databricks Apps does **not** accept
> `access-management` as a `user_authorization.scopes` /
> `user_api_scopes` value (bundle CLI: "is not a valid scope").
> Until Apps exposes that scope, the Grant button is documentation
> for the eventual happy path; use the curl fallback below to grant
> the SP today. The `_classify_acl_error` helper detects this
> specific error and returns a message pointing the admin at §11.2.1,
> so the button is self-explanatory in the UI. Rescan metadata does
> **not** need this scope (SP already has `all-apis`) and works as
> soon as the SP ACL grant lands via curl.

**Fallback path — curl (required today; headless / CI in the future).**
Use the permissions API directly:

```
TOKEN=$(databricks auth token --profile <profile> \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
SP_ID=<app-service-principal-uuid>
TILE_ID=<agent-bricks-tile-uuid>
curl -s -X PATCH \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"access_control_list\":[{\"service_principal_name\":\"$SP_ID\",\"permission_level\":\"CAN_MANAGE\"}]}" \
  "https://<host>/api/2.0/permissions/knowledge-assistants/$TILE_ID"
```

After either path, the next render of `/catalog` (or the Rescan
button) produces logs of the form
`multi-agent-supervisor detail parsed for mas-…-endpoint: name='…', sub_agents=N`
followed by `Refreshed MAS detail…`. The Lakebase row is updated with
the real name, description, and sub-agent list, so subsequent page
loads render the real name even without a re-refresh.

#### 11.2.2 Verified example — `mas-93f96edd-endpoint`

Granted SP `CAN_MANAGE` on tile `93f96edd-a638-4d8e-9be9-a3eec93d1209`
on 2026-04-27. Backend refresh succeeded on the next page load; logs
showed `name='PTTOR_Ecosystem_Intelligence', sub_agents=5` and the UI
rendered the real name, full description, and all 5 sub-components
(policy_operations_agent, analytics_agent, thailand_news_agent,
oil_price_agent, ml_customer_intelligence_agent).

#### 11.2.3 Further automation ideas

1. ~~Admin-UI "Grant access" button~~ — **delivered in Deploy J**
   (`POST /api/v1/admin/catalog/grant-access` + the button on
   `/admin/catalog`; see §11.2.1).
2. ~~Admin-UI "Rescan metadata" button~~ — **delivered in Deploy J**
   (`POST /api/v1/admin/catalog/rescan-metadata`; bypasses the 60 s
   TTL so admins can see fresh Agent Bricks state immediately after
   granting access).
3. **Auto-grant during catalog discovery** when the admin triggering
   discovery has `CAN_MANAGE` on the tile. Same OBO assumption as the
   Grant button; avoids the two-click Grant → Rescan flow for the
   common happy path. Not yet implemented — for now we prefer the
   explicit admin action so a mis-clicked Discover cannot silently
   mutate tile ACLs.
4. **Wait for Databricks Apps to expose a narrower scope for Agent
   Bricks** so OBO can call the detail endpoint directly without
   needing any SP ACL. Track via platform roadmap.

### 11.3 Rollback lever

The `AGENT_HUB_LEGACY_UI=1` env flag from Deploy H remains applicable and
reverts the visual changes. Backend changes are additive — the refresh
path is gated on stale-row detection and silent-fails on any API error,
so removing the UI doesn't leave the DB in a bad state. To remove the
backend changes entirely, revert `catalog_service.py` to the Deploy H
baseline commit, rebuild, and redeploy.

### 11.4 Verification baseline

- Unit tests: `pytest tests/test_mas_tile_detail.py tests/test_admin_catalog_endpoints.py`
  — 41 passing (24 MAS tile detail incl. the `force=True` cache-bypass
  case, 17 admin catalog endpoint tests covering idempotency,
  403-as-unauthorized classification, ACL-present short-circuit,
  rescan cache bypass, and sub-agent payload shape).
- Full test suite: `pytest` — 157 passing.
- Post-deploy logs for `mas-93f96edd-endpoint` (after the SP was
  granted `CAN_MANAGE` on the Agent Bricks tile per §11.2.1) show
  `Resolved tile_id for mas-93f96edd-endpoint -> 93f96edd-a638-4d8e-9be9-a3eec93d1209 via serving-endpoints`
  → `multi-agent-supervisor detail parsed for mas-93f96edd-endpoint: name='PTTOR_Ecosystem_Intelligence', sub_agents=5`
  → `Refreshed MAS detail for mas-93f96edd-endpoint`. The detail page
  then renders the real tile name, full description, and the five
  sub-components (KA/Genie/UC-function rows).
- Visual: staggered catalog entrance, tinted sub-agent rows, press
  states on agent cards, collapsed technical details disclosure all
  render as designed (screenshots captured in the PR note).

---

_Authored during the close-obo-gaps rollout on 2026-04-17. Deploy D
appended 2026-04-09. Deploy E (Genie chat + streaming UX) appended
2026-04-09. Deploy F (UC-tag agent types) appended 2026-04-27. Deploy
G (UC HTTP + MCP chat invocation) appended 2026-04-27. Deploy H
("Clarity" iOS redesign) appended 2026-04-27. Deploy I (MAS names,
sub-components, native detail page) appended 2026-04-27. Delete once
the ES ticket for F3 lands and we stabilise a new baseline._
