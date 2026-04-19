import json
import logging
import re
import asyncio
from typing import Dict, List, Any, AsyncGenerator, Optional, Callable

from playwright.async_api import Page
from backend.deepagent.engine import get_llm_model
from backend.rpa.assistant_runtime import (
    build_frame_path_from_frame,
    build_page_snapshot,
    execute_structured_intent,
    resolve_structured_intent,
    resolve_collection_target,
)
from backend.rpa.dom_flow import prepare_dom_context

# Active ReAct agent instances keyed by session_id
_active_agents: Dict[str, "RPAReActAgent"] = {}

logger = logging.getLogger(__name__)

ELEMENT_EXTRACTION_TIMEOUT_S = 5.0
EXECUTION_TIMEOUT_S = 60.0
THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
THINK_CONTENT_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


# JS to extract interactive elements from the page
EXTRACT_ELEMENTS_JS = r"""() => {
    const INTERACTIVE = 'a,button,input,textarea,select,[role=button],[role=link],[role=menuitem],[role=menuitemradio],[role=tab],[role=checkbox],[role=radio],[contenteditable=true]';
    const els = document.querySelectorAll(INTERACTIVE);
    const results = [];
    let index = 1;
    const seen = new Set();
    for (const el of els) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;
        if (el.disabled) continue;
        const style = getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;

        const tag = el.tagName.toLowerCase();
        const role = el.getAttribute('role') || '';
        const name = (el.getAttribute('aria-label') || el.innerText || '').trim().substring(0, 80);
        const placeholder = el.getAttribute('placeholder') || '';
        const href = el.getAttribute('href') || '';
        const value = el.value || '';
        const type = el.getAttribute('type') || '';

        const key = tag + role + name + placeholder + href;
        if (seen.has(key)) continue;
        seen.add(key);

        const info = { index, tag };
        if (role) info.role = role;
        if (name) info.name = name.replace(/\s+/g, ' ');
        if (placeholder) info.placeholder = placeholder;
        if (href) info.href = href.substring(0, 120);
        if (value && tag !== 'input') info.value = value.substring(0, 80);
        if (type) info.type = type;
        const checked = el.checked;
        if (checked !== undefined) info.checked = checked;

        results.push(info);
        index++;
        if (index > 150) break;
    }
    return JSON.stringify(results);
}"""

SYSTEM_PROMPT = """You are an RPA recording assistant.

Prefer returning a structured JSON action instead of raw Playwright code.

For common atomic actions, respond with JSON in this shape:
{
  "action": "navigate|click|fill|extract_text|press",
  "description": "short action summary",
  "prompt": "original user instruction",
  "result_key": "short_ascii_snake_case_key_for_extracted_value",
  "target_hint": {
    "role": "button|link|textbox|...",
    "name": "semantic label if known"
  },
  "collection_hint": {
    "kind": "search_results|table_rows|cards"
  },
  "ordinal": "first|last|1|2|3",
  "value": "text to fill or key to press when relevant"
}

Rules:
1. If the user says first or nth, use collection semantics and avoid hard-coded dynamic content.
2. Prefer role, label, placeholder, and structural hints over concrete titles or dynamic href values.
3. For opening a website or navigating to a known URL, prefer `"action": "navigate"` with the URL in `value`. Do not model browser chrome such as the address bar as a page textbox.
4. The backend resolves frame context automatically, so do not invent iframe selectors unless the user explicitly names a frame.
5. If the prompt includes "DOM Context For Data Extraction" and the user is asking to summarize, read, list, or extract current page information, answer directly in natural language when no extra browser interaction is needed.
6. Only output Python code for genuinely complex custom logic that cannot be expressed as one atomic structured action.
7. If you output Python, define async def run(page): and use Playwright async API.
8. For extract_text actions, include result_key as a short ASCII snake_case key such as latest_issue_title. Do not use Chinese, spaces, or hyphens.
"""

DATA_EXTRACTION_SYSTEM_PROMPT = """You are an RPA page-reading assistant.

Your job is to answer the user's question from the current page DOM context.

Rules:
1. If the prompt includes "DOM Context For Data Extraction", use that DOM context as the primary source of truth.
2. Summarize the current page directly in natural language when the user asks to get, read, summarize, list, or extract current page information.
3. Do not output JSON actions unless the user explicitly asks you to interact with the page or the answer cannot be produced from the provided DOM context.
4. Do not invent missing fields. If a value is not present in the DOM context, say it is not visible.
5. Prefer a structured, human-readable answer with key fields and short explanations when useful.
6. FALLBACK_RAW_HTML is only supplementary context. Prefer the structured DOM view over fallback raw HTML.
"""

