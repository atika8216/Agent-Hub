# Phase 3: Chat Core

**Timeline**: Week 6-7
**Goal**: Users can chat with accessible agents via streaming SSE, manage conversations, and resume sessions.
**Depends on**: Phase 2 (catalog working, agents discoverable, access checks functional)

---

## Sprint 3a: Chat Backend (Week 6)

### Step 3a.1: Chat Service

Create `src/scgp_agent_hub/backend/services/chat_service.py`.

**`stream_chat(endpoint_name, conversation_id, user_message, user_email, ws, session)`**:
1. Verify user has access via OBO (call `catalog_service.check_access`)
2. Load or create conversation:
   - If `conversation_id` provided, load from DB and verify ownership
   - If new, create conversation record in `conversations` table
3. Persist user message to `messages` table
4. Build message history for context:
   - Load previous messages from this conversation
   - Check memory settings (Phase 4 will enhance this)
5. Call Databricks serving endpoint via SDK:
   ```python
   from databricks.sdk import WorkspaceClient

   response = ws.serving_endpoints.query(
       name=endpoint_name,
       messages=[
           {"role": m.role, "content": m.content}
           for m in conversation_messages
       ],
       stream=True,
   )
   ```
6. Yield SSE events as tokens arrive:
   ```python
   async def generate():
       full_response = ""
       for chunk in response:
           token = chunk.choices[0].delta.content or ""
           full_response += token
           yield f"data: {json.dumps({'token': token, 'done': False})}\n\n"
       # Persist assistant message
       persist_message(session, conversation_id, "assistant", full_response)
       yield f"data: {json.dumps({'done': True, 'conversation_id': str(conversation_id)})}\n\n"
   ```

**`list_conversations(user_email, session)`**:
1. Query `conversations` table where `user_id = user_email`
2. Order by `updated_at DESC`
3. Return with last message preview and agent name

**`get_conversation(conversation_id, user_email, session)`**:
1. Load conversation with all messages
2. Verify ownership (`user_id = user_email`)
3. Return conversation with messages ordered by `created_at ASC`

**`delete_conversation(conversation_id, user_email, session)`**:
1. Verify ownership
2. Delete conversation (cascades to messages via FK)

**Auto-title generation**:
After the first assistant response, generate a conversation title:
- Use the first user message (truncated to 60 chars) as the title
- Or call a simple model to summarize the topic

### Step 3a.2: SSE Streaming Route

Add to `router.py`:

```python
from fastapi.responses import StreamingResponse

@router.post("/chat/{endpoint_name}")
async def chat(
    endpoint_name: str,
    body: ChatRequest,
    request: Request,
    session: LakebaseDependency,
):
    user_email = _resolve_user_email(request)
    ws = request.app.state.workspace_client

    generator = chat_service.stream_chat(
        endpoint_name=endpoint_name,
        conversation_id=body.conversation_id,
        user_message=body.message,
        user_email=user_email,
        ws=ws,
        session=session,
    )

    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

### Step 3a.3: Conversation CRUD Routes

```python
@router.get("/conversations")
async def list_conversations(request: Request, session: LakebaseDependency):
    ...

@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request, session: LakebaseDependency):
    ...

@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, request: Request, session: LakebaseDependency):
    ...
```

### Step 3a.4: Pydantic Models

Add to `models.py`:

```python
class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None

class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    metadata: dict = {}
    created_at: datetime

class ConversationOut(BaseModel):
    id: str
    agent_endpoint_name: str
    title: str
    last_message_preview: str | None = None
    message_count: int = 0
    created_at: datetime
    updated_at: datetime

class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut]

class ConversationListOut(BaseModel):
    conversations: list[ConversationOut]
    total: int
```

### Sprint 3a Acceptance Criteria
- [ ] `POST /api/v1/chat/{endpoint_name}` streams SSE tokens
- [ ] Conversations created and persisted in Lakebase
- [ ] Messages stored with correct role and content
- [ ] `GET /api/v1/conversations` returns user's conversations
- [ ] `GET /api/v1/conversations/{id}` returns full message history
- [ ] `DELETE /api/v1/conversations/{id}` removes conversation
- [ ] Access check enforced before chat (403 if no CAN_QUERY)
- [ ] Auto-title generated after first exchange

---

## Sprint 3b: Chat Frontend (Week 7)

### Step 3b.1: Chat State Management

Create `lib/stores/chat-store.ts` using Zustand:

```typescript
interface ChatState {
  isStreaming: boolean;
  currentMessage: string;    // accumulating streaming tokens
  messages: Message[];
  conversationId: string | null;

