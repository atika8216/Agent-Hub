# Installation troubleshooting

Real failures from prior deployments and how to fix each. Match the error string in your logs against the headings below.

## "Lakebase unavailable (Project with name 'projects/X' not found)"

Two possible root causes:

### Cause A — wrong `LAKEBASE_PROJECT_ID`

The value in `app.yaml` doesn't match any project in your workspace. Verify with:

```bash
databricks lakebase list-projects --profile <profile> -o json
```

Edit `app.yaml`:

```yaml
env:
  - name: "LAKEBASE_PROJECT_ID"
    value: "<the actual project name from the list above>"
```

Redeploy: `databricks bundle deploy --target dev --profile <profile>`.

### Cause B — Apps runtime silently dropped the `env:` block

The Databricks Apps runtime YAML parser drops the entire `env:` list if comments precede the first `- name:` entry. The backend then falls back to its default project name (`agent-hub`), which usually doesn't exist.

**Bad** (silently broken — runtime ignores everything in `env:`):

```yaml
env:
  # leading comments before the first list item make the parser
  # drop the whole block silently
  - name: "LAKEBASE_PROJECT_ID"
    value: "my-project"
```

**Good** (move all comments above `env:` or after the first item):

```yaml
# Comments belong here, above the env keyword.
env:
  - name: "LAKEBASE_PROJECT_ID"
    value: "my-project"
  # Comments after each item are fine.
  - name: "LOG_LEVEL"
    value: "INFO"
```

## "unknown field: config" warning during `databricks bundle validate`

Expected and harmless — but it means the `config` block was silently dropped. The bundle CLI does **not** support setting per-app environment variables via `databricks.yml`. `app.yaml` is the only place runtime env is read from.

If you tried to add env to `databricks.yml`, move it back to `app.yaml`.

## "password authentication failed for user '<sp-uuid>'"

The app's service principal exists but lacks Lakebase ACL grants. The migration runs as the SP, and Lakebase rejected the connection.

**Preferred fix — run the bundled script.** It looks up the SP, mints a database credential as your CLI profile, and runs an idempotent grant:

```bash
python scripts/grant_lakebase_sp.py \
    --profile <profile> \
    --lakebase-project <lakebase_project_id> \
    --app-name <app-slug>
```

Then restart so the migration retries:

```bash
databricks apps stop  <app-slug> --profile <profile>
databricks apps start <app-slug> --profile <profile>
```

The script exits non-zero with a clear message if the profile lacks Lakebase admin (code `4`) or the project/endpoint isn't found (code `3`). See the script docstring for all exit codes.

**While this is pending: the Admin link still works.** Any email listed in `BOOTSTRAP_ADMIN_EMAILS` is treated as `admin` regardless of DB state — `/api/v1/me` consults the env var before touching `user_roles`. So you can reach `/admin` even during a Lakebase outage.

**UI fallback.** When the script can't run (e.g. air-gapped CLI), open **Compute → Lakebase → `<lakebase_project_id>` → Roles → New role**, pick **Service principal**, and paste the SP UUID. The UI calls the same Lakebase Roles API the script uses, so the resulting role has the required `LAKEBASE_OAUTH_V1` auth method and `DATABRICKS_SUPERUSER` membership.

The `<sp-uuid>` is in the error message. You can also see it under **Compute → Apps → <app> → Settings → Service principal**, or via `databricks apps get <app-slug> -o json | jq .service_principal_client_id`.

**Do NOT run `CREATE ROLE "<sp-uuid>" WITH LOGIN` in the Lakebase SQL editor.** That creates a `NO_LOGIN` postgres role: Lakebase tracks the role, but OAuth auth never engages, so the app still fails with `password authentication failed`. Only the Lakebase Roles API/UI sets `auth_method=LAKEBASE_OAUTH_V1` + `membership_roles=[DATABRICKS_SUPERUSER]`, which is what makes the SP's workspace OAuth token actually authenticate. If you've already created a stale role this way, the grant script will detect it (wrong `auth_method`) and recreate it cleanly.

## "scope X is not a valid scope" during `bundle deploy`

The bundle CLI rejected an OAuth scope. This is currently expected for `model-serving` and `iam.access-control:workspace` (the platform doesn't accept them in `databricks.yml` yet). They remain in `app.yaml` so the platform can re-add them automatically once accepted.

Run `python scripts/check_scopes.py` to see the diff between `app.yaml` and `databricks.yml`. Exit code:

- `0` — aligned
- `1` — drift the script can't reconcile; fix it
- `2` — F5 reminder: the user-facing scope set changed, so every existing user must revoke + re-consent

## App stuck in `STOPPED | UNAVAILABLE` after `bundle deploy`

The bundle uploaded the source but didn't start the runtime. Run:

```bash
databricks apps start <app_name>-dev --profile <profile>
databricks bundle run agent_hub --target dev --profile <profile>
```

Then wait ~60s and `databricks apps get <app_name>-dev --profile <profile>` should show `RUNNING | AVAILABLE`.

## `HTTP 401` on `/api/v1/health/live`

Expected without auth. Databricks Apps enforces OAuth on every request. Use a bearer token:

```bash
TOKEN=$(databricks auth token --profile <profile> -o json | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')
curl -H "Authorization: Bearer $TOKEN" "$APP_URL/api/v1/health/live"
```

Expected response: `{"ok": true}` with HTTP 200.

## `bun run build` succeeds but the deployed app shows a blank page

The runtime serves `src/agent_hub/__dist__/`. If the directory is missing or stale, you'll get a blank page or the FastAPI 404.

```bash
ls src/agent_hub/__dist__/index.html
```

If missing, rerun `bun install && bun run build`. The bundle's `sync.exclude` deliberately drops `package.json` / `bun.lock` from the upload, so the runtime cannot rebuild — you must build locally before each deploy.

## "unknown field: model-serving" in deploy output

You added `model-serving` (or `iam.access-control:workspace`) to `databricks.yml`'s `user_api_scopes`. Remove it — those two scopes only belong in `app.yaml` until the platform accepts them at the bundle layer.

## CLI prompts for browser auth on every command

Your profile token is expired. Re-login:

```bash
databricks auth login --host https://<workspace>.cloud.databricks.com --profile <profile>
```

## `bundle deploy` succeeds but logs show old code

Bundle uploads sometimes hit a stale workspace cache. Force a re-sync:

```bash
databricks bundle destroy --target dev --profile <profile>
databricks bundle deploy  --target dev --profile <profile>
databricks bundle run     agent_hub --target dev --profile <profile>
```

This deletes and recreates the App resource. Conversation history in Lakebase is **not** affected — that lives in the Lakebase project, not the App.