async def _get_page_elements(page: Page) -> str:
    """Extract interactive elements directly from the page."""
    try:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=2000)
        except Exception:
            pass
        result = await asyncio.wait_for(
            page.evaluate(EXTRACT_ELEMENTS_JS),
            timeout=ELEMENT_EXTRACTION_TIMEOUT_S,
        )
        return result if isinstance(result, str) else json.dumps(result)
    except Exception as e:
        logger.warning(f"Failed to extract elements from {page.url!r}: {e}")
        return "[]"


async def _execute_on_page(page: Page, code: str) -> Dict[str, Any]:
    """Execute AI-generated code directly on the page object."""
    try:
        await page.evaluate("window.__rpa_paused = true")
    except Exception:
        pass
    try:
        namespace: Dict[str, Any] = {"page": page}
        exec(compile(code, "<rpa_assistant>", "exec"), namespace)
        if "run" in namespace and callable(namespace["run"]):
            ret = await asyncio.wait_for(namespace["run"](page), timeout=EXECUTION_TIMEOUT_S)
            return {"success": True, "output": str(ret) if ret else "ok", "error": None}
        else:
            return {"success": False, "output": "", "error": "No run(page) function defined"}
    except asyncio.TimeoutError:
        return {"success": False, "output": "", "error": f"Command execution timed out ({EXECUTION_TIMEOUT_S:.0f}s)"}
    except Exception:
        import traceback
        return {"success": False, "output": "", "error": traceback.format_exc()}
    finally:
        try:
            await page.evaluate("window.__rpa_paused = false")
        except Exception:
            pass


def _extract_llm_response_text(response: Any) -> str:
    """Normalize LangChain AIMessage content into a plain text response."""
    content = getattr(response, "content", "")
    additional_kwargs = getattr(response, "additional_kwargs", {}) or {}

    reasoning = additional_kwargs.get("reasoning_content", "")
    fallback_text = reasoning.strip() if isinstance(reasoning, str) else ""

    if isinstance(content, list):
        text_parts: List[str] = []
        thinking_parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "thinking":
                    thinking_parts.append(str(block.get("thinking", "")).strip())
                    continue
                text = block.get("text") or block.get("content")
                if text:
                    text_parts.append(str(text))
            elif isinstance(block, str):
                text_parts.append(block)
            elif block is not None:
                text_parts.append(str(block))
        clean = "\n".join(part.strip() for part in text_parts if str(part).strip()).strip()
        if clean:
            return clean
        thoughts = "\n".join(part for part in thinking_parts if part).strip()
        return thoughts or fallback_text

    if isinstance(content, str):
        clean = THINK_TAG_RE.sub("", content).strip()
        if clean:
            return clean
        if not fallback_text:
            matches = THINK_CONTENT_RE.findall(content)
            fallback_text = "\n".join(match.strip() for match in matches if match.strip()).strip()
        return fallback_text

    if content is None:
        return fallback_text

    text = str(content).strip()
    return text or fallback_text


def _extract_llm_chunk_text(chunk: Any) -> str:
    """Extract displayable text from a streamed chunk."""
    content = getattr(chunk, "content", "")
    if isinstance(content, list):
        text_parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "thinking":
                    continue
                text = block.get("text") or block.get("content")
                if text:
                    text_parts.append(str(text))
            elif isinstance(block, str):
                text_parts.append(block)
            elif block is not None:
                text_parts.append(str(block))
        return "".join(text_parts)
    if isinstance(content, str):
        return THINK_TAG_RE.sub("", content)
    return ""


