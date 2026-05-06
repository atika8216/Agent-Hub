# Phase 1: Foundation and Scaffolding

**Timeline**: Week 3
**Goal**: Runnable local dev environment with auth, database, and basic routing.
**Depends on**: Phase 0 (design tokens, API contract)

---

## Step 1.1: Python Backend Scaffolding

### Directory Structure

```
src/
  agent_hub/
    __init__.py
    _metadata.py
    backend/
      __init__.py
      app.py                  # FastAPI app creation (create_app factory)
      router.py               # Main API router
      models.py               # Pydantic request/response models
      core/
        __init__.py
        _factory.py           # App factory with middleware
        _config.py            # Logger and app config
        auth.py               # OBO auth middleware
        lakebase.py           # Lakebase connection, engine, session dependency
      services/
        __init__.py
        base.py               # Base exceptions (NotFoundError, ForbiddenError)
        catalog_service.py    # Agent discovery, introspection, access checks
        chat_service.py       # SSE streaming, message persistence
        memory_service.py     # Short-term and long-term memory
        admin_service.py      # Admin settings and catalog config CRUD
    ui/                       # React SPA (created in Step 1.2)
    __dist__/                 # Built UI (created by vite build)
```

### Files to Create

**`pyproject.toml`** -- Based on Agent Catalog App reference pattern:
```toml
[project]
name = "agent-hub"
dynamic = ["version"]
description = "Agent Chat Hub - Multi-agent catalog with chat and memory"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.119.0",
    "pydantic-settings>=2.11.0",
    "uvicorn>=0.37.0",
    "databricks-sdk>=0.74.0",
    "sqlmodel>=0.0.27",
    "psycopg[binary,pool]>=3.2.11",
    "httpx>=0.27.0",
]

[dependency-groups]
dev = [
    "pytest>=8.0.0",
    "pytest-cov>=6.0.0",
    "pytest-asyncio>=0.24.0",
]

[tool.apx.metadata]
app-name = "agent-hub"
app-slug = "agent_hub"
app-entrypoint = "agent_hub.backend.app:app"
api-prefix = "/api/v1"
metadata-path = "src/agent_hub/_metadata.py"

[tool.apx.ui]
root = "src/agent_hub/ui"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

**`src/agent_hub/_metadata.py`**:
```python
api_prefix = "/api/v1"
dist_dir = "__dist__"
```

**`src/agent_hub/backend/core/auth.py`** -- Based on `Agent Catalog App/src/agent_catalog_app/backend/core/auth.py`:
- `_resolve_user_email(request)` extracts from `X-Forwarded-Email` header or Databricks CLI
- `_get_user_role(session, email)` looks up role from `user_roles` table
- `require_role(*allowed_roles)` dependency factory
- First-user-is-admin pattern

**`src/agent_hub/backend/core/lakebase.py`** -- Based on `Agent Catalog App/src/agent_catalog_app/backend/core/lakebase.py`:
- `DatabaseConfig` pydantic settings (PGHOST, PGPORT, PGDATABASE, etc.)
- `_build_engine_url()` with dev (local port) and prod (Lakebase Autoscale) paths
- `create_db_engine()` with connection pooling and OBO credential refresh
- `validate_db()` checks connectivity
- `_run_migrations_bg()` creates tables via DDL in background thread
- `LakebaseDependency` type alias for FastAPI dependency injection

**`src/agent_hub/backend/core/_factory.py`**:
- `create_app(routers)` factory function
- CORS middleware (allow localhost:3000 in dev)
- WorkspaceClient initialization on startup
- Lakebase lifespan integration
- Exception handlers mapping service errors to HTTP status codes
- Static SPA mount from `__dist__` when present

**`src/agent_hub/backend/app.py`**:
```python
from .core import create_app
from .router import router

app = create_app(routers=[router])
```

**`src/agent_hub/backend/router.py`**:
```python
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1")

@router.get("/me")
async def me(request: Request):
    # Return current user info via OBO
    ...
