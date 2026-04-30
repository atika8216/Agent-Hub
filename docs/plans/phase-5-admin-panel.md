# Phase 5: Admin Panel

**Timeline**: Week 9
**Goal**: Admin can manage catalog visibility and memory settings.
**Depends on**: Phase 4 (memory system working), Phase 2 (catalog config in DB)

---

## Step 5.1: Admin Service

Create `src/scgp_agent_hub/backend/services/admin_service.py`.

**`get_all_settings(session) -> dict`**:
```python
def get_all_settings(session: Session) -> dict:
    rows = session.exec(text("SELECT key, value FROM admin_settings ORDER BY key"))
    return {row[0]: json.loads(row[1]) if row[1] else None for row in rows}
```

**`update_setting(key, value, user_email, session)`**:
```python
def update_setting(key: str, value: Any, user_email: str, session: Session):
    session.execute(
        text("""
            INSERT INTO admin_settings (key, value, updated_by, updated_at)
            VALUES (:key, :value, :updated_by, NOW())
            ON CONFLICT (key) DO UPDATE SET
                value = EXCLUDED.value,
                updated_by = EXCLUDED.updated_by,
                updated_at = NOW()
        """),
        {"key": key, "value": json.dumps(value), "updated_by": user_email},
    )
    session.commit()
```

**`list_catalog_config(session) -> list[CatalogConfigOut]`**:
```python
def list_catalog_config(session: Session) -> list[CatalogConfigOut]:
    rows = session.exec(
        text("SELECT * FROM catalog_config ORDER BY display_name")
    )
    return [CatalogConfigOut(**row._mapping) for row in rows]
```

**`update_catalog_entry(endpoint_name, updates, user_email, session)`**:
```python
def update_catalog_entry(
    endpoint_name: str, updates: CatalogConfigUpdate, user_email: str, session: Session
):
    set_clauses = []
    params = {"endpoint_name": endpoint_name, "updated_by": user_email}

    if updates.visible is not None:
        set_clauses.append("visible = :visible")
        params["visible"] = updates.visible
    if updates.display_name is not None:
        set_clauses.append("display_name = :display_name")
        params["display_name"] = updates.display_name
    if updates.description is not None:
        set_clauses.append("description = :description")
        params["description"] = updates.description
    if updates.category is not None:
        set_clauses.append("category = :category")
        params["category"] = updates.category

    set_clauses.append("updated_by = :updated_by")
    set_clauses.append("updated_at = NOW()")

    session.execute(
        text(f"UPDATE catalog_config SET {', '.join(set_clauses)} WHERE endpoint_name = :endpoint_name"),
        params,
    )
    session.commit()
```

### Acceptance Criteria
- [ ] All admin service functions working with raw SQL
- [ ] Settings upsert correctly (insert or update)
- [ ] Catalog config updates specific fields only

---

## Step 5.2: Admin API Routes

Add to `router.py` (all admin routes require admin role):

```python
# --- Admin Settings ---

@router.get("/admin/settings", dependencies=[Depends(require_role("admin"))])
async def get_settings(session: LakebaseDependency):
    return admin_service.get_all_settings(session)

@router.put("/admin/settings/{key}", dependencies=[Depends(require_role("admin"))])
async def update_setting(key: str, body: SettingUpdate, request: Request, session: LakebaseDependency):
    user_email = _resolve_user_email(request)
    admin_service.update_setting(key, body.value, user_email, session)
    return {"ok": True}

# --- Admin Catalog ---

@router.get("/admin/catalog", dependencies=[Depends(require_role("admin"))])
async def list_catalog(session: LakebaseDependency):
    return admin_service.list_catalog_config(session)

@router.put("/admin/catalog/{endpoint_name}", dependencies=[Depends(require_role("admin"))])
async def update_catalog(
    endpoint_name: str, body: CatalogConfigUpdate, request: Request, session: LakebaseDependency
):
    user_email = _resolve_user_email(request)
    admin_service.update_catalog_entry(endpoint_name, body, user_email, session)
    return {"ok": True}

@router.post("/admin/catalog/discover", dependencies=[Depends(require_role("admin"))])
async def discover(request: Request, session: LakebaseDependency):
    ws = request.app.state.workspace_client
    agents = catalog_service.discover_from_workspace(ws, session)
    return {"discovered": len(agents), "agents": agents}
```

### Pydantic Models

```python
class SettingUpdate(BaseModel):
    value: Any

class CatalogConfigUpdate(BaseModel):
    visible: bool | None = None
    display_name: str | None = None
    description: str | None = None
    category: str | None = None
```

