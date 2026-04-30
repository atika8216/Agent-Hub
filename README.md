# SCGP Agent Chat Hub

Multi-agent catalog with chat and memory, built on Databricks.

## Quick Start

```bash
# Install dependencies
uv sync
bun install

# Run backend
uvicorn scgp_agent_hub.backend.app:app --reload

# Run frontend
bun run dev
```

## Architecture

- **Backend**: FastAPI with OBO authentication, Lakebase (Postgres), SSE streaming
- **Frontend**: React 19, TanStack Router/Query, Tailwind CSS v4, Radix UI
- **Design**: The Observatory design system (Satoshi + Switzer, OKLCH color palette)

## Deploy

Pre-deploy: always run the scope-drift check and capture the output.

```bash
python scripts/check_scopes.py        # exit 0 = aligned; exit 1 = drift; exit 2 = snapshot changed (F5)
databricks bundle deploy --target dev     # or --target prod
python scripts/check_scopes.py --update-snapshot   # only after a successful deploy
```

Why: `app.yaml` (`user_authorization.scopes`) and `databricks.yml`
(`user_api_scopes`) must stay in sync, and when the effective scope set
changes, every existing user must revoke + re-consent to the app for
their forwarded token to pick up the change (F5 in
[`docs/obo-auth-design.md`](docs/obo-auth-design.md)). The script prints
the F5 reminder automatically whenever the snapshot differs.

If the deploy goes sideways, see
[`docs/rollback-obo-gaps-2026-04-17.md`](docs/rollback-obo-gaps-2026-04-17.md)
for the step-by-step undo (full rollback + partial rollbacks for each
component: debug endpoint, tiles-403 logging, bootstrap-admin gate,
scope-diff script).