```

**`src/agent_hub/backend/services/base.py`**:
```python
class NotFoundError(Exception): ...
class ForbiddenError(Exception): ...
class ConflictError(Exception): ...
```

### Acceptance Criteria
- [ ] `pyproject.toml` created with all dependencies
- [ ] APX metadata configured
- [ ] Backend package structure created
- [ ] All `__init__.py` files in place
- [ ] `uv sync` installs dependencies successfully

---

## Step 1.2: React Frontend Scaffolding

### Directory Structure

```
src/agent_hub/ui/
  index.html
  main.tsx
  vite.config.ts
  package.json
  tsconfig.json
  styles/
    globals.css                # Tailwind imports + design tokens from Phase 0
  routes/
    __root.tsx                 # Root layout (QueryClientProvider, ThemeProvider)
    _sidebar/
      route.tsx                # Sidebar layout component
    _sidebar/
      index.tsx                # Redirect to /catalog
      catalog.tsx              # Agent catalog page (placeholder)
      catalog.$agentId.tsx     # Agent detail page (placeholder)
      chat.new.tsx             # New chat page (placeholder)
      chat.$conversationId.tsx # Chat page (placeholder)
      admin.tsx                # Admin redirect
      admin.catalog.tsx        # Admin catalog management (placeholder)
      admin.settings.tsx       # Admin settings (placeholder)
  components/
    ui/                        # Shared UI primitives (button, badge, etc.)
    layout/
      sidebar-layout.tsx       # Main sidebar wrapper
  lib/
    api.ts                     # Axios client + TanStack Query hooks
    utils.ts                   # cn() utility (clsx + tailwind-merge)
```

### Files to Create

**`package.json`**:
```json
{
  "name": "agent-hub-ui",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "@tanstack/react-router": "latest",
    "@tanstack/react-query": "latest",
    "@radix-ui/react-dialog": "latest",
    "@radix-ui/react-dropdown-menu": "latest",
    "@radix-ui/react-switch": "latest",
    "@radix-ui/react-tooltip": "latest",
    "@radix-ui/react-radio-group": "latest",
    "axios": "latest",
    "clsx": "latest",
    "tailwind-merge": "latest",
    "class-variance-authority": "latest",
    "lucide-react": "latest",
    "zustand": "latest",
    "sonner": "latest",
    "motion": "latest"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "latest",
    "typescript": "^5.9.0",
    "vite": "^7.0.0",
    "@tailwindcss/vite": "latest",
    "tailwindcss": "^4.0.0"
  }
}
```

**`vite.config.ts`** with proxy to FastAPI:
```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@": path.resolve(__dirname, ".") },
  },
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "../__dist__",
    emptyOutDir: true,
  },
});
```

**`styles/globals.css`** -- Copy design tokens from Phase 0, adapting the Agent Catalog App pattern with project-specific customizations.

**`lib/api.ts`** -- Axios instance + TanStack Query hooks:
```typescript
import axios from "axios";

export const api = axios.create({ baseURL: "/api/v1" });

