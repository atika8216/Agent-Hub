# Phase 2: Agent Catalog

**Timeline**: Week 4-5
**Goal**: Users can browse MAS endpoints, see sub-agents, and check their access.
**Depends on**: Phase 1 (backend running, auth working, Lakebase connected, routes scaffolded)

---

## Sprint 2a: Catalog Backend (Week 4)

### Step 2a.1: Catalog Service

Create `src/scgp_agent_hub/backend/services/catalog_service.py`.

**`discover_from_workspace(ws: WorkspaceClient, session: Session)`**:
1. Call `ws.serving_endpoints.list()` to get all serving endpoints
2. Filter for MAS endpoints (check endpoint config for `served_entities` with agent type)
3. For each MAS endpoint, upsert into `catalog_config` table:
   - `endpoint_name` = endpoint name
   - `display_name` = endpoint name (humanized)
   - `visible` = `True` (default)
4. Return list of discovered endpoints

**`list_agents(session: Session, user_email: str)`**:
1. Query `catalog_config` where `visible = true`
2. For each visible agent, build response with:
   - Name, description, category from `catalog_config`
   - Sub-agent count (from introspection or cached)
3. Return list of `AgentOut` models

**`get_agent_detail(endpoint_name: str, ws: WorkspaceClient, session: Session)`**:
1. Look up `catalog_config` by `endpoint_name`
2. Call `ws.serving_endpoints.get(endpoint_name)` to get endpoint config
3. Introspect the MAS endpoint configuration to extract sub-agents:
   - Parse `served_entities` or endpoint config for agent definitions
   - Each sub-agent has: name, type (Genie/KA/UC Function/External MCP), description
4. Return `AgentDetailOut` with sub-agents list

**`check_access(endpoint_name: str, ws: WorkspaceClient, user_email: str) -> bool`**:
1. Use OBO WorkspaceClient to check permissions
2. Try `ws.serving_endpoints.get(endpoint_name)` -- if no permission error, user has access
3. For sub-agents, check each underlying resource:
   - Genie: check Genie space access
   - KA: check knowledge base access
   - UC Function: check function execute permission
   - External MCP: check MCP server connectivity
4. Return per-component access status

### Step 2a.2: Pydantic Models

Add to `src/scgp_agent_hub/backend/models.py`:

```python
from enum import Enum
from pydantic import BaseModel
from datetime import datetime

class SubAgentType(str, Enum):
    GENIE = "genie"
    KA = "knowledge_assistant"
    UC_FUNCTION = "uc_function"
    EXTERNAL_MCP = "external_mcp"

class SubAgentOut(BaseModel):
    name: str
    type: SubAgentType
    description: str | None = None
    has_access: bool = False
    owner: str | None = None

class AgentOut(BaseModel):
    endpoint_name: str
    display_name: str
    description: str | None = None
    category: str = "general"
    sub_agent_count: int = 0
    has_access: bool = False
    owner: str | None = None

class AgentDetailOut(AgentOut):
    sub_agents: list[SubAgentOut] = []

class AgentListOut(BaseModel):
    agents: list[AgentOut]
    total: int

class AccessCheckOut(BaseModel):
    endpoint_name: str
    has_access: bool
    sub_agent_access: dict[str, bool] = {}

class CatalogConfigOut(BaseModel):
    endpoint_name: str
    visible: bool
    display_name: str | None
    description: str | None
    category: str
    updated_by: str | None
    updated_at: datetime | None
```

### Step 2a.3: API Routes

Add to `src/scgp_agent_hub/backend/router.py`:

```python
@router.get("/agents", response_model=AgentListOut)
async def list_agents(request: Request, session: LakebaseDependency):
    ...

@router.get("/agents/{endpoint_name}", response_model=AgentDetailOut)
async def get_agent(endpoint_name: str, request: Request, session: LakebaseDependency):
    ...

@router.get("/agents/{endpoint_name}/access", response_model=AccessCheckOut)
async def check_agent_access(endpoint_name: str, request: Request):
    ...

@router.post("/agents/discover", dependencies=[Depends(require_role("admin"))])
async def discover_agents(request: Request, session: LakebaseDependency):
    ...
```

### Sprint 2a Acceptance Criteria
- [ ] `GET /api/v1/agents` returns list of visible agents
- [ ] `GET /api/v1/agents/{name}` returns agent with sub-agents
- [ ] `GET /api/v1/agents/{name}/access` returns per-component access
- [ ] `POST /api/v1/agents/discover` scans workspace for MAS endpoints (admin only)
- [ ] Access check uses OBO to verify CAN_QUERY permission
- [ ] Sub-agents correctly typed (Genie, KA, UC Function, External MCP)