  startStream: () => void;
  appendToken: (token: string) => void;
  finishStream: (conversationId: string) => void;
  setMessages: (messages: Message[]) => void;
  addUserMessage: (content: string) => void;
  reset: () => void;
}
```

### Step 3b.2: SSE Client Hook

Create `lib/hooks/useChat.ts`:

```typescript
function useChat(endpointName: string) {
  const store = useChatStore();

  async function sendMessage(message: string) {
    store.addUserMessage(message);
    store.startStream();

    const response = await fetch(`/api/v1/chat/${endpointName}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        conversation_id: store.conversationId,
      }),
    });

    const reader = response.body?.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader!.read();
      if (done) break;

      const text = decoder.decode(value);
      // Parse SSE lines
      for (const line of text.split("\n")) {
        if (line.startsWith("data: ")) {
          const data = JSON.parse(line.slice(6));
          if (data.done) {
            store.finishStream(data.conversation_id);
          } else {
            store.appendToken(data.token);
          }
        }
      }
    }
  }

  return { sendMessage, ...store };
}
```

### Step 3b.3: Chat Interface Page

Implement `routes/_sidebar/chat.$conversationId.tsx`:

**Layout**:
- Left panel (280px): Conversation sidebar
- Right panel: Chat area

**Conversation Sidebar**:
- "New Chat" button at top
- Conversations grouped by agent name
- Each item: title, relative time, agent badge
- Active conversation highlighted
- Swipe/hover to delete

**Chat Area**:
- **Top bar**: Agent name + type badge + access status + memory mode indicator
- **Messages area** (scrollable):
  - User messages: right-aligned, dark card (`bg-zinc-800`)
  - Assistant messages: left-aligned, slightly lighter (`bg-zinc-900`)
  - Streaming indicator: typing dots or cursor animation during stream
  - Markdown rendering for assistant messages (headers, lists, code blocks)
  - Code blocks with syntax highlighting and copy button
- **Input area**:
  - Auto-resizing textarea
  - Send button (disabled during streaming)
  - Hint text: "Ask {agent_name}..."
  - Keyboard shortcut: Enter to send, Shift+Enter for newline

### Step 3b.4: New Chat Flow

Implement `routes/_sidebar/chat.new.tsx`:

1. Read `?agent=endpoint_name` from URL search params
2. Verify agent exists and user has access
3. Display empty chat with agent header
4. On first message send, create conversation and redirect to `/chat/{conversation_id}`

### Step 3b.5: Conversation Resume

When navigating to `/chat/{conversationId}`:
1. Load conversation via `GET /api/v1/conversations/{id}`
2. Render all previous messages
3. Scroll to bottom
4. Ready for new messages

### Step 3b.6: Message Rendering Components

Create `components/chat/`:
- `MessageBubble.tsx` -- renders a single message with role-based styling
- `StreamingMessage.tsx` -- renders the in-progress streaming message with cursor
- `MarkdownRenderer.tsx` -- renders markdown with code highlighting
- `ChatInput.tsx` -- auto-resizing textarea with send button
- `ConversationSidebar.tsx` -- list of conversations with grouping
- `AgentHeader.tsx` -- top bar showing agent info

### Sprint 3b Acceptance Criteria
- [ ] Chat page renders with conversation sidebar and message area
- [ ] Messages stream token-by-token with visible progress
- [ ] Markdown and code blocks render correctly in assistant messages
- [ ] Conversation sidebar lists previous conversations
- [ ] Clicking a conversation loads its messages
- [ ] "New Chat" from catalog detail creates new conversation
- [ ] Conversation auto-titled after first exchange
- [ ] Delete conversation works from sidebar
- [ ] Input disabled during streaming
- [ ] Enter sends, Shift+Enter adds newline
- [ ] Auto-scroll to latest message

---

## Phase 3 Completion Checklist

- [ ] Backend: SSE streaming from serving endpoint works
- [ ] Backend: conversations CRUD fully functional
- [ ] Backend: messages persisted to Lakebase
- [ ] Backend: access check before chat
- [ ] Frontend: streaming chat UI with token-by-token display
- [ ] Frontend: conversation sidebar with resume capability
- [ ] Frontend: markdown rendering with code highlighting
- [ ] Frontend: new chat flow from catalog
- [ ] Integration: end-to-end chat from catalog browse to conversation
