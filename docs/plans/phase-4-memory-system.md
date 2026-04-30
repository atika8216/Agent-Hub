# Phase 4: Memory System

**Timeline**: Week 8
**Goal**: Short-term and long-term memory working with global admin toggle.
**Depends on**: Phase 3 (chat working, conversations persisted)

---

## Step 4.1: Memory Service

Create `src/scgp_agent_hub/backend/services/memory_service.py`.

### Short-Term Memory

Short-term memory = loading previous messages from the current conversation into the context window.

**`get_short_term_context(conversation_id, session, max_messages=20)`**:
1. Query messages table for this conversation, ordered by `created_at ASC`
2. Take the last `max_messages` messages
3. Format as chat history:
   ```python
   [{"role": m.role, "content": m.content} for m in messages]
   ```
4. Return formatted messages for injection into the serving endpoint call

**Integration with chat_service**:
- Before calling the serving endpoint, check if short-term memory is enabled
- If yes, load conversation history and prepend to the messages array
- The serving endpoint receives full conversation context

### Long-Term Memory

Long-term memory = extracting and storing user insights across conversations.

**`extract_insights(conversation_messages, user_email, ws)`**:
1. After a conversation turn (async, non-blocking), analyze the conversation
2. Call a foundation model (via FMAPI or the MAS endpoint itself) with a prompt:
   ```
   Analyze this conversation and extract key user preferences, facts, and patterns.
   Return a JSON array of insights, each with:
   - "type": one of "preference", "fact", "pattern", "context"
   - "content": the insight text
   Only extract genuinely useful insights, not conversation summaries.
   ```
3. Parse the response and store each insight in `memory_long_term`:
   ```python
   for insight in extracted_insights:
       session.execute(
           text("""
               INSERT INTO memory_long_term (user_id, insight_type, content, metadata)
               VALUES (:user_id, :type, :content, :metadata)
           """),
           {
               "user_id": user_email,
               "type": insight["type"],
               "content": insight["content"],
               "metadata": json.dumps({"source_conversation": str(conversation_id)}),
           }
       )
   ```

**`get_long_term_context(user_email, session, max_insights=10)`**:
1. Query `memory_long_term` for this user, ordered by `created_at DESC`
2. Take the most recent `max_insights` insights
3. Format as a system message:
   ```
   You have the following context about this user from previous conversations:
   - [preference] User prefers data in tabular format
   - [fact] User works on the supply chain forecasting team
   - [pattern] User frequently asks about Q4 demand projections
   Use this context to provide more personalized and relevant responses.
   ```
4. Return as a system message to prepend to the conversation

### Memory Mode Check

**`get_memory_mode(session) -> str`**:
1. Query `admin_settings` for key `memory_mode`
2. Return value: `"off"`, `"short_term"`, `"long_term"`, or `"both"`
3. Default to `"off"` if not set

**`build_context(conversation_id, user_email, session, ws) -> list[dict]`**:
1. Get memory mode
2. Build context based on mode:
   - `"off"`: return empty list (only the current user message)
   - `"short_term"`: return conversation history
   - `"long_term"`: return long-term insights as system message
   - `"both"`: return long-term insights + conversation history

### Acceptance Criteria
- [ ] `get_short_term_context` loads conversation messages
- [ ] `get_long_term_context` loads user insights as system message
- [ ] `extract_insights` calls a model to analyze conversations
- [ ] `get_memory_mode` reads from admin_settings
- [ ] `build_context` combines memory sources based on mode

---

## Step 4.2: Integrate Memory into Chat Service

Modify `chat_service.stream_chat()`:

```python
async def stream_chat(endpoint_name, conversation_id, user_message, user_email, ws, session):
    # ... existing access check and conversation creation ...

    # Build context based on memory settings
    context_messages = memory_service.build_context(
        conversation_id=conversation_id,
        user_email=user_email,
        session=session,
        ws=ws,
    )

    # Combine: context + user message
    all_messages = context_messages + [{"role": "user", "content": user_message}]

    # Call serving endpoint with full context
    response = ws.serving_endpoints.query(
        name=endpoint_name,
        messages=all_messages,
        stream=True,
    )

    # ... existing streaming logic ...

    # After streaming completes, trigger async insight extraction (if long-term enabled)
    memory_mode = memory_service.get_memory_mode(session)
    if memory_mode in ("long_term", "both"):
        # Run in background thread to not block response
        import threading
        threading.Thread(
            target=memory_service.extract_insights,
            args=(all_messages + [{"role": "assistant", "content": full_response}], user_email, ws),
            daemon=True,
        ).start()
```

### Acceptance Criteria
- [ ] Chat service reads memory mode before each conversation turn
- [ ] Short-term memory loads prior messages into context
- [ ] Long-term memory injects user insights as system context
- [ ] Insight extraction runs asynchronously after each turn
- [ ] Memory mode "off" sends only the current message
- [ ] Memory mode "both" combines short-term + long-term

---

## Step 4.3: Memory Mode UI Indicator

Add a memory mode badge to the chat interface top bar.

**In `AgentHeader.tsx`**:
- Query `GET /api/v1/admin/settings` for `memory_mode`
- Display a small badge next to the agent name:
  - "Memory: Off" (muted/gray)
  - "Memory: Short-term" (blue badge)
  - "Memory: Long-term" (purple badge)
  - "Memory: Both" (green badge)
- Tooltip on hover explaining what the mode does

### Acceptance Criteria
- [ ] Memory mode badge visible in chat header
- [ ] Badge color corresponds to memory mode
- [ ] Tooltip explains memory behavior

---

## Step 4.4: Default Memory Settings

On first app startup (when `admin_settings` is empty), seed default settings:

```python
def _seed_default_settings(session):
    """Insert default admin settings if none exist."""
    defaults = {
        "memory_mode": json.dumps("off"),
    }
    for key, value in defaults.items():
        session.execute(
            text("""
                INSERT INTO admin_settings (key, value) VALUES (:key, :value)
                ON CONFLICT (key) DO NOTHING
            """),
            {"key": key, "value": value},
        )
    session.commit()
```

Add this to the background migration in `lakebase.py`.

### Acceptance Criteria
- [ ] Default `memory_mode` = `"off"` seeded on first startup
- [ ] Existing settings not overwritten on subsequent starts

---

## Phase 4 Completion Checklist

- [ ] Memory service fully implemented (short-term + long-term)
- [ ] Chat service integrates memory based on admin toggle
- [ ] Long-term insight extraction runs asynchronously
- [ ] Memory mode indicator visible in chat UI
- [ ] Default settings seeded on first startup
- [ ] All memory modes tested: off, short_term, long_term, both
