# Agent Hub

Multi-agent catalog with chat and conversational memory, built on **Databricks Apps**.

- **Backend** — FastAPI, OBO (on-behalf-of) auth, Lakebase (Postgres), SSE streaming
- **Frontend** — React 19, TanStack Router / Query, Tailwind v4, Radix UI, ECharts
- **Runtime** — Databricks Apps (serverless), deployed via [Databricks Asset Bundles](https://docs.databricks.com/dev-tools/bundles/index.html)

---

## Deploy to Your Own Databricks Workspace

The steps below get a forked copy of this app running as a Databricks App under your workspace.

### 1 · Prerequisites

| What | Why |
|---|---|
| A **Databricks workspace** (AWS, Azure, or GCP) with Unity Catalog enabled | Runs the app + backs it with Lakebase |
| **[Databricks CLI](https://docs.databricks.com/dev-tools/cli/install.html)** ≥ 0.240 authenticated to that workspace | Required for `databricks bundle deploy` |
| **[uv](https://docs.astral.sh/uv/)** (Python package manager) | Installs backend Python deps |
| **[Bun](https://bun.sh/)** ≥ 1.2 *or* **Node.js** ≥ 20 | Builds the React UI |
| A **SQL Warehouse** in the target workspace | Used for Unity Catalog tag discovery |
| A **Lakebase** database instance (Postgres) | Stores conversations, pins, suggestions |
| Permission to create **Databricks Apps** in the workspace | Required for deploy |

Authenticate the CLI once before deploying:

```bash
databricks auth login --host https://<your-workspace>.cloud.databricks.com --profile my-workspace
```

### 2 · Clone

```bash
git clone https://github.com/atika8216/agent_hub.git
cd agent_hub
```

### 3 · Configure for your workspace

You need to replace the author's workspace / warehouse / Lakebase / admin-email values with your own. Two files, three variables in each.

**`databricks.yml`** — update the `variables` block and both target `workspace` blocks:

```yaml
variables:
  scgp_admin_warehouse_id:
    default: <YOUR_SQL_WAREHOUSE_ID>           # e.g. 7b7cd1e7e17a3862
  workspace_host:
    default: https://<your-workspace>.cloud.databricks.com
  lakebase_project_id:
    default: <your-lakebase-project>
  lakebase_branch_id:
    default: production

targets:
  dev:
    workspace:
      host: https://<your-workspace>.cloud.databricks.com
      profile: my-workspace                    # the CLI profile from step 1
  prod:
    workspace:
      host: https://<your-workspace>.cloud.databricks.com
      profile: my-workspace
```

**`app.yaml`** — update the `env` block:

```yaml
env:
  - name: LAKEBASE_PROJECT_ID
    value: <your-lakebase-project>
  - name: LAKEBASE_BRANCH_ID
    value: production
  - name: BOOTSTRAP_ADMIN_EMAILS
    value: <your.email>@<your-domain>          # app owner(s), comma-separated
  - name: SCGP_ADMIN_WAREHOUSE_ID
    value: "<YOUR_SQL_WAREHOUSE_ID>"           # must match databricks.yml
```

> **Finding these values**
> - *Warehouse ID*: **SQL Warehouses** → your warehouse → ID in the URL (`/sql/warehouses/<id>`)
> - *Lakebase project / branch*: **Compute** → **Lakebase** → your instance
> - *Workspace host*: the `https://...cloud.databricks.com` URL in your browser

### 4 · Install dependencies

```bash
uv sync              # backend Python deps
bun install          # or: npm install
```

### 5 · Build the frontend

The Databricks Apps runtime serves the pre-built React bundle from `src/scgp_agent_hub/__dist__/`. Build it locally before deploying:

```bash
bun run build        # or: npm run build
```

> **Why pre-build?** `databricks.yml` deliberately excludes `package.json` / `bun.lock` from sync so the Apps runtime doesn't try to reinstall node modules via its npm proxy (which can time out on large packages like `echarts`). The shipped `__dist__/` bundle is all the runtime needs.

### 6 · Deploy

```bash
# Dev target (default)
databricks bundle deploy --target dev

# Or prod target
databricks bundle deploy --target prod
```

This uploads the source, creates the Databricks App (`scgp-agent-hub-dev` or `scgp-agent-hub`), grants you `CAN_MANAGE`, and starts the app.

Open the app in the workspace UI: **Compute → Apps → `scgp-agent-hub[-dev]`**.

### 7 · First-login consent (OBO)

Databricks Apps asks each user to consent to the OAuth scopes declared in `app.yaml` → `user_authorization.scopes`. On the first visit, click through the consent prompt. If you later change scopes, **every existing user must revoke + re-consent** for the new scopes to land in their forwarded token:

1. User → profile menu → **Revoke access** for the app
2. Reload app → re-approve consent

This is documented as step **F5** in [`docs/obo-auth-design.md`](docs/obo-auth-design.md). The helper below prints the F5 reminder whenever your scope set changes.

### 8 · Pre-deploy scope-drift check (recommended)

`app.yaml` and `databricks.yml` each declare OAuth scopes and they must stay aligned. Before every deploy:

```bash
python scripts/check_scopes.py              # exit 0 = aligned; 1 = drift; 2 = F5 required
databricks bundle deploy --target dev
python scripts/check_scopes.py --update-snapshot   # only after a successful deploy
```

---

## Local development

Run the backend and UI directly against your Databricks workspace (no App deploy needed):

```bash
uv sync
bun install

# Backend (FastAPI, hot reload)
uvicorn scgp_agent_hub.backend.app:app --reload

# Frontend (Vite dev server)
bun run dev
```

Local dev uses your `.env` file and the default Databricks CLI profile. Start from `.env.example`:

```bash
cp .env.example .env
# edit .env → set DATABRICKS_HOST, LAKEBASE_PROJECT_ID, etc.
```

---

## Project layout

```
agent_hub/
├── app.yaml                  # Databricks Apps runtime config (command, env, scopes, health)
├── databricks.yml            # Asset Bundle (targets, variables, app resource)
├── pyproject.toml            # Python package + apx metadata
├── package.json              # UI deps (Bun/npm)
├── src/scgp_agent_hub/
│   ├── backend/              # FastAPI app, services, routes
│   ├── ui/                   # React source (TanStack Router/Query)
│   └── __dist__/             # Pre-built UI bundle (gitignored locally, shipped on deploy)
├── scripts/check_scopes.py   # Scope-drift guard (see step 8)
├── tests/                    # pytest suite
└── docs/                     # Design docs (OBO auth, rollbacks, diagrams)
```

---

## Troubleshooting / rollback

- **Scope-drift & OBO issues** — [`docs/obo-auth-design.md`](docs/obo-auth-design.md)
- **Emergency rollback levers** — [`docs/rollback-obo-gaps-2026-04-17.md`](docs/rollback-obo-gaps-2026-04-17.md) documents per-phase disable flags (`SCGP_DISABLE_UC_MCP_DISCOVERY`, `SCGP_DISABLE_UC_MCP_CHAT`, `SCGP_LEGACY_UI`) you can flip via the Databricks App UI without a redeploy.

---

## License

Internal — see repository owner for usage terms.
