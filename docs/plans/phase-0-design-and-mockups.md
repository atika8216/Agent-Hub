# Phase 0: Design and Mockups

**Timeline**: Week 1-2
**Goal**: High-fidelity Google Stitch mockups for all key screens before any code is written.
**Depends on**: Nothing (first phase)

---

## Step 0.1: Create `.impeccable.md` Design Context

Create the file `.impeccable.md` at the project root with the design context for the SCGP Agent Chat Hub.

### Design Context

```markdown
## Design Context

### Users
- **Primary**: SCGP internal teams (data engineers, data scientists, business analysts) who
  interact with Databricks AI agents for their daily tasks.
- **Secondary**: Admins who manage which agents are visible and configure memory settings.
- **Context**: Desktop-first, used during work hours in an office setting. Users may have
  multiple browser tabs open alongside their IDE and Databricks workspace.

### Use Cases
- Browse available AI agents and understand what each does
- Check whether they have access to a specific agent and its sub-components
- Chat with agents in real-time with streaming responses
- Resume previous conversations across sessions
- Admin: manage catalog visibility and memory configuration

### Brand Personality
- **Precise**: Like a well-organized instrument panel
- **Capable**: Conveys power without complexity
- **Trustworthy**: Clear access indicators, no ambiguity about what you can or cannot use

### Aesthetic Direction
- **Theme**: Dark mode primary (users are in a technical context alongside IDEs and Databricks)
- **Tone**: Technical-professional, not corporate-bland. Think mission control, not PowerPoint.
- **Anti-references**: Generic SaaS dashboards, chatbot templates with rounded bubbles and
  pastel colors, anything that looks like "default Tailwind UI"
- **Key differentiator**: The catalog-to-chat transition should feel seamless -- browsing agents
  and chatting with them should be one fluid journey, not two separate apps stitched together.

### Design Principles
1. **Access clarity first** -- a user should know within 1 second if they can use an agent
2. **Information density over whitespace** -- respect the user's screen real estate
3. **Progressive disclosure** -- show agent overview in catalog, reveal sub-components on drill-in
4. **Continuity** -- the transition from catalog browse to chat should be zero-friction
5. **Status at a glance** -- memory mode, connection status, agent health visible without digging
```

### Acceptance Criteria
- [ ] `.impeccable.md` exists at project root with the above Design Context section
- [ ] Design principles are documented and will guide all subsequent mockup decisions

---

## Step 0.2: Create Google Stitch Project and Design System

Use the Stitch MCP to create a project and establish the design system.

### Actions

1. **Create Stitch project**:
   ```
   CallMcpTool: user-stitch / create_project
   Arguments: { "title": "SCGP Agent Chat Hub" }
   ```

2. **Create design system** with these parameters:
   ```
   CallMcpTool: user-stitch / create_design_system
   Arguments: {
     "projectId": "<project_id from step 1>",
     "designSystem": {
       "displayName": "SCGP Agent Hub Design System",
       "theme": {
         "colorMode": "DARK",
         "headlineFont": "GEIST",
         "bodyFont": "BE_VIETNAM_PRO",
         "labelFont": "GEIST",
         "customColor": "#ff3621",
         "colorVariant": "TONAL_SPOT",
         "roundness": "ROUND_EIGHT",
         "overridePrimaryColor": "#ff3621",
         "overrideNeutralColor": "#18181b",
         "overrideSecondaryColor": "#3b82f6",
         "overrideTertiaryColor": "#22c55e"
       }
     }
   }
   ```

3. **Apply design system**:
   ```
   CallMcpTool: user-stitch / update_design_system
   ```

### Color Palette Rationale
- **Primary `#ff3621`**: Databricks red-orange, carried from the Agent Catalog App reference
- **Neutral `#18181b`**: Deep zinc for dark backgrounds
- **Secondary `#3b82f6`**: Blue for information and links
- **Tertiary `#22c55e`**: Green for "access granted" indicators
- **Font**: Geist for headlines (technical, sharp), Be Vietnam Pro for body (readable, distinctive)

### Acceptance Criteria
- [ ] Stitch project created with ID recorded
- [ ] Design system applied with dark mode, Databricks brand colors, Geist + Be Vietnam Pro fonts

---

## Step 0.3: Generate Mockup Screens

Generate each screen in Stitch using `generate_screen_from_text`. Use `DESKTOP` device type and `GEMINI_3_1_PRO` model for all screens.

### Screen 1: Agent Catalog Page

**Prompt**:
```
Design a dark-themed desktop Agent Catalog page for an internal AI agent hub.

Layout:
- Left sidebar (240px): navigation with sections "Catalog" (active), "Chat", "Admin". 
  Show user avatar and "SCGP Agent Hub" logo at top.
- Main content area with a header row: page title "Agent Catalog", search input, 
  and filter chips for agent type.
- Below: a grid of agent cards (3 columns).

Each agent card shows:
- Agent name (bold, large)
- Short description (1-2 lines, muted text)
- Badge showing number of sub-agents (e.g. "4 sub-agents")
- Access status indicator: green checkmark icon with "Access granted" label 
  OR red lock icon with "No access" and small "Request access" link
- Agent type tag (e.g. "MAS", "Knowledge Assistant")

Show 6 cards: 4 with access granted, 2 with no access (locked).
Use Databricks red-orange (#ff3621) as accent, zinc/slate dark backgrounds.
No glassmorphism. Cards should have subtle 1px borders, not heavy shadows.
```