---

## Sprint 2b: Catalog Frontend (Week 5)

### Step 2b.1: TanStack Query Hooks

Add to `lib/api.ts`:

```typescript
// Types
interface Agent {
  endpoint_name: string;
  display_name: string;
  description: string | null;
  category: string;
  sub_agent_count: number;
  has_access: boolean;
  owner: string | null;
}

interface SubAgent {
  name: string;
  type: "genie" | "knowledge_assistant" | "uc_function" | "external_mcp";
  description: string | null;
  has_access: boolean;
  owner: string | null;
}

interface AgentDetail extends Agent {
  sub_agents: SubAgent[];
}

// Hooks
export function useAgents() {
  return useQuery({
    queryKey: queryKeys.agents.all,
    queryFn: () => api.get<{ agents: Agent[]; total: number }>("/agents"),
  });
}

export function useAgent(endpointName: string) {
  return useQuery({
    queryKey: ["agents", endpointName],
    queryFn: () => api.get<AgentDetail>(`/agents/${endpointName}`),
  });
}

export function useAgentAccess(endpointName: string) {
  return useQuery({
    queryKey: ["agents", endpointName, "access"],
    queryFn: () => api.get(`/agents/${endpointName}/access`),
  });
}
```

### Step 2b.2: Agent Catalog Page

Implement `routes/_sidebar/catalog.tsx`:

**Components needed**:
- `AgentCard` -- displays agent name, description, sub-agent count, access badge
- `AccessBadge` -- green checkmark or red lock with text
- `SearchInput` -- search bar for filtering agents
- `FilterChips` -- filter by type, access status

**Layout**:
1. Header row: "Agent Catalog" title + search input + filter chips
2. Grid: `grid-template-columns: repeat(auto-fit, minmax(320px, 1fr))` with 16px gap
3. Each `AgentCard`:
   - Agent name (bold)
   - Description (2-line clamp, muted)
   - Sub-agent count badge
   - Access indicator (prominent, top-right)
   - Click navigates to `/catalog/${endpoint_name}`

**Filtering** (client-side):
- Search: match against name + description
- Access filter: "All", "Accessible", "No Access"

### Step 2b.3: Agent Detail Page

Implement `routes/_sidebar/catalog.$agentId.tsx`:

**Layout**:
1. Breadcrumb: "Catalog > {agent_name}"
2. Agent header: name, description, owner, overall access badge
3. "Start Chat" button (disabled if no access, with tooltip explaining why)
4. Sub-agents table:
   - Name column
   - Type column with color-coded badge (using design tokens from Phase 0)
   - Access column (green check / red lock)
   - Description column
   - For no-access rows: "Request Access" button linking to owner email

**Sub-agent type badge colors**:
- Genie: `bg-blue-500/10 text-blue-400 border-blue-500/20`
- KA: `bg-purple-500/10 text-purple-400 border-purple-500/20`
- UC Function: `bg-amber-500/10 text-amber-400 border-amber-500/20`
- External MCP: `bg-teal-500/10 text-teal-400 border-teal-500/20`

### Step 2b.4: Empty and Loading States

**Empty catalog**: Show the "No Agents Found" design from Phase 0 mockups
**Loading**: Skeleton cards in the grid (3-column shimmer)
**Error**: Error boundary with retry button

### Sprint 2b Acceptance Criteria
- [ ] Catalog page renders grid of agent cards
- [ ] Access badges show green/red status prominently
- [ ] Search filters agents by name/description
- [ ] Clicking card navigates to agent detail
- [ ] Agent detail shows sub-agents with typed badges
- [ ] "Start Chat" button links to `/chat/new?agent=endpoint_name`
- [ ] Empty state renders when no agents
- [ ] Loading skeletons appear during data fetch
- [ ] "Request Access" links include owner email

---

## Phase 2 Completion Checklist

- [ ] Backend: all 4 catalog API endpoints working
- [ ] Backend: OBO access check verifies CAN_QUERY
- [ ] Backend: discover endpoint finds MAS endpoints from workspace
- [ ] Frontend: catalog grid with search and filters
- [ ] Frontend: agent detail with sub-agents and typed badges
- [ ] Frontend: access indicators on catalog and detail pages
- [ ] Frontend: "Start Chat" CTA on agent detail
- [ ] Frontend: empty and loading states
- [ ] Integration: frontend calls backend, data flows end-to-end