// Query key factories
export const queryKeys = {
  agents: { all: ["agents"] as const },
  conversations: { all: ["conversations"] as const },
  admin: { settings: ["admin", "settings"] as const },
};
```

### Acceptance Criteria
- [ ] `package.json` created with all dependencies
- [ ] `npm install` succeeds
- [ ] `npm run dev` starts Vite dev server on port 3000
- [ ] Vite proxy forwards `/api` to FastAPI on port 8000
- [ ] TanStack Router configured with file-based routes
- [ ] All placeholder route files created
- [ ] `globals.css` has design tokens from Phase 0
- [ ] Sidebar layout renders with nav groups

---

## Step 1.3: OBO Authentication

### Backend

Implement in `src/agent_hub/backend/core/auth.py`:

1. `_resolve_user_email(request)`:
   - Check `X-Forwarded-Email` header (Databricks Apps deployed mode)
   - Fallback: `WorkspaceClient().current_user.me().user_name` (local dev)
   - Final fallback: `os.environ.get("USER", "anonymous")`

2. `GET /api/v1/me` endpoint in `router.py`:
   - Returns `{ "email": "...", "role": "admin|viewer", "display_name": "..." }`
   - Uses `_resolve_user_email` + `_get_user_role`

3. `require_role(*roles)` dependency:
   - Same pattern as Agent Catalog App `auth.py`
   - Role hierarchy: `viewer < operator < admin`

### Frontend

Create `lib/hooks/useCurrentUser.ts`:
- TanStack Query hook calling `GET /api/v1/me`
- Cache user info globally
- Used in sidebar to show user avatar and role badge

### Acceptance Criteria
- [ ] `GET /api/v1/me` returns user info
- [ ] OBO works with `X-Forwarded-Email` header
- [ ] OBO falls back to Databricks CLI profile for local dev
- [ ] `require_role("admin")` blocks non-admin users
- [ ] First user auto-promoted to admin
- [ ] Frontend displays current user in sidebar

---

## Step 1.4: Lakebase Connection and Migrations

### Backend

Implement in `src/agent_hub/backend/core/lakebase.py` following the Agent Catalog App pattern.

**DDL Statements** (in `_ALEMBIC_ONLY_TABLES_DDL` list):

```sql
-- conversations
CREATE TABLE IF NOT EXISTS conversations (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id TEXT NOT NULL,
    agent_endpoint_name TEXT NOT NULL,
    title TEXT DEFAULT 'New Conversation',
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- messages
CREATE TABLE IF NOT EXISTS messages (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- memory_long_term
CREATE TABLE IF NOT EXISTS memory_long_term (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id TEXT NOT NULL,
    insight_type TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- catalog_config
CREATE TABLE IF NOT EXISTS catalog_config (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    endpoint_name TEXT NOT NULL UNIQUE,
    visible BOOLEAN DEFAULT true,
    display_name TEXT,
    description TEXT,
    category TEXT DEFAULT 'general',
    updated_by TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- admin_settings
CREATE TABLE IF NOT EXISTS admin_settings (
    key TEXT PRIMARY KEY,
    value JSONB DEFAULT '{}'::jsonb,
    updated_by TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- user_roles
CREATE TABLE IF NOT EXISTS user_roles (
    id SERIAL PRIMARY KEY,
    user_email TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT 'viewer',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
```

**Indexes** to add after table creation:
```sql
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_conversations_agent ON conversations(agent_endpoint_name);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_memory_user ON memory_long_term(user_id);
```

### Acceptance Criteria
- [ ] Lakebase connection established on app startup
- [ ] Tables created via background migration thread
- [ ] `LakebaseDependency` injectable in FastAPI routes
- [ ] Local dev mode with `APX_DEV_DB_PORT` works
- [ ] Production mode with Lakebase Autoscale endpoint works
- [ ] All 6 tables created with correct schema
- [ ] Indexes created for query performance

---

## Step 1.5: Deployment Configuration Templates

### Files to Create

**`.env.example`**:
```env
# Databricks authentication
DATABRICKS_HOST=https://your-workspace.cloud.databricks.com
DATABRICKS_CONFIG_PROFILE=chatbot_template

# Lakebase (for local dev, set APX_DEV_DB_PORT; for deployed, use PG* vars)
# APX_DEV_DB_PORT=5432
# APX_DEV_DB_PWD=postgres

# OR for deployed Lakebase:
# PGHOST=your-lakebase-host
# PGPORT=5432
# PGDATABASE=databricks_postgres

# Lakebase project config
LAKEBASE_PROJECT_ID=agent-hub
LAKEBASE_BRANCH_ID=production

# App config
LOG_LEVEL=DEBUG
```

**`databricks.yml`**:
```yaml
bundle:
  name: agent-hub

variables:
  workspace_host:
    description: Workspace URL
    default: https://YOUR_WORKSPACE.cloud.databricks.com
  app_name:
    description: Application display name
    default: agent-hub
  lakebase_project_id:
    description: Lakebase project ID
    default: agent-hub
  lakebase_branch_id:
    description: Lakebase branch ID
    default: production

targets:
  dev:
    mode: development
    default: true
    workspace:
      host: https://YOUR_WORKSPACE.cloud.databricks.com
    variables:
      app_name: agent-hub-dev
      lakebase_branch_id: development
```

**`app.yaml`**:
```yaml
command:
  - uvicorn
  - agent_hub.backend.app:app
  - --host=0.0.0.0
  - --port=8000
env:
  - name: LOG_LEVEL
    value: INFO
  - name: LAKEBASE_PROJECT_ID
    value: agent-hub
  - name: LAKEBASE_BRANCH_ID
    value: production
```

### Acceptance Criteria
- [ ] `.env.example` documents all environment variables
- [ ] `databricks.yml` has dev target configured
- [ ] `app.yaml` configured for Databricks Apps deployment
- [ ] `databricks bundle validate` passes (once workspace is set)

---

## Phase 1 Completion Checklist

- [ ] Backend starts with `uvicorn agent_hub.backend.app:app --reload`
- [ ] Frontend starts with `npm run dev` (in `src/agent_hub/ui/`)
- [ ] Vite proxy forwards `/api` to backend
- [ ] `GET /api/v1/me` returns user info
- [ ] Lakebase tables created on startup
- [ ] Sidebar layout renders with navigation
- [ ] All route placeholders load without errors
- [ ] `.env.example`, `databricks.yml`, `app.yaml` created
