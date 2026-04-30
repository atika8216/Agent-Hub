# Draft — Engineering Support ticket

> **Status:** DRAFT. Do not auto-file. Copy-paste into JIRA when ready.
> **Component:** `apps-platform` / `agent-bricks`
> **Severity:** Sev 4 (feature-parity gap, workaround in place)

---

## Title

Apps OBO token missing scope required by `GET /api/2.0/tiles` — cannot classify / name Agent Bricks endpoints from Databricks Apps

## TL;DR

The Databricks App `scgp-agent-hub` (running in workspace `fevm-aan-demo`)
needs to call `GET /api/2.0/tiles` on behalf of the signed-in user so the
Agent Catalog can label MAS/KA endpoints with their Agent Bricks display
names and parse their `instructions` into sub-components. The forwarded
user OAuth token (`X-Forwarded-Access-Token`) returns 403 `Provided OAuth
token does not have required scopes`. We have tried every scope currently
exposed via `user_api_scopes` (`serving.serving-endpoints`, `sql`,
`dashboards.genie`, `iam.access-control:workspace`) and none unlock the
tiles API.

We would like to know:

1. **What OAuth scope does `GET /api/2.0/tiles` require?**
2. **Is that scope exposable via `user_api_scopes` in the Apps manifest today?**
3. **Why does the bundle CLI reject `model-serving` and
   `iam.access-control:workspace` as "not a valid scope"? Is there an
   authoritative list of accepted `user_api_scopes` values?**
4. **If the tiles scope is not exposable via OBO today, is there an ETA or
   an approved workaround (SP pattern that sees user-owned tiles)?**

## Context

- App ID / path: `scgp-agent-hub`
- Workspace: `fevm-aan-demo` (`https://fevm-aan-demo-workspace.cloud.databricks.com`)
- Current user_api_scopes (see [databricks.yml](../../databricks.yml)):

  ```yaml
  user_api_scopes:
    - serving.serving-endpoints
    - sql
    - dashboards.genie
  ```

- Current `app.yaml` additionally lists `model-serving` and
  `iam.access-control:workspace`. Both are **rejected** by
  `databricks bundle deploy` as *"not a valid scope"* (confirmed 2026-04-17
  on `fevm-aan-demo`, dev target). We cannot try them end-to-end without a
  platform change.

## Reproduction

From inside the app, using the forwarded user token:

```python
ws.api_client.do("GET", "/api/2.0/tiles")
```

Fails with:

```
Provided OAuth token does not have required scopes
```

From the same app via the app's **service principal** client, `/api/2.0/tiles`
returns `{"tiles": []}` — confirming tiles are user-owned and the SP
fallback is not a real workaround.

From a personal CLI token (full user scopes), the same call succeeds and
returns the expected 4 tiles (2 MAS + 2 KA) on `fevm-aan-demo`.

## Impact

- Prod Agent Catalog UI falls back to raw endpoint names
  (`mas-<hash>-endpoint`) instead of the friendly display names shown in
  the Agent Bricks UI.
- Sub-component breakdown under each MAS card is empty on prod because we
  rely on `tile.instructions` to list Genie Space / UC Function children.
- Functional classification still works via a naming-convention fallback
  (`mas-…-endpoint` → MAS, `ka-…-endpoint` → KA). So the app is
  **operational**; this is a quality-of-experience gap, not a P0.

## Cross-references

- Engineering design note: [`docs/obo-auth-design.md`](../obo-auth-design.md) — see F3.
- Prior Slack thread on MAS + `model-serving` scope:
  [#apa-apps p1775754820](https://databricks.slack.com/archives/C05E5R3F57B/p1775754820145389)
- Related Apps allowlist gap (not the same, but same class of issue):
  [#eng-databricks-apps p1776255993](https://databricks.slack.com/archives/C08CVK3UKP1/p1776255993507219)

## Evidence attached (to paste into JIRA)

1. Truncated app log lines from prod discovery run on 2026-04-17 after the
   P2a + P2b instrumentation deployed:

   ```
   WARNING Tiles API lookup via obo failed: Provided OAuth token does not have required scopes [ReqId: a9d21405-8804-4210-b05b-4470c78b67db]. | required_scope=unknown
   INFO    Tiles API lookup OK via sp
   INFO    Loaded 0 Agent Bricks tiles
   ```

   **The 403 payload does not name the required scope** — only the generic
   "does not have required scopes" message. This makes the user-facing
   remediation path (add the scope, tell users to re-consent) impossible
   without platform input. Our structured logger writes
   `required_scope=unknown` in that case, which is what we want the
   ticket to disambiguate.

2. Fresh-token introspection via `GET /api/v1/debug/me/scopes` on prod the
   same day confirms all 5 declared scopes plus the 4 standard OIDC scopes
   are in the forwarded token:

   ```json
   {
     "ok": true,
     "declared": ["dashboards.genie", "iam.access-control:read",
                  "iam.current-user:read", "serving.serving-endpoints", "sql"],
     "in_token": [..., "email", "offline_access", "openid", "profile", ...],
     "missing_from_token": [],
     "notes": []
   }
   ```

   So the 403 is not an F5 re-consent gap — we are genuinely missing a
   scope the platform hasn't exposed to `user_api_scopes` yet.
3. Matrix of scopes tried → all 403:
   - `serving.serving-endpoints`: 403
   - `iam.access-control:read` (platform-auto-granted): 403
   - `iam.current-user:read` (platform-auto-granted): 403
   - `iam.access-control:workspace`: rejected by bundle CLI, never attempted
   - `model-serving`: rejected by bundle CLI, never attempted
   - `sql`: 403
   - `dashboards.genie`: 403
4. SP fallback returns `{"tiles": []}` (0 tiles), whereas a personal CLI
   user token on the same workspace returns 4 tiles. So the SP fallback
   is **not** a real workaround — it silently succeeds but returns no
   data. The catalog's MAS/KA classification only survives today because
   we have a naming-convention fallback (`mas-*-endpoint` /
   `ka-*-endpoint`).

## Asks

- [ ] Confirm the scope name required by `/api/2.0/tiles`.
- [ ] Confirm whether that scope is presently exposable via `user_api_scopes`.
- [ ] If it is exposable but was missed, please add to the allowlist (see
      `AppsCommonConf.scala`).
- [ ] If it is not exposable, suggest a stable workaround or timeline.

---

## Internal notes (remove before filing)

- Workaround in the codebase lives in
  [`src/scgp_agent_hub/backend/services/catalog_service.py`](../../src/scgp_agent_hub/backend/services/catalog_service.py)
  `_load_tiles_map` — OBO → SP fallback with structured logging.
- After this ticket closes, we should revert the fallback to OBO-only
  and delete the naming-convention classifier branch.