### Screen 2: Agent Detail Page

**Prompt**:
```
Design a dark-themed desktop Agent Detail page showing a specific Multi-Agent 
Supervisor (MAS) agent and its sub-components.

Layout:
- Same left sidebar as catalog page, but "Catalog" still active with breadcrumb 
  "Catalog > Supply Chain Forecaster"
- Top section: Agent name "Supply Chain Forecaster", description paragraph, 
  owner info "owned by data-platform-team@scgp.com", 
  overall access badge showing green "Access granted"
- Prominent "Start Chat" button (primary red-orange, full width of top section)
- Below: "Sub-agents & Components" section header
- A list/table of sub-components with columns:
  - Component name (e.g. "Demand Forecast Genie", "Product Knowledge Base")
  - Type badge (Genie / KA / UC Function / External MCP) with distinct colors per type
  - Access indicator (green check or red lock per component)
  - Brief description

Show 5 sub-components: 
  1. "Demand Forecast Genie" (type: Genie, access: granted)
  2. "Product Knowledge Base" (type: KA, access: granted)
  3. "Inventory Calculator" (type: UC Function, access: granted)
  4. "SAP Integration" (type: External MCP, access: denied)
  5. "Historical Trends Analyzer" (type: Genie, access: granted)

For denied components, show the owner and a "Request Access" button.
Use color-coded type badges: Genie=blue, KA=purple, UC Function=amber, External MCP=teal.
```

### Screen 3: Chat Interface

**Prompt**:
```
Design a dark-themed desktop Chat Interface for conversing with an AI agent.

Layout:
- Left sidebar (280px): "Conversations" header with "New Chat" button at top.
  Below: list of previous conversations grouped by agent name.
  Each conversation shows: title (truncated), relative time (e.g. "2 hours ago"), 
  agent name tag. Active conversation highlighted.
- Main chat area:
  - Top bar: agent name "Supply Chain Forecaster" with type badge "MAS", 
    green access dot, and "Memory: Short-term" badge indicator
  - Message area: alternating user and assistant messages
    - User messages: right-aligned, subtle dark card
    - Assistant messages: left-aligned, slightly lighter card with markdown 
      formatted text, a code block with syntax highlighting
  - Bottom: message input bar with text field, send button. 
    Subtle hint text "Ask the Supply Chain Forecaster..."
    
Show a conversation with 4 messages: 
  user asks about demand forecast, 
  assistant responds with a table and explanation,
  user asks follow-up about specific product,
  assistant responds with code snippet.

The conversation sidebar should show 5 previous conversations.
Clean, minimal design. No chat bubbles. Flat message cards with clear role distinction.
```

### Screen 4: Admin - Catalog Management

**Prompt**:
```
Design a dark-themed desktop Admin Catalog Management page.

Layout:
- Same left sidebar, "Admin" section active, sub-nav shows "Catalog" (active) 
  and "Settings"
- Top: page title "Catalog Management", "Discover Agents" button (secondary style)
- Main: A table of all MAS agents with columns:
  - Agent name
  - Endpoint name (monospace, muted)
  - Type
  - Sub-agents count
  - Visible toggle (switch component -- on/off per agent)
  - Last updated timestamp
  - Actions: Edit button

Show 8 agents, 6 visible and 2 hidden (toggle off, row slightly dimmed).
Include a "Discover Agents" button that scans the workspace for new MAS endpoints.
Table should have sorting indicators on column headers.
Clean administrative interface, functional over decorative.
```

### Screen 5: Admin - Settings

**Prompt**:
```
Design a dark-themed desktop Admin Settings page.

Layout:
- Same left sidebar, "Admin" active, sub-nav "Settings" active
- Main content split into sections:

Section 1: "Memory Configuration"
  - Title and description: "Control how the app manages conversation context"
  - Radio button group with 4 options displayed as selectable cards:
    1. "Off" -- No memory, each conversation starts fresh
    2. "Short-term only" -- Messages within a conversation used as context
    3. "Long-term only" -- Cross-conversation user insights injected
    4. "Both" -- Short-term and long-term memory active
  - Currently selected: "Short-term only" (highlighted with primary border)
  - "Save" button below

Section 2: "System Status"
  - Lakebase connection status: green dot + "Connected" with host info
  - App version: "1.0.0"
  - Deployment target: "Local Development"

Section 3: "Admin Users"
  - Current admin email displayed
  - "First user becomes admin" note

Clean settings layout. Each section separated by subtle dividers.
No cards-within-cards. Flat layout with clear section headers.
```

### Screen 6: Empty and Edge States

