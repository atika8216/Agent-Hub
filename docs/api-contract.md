# API Contract

Base path: `/api/v1`
Auth: All endpoints require OBO authentication via `X-Forwarded-Email` header (set by Databricks Apps proxy).
Admin endpoints require the caller to have the `admin` role in `user_roles`.

---

## 1. GET /api/v1/me

Returns the current authenticated user.

**Response 200:**
```json
{
  "email": "user@scgp.com",
  "role": "admin" | "user",
  "first_seen": "2025-01-15T10:30:00Z"
}
```

---

## 2. GET /api/v1/agents

List all visible agents in the catalog.

**Query params:** `?search=<text>&type=<MAS|KA>`

**Response 200:**
```json
{
  "agents": [
    {
      "endpoint_name": "supply-chain-forecaster",
      "display_name": "Supply Chain Forecaster",
      "description": "Orchestrates end-to-end supply chain visibility...",
      "agent_type": "MAS",
      "sub_agent_count": 5,
      "has_access": true,
      "owner_email": "data-platform-team@scgp.com"
    }
  ]
}
```

---

## 3. GET /api/v1/agents/{endpoint_name}

Get agent detail with sub-agent information.

**Response 200:**
```json
{
  "endpoint_name": "supply-chain-forecaster",
  "display_name": "Supply Chain Forecaster",
  "description": "...",
  "agent_type": "MAS",
  "owner_email": "data-platform-team@scgp.com",
  "has_access": true,
  "sub_agents": [
    {
      "name": "Demand Forecast Genie",
      "type": "genie",
      "description": "ML-driven demand prediction model",
      "has_access": true
    },
    {
      "name": "SAP Integration",
      "type": "external_mcp",
      "description": "Direct ERP connector",
      "has_access": false,
      "owner_email": "sap-admin@scgp.com"
    }
  ]
}
```

---

## 4. GET /api/v1/agents/{endpoint_name}/access

Check current user's access to agent and its sub-components.

**Response 200:**
```json
{
  "endpoint_name": "supply-chain-forecaster",
  "has_access": true,
  "permission_level": "CAN_QUERY",
  "sub_agent_access": {
    "Demand Forecast Genie": true,
    "SAP Integration": false
  }
}
```

---

## 5. POST /api/v1/agents/discover

Admin only. Discover MAS agents from workspace serving endpoints.

**Response 200:**
```json
{
  "discovered": 3,
  "new": 1,
  "agents": [
    {
      "endpoint_name": "new-agent-endpoint",
      "display_name": "New Agent",
      "agent_type": "MAS"
    }
  ]
}
```

---

## 6. POST /api/v1/chat/{endpoint_name}

Start or continue a chat with an agent. Returns SSE stream.

**Request body:**
```json
{
  "message": "What is the demand forecast for Q3?",
  "conversation_id": "uuid-or-null"
}
```

**Response:** `text/event-stream`
```
data: {"type": "conversation_id", "value": "conv-uuid-123"}

data: {"type": "chunk", "content": "Based on the "}

data: {"type": "chunk", "content": "latest analysis..."}

data: {"type": "done", "usage": {"input_tokens": 150, "output_tokens": 320}}
```

---

## 7. GET /api/v1/conversations

List conversations for the current user.

**Query params:** `?agent=<endpoint_name>&limit=50&offset=0`

**Response 200:**
```json
{
  "conversations": [
    {
      "id": "conv-uuid-123",
      "title": "Q3 Demand Forecast",
      "endpoint_name": "supply-chain-forecaster",
      "display_name": "Supply Chain Forecaster",
      "message_count": 12,
      "created_at": "2025-04-01T14:00:00Z",
      "updated_at": "2025-04-01T14:35:00Z"
    }
  ],
  "total": 24
}
```

---

## 8. GET /api/v1/conversations/{id}

Get full conversation with messages.

**Response 200:**
```json
{
  "id": "conv-uuid-123",
  "title": "Q3 Demand Forecast",
  "endpoint_name": "supply-chain-forecaster",
  "messages": [
    {
      "id": "msg-uuid-1",
      "role": "user",
      "content": "What is the demand forecast for Q3?",
      "created_at": "2025-04-01T14:00:00Z"
    },
    {
      "id": "msg-uuid-2",
      "role": "assistant",
      "content": "Based on the latest analysis...",
      "created_at": "2025-04-01T14:00:05Z"
    }
  ]
}
```