### Acceptance Criteria
- [ ] All admin routes protected with `require_role("admin")`
- [ ] Non-admin users get 403 on admin endpoints
- [ ] Settings CRUD working
- [ ] Catalog config CRUD working
- [ ] Discover endpoint triggers workspace scan

---

## Step 5.3: Admin Catalog Management Page

Implement `routes/_sidebar/admin.catalog.tsx`:

**Layout**:
1. Page title: "Catalog Management"
2. Action bar: "Discover Agents" button (triggers `POST /admin/catalog/discover`)
3. Table of all agents (visible + hidden):

| Column | Description |
|--------|-------------|
| Agent Name | `display_name` (editable inline) |
| Endpoint | `endpoint_name` (monospace, read-only) |
| Category | dropdown or text (editable) |
| Visible | Toggle switch (on/off) |
| Updated | relative timestamp |
| Actions | Edit button (opens detail editor) |

4. Hidden agents (visible=false) shown with dimmed row styling
5. "Discover Agents" shows a loading indicator, then refreshes the table

**Components**:
- `AdminCatalogTable` -- renders the full table
- `VisibilityToggle` -- Radix Switch that calls `PUT /admin/catalog/{name}`
- `DiscoverButton` -- triggers discovery with loading state

**Mutations** (TanStack Query):
```typescript
function useUpdateCatalogEntry() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ endpointName, updates }) =>
      api.put(`/admin/catalog/${endpointName}`, updates),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin", "catalog"] }),
  });
}

function useDiscoverAgents() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post("/admin/catalog/discover"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "catalog"] });
      queryClient.invalidateQueries({ queryKey: queryKeys.agents.all });
    },
  });
}
```

### Acceptance Criteria
- [ ] Table shows all agents (visible and hidden)
- [ ] Visibility toggle immediately updates and refreshes
- [ ] "Discover Agents" scans workspace and adds new entries
- [ ] Hidden agents visually dimmed
- [ ] Admin-only (redirects or shows 403 for non-admins)

---

## Step 5.4: Admin Settings Page

Implement `routes/_sidebar/admin.settings.tsx`:

**Layout**:

**Section 1: Memory Configuration**
- Title: "Memory Configuration"
- Description: "Control how the app manages conversation context across sessions"
- Radio group (styled as selectable cards using Radix RadioGroup):
  1. **Off** -- "No memory. Each conversation starts fresh with no prior context."
  2. **Short-term only** -- "Messages within a conversation are used as context for follow-up questions."
  3. **Long-term only** -- "Key insights extracted across conversations are injected as user context."
  4. **Both** -- "Short-term conversation history plus long-term cross-session insights."
- Currently selected option highlighted with primary accent border
- "Save Changes" button (disabled if no change)

**Section 2: System Status**
- Lakebase connection: green/red dot + status text
- App version
- Current user (admin email)

**Mutations**:
```typescript
function useUpdateMemoryMode() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (mode: string) =>
      api.put("/admin/settings/memory_mode", { value: mode }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["admin", "settings"] }),
  });
}
```

### Acceptance Criteria
- [ ] Memory mode selector renders with 4 options as cards
- [ ] Current mode pre-selected from admin settings
- [ ] Save button updates the setting
- [ ] Success toast on save
- [ ] System status section shows Lakebase connection
- [ ] Admin-only access enforced

---

## Step 5.5: Admin Navigation

Update the sidebar layout to show admin sub-navigation when on admin routes:

```typescript
// In _sidebar/route.tsx
const navGroups = [
  {
    id: "main",
    label: "Main",
    items: [
      { to: "/catalog", label: "Agent Catalog", icon: <Bot size={16} /> },
      { to: "/chat/new", label: "Chat", icon: <MessageSquare size={16} /> },
    ],
  },
  {
    id: "admin",
    label: "Admin",
    items: [
      { to: "/admin/catalog", label: "Catalog Management", icon: <Settings size={16} /> },
      { to: "/admin/settings", label: "Settings", icon: <Sliders size={16} /> },
    ],
    requireRole: "admin",  // Only show if user is admin
  },
];
```

Use the `GET /api/v1/me` response to conditionally render admin nav group.

### Acceptance Criteria
- [ ] Admin nav group visible only for admin users
- [ ] Non-admin users see only Catalog and Chat
- [ ] Admin routes redirect non-admins to catalog

---

## Phase 5 Completion Checklist

- [ ] Admin settings API working (get, update)
- [ ] Admin catalog API working (list, update, discover)
- [ ] All admin routes protected with role check
- [ ] Admin catalog management page with visibility toggles
- [ ] Admin settings page with memory mode selector
- [ ] System status displayed on settings page
- [ ] Admin nav hidden for non-admin users
- [ ] Discover agents scans workspace and updates catalog