**Prompt**:
```
Design a dark-themed desktop page showing 4 empty/edge states in a 2x2 grid layout:

Top-left: "No Agents Found" empty state
  - Illustration: simple geometric icon suggesting search/discovery
  - Heading: "No agents in your catalog yet"
  - Body: "Ask your admin to discover and enable agents from the workspace"
  - No action button (this is for regular users)

Top-right: "No Access" state for an agent
  - Lock icon
  - Heading: "You don't have access to this agent"  
  - Body: "Contact the owner to request CAN_QUERY permission on the serving endpoint"
  - "Copy owner email" button (secondary)

Bottom-left: "No Conversations" empty chat sidebar
  - Chat icon
  - Heading: "No conversations yet"
  - Body: "Start a new chat from the Agent Catalog to begin"
  - "Browse Agents" link button

Bottom-right: "Loading Agent Detail" skeleton state
  - Show a shimmer/skeleton version of the Agent Detail page
  - Skeleton blocks for agent name, description, sub-agents list
  - Animated pulse effect placeholders

All states should feel intentional and helpful, not just "nothing here".
Use muted colors, no bright accents on empty states.
```

### Acceptance Criteria
- [ ] 6 Stitch screens generated and reviewed
- [ ] All screens follow the dark theme with Databricks brand colors
- [ ] Catalog shows access badges prominently
- [ ] Agent detail shows sub-component types with color-coded badges
- [ ] Chat interface has conversation sidebar with session resume
- [ ] Admin pages are functional and clear
- [ ] Empty/loading states are designed and feel intentional

---

## Step 0.4: Document Design Tokens

Create `docs/design-tokens.md` summarizing the design system for implementation.

```markdown
# Design Tokens

## Colors
- Primary: #ff3621 (Databricks red-orange)
- Primary foreground: #ffffff
- Background: #09090b
- Card: #111113
- Border: #27272a
- Muted foreground: #a1a1aa
- Success (access granted): #22c55e
- Destructive (no access): #ef4444
- Info (links, secondary): #3b82f6

## Type Badges (Sub-agent types)
- Genie: #3b82f6 (blue)
- KA (Knowledge Assistant): #a855f7 (purple)
- UC Function: #eab308 (amber)
- External MCP: #14b8a6 (teal)

## Typography
- Headlines: Geist, weight 600-700
- Body: Be Vietnam Pro, weight 400-500
- Monospace: system monospace stack
- Scale: 14px base, 1.25 ratio (14, 18, 22, 28, 35)

## Spacing
- 4pt base scale: 4, 8, 12, 16, 24, 32, 48, 64
- Card padding: 16px
- Grid gap: 16px
- Section gap: 32px
- Sidebar width: 240px (catalog), 280px (chat)

## Border Radius
- Small: 6px (badges, buttons)
- Medium: 8px (cards, inputs)
- Large: 12px (modals, sections)

## Shadows
- Card: 0 1px 2px rgba(0, 0, 0, 0.3)
- No heavy drop shadows, use 1px borders instead
```

### Acceptance Criteria
- [ ] `docs/design-tokens.md` created and documents all visual decisions
- [ ] Token values align with Stitch mockup outputs

---

## Step 0.5: Finalize Data Model and API Contract

Review and finalize the data model and API contract from the main plan. Create `docs/api-contract.md`.

### Contents

Document every API endpoint with:
- Method + path
- Request body (JSON schema)
- Response body (JSON schema)
- Auth requirements
- Example request/response

Key endpoints to document:
1. `GET /api/v1/me` -- current user
2. `GET /api/v1/agents` -- list agents
3. `GET /api/v1/agents/{name}` -- agent detail with sub-agents
4. `GET /api/v1/agents/{name}/access` -- check access
5. `POST /api/v1/agents/discover` -- discover from workspace
6. `POST /api/v1/chat/{endpoint_name}` -- SSE chat stream
7. `GET /api/v1/conversations` -- list conversations
8. `GET /api/v1/conversations/{id}` -- get conversation
9. `DELETE /api/v1/conversations/{id}` -- delete conversation
10. `GET /api/v1/admin/settings` -- get settings
11. `PUT /api/v1/admin/settings/{key}` -- update setting
12. `GET /api/v1/admin/catalog` -- list catalog config
13. `PUT /api/v1/admin/catalog/{endpoint_name}` -- update catalog entry
14. `POST /api/v1/admin/catalog/discover` -- discover agents

### Database Schema DDL

Document the exact SQL DDL for each table:
- `conversations`
- `messages`
- `memory_long_term`
- `catalog_config`
- `admin_settings`
- `user_roles`

### Acceptance Criteria
- [ ] `docs/api-contract.md` created with all endpoints documented
- [ ] Database DDL finalized and reviewed
- [ ] Request/response schemas defined for all endpoints

---

## Phase 0 Completion Checklist

- [ ] `.impeccable.md` created at project root
- [ ] Google Stitch project created with design system applied
- [ ] 6 mockup screens generated and reviewed
- [ ] `docs/design-tokens.md` created
- [ ] `docs/api-contract.md` created with full API contract and DDL
- [ ] Design approved by team before moving to Phase 1