---

## 9. DELETE /api/v1/conversations/{id}

Delete a conversation and its messages.

**Response 204:** No content

---

## 10. GET /api/v1/admin/settings

Admin only. Get all admin settings.

**Response 200:**
```json
{
  "settings": {
    "memory_mode": "short_term",
    "app_version": "1.0.0"
  }
}
```

---

## 11. PUT /api/v1/admin/settings/{key}

Admin only. Update a single setting.

**Request body:**
```json
{
  "value": "both"
}
```

**Response 200:**
```json
{
  "key": "memory_mode",
  "value": "both",
  "updated_at": "2025-04-01T15:00:00Z"
}
```

---

## 12. GET /api/v1/admin/catalog

Admin only. List all catalog entries (including hidden ones).

**Response 200:**
```json
{
  "entries": [
    {
      "endpoint_name": "supply-chain-forecaster",
      "display_name": "Supply Chain Forecaster",
      "visible": true,
      "agent_type": "MAS",
      "sub_agent_count": 5,
      "updated_at": "2025-04-01T10:00:00Z"
    }
  ]
}
```

---

## 13. PUT /api/v1/admin/catalog/{endpoint_name}

Admin only. Update catalog entry (visibility, display name, etc.).

**Request body:**
```json
{
  "visible": false,
  "display_name": "Supply Chain Forecaster v2"
}
```

**Response 200:**
```json
{
  "endpoint_name": "supply-chain-forecaster",
  "visible": false,
  "display_name": "Supply Chain Forecaster v2",
  "updated_at": "2025-04-01T15:30:00Z"
}
```

---

## 14. POST /api/v1/admin/catalog/discover

Admin only. Trigger agent discovery from workspace.

**Response 200:**
```json
{
  "discovered": 8,
  "new": 2,
  "updated": 1,
  "agents": [
    {
      "endpoint_name": "new-forecaster",
      "display_name": "New Forecaster",
      "status": "new"
    }
  ]
}
```

---

# Database Schema (Lakebase / PostgreSQL)

## Table: conversations

```sql
CREATE TABLE conversations (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_email    VARCHAR(255) NOT NULL,
    endpoint_name VARCHAR(255) NOT NULL,
    title         VARCHAR(500),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_conversations_user ON conversations (user_email, updated_at DESC);
CREATE INDEX idx_conversations_endpoint ON conversations (endpoint_name);
```

## Table: messages

```sql
CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL,
    token_count     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_messages_conversation ON messages (conversation_id, created_at ASC);
```

## Table: memory_long_term

```sql
CREATE TABLE memory_long_term (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_email    VARCHAR(255) NOT NULL,
    endpoint_name VARCHAR(255) NOT NULL,
    insight       TEXT NOT NULL,
    source_msg_id UUID REFERENCES messages(id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ
);

CREATE INDEX idx_memory_user_endpoint ON memory_long_term (user_email, endpoint_name, created_at DESC);
```

## Table: catalog_config

```sql
CREATE TABLE catalog_config (
    endpoint_name  VARCHAR(255) PRIMARY KEY,
    display_name   VARCHAR(500),
    description    TEXT,
    agent_type     VARCHAR(50) NOT NULL DEFAULT 'MAS',
    visible        BOOLEAN NOT NULL DEFAULT true,
    owner_email    VARCHAR(255),
    metadata_json  JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_catalog_visible ON catalog_config (visible) WHERE visible = true;
```

## Table: admin_settings

```sql
CREATE TABLE admin_settings (
    key        VARCHAR(100) PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by VARCHAR(255)
);
```

Seed data:
```sql
INSERT INTO admin_settings (key, value) VALUES
    ('memory_mode', 'short_term')
ON CONFLICT (key) DO NOTHING;
```

## Table: user_roles

```sql
CREATE TABLE user_roles (
    email      VARCHAR(255) PRIMARY KEY,
    role       VARCHAR(20) NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