def _extract_llm_chunk_fallback_text(chunk: Any) -> str:
    """Extract reasoning/thinking fallback text from a streamed chunk."""
    additional_kwargs = getattr(chunk, "additional_kwargs", {}) or {}
    reasoning = additional_kwargs.get("reasoning_content", "")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning.strip()

    content = getattr(chunk, "content", "")
    if isinstance(content, list):
        thoughts: List[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "thinking":
                thought = str(block.get("thinking", "")).strip()
                if thought:
                    thoughts.append(thought)
        return "\n".join(thoughts).strip()

    if isinstance(content, str):
        matches = THINK_CONTENT_RE.findall(content)
        return "\n".join(match.strip() for match in matches if match.strip()).strip()

    return ""


def _extract_json_object_blocks(text: str) -> List[str]:
    blocks: List[str] = []
    start: Optional[int] = None
    depth = 0
    in_string = False
    escape = False

    for index, char in enumerate(text):
        if start is None:
            if char == "{":
                start = index
                depth = 1
                in_string = False
                escape = False
            continue

        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                blocks.append(text[start:index + 1])
                start = None

    return blocks


def _normalize_hint_value(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_fillable_role(role: str) -> bool:
    return role in {"textbox", "spinbutton", "combobox", "listbox"}


def _iter_snapshot_actionable_candidates(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for node in snapshot.get("actionable_nodes", []):
        candidates.append(node)
    for frame in snapshot.get("frames", []):
        frame_path = list(frame.get("frame_path") or [])
        for element in frame.get("elements", []):
            candidates.append({**element, "frame_path": list(element.get("frame_path") or frame_path)})
    return candidates


def _field_match_score(field_name: str, node: Dict[str, Any]) -> int:
    normalized_field = _normalize_hint_value(field_name)
    if not normalized_field:
        return -1
    candidates = [
        _normalize_hint_value(node.get("name")),
        _normalize_hint_value(node.get("label")),
        _normalize_hint_value(node.get("text")),
        _normalize_hint_value(node.get("title")),
        _normalize_hint_value(node.get("placeholder")),
    ]
    best = -1
    for candidate in candidates:
        if not candidate:
            continue
        if candidate == normalized_field:
            best = max(best, 20)
        elif normalized_field in candidate:
            best = max(best, 14)
        elif candidate in normalized_field:
            best = max(best, 10)
    return best


def _expand_mapping_fill_intent(snapshot: Dict[str, Any], intent: Dict[str, Any]) -> List[Dict[str, Any]]:
    if _normalize_hint_value(intent.get("action")) != "fill":
        return []
    value = intent.get("value")
    if not isinstance(value, dict):
        return []

    expanded: List[Dict[str, Any]] = []
    candidates = _iter_snapshot_actionable_candidates(snapshot)
    used_node_keys: set[tuple[str, str, str]] = set()

    for field_name, field_value in value.items():
        if field_value in (None, ""):
            continue

        best_node: Optional[Dict[str, Any]] = None
        best_score = -1
        for node in candidates:
            role = _normalize_hint_value(node.get("role"))
            if not _is_fillable_role(role):
                continue
            action_kinds = {str(kind).lower() for kind in (node.get("action_kinds") or [])}
            if action_kinds and "fill" not in action_kinds and "select" not in action_kinds:
                continue
            score = _field_match_score(str(field_name), node)
            if score < 0:
                continue
            node_key = (
                str(node.get("frame_path") or []),
                str(node.get("name") or node.get("label") or node.get("placeholder") or ""),
                role,
            )
            if node_key in used_node_keys:
                continue
            if score > best_score:
                best_node = node
                best_score = score

        target_hint: Dict[str, Any] = {"name": str(field_name)}
        if best_node:
            role = best_node.get("role")
            if role:
                target_hint["role"] = role
            if best_node.get("placeholder"):
                target_hint["placeholder"] = best_node["placeholder"]
            used_node_keys.add(
                (
                    str(best_node.get("frame_path") or []),
                    str(best_node.get("name") or best_node.get("label") or best_node.get("placeholder") or ""),
                    str(best_node.get("role") or ""),
                )
            )

        expanded.append(
            {
                "action": "fill",
                "description": f"填写{field_name}字段",
                "prompt": intent.get("prompt"),
                "target_hint": target_hint,
                "value": str(field_value),
            }
        )

    return expanded


def _snapshot_frame_lines(snapshot: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    for container in snapshot.get("containers", []):
        lines.append(
            "Container: "
            f"{container.get('container_kind', 'container')} "
            f"{container.get('name', '')} "
            f"(actionable={len(container.get('child_actionable_ids') or [])}, "
            f"content={len(container.get('child_content_ids') or [])})"
        )
    for frame in snapshot.get("frames", []):
        lines.append(f"Frame: {frame.get('frame_hint', 'main document')}")
        for collection in frame.get("collections", []):
            lines.append(
                f"  Collection: {collection.get('kind', 'collection')} ({collection.get('item_count', 0)} items)"
            )
        for element in frame.get("elements", []):
            parts = [f"[{element.get('index', '?')}]"]
            if element.get("role"):
                parts.append(element["role"])
            parts.append(element.get("tag", "element"))
            if element.get("name"):
                parts.append(f'"{element["name"]}"')
            if element.get("placeholder"):
                parts.append(f'placeholder="{element["placeholder"]}"')
            if element.get("href"):
                parts.append(f'href="{element["href"]}"')
            if element.get("type"):
                parts.append(f'type={element["type"]}')
            lines.append("  " + " ".join(parts))
    return lines


REACT_SYSTEM_PROMPT = """You are an RPA automation agent.

You receive a goal and must iteratively observe the current page, decide the next atomic action, execute it, and continue until the goal is complete.

Return exactly one JSON object per turn, not wrapped in markdown.

Preferred format:
{
  "thought": "brief reasoning about the current page and next step",
  "action": "execute|done|abort",
  "operation": "navigate|click|fill|extract_text|press",
  "description": "short action summary",
  "result_key": "short_ascii_snake_case_key_for_extracted_value",
  "target_hint": {
    "role": "button|link|textbox|...",
    "name": "semantic label if known"
  },
  "collection_hint": {
    "kind": "search_results|table_rows|cards"
  },
  "ordinal": "first|last|1|2|3",
  "value": "text to fill or key to press when relevant",
  "risk": "none|high",
  "risk_reason": "required when risk is high"
}

Rules:
1. Prefer structured atomic actions with operation/target_hint/collection_hint over raw Playwright code.
2. Use collection semantics for first, last, and nth requests. Do not hard-code dynamic titles or href values.
3. For opening a website or jumping to a known URL, use operation=navigate with the URL in value. Do not refer to the browser address bar as a page textbox.
4. The backend resolves iframe context automatically from the snapshot. Do not invent iframe selectors unless the user explicitly names a frame.
5. Only use the code field for custom Playwright code when the action cannot be expressed as one atomic structured action.
6. For irreversible operations such as submit, delete, pay, or authorize, set risk to high.
7. For extraction tasks, use operation=extract_text, describe what data is being extracted, and include result_key as a short ASCII snake_case key such as latest_issue_title.
8. Do not mark the task done just because the data is visible on the page.
9. Execute the extraction step first and return the extracted value.
10. For example, if the user asks to get or read a title, first run extract_text on the target element, set result_key to something like latest_issue_title, then summarize the extracted value in description.
"""




class RPAReActAgent:
    """ReAct-based autonomous agent: Observe → Think → Act loop."""

    MAX_STEPS = 20

    def __init__(self):
        self._confirm_event: Optional[asyncio.Event] = None
        self._confirm_approved: bool = False
        self._aborted: bool = False
        self._history: List[Dict[str, str]] = []  # persists across turns

    def resolve_confirm(self, approved: bool) -> None:
        self._confirm_approved = approved
        if self._confirm_event:
            self._confirm_event.set()

    def abort(self) -> None:
        self._aborted = True
        if self._confirm_event:
            self._confirm_event.set()

    async def run(
        self,
        session_id: str,
        page: Page,
        goal: str,
        existing_steps: List[Dict[str, Any]],
        model_config: Optional[Dict[str, Any]] = None,
        page_provider: Optional[Callable[[], Optional[Page]]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        self._aborted = False
        steps_done = 0

        # Append new user goal to persistent history
        steps_summary = ""
        if existing_steps:
            lines = [f"{i+1}. {s.get('description', s.get('action', ''))}" for i, s in enumerate(existing_steps)]
            steps_summary = "\nExisting steps:\n" + "\n".join(lines) + "\n"
        self._history.append({"role": "user", "content": f"Goal: {goal}{steps_summary}"})

        for iteration in range(self.MAX_STEPS):
            if self._aborted:
                yield {"event": "agent_aborted", "data": {"reason": "用户中止"}}
                return

            # Observe
            current_page = page_provider() if page_provider else page
            if current_page is None:
                yield {"event": "agent_aborted", "data": {"reason": "No active page available"}}
                return
            snapshot = await build_page_snapshot(current_page, build_frame_path_from_frame)
            _intent, dom_mode, _candidates, dom_context_block, dom_debug = await prepare_dom_context(
                current_page,
                goal,
                model_config=model_config,
            )
            obs = self._build_observation(snapshot, steps_done, dom_mode, dom_context_block, dom_debug)
            yield {"event": "dom_context_debug", "data": dom_debug}

            # Think — stream LLM response
            full_response = ""
            turn_messages = self._history + [{"role": "user", "content": obs}]
            async for chunk in self._stream_llm(turn_messages, model_config):
                full_response += chunk

            self._history.append({"role": "assistant", "content": full_response})

            # Parse JSON
            parsed = self._parse_json(full_response)
            if not parsed:
                yield {"event": "agent_aborted", "data": {"reason": f"Unable to parse agent response: {full_response[:200]}"}}
                return

            thought = parsed.get("thought", "")
            action = parsed.get("action", "execute")
            structured_intent = self._extract_structured_execute_intent(parsed, goal)
            code = parsed.get("code", "")
            description = parsed.get("description", "Execute step")
            risk = parsed.get("risk", "none")
            risk_reason = parsed.get("risk_reason", "")
            action_payload = code or ""
            if structured_intent:
                action_payload = json.dumps(structured_intent, ensure_ascii=False)

            yield {"event": "agent_thought", "data": {"text": thought}}

            if action == "done":
                yield {"event": "agent_done", "data": {"total_steps": steps_done}}
                return

            if action == "abort":
                yield {"event": "agent_aborted", "data": {"reason": thought}}
                return

            # High-risk confirmation
            if risk == "high":
                self._confirm_event = asyncio.Event()
                self._confirm_approved = False
                yield {"event": "confirm_required", "data": {
                    "description": description,
                    "risk_reason": risk_reason,
                    "code": action_payload,
                }}
                await self._confirm_event.wait()
                self._confirm_event = None
                if self._aborted:
                    yield {"event": "agent_aborted", "data": {"reason": "User aborted"}}
                    return
                if not self._confirm_approved:
                    self._history.append({"role": "user", "content": "User rejected that step. Continue with a safer next step or finish."})
                    continue

            # Act
            yield {
                "event": "agent_action",
                "data": {
                    "description": description,
                    "code": action_payload,
                },
            }
            current_page = page_provider() if page_provider else page
            if current_page is None:
                yield {"event": "agent_aborted", "data": {"reason": "No active page available"}}
                return
            if structured_intent:
                resolved_intent = resolve_structured_intent(snapshot, structured_intent)
                result = await execute_structured_intent(current_page, resolved_intent)
            else:
                executable = self._wrap_code(code)
                result = await _execute_on_page(current_page, executable)
            if result["success"]:
                steps_done += 1
                step_data = result.get("step") or {
                    "action": "ai_script",
                    "source": "ai",
                    "value": code,
                    "description": description,
                    "prompt": goal,
                }
                output = result.get("output", "")
                output_meta = result.get("output_meta")
                # If there's meaningful output, append to description for visibility
                if output and output != "ok" and output != "None":
                    yield {"event": "agent_step_done", "data": {"step": step_data, "output": output, "output_meta": output_meta}}
                    self._history.append({"role": "user", "content": f"Step succeeded: {description}\nOutput: {output}"})
                else:
                    yield {"event": "agent_step_done", "data": {"step": step_data}}
                    self._history.append({"role": "user", "content": f"Step succeeded: {description}"})
            else:
                error_msg = result.get("error", "Unknown error")
                self._history.append({"role": "user", "content": f"Execution failed: {error_msg[:500]}\nAnalyze the failure and adjust the strategy."})

        yield {"event": "agent_done", "data": {"total_steps": steps_done}}

    @staticmethod
    def _build_observation(
        snapshot: Dict[str, Any],
        steps_done: int,
        dom_mode: str = "compact",
        dom_context_block: str = "",
        dom_debug: Optional[Dict[str, Any]] = None,
    ) -> str:
        frame_lines = _snapshot_frame_lines(snapshot)
        dom_section = ""
        if dom_context_block.strip():
            title = "当前页面 DOM 上下文（用于读取/提取数据）" if dom_mode == "full" else "当前页面 DOM 上下文（用于页面操作）"
            dom_section = f"""

{title}:
{dom_context_block}"""
        debug_section = ""
        if dom_debug:
            debug_section = f"""

DOM debug:
{json.dumps(dom_debug, ensure_ascii=False)}"""
        return f"""Current page state:
URL: {snapshot.get('url', '')}
Title: {snapshot.get('title', '')}
Completed steps: {steps_done}

Current page snapshot:
{chr(10).join(frame_lines) or "(no observable elements)"}
{dom_section}
{debug_section}

Return the next JSON action."""

    @staticmethod
    def _extract_structured_execute_intent(parsed: Dict[str, Any], prompt: str) -> Optional[Dict[str, Any]]:
        action = str(parsed.get("action", "") or "").strip().lower()
        operation = str(parsed.get("operation", "") or "").strip().lower()
        atomic_actions = {"navigate", "click", "fill", "extract_text", "press"}

        if action in atomic_actions:
            operation = action
        if action not in {"", "execute"} and action not in atomic_actions:
            return None
        if operation not in atomic_actions:
            return None

        intent: Dict[str, Any] = {
            "action": operation,
            "description": parsed.get("description", operation),
            "prompt": prompt,
        }
        for key in ("target_hint", "collection_hint", "ordinal", "value", "result_key"):
            value = parsed.get(key)
            if value is not None:
                intent[key] = value
        return intent

    @staticmethod
    def _parse_json(text: str) -> Optional[Dict[str, Any]]:
        # Try raw JSON first
        text = text.strip()
        try:
            return json.loads(text)
        except Exception:
            pass
        # Try extracting from code block
        m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except Exception:
                pass
        # Try finding { ... } block
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return None

    @staticmethod
    def _wrap_code(code: str) -> str:
        """Wrap bare code in async def run(page) if not already wrapped."""
        stripped = code.strip()
        if stripped.startswith("async def run(") or stripped.startswith("def run("):
            return stripped
        indented = "\n".join("    " + line for line in stripped.splitlines())
        return f"async def run(page):\n{indented}"

    @staticmethod
    async def _stream_llm(
        history: List[Dict[str, str]],
        model_config: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[str, None]:
        from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
        model = get_llm_model(config=model_config, streaming=True)
        lc_messages = [SystemMessage(content=REACT_SYSTEM_PROMPT)]
        for m in history:
            if m["role"] == "user":
                lc_messages.append(HumanMessage(content=m["content"]))
            elif m["role"] == "assistant":
                lc_messages.append(AIMessage(content=m["content"]))
        if hasattr(model, "astream"):
            text_parts: List[str] = []
            fallback_parts: List[str] = []
            async for chunk in model.astream(lc_messages):
                text = _extract_llm_chunk_text(chunk)
                if text:
                    text_parts.append(text)
                    continue
                fallback = _extract_llm_chunk_fallback_text(chunk)
                if fallback:
                    fallback_parts.append(fallback)
            full_text = "".join(text_parts)
            if full_text.strip():
                yield full_text
                return
            fallback_text = "\n".join(part for part in fallback_parts if part).strip()
            if fallback_text:
                yield fallback_text
                return

        response = await model.ainvoke(lc_messages)
        yield _extract_llm_response_text(response)


class RPAAssistant:
    """Frame-aware AI recording assistant."""

    def __init__(self):
        self._histories: Dict[str, List[Dict[str, str]]] = {}

    def _get_history(self, session_id: str) -> List[Dict[str, str]]:
        if session_id not in self._histories:
            self._histories[session_id] = []
        return self._histories[session_id]

    def _trim_history(self, session_id: str, max_rounds: int = 10):
        hist = self._get_history(session_id)
        max_msgs = max_rounds * 2
        if len(hist) > max_msgs:
            self._histories[session_id] = hist[-max_msgs:]

    async def chat(
        self,
        session_id: str,
        page: Page,
        message: str,
        steps: List[Dict[str, Any]],
        model_config: Optional[Dict[str, Any]] = None,
        page_provider: Optional[Callable[[], Optional[Page]]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        yield {"event": "message_chunk", "data": {"text": "正在分析当前页面......\n\n"}}
        current_page = page_provider() if page_provider else page
        if current_page is None:
            yield {"event": "error", "data": {"message": "No active page available"}}
            yield {"event": "done", "data": {}}
            return

        snapshot = await build_page_snapshot(current_page, build_frame_path_from_frame)
        _intent, dom_mode, _candidates, dom_context_block, dom_debug = await prepare_dom_context(
            current_page,
            message,
            model_config=model_config,
        )
        history = self._get_history(session_id)
        messages = self._build_messages(message, steps, snapshot, history, dom_mode, dom_context_block, dom_debug)

        full_response = ""
        async for chunk_text in self._stream_llm(messages, model_config):
            full_response += chunk_text
            yield {"event": "message_chunk", "data": {"text": chunk_text}}

        yield {"event": "executing", "data": {}}
        result, final_response, code, resolution, retry_notice = await self._execute_with_retry(
            page=page,
            page_provider=page_provider,
            snapshot=snapshot,
            full_response=full_response,
            messages=messages,
            model_config=model_config,
        )

        if retry_notice:
            yield {"event": "message_chunk", "data": {"text": retry_notice}}
        if resolution:
            yield {"event": "resolution", "data": {"intent": resolution}}
        yield {"event": "dom_context_debug", "data": dom_debug}

        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": final_response})
        self._trim_history(session_id)

        step_data = None
        steps_data = result.get("steps") or []
        if result["success"]:
            if result.get("step"):
                step_data = result["step"]
            elif code:
                body = self._extract_function_body(code)
                step_data = {
                    "action": "ai_script",
                    "source": "ai",
                    "value": body,
                    "description": message,
                    "prompt": message,
                }

        yield {
            "event": "result",
            "data": {
                "success": result["success"],
                "error": result.get("error"),
                "step": step_data,
                "steps": steps_data,
                "output": result.get("output"),
                "output_meta": result.get("output_meta"),
                "dom_debug": dom_debug,
            },
        }
        yield {"event": "done", "data": {}}

    async def _execute_with_retry(
        self,
        page: Page,
        page_provider: Optional[Callable[[], Optional[Page]]],
        snapshot: Dict[str, Any],
        full_response: str,
        messages: List[Dict[str, str]],
        model_config: Optional[Dict[str, Any]],
    ) -> tuple[Dict[str, Any], str, Optional[str], Optional[Dict[str, Any]], str]:
        current_page = page_provider() if page_provider else page
        if current_page is None:
            return {"success": False, "error": "No active page available", "output": ""}, full_response, None, None, ""

        try:
            result, code, resolution = await self._execute_single_response(current_page, snapshot, full_response)
            if result["success"]:
                return result, full_response, code, resolution, ""
        except Exception as exc:
            result = {"success": False, "error": str(exc), "output": ""}
            code = None
            resolution = None

        retry_messages = messages + [
            {"role": "assistant", "content": full_response},
            {"role": "user", "content": f"Execution error: {result['error']}\nPlease fix it and retry."},
        ]
        retry_response = ""
        async for chunk_text in self._stream_llm(retry_messages, model_config):
            retry_response += chunk_text

        current_page = page_provider() if page_provider else page
        if current_page is None:
            return {"success": False, "error": "No active page available", "output": ""}, retry_response, None, None, "\n\nExecution failed. Retrying.\n\n"

        retry_snapshot = await build_page_snapshot(current_page, build_frame_path_from_frame)
        try:
            retry_result, retry_code, retry_resolution = await self._execute_single_response(
                current_page,
                retry_snapshot,
                retry_response,
            )
            return retry_result, retry_response, retry_code, retry_resolution, "\n\nExecution failed. Retrying.\n\n"
        except Exception as exc:
            return {"success": False, "error": str(exc), "output": ""}, retry_response, None, None, "\n\nExecution failed. Retrying.\n\n"

    async def _execute_single_response(
        self,
        current_page: Page,
        snapshot: Dict[str, Any],
        full_response: str,
    ) -> tuple[Dict[str, Any], Optional[str], Optional[Dict[str, Any]]]:
        structured_intents = self._extract_structured_intents(full_response)
        if len(structured_intents) == 1:
            expanded = _expand_mapping_fill_intent(snapshot, structured_intents[0])
            if expanded:
                structured_intents = expanded
        if len(structured_intents) > 1:
            executed_steps: List[Dict[str, Any]] = []
            last_output = "ok"
            last_output_meta: Dict[str, Any] = {}
            resolved_batch: List[Dict[str, Any]] = []
            for intent in structured_intents:
                resolved_intent = resolve_structured_intent(snapshot, intent)
                resolved_batch.append(resolved_intent)
                result = await execute_structured_intent(current_page, resolved_intent)
                if result.get("step"):
                    executed_steps.append(result["step"])
                if result.get("output") not in (None, ""):
                    last_output = result["output"]
                if result.get("output_meta"):
                    last_output_meta = result["output_meta"]
            return {
                "success": True,
                "output": last_output,
                "output_meta": last_output_meta,
                "steps": executed_steps,
                "step": executed_steps[-1] if executed_steps else None,
            }, None, {"resolved_batch": resolved_batch}

        structured_intent = structured_intents[0] if structured_intents else None
        if structured_intent:
            resolved_intent = resolve_structured_intent(snapshot, structured_intent)
            result = await execute_structured_intent(current_page, resolved_intent)
            return result, None, resolved_intent

        code = self._extract_code(full_response)
        if not code:
            answer = full_response.strip()
            if not answer:
                raise ValueError("Unable to extract structured intent, executable code, or direct answer from assistant response")
            return {"success": True, "output": answer, "output_meta": {}, "step": None}, None, None
        result = await self._execute_on_page(current_page, code)
        return result, code, None

    def _build_messages(
        self,
        user_message: str,
        steps: List[Dict[str, Any]],
        snapshot: Dict[str, Any],
        history: List[Dict[str, str]],
        dom_mode: str = "compact",
        dom_context_block: str = "",
        dom_debug: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, str]]:
        steps_text = ""
        if steps:
            lines = []
            for i, step in enumerate(steps, 1):
                source = step.get("source", "record")
                desc = step.get("description", step.get("action", ""))
                lines.append(f"{i}. [{source}] {desc}")
            steps_text = "\n".join(lines)

        frame_lines = _snapshot_frame_lines(snapshot)

        dom_block = ""
        if dom_context_block.strip():
            heading = "## DOM Context For Data Extraction" if dom_mode == "full" else "## DOM Context For Operation"
            dom_block = f"""

{heading}
{dom_context_block}"""
        debug_block = ""
        if dom_debug:
            debug_block = f"""

## DOM Debug
{json.dumps(dom_debug, ensure_ascii=False)}"""

        context = f"""## History Steps
{steps_text or "(none)"}

## Current Page Snapshot
{chr(10).join(frame_lines) or "(no observable elements)"}
{dom_block}
{debug_block}

## User Instruction
{user_message}"""

        system_prompt = DATA_EXTRACTION_SYSTEM_PROMPT if dom_mode == "full" else SYSTEM_PROMPT
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": context})
        return messages

    async def _stream_llm(
        self,
        messages: List[Dict[str, str]],
        model_config: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[str, None]:
        model = get_llm_model(config=model_config, streaming=True)
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        lc_messages = []
        for message in messages:
            if message["role"] == "system":
                lc_messages.append(SystemMessage(content=message["content"]))
            elif message["role"] == "user":
                lc_messages.append(HumanMessage(content=message["content"]))
            elif message["role"] == "assistant":
                lc_messages.append(AIMessage(content=message["content"]))

        async for chunk in model.astream(lc_messages):
            text = _extract_llm_chunk_text(chunk)
            if text:
                yield text
                continue
            fallback = _extract_llm_chunk_fallback_text(chunk)
            if fallback:
                yield fallback

    @staticmethod
    def _extract_structured_intent(text: str) -> Optional[Dict[str, Any]]:
        intents = RPAAssistant._extract_structured_intents(text)
        return intents[0] if intents else None

    @staticmethod
    def _extract_structured_intents(text: str) -> List[Dict[str, Any]]:
        intents: List[Dict[str, Any]] = []
        stripped = text.strip()
        try:
            parsed = json.loads(stripped)
        except Exception:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("action"):
            return [parsed]
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict) and item.get("action")]

        match = re.search(r"```json\s*\n(.*?)```", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1).strip())
            except Exception:
                parsed = None
            if isinstance(parsed, dict) and parsed.get("action"):
                return [parsed]
            if isinstance(parsed, list):
                intents.extend(item for item in parsed if isinstance(item, dict) and item.get("action"))
                if intents:
                    return intents

        for block in _extract_json_object_blocks(text):
            try:
                parsed = json.loads(block)
            except Exception:
                continue
            if isinstance(parsed, dict) and parsed.get("action"):
                intents.append(parsed)
        return intents

    @staticmethod
    def _extract_code(text: str) -> Optional[str]:
        pattern = r"```python\s*\n(.*?)```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        pattern2 = r"(async def run\(page\):.*)"
        match2 = re.search(pattern2, text, re.DOTALL)
        if match2:
            return match2.group(1).strip()
        pattern3 = r"(def run\(page\):.*)"
        match3 = re.search(pattern3, text, re.DOTALL)
        if match3:
            return match3.group(1).strip()
        return None

    @staticmethod
    def _extract_function_body(code: str) -> str:
        lines = code.split("\n")
        body_lines = []
        in_body = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("async def run(") or stripped.startswith("def run("):
                in_body = True
                continue
            if in_body:
                if line.startswith("    "):
                    body_lines.append(line[4:])
                elif line.strip() == "":
                    body_lines.append("")
                else:
                    body_lines.append(line)
        return "\n".join(body_lines).strip()

    async def _get_page_elements(self, page: Page) -> str:
        return await _get_page_elements(page)

    async def _execute_on_page(self, page: Page, code: str) -> Dict[str, Any]:
        return await _execute_on_page(page, code)
