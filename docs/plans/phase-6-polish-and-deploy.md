# Phase 6: Polish, Testing, and Deployment

**Timeline**: Week 10-11
**Goal**: Production-ready app deployed to Databricks Apps.
**Depends on**: Phases 0-5 complete

---

## Sprint 6a: Design Polish (Week 10)

### Step 6a.1: Impeccable Design Pass

Run `/impeccable craft` on the full application. Key areas to polish:

**Typography**:
- Verify Geist (headlines) + Be Vietnam Pro (body) fonts are loading correctly
- Check type scale has sufficient contrast between levels (1.25 ratio minimum)
- Ensure monospace code blocks use a good fixed-width font
- Line lengths capped at 65-75ch for body text

**Color and Theme**:
- Verify dark mode tokens from Phase 0 are consistently applied
- Check that all text meets WCAG AA contrast ratio (4.5:1 for body, 3:1 for large text)
- Ensure access badges (green/red) have sufficient contrast on dark backgrounds
- Verify sub-agent type badges are distinguishable for color-blind users

**Spacing and Layout**:
- Audit spacing consistency (using the 4pt scale: 4, 8, 12, 16, 24, 32, 48, 64)
- Verify grid gap consistency in catalog
- Check sidebar widths and padding
- Ensure chat message area uses space efficiently

**Interaction**:
- All interactive elements have visible hover/focus states
- Buttons have appropriate active/pressed states
- Form inputs have clear focus rings
- Toggle switches have obvious on/off states

### Step 6a.2: Animations and Transitions

**Page Transitions**:
- Route changes use a subtle fade or slide transition
- Use `motion` library for enter/exit animations on route components
- Keep transitions under 300ms for responsiveness

**Loading States**:
- Skeleton placeholders use a subtle shimmer animation
- Chat streaming has a typing indicator (pulsing dots)
- Button loading states show a spinner

**Micro-interactions**:
- Access badge on hover shows tooltip with details
- Conversation sidebar items have hover highlight
- "Start Chat" button has subtle scale on hover
- Toast notifications slide in from top-right

### Step 6a.3: Responsive Design

While desktop-first, ensure the app is usable at:
- **1920px+**: Full layout, 3+ column catalog grid
- **1280px**: 2-column grid, sidebar may collapse
- **1024px**: Sidebar becomes a drawer/overlay
- **768px**: Single column, stacked layout

Key responsive behaviors:
- Sidebar collapses to icon-only or drawer on narrow screens
- Chat conversation sidebar becomes a slide-out panel on mobile
- Catalog grid auto-fits with `minmax(320px, 1fr)`
- Admin tables become horizontally scrollable

### Step 6a.4: Accessibility Audit

**Keyboard Navigation**:
- [ ] All interactive elements reachable via Tab
- [ ] Sidebar navigation works with arrow keys
- [ ] Chat input focusable and Enter sends
- [ ] Modal dialogs trap focus
- [ ] Escape closes dropdowns and modals

**Screen Reader**:
- [ ] All images have alt text
- [ ] Access badges have `aria-label` (e.g., "Access granted" or "No access")
- [ ] Conversation sidebar items have `aria-current="page"` for active
- [ ] Live region for streaming chat messages (`aria-live="polite"`)
- [ ] Form inputs have associated labels

**Reduced Motion**:
- [ ] All animations respect `prefers-reduced-motion: reduce`
- [ ] Streaming messages display without animation in reduced motion

### Step 6a.5: Edge Cases and Error States

**Error States to Handle**:
- Network error during chat stream (show retry button)
- Agent serving endpoint unavailable (show status in catalog)
- Lakebase connection lost (show degraded mode banner)
- Session expired / OBO token refresh failure (show re-auth prompt)
- Rate limited by serving endpoint (show retry-after message)

**Edge Cases**:
- Very long messages (horizontal scroll for code, word-wrap for text)
- Empty conversation (just created, no messages)
- Agent with 0 sub-agents
- Agent with >20 sub-agents (scrollable list)
- User with >100 conversations (virtualized list or pagination)
- Simultaneous streams (prevent, show "already streaming" message)

### Sprint 6a Acceptance Criteria
- [ ] Typography passes impeccable audit
- [ ] Color contrast meets WCAG AA
- [ ] Spacing is consistent on 4pt scale
- [ ] Page transitions are smooth and fast
- [ ] Loading skeletons have shimmer animation
- [ ] Responsive down to 768px
- [ ] All keyboard navigation works
- [ ] Screen reader announces access status and streaming messages
- [ ] All error states handled gracefully
- [ ] Edge cases don't break the UI

---

## Sprint 6b: Deployment (Week 11)

### Step 6b.1: DABs Configuration

Finalize `databricks.yml`:

