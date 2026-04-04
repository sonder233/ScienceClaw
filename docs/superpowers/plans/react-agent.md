# ReAct Agent Mode for RPA Recording Assistant

## Overview

Add a ReAct (Observe→Think→Act) agent mode to the RPA recording assistant. The agent autonomously loops through page observation, LLM reasoning, and action execution — pausing only for high-risk operations that require user confirmation.

---

## Step 1: Add `RPAReActAgent` to `assistant.py`

**File:** `ScienceClaw/backend/rpa/assistant.py`

### What to add

A new class `RPAReActAgent` with:

- `__init__(self, page, session)` — takes the Playwright page and session object
- `run(self, goal: str) -> AsyncGenerator` — main entry point, yields SSE event dicts
- `resolve_confirm(self, approved: bool)` — called externally to unblock a pending confirmation
- `abort(self)` — sets an abort flag to stop the loop

### ReAct loop logic (max 20 iterations)

```
for step in range(20):
    if aborted: emit agent_aborted; return

    # Observe
    url, title, elements = await _get_page_elements(page)

    # Think
    response = await llm(system_prompt, history + observation)
    parsed = parse_json(response)  # extract JSON from code block or raw
    yield {"event": "agent_thought", "data": {"text": parsed["thought"]}}

    if parsed["action"] == "done":
        yield {"event": "agent_done", "data": {"total_steps": step}}
        return

    if parsed["action"] == "abort":
        yield {"event": "agent_aborted", "data": {"reason": parsed["thought"]}}
        return

    # Act
    if parsed["risk"] == "high":
        self._confirm_event = asyncio.Event()
        self._confirm_approved = None
        yield {"event": "confirm_required", "data": {
            "description": parsed["description"],
            "risk_reason": parsed["risk_reason"],
            "code": parsed["code"]
        }}
        await self._confirm_event.wait()
        if not self._confirm_approved:
            continue  # skip this step

    result = await _execute_on_page(page, parsed["code"])
    if result.success:
        step_obj = session.add_step(parsed["description"], parsed["code"])
        yield {"event": "agent_step_done", "data": {"step": step_obj, "step_index": step}}
    else:
        # append error to history, let LLM decide next action
        history.append({"role": "tool", "content": f"Error: {result.error}"})

yield {"event": "agent_done", "data": {"total_steps": 20}}  # max steps reached
```

### System prompt for Think step

Instruct the LLM to output strictly:
```json
{
  "thought": "...",
  "action": "execute|done|abort",
  "code": "await page...",
  "description": "...",
  "risk": "none|high",
  "risk_reason": "..."
}
```

### Key notes

- Reuse `_get_page_elements` and `_execute_on_page` from `RPAAssistant` — extract them to module-level helpers if they are currently instance methods
- `resolve_confirm` sets `self._confirm_approved = approved` then calls `self._confirm_event.set()`
- Store one agent instance per session in a module-level dict: `_active_agents: dict[str, RPAReActAgent]`

---

## Step 2: Update `rpa.py` — mode field + new endpoints

**File:** `ScienceClaw/backend/route/rpa.py`

### Changes

1. Add `mode: str = "chat"` to `ChatRequest` Pydantic model

2. In `chat_with_assistant`, branch on mode:
```python
if body.mode == "react":
    agent = RPAReActAgent(page, session)
    _active_agents[session_id] = agent
    async for event in agent.run(body.message):
        yield sse(event)
    _active_agents.pop(session_id, None)
else:
    # existing RPAAssistant.chat() path
```

3. New endpoint — confirm/skip:
```python
@router.post("/session/{session_id}/agent/confirm")
async def agent_confirm(session_id: str, body: ConfirmRequest):
    # ConfirmRequest: approved: bool
    agent = _active_agents.get(session_id)
    if agent:
        agent.resolve_confirm(body.approved)
    return {"ok": True}
```

4. New endpoint — abort:
```python
@router.post("/session/{session_id}/agent/abort")
async def agent_abort(session_id: str):
    agent = _active_agents.get(session_id)
    if agent:
        agent.abort()
    return {"ok": True}
```

### Key notes

- `_active_agents` is a module-level dict, not persistent — one entry per active agent run
- Import `RPAReActAgent` from `assistant.py`

---

## Step 3: Update `RecorderPage.vue` — agent UI

**File:** `ScienceClaw/frontend/src/pages/rpa/RecorderPage.vue`

### State additions

```js
const agentMode = ref(false)      // toggle in chat header
const agentRunning = ref(false)   // true while agent loop active
const pendingConfirm = ref(null)  // { description, risk_reason, code } | null
```

### `sendMessage()` changes

- If `agentMode.value`, add `mode: "react"` to request body
- Set `agentRunning.value = true` before fetch, `false` on `agent_done` / `agent_aborted`

### New SSE event handlers

```js
case "agent_thought":
  // append italic thought text to current assistant message bubble
  currentMessage.value += `\n*${data.text}*`
  break

case "agent_step_done":
  // push step immediately without waiting for poll
  steps.value.push(data.step)
  break

case "confirm_required":
  pendingConfirm.value = data
  break

case "agent_done":
case "agent_aborted":
  agentRunning.value = false
  pendingConfirm.value = null
  finalizeMessage()
  break
```

### Template additions

1. Agent mode toggle in chat header:
```html
<label>
  <input type="checkbox" v-model="agentMode" :disabled="agentRunning" />
  Agent 模式
</label>
```

2. Abort button (shown while running):
```html
<button v-if="agentRunning" @click="abortAgent">中止 Agent</button>
```

3. Inline confirm dialog (shown when `pendingConfirm` is set):
```html
<div v-if="pendingConfirm" class="confirm-dialog">
  <p>{{ pendingConfirm.description }}</p>
  <p class="risk">风险: {{ pendingConfirm.risk_reason }}</p>
  <button @click="sendConfirm(true)">确认执行</button>
  <button @click="sendConfirm(false)">跳过</button>
</div>
```

4. Disable input while agent running:
```html
<input :disabled="agentRunning" ... />
```

### Helper methods

```js
async function abortAgent() {
  await fetch(`/rpa/session/${sessionId}/agent/abort`, { method: "POST" })
}

async function sendConfirm(approved) {
  pendingConfirm.value = null
  await fetch(`/rpa/session/${sessionId}/agent/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved })
  })
}
```

### Key notes

- `agentMode` toggle should be disabled while `agentRunning` is true to prevent mid-run switching
- The confirm dialog should appear inline in the chat panel, not as a modal, to keep the UI non-blocking
- Existing SSE events (`message_chunk`, `script`, `executing`, `result`, `error`, `done`) remain unchanged for the `chat` mode path