```yaml
bundle:
  name: scgp-agent-hub

variables:
  workspace_host:
    description: Workspace URL
    default: https://YOUR_WORKSPACE.cloud.databricks.com
  app_name:
    description: Application name
    default: scgp-agent-hub
  lakebase_project_id:
    description: Lakebase project ID
    default: scgp-agent-hub
  lakebase_branch_id:
    description: Lakebase branch ID
    default: production

targets:
  dev:
    mode: development
    default: true
    workspace:
      host: https://YOUR_WORKSPACE.cloud.databricks.com
      profile: YOUR_PROFILE
    variables:
      app_name: scgp-agent-hub-dev
      lakebase_branch_id: development

  staging:
    mode: production
    workspace:
      host: https://YOUR_STAGING_WORKSPACE.cloud.databricks.com
    variables:
      app_name: scgp-agent-hub-staging
      lakebase_branch_id: staging

  prod:
    mode: production
    workspace:
      host: https://YOUR_PROD_WORKSPACE.cloud.databricks.com
    variables:
      app_name: scgp-agent-hub
      lakebase_branch_id: production

resources:
  apps:
    scgp_agent_hub:
      name: ${var.app_name}
      description: "SCGP Agent Chat Hub"
      source_code_path: .
      config:
        command:
          - uvicorn
          - scgp_agent_hub.backend.app:app
          - --host=0.0.0.0
          - --port=8000
        env:
          - name: LAKEBASE_PROJECT_ID
            value: ${var.lakebase_project_id}
          - name: LAKEBASE_BRANCH_ID
            value: ${var.lakebase_branch_id}
      resources:
        # Lakebase database
        - name: database
          database:
            name: ${var.app_name}-db
        # MAS serving endpoints (add as needed)
        # - name: mas_endpoint
        #   serving_endpoint:
        #     name: your-mas-endpoint
        #     permission: CAN_QUERY
      permissions:
        - user_name: ${workspace.current_user.userName}
          level: CAN_MANAGE
```

Finalize `app.yaml`:
```yaml
command:
  - uvicorn
  - scgp_agent_hub.backend.app:app
  - --host=0.0.0.0
  - --port=8000
env:
  - name: LAKEBASE_PROJECT_ID
    value: scgp-agent-hub
  - name: LAKEBASE_BRANCH_ID
    value: production
  - name: LOG_LEVEL
    value: INFO
```

### Step 6b.2: Build Pipeline

Create `scripts/build.sh`:
```bash
#!/bin/bash
set -euo pipefail

# Build frontend
cd src/scgp_agent_hub/ui
npm ci
npm run build
cd ../../..

# Validate bundle
databricks bundle validate

echo "Build complete. Run 'databricks bundle deploy' to deploy."
```

### Step 6b.3: E2E Tests

Create `tests/e2e/` with Playwright tests:

**`test_catalog.py`**:
- Navigate to catalog page
- Verify agent cards render
- Verify access badges visible
- Click agent card, verify detail page loads
- Verify sub-agents listed

**`test_chat.py`**:
- Start new chat from agent detail
- Send a message
- Verify streaming response appears
- Verify conversation appears in sidebar
- Navigate away and back, verify messages persist

**`test_admin.py`**:
- Navigate to admin catalog
- Toggle agent visibility
- Verify agent disappears from catalog
- Change memory mode
- Verify mode persists

### Step 6b.4: Documentation

Create `README.md`:
- Project description
- Prerequisites (Databricks CLI, Node.js, Python 3.11+)
- Local development setup
- Deployment instructions
- Architecture overview
- Configuration reference

### Step 6b.5: Deployment Steps

Execute in order:
1. `databricks bundle validate` -- verify config
2. `databricks bundle deploy` -- deploy resources
3. Wait for Lakebase provisioning (first deploy, may take several minutes)
4. `databricks bundle run scgp_agent_hub` -- start the app
5. `databricks bundle summary` -- verify deployment
6. Open the app URL and test end-to-end

### Sprint 6b Acceptance Criteria
- [ ] `databricks.yml` configured with dev/staging/prod targets
- [ ] `app.yaml` configured with correct entrypoint
- [ ] Build script works: frontend builds to `__dist__`
- [ ] `databricks bundle validate` passes
- [ ] `databricks bundle deploy` succeeds
- [ ] Lakebase database provisioned
- [ ] App starts and is accessible via Databricks Apps URL
- [ ] E2E tests pass: catalog, chat, admin flows
- [ ] README documents setup and deployment

---

## Phase 6 Completion Checklist

- [ ] Design polish pass complete (impeccable quality)
- [ ] Animations and transitions implemented
- [ ] Responsive design verified at key breakpoints
- [ ] Accessibility audit passed
- [ ] Error states handled gracefully
- [ ] DABs configuration finalized
- [ ] Build pipeline working
- [ ] E2E tests passing
- [ ] README and documentation complete
- [ ] First successful deployment to Databricks Apps
- [ ] End-to-end flow verified: catalog -> agent detail -> chat -> resume session
