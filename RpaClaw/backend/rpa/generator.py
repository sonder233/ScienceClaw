import json
import logging
import re
from typing import List, Dict, Any, Optional

from backend.rpa.playwright_security import get_chromium_launch_kwargs, get_context_kwargs

logger = logging.getLogger(__name__)

RPA_PLAYWRIGHT_TIMEOUT_MS = 60000
RPA_NAVIGATION_TIMEOUT_MS = 60000


class PlaywrightGenerator:
    """Generate Playwright Python scripts from recorded RPA steps.

    Locators are pre-computed in the browser using a Playwright-codegen-style
    algorithm (role > testid > label > placeholder > alt > title > css).
    The generator simply translates the locator objects into Playwright API calls.
    """

    # Docker mode: connects to sandbox's browser via CDP
    RUNNER_TEMPLATE_DOCKER = '''\
import asyncio
import json as _json
import sys
import httpx
from playwright.async_api import async_playwright


async def _get_cdp_url() -> str:
    """Fetch CDP WebSocket URL from the local sandbox API."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get("http://127.0.0.1:8080/v1/browser/info")
        resp.raise_for_status()
        return resp.json()["data"]["cdp_url"]


{execute_skill_func}


async def main():
    kwargs = {{}}
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            k, v = arg[2:].split("=", 1)
            kwargs[k] = v

    cdp_url = await _get_cdp_url()
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(cdp_url)
    context = await browser.new_context(**{context_kwargs})
    page = await context.new_page()
    page.set_default_timeout({default_timeout_ms})
    page.set_default_navigation_timeout({navigation_timeout_ms})
    try:
        _result = await execute_skill(page, **kwargs)
        if _result:
            print("SKILL_DATA:" + _json.dumps(_result, ensure_ascii=False, default=str))
        print("SKILL_SUCCESS")
    except Exception as e:
        print(f"SKILL_ERROR: {{e}}", file=sys.stderr)
        sys.exit(1)
    finally:
        await context.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
'''

    # Local mode: launches local browser directly
    RUNNER_TEMPLATE_LOCAL = '''\
import asyncio
import json as _json
import sys
from playwright.async_api import async_playwright


{execute_skill_func}


async def main():
    kwargs = {{}}
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            k, v = arg[2:].split("=", 1)
            kwargs[k] = v

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(**{launch_kwargs})
    context = await browser.new_context(**{context_kwargs})
    page = await context.new_page()
    page.set_default_timeout({default_timeout_ms})
    page.set_default_navigation_timeout({navigation_timeout_ms})
    try:
        _result = await execute_skill(page, **kwargs)
        if _result:
            print("SKILL_DATA:" + _json.dumps(_result, ensure_ascii=False, default=str))
        print("SKILL_SUCCESS")
    except Exception as e:
        print(f"SKILL_ERROR: {{e}}", file=sys.stderr)
        sys.exit(1)
    finally:
        await context.close()
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
'''

    TEST_MODE_PREAMBLE = '''\

class StepExecutionError(Exception):
    def __init__(self, step_index: int, original_error: str = ""):
        self.step_index = step_index
        self.original_error = original_error
        super().__init__(f"STEP_FAILED:{step_index}:{original_error}")
'''

    def generate_script(
        self,
        steps: List[Dict[str, Any]],
        params: Dict[str, Any] = None,
        is_local: bool = False,
        test_mode: bool = False,
        extraction_implementation: str = "auto",
    ) -> str:
        params = params or {}
        extraction_implementation = self._normalize_extraction_implementation(extraction_implementation)
        deduped = self._deduplicate_steps(steps)
        deduped = self._infer_missing_tab_transitions(deduped)
        deduped = self._normalize_step_signals(deduped)
        root_tab_id = deduped[0].get("tab_id") if deduped else None
        root_tab_id = root_tab_id or "tab-1"
        used_result_keys: Dict[str, int] = {}

        lines = [
            "",
            "def _lookup_extracted_value(results, result_key, field_name, default_value=''):",
            "    result = results.get(result_key) or {}",
            "    if isinstance(result, dict):",
            "        fields = result.get('fields') or {}",
            "        if isinstance(fields, dict):",
            "            entry = fields.get(field_name)",
            "            if isinstance(entry, dict) and entry.get('content') not in (None, ''):",
            "                return str(entry.get('content'))",
            "            for candidate in fields.values():",
            "                if isinstance(candidate, dict) and candidate.get('label') == field_name and candidate.get('content') not in (None, ''):",
            "                    return str(candidate.get('content'))",
            "    return default_value",
            "",
            "async def execute_skill(page, **kwargs):",
            '    """Auto-generated skill from RPA recording."""',
            "    _results = {}",
            f'    tabs = {{"{root_tab_id}": page}}',
            "    current_page = page",
        ]

        current_tab_id = root_tab_id
        prev_url = None
        prev_action = None
        # Add initial navigation if first step isn't a navigate action
        if deduped and deduped[0].get("action") not in ("navigate", "goto"):
            first_url = deduped[0].get("url", "")
            if first_url:
                lines.append(f'    await current_page.goto("{first_url}")')
                lines.append('    await current_page.wait_for_load_state("domcontentloaded")')
                lines.append("")
                prev_url = first_url

        for step_index, step in enumerate(deduped):
            action = step.get("action", "")
            target = step.get("target", "")
            value = step.get("value", "")
            url = step.get("url", "")
            desc = step.get("description", "")
            frame_path = step.get("frame_path") or []

            step_lines: List[str] = []

            if desc:
                lines.append(f"    # {desc}")

            # AI-generated script — embed directly with sync→async conversion
            if action == "ai_script":
                ai_code = step.get("value", "")
                if ai_code:
                    converted = self._sync_to_async(ai_code)
                    converted = self._inject_result_capture(converted)
                    converted = self._strip_locator_result_capture(converted)
                    for code_line in converted.split("\n"):
                        step_lines.append(f"    {code_line}" if code_line.strip() else "")
                lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
                lines.append("")
                continue

            # Navigation
            if action == "navigate" or (action == "goto" and url):
                step_lines.append(f'    await current_page.goto("{url}")')
                step_lines.append('    await current_page.wait_for_load_state("domcontentloaded")')
                prev_url = url
                prev_action = "navigate"
                lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
                lines.append("")
                continue

            if action == "switch_tab":
                target_tab_id = step.get("target_tab_id") or step.get("tab_id") or root_tab_id
                step_lines.append(f'    current_page = tabs["{target_tab_id}"]')
                step_lines.append("    await current_page.bring_to_front()")
                lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
                lines.append("")
                current_tab_id = target_tab_id
                prev_action = action
                continue

            if action == "close_tab":
                closing_tab_id = step.get("tab_id") or step.get("source_tab_id")
                fallback_tab_id = step.get("target_tab_id")
                if closing_tab_id:
                    step_lines.append(f'    closing_page = tabs.pop("{closing_tab_id}", current_page)')
                else:
                    step_lines.append("    closing_page = current_page")
                step_lines.append("    await closing_page.close()")
                if closing_tab_id == current_tab_id:
                    if fallback_tab_id:
                        step_lines.append(f'    current_page = tabs["{fallback_tab_id}"]')
                        step_lines.append("    await current_page.bring_to_front()")
                        current_tab_id = fallback_tab_id
                    else:
                        current_tab_id = closing_tab_id
                lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
                lines.append("")
                prev_action = action
                continue

            # Standalone download step has no locator — handle before _build_locator
            if action == "download":
                safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', (value or "file").split('.')[0]) or "file"
                step_lines.append(f'    # NOTE: download of "{value}" was triggered by a previous action')
                step_lines.append(f'    # If this step appears, manually wrap the triggering click with expect_download()')
                lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
                lines.append("")
                continue

            scope_var = "current_page"
            if frame_path:
                scope_var = "frame_scope"
                frame_parent = "current_page"
                for frame_selector in frame_path:
                    step_lines.append(
                        f'    frame_scope = {frame_parent}.frame_locator("{self._escape(frame_selector)}")'
                    )
                    frame_parent = "frame_scope"

            # Prefer adaptive collection locators for AI steps like "click the first item".
            locator = self._build_adaptive_locator_for_step(step, scope_var)
            if not locator:
                # Parse the locator object from target (stored as JSON string)
                locator = self._build_locator_for_page(target, scope_var)
            
            # Enhance text-based locators to handle multi-match scenarios (e.g., form fields with fake inputs)
            if 'get_by_text(' in locator or 'get_by_text("' in locator:
                enhanced_locator = self._enhance_locator_for_robustness(locator, scope_var, action)
                if enhanced_locator != locator:
                    locator = enhanced_locator

            popup_signal = self._popup_signal(step)
            download_signal = self._download_signal(step)
            if popup_signal and not self._should_materialize_popup(deduped, step_index, popup_signal, download_signal):
                popup_signal = None
            if action in {"click", "press"} and (popup_signal or download_signal):
                interaction = f'await {locator}.click()' if action == "click" else f'await {locator}.press("{value}")'
                outer_indent = "    "
                if download_signal:
                    step_lines.append(f"{outer_indent}async with current_page.expect_download() as _dl_info:")
                    outer_indent += "    "
                if popup_signal:
                    step_lines.append(f"{outer_indent}async with current_page.expect_popup() as popup_info:")
                    outer_indent += "    "
                step_lines.append(f"{outer_indent}{interaction}")

                if popup_signal:
                    popup_indent = "    " + ("    " if download_signal else "")
                    target_tab_id = popup_signal.get("target_tab_id") or step.get("target_tab_id") or "tab-new"
                    step_lines.append(f"{popup_indent}new_page = await popup_info.value")
                    step_lines.append(f'{popup_indent}tabs["{target_tab_id}"] = new_page')
                    step_lines.append(f"{popup_indent}current_page = new_page")
                    current_tab_id = target_tab_id

                if download_signal:
                    download_name = download_signal.get("filename") or value or "file"
                    safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', str(download_name).split('.')[0]) or "file"
                    step_lines.append("    _dl = await _dl_info.value")
                    step_lines.append("    _dl_dir = kwargs.get('_downloads_dir', '.')")
                    step_lines.append("    import os as _os; _os.makedirs(_dl_dir, exist_ok=True)")
                    step_lines.append("    _dl_dest = _os.path.join(_dl_dir, _dl.suggested_filename)")
                    step_lines.append("    await _dl.save_as(_dl_dest)")
                    step_lines.append(f'    _results["download_{safe_name}"] = {{"filename": _dl.suggested_filename, "path": _dl_dest}}')

                lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
                lines.append("")
                prev_action = action
                continue

            if action == "navigate_click":
                step_lines.append(f"    async with current_page.expect_navigation(wait_until='domcontentloaded', timeout={RPA_NAVIGATION_TIMEOUT_MS}):")
                step_lines.append(f"        await {locator}.click()")
            elif action == "navigate_press":
                step_lines.append(f"    async with current_page.expect_navigation(wait_until='domcontentloaded', timeout={RPA_NAVIGATION_TIMEOUT_MS}):")
                step_lines.append(f'        await {locator}.press("{value}")')
            elif action == "click":
                step_lines.append(f"    await {locator}.click()")
                # After non-navigation click, wait briefly for UI changes
                step_lines.append("    await current_page.wait_for_timeout(500)")
            elif action == "download_click":
                # Click that triggers a file download — wrap with expect_download
                safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', (value or "file").split('.')[0]) or "file"
                step_lines.append(f"    async with current_page.expect_download() as _dl_info:")
                step_lines.append(f"        await {locator}.click()")
                step_lines.append(f"    _dl = await _dl_info.value")
                step_lines.append(f"    _dl_dir = kwargs.get('_downloads_dir', '.')")
                step_lines.append(f"    import os as _os; _os.makedirs(_dl_dir, exist_ok=True)")
                step_lines.append(f"    _dl_dest = _os.path.join(_dl_dir, _dl.suggested_filename)")
                step_lines.append(f"    await _dl.save_as(_dl_dest)")
                step_lines.append(f'    _results["download_{safe_name}"] = {{"filename": _dl.suggested_filename, "path": _dl_dest}}')
            elif action == "fill":
                fill_value = self._build_fill_value_expression(step, value, params)
                step_lines.append(f"    await {locator}.fill({fill_value})")
            elif action == "check":
                step_lines.append(f"    await {locator}.check()")
            elif action == "uncheck":
                step_lines.append(f"    await {locator}.uncheck()")
            elif action == "set_input_files":
                input_files_value = self._build_input_files_value(step, value, params)
                step_lines.append(f"    await {locator}.set_input_files({input_files_value})")
            elif action == "extract_text":
                result_key = self._build_extract_result_key(step, used_result_keys)
                step_lines.extend(
                    self._build_extract_text_step_lines(
                        step=step,
                        locator=locator,
                        scope_var=scope_var,
                        result_key=result_key,
                        step_index=step_index,
                        extraction_implementation=extraction_implementation,
                    )
                )
            elif action == "press":
                step_lines.append(f'    await {locator}.press("{value}")')
            elif action == "select":
                select_value = self._build_fill_value_expression(step, value, params)
                step_lines.append("    try:")
                step_lines.append(f"        await {locator}.select_option(label={select_value})")
                step_lines.append("    except Exception:")
                step_lines.append(f"        await {locator}.select_option(value={select_value})")

            prev_action = action
            lines.extend(self._wrap_step_lines(step_lines, step_index, test_mode))
            lines.append("")

        lines.append("    return _results")

        # Wrap execute_skill function with the runner boilerplate
        execute_skill_func = "\n".join(lines)
        if test_mode:
            execute_skill_func = self.TEST_MODE_PREAMBLE + execute_skill_func
        template = self.RUNNER_TEMPLATE_LOCAL if is_local else self.RUNNER_TEMPLATE_DOCKER
        return template.format(
            execute_skill_func=execute_skill_func,
            default_timeout_ms=RPA_PLAYWRIGHT_TIMEOUT_MS,
            navigation_timeout_ms=RPA_NAVIGATION_TIMEOUT_MS,
            launch_kwargs=repr(get_chromium_launch_kwargs(headless=False)),
            context_kwargs=repr(get_context_kwargs()),
        )

    @staticmethod
    def _wrap_step_lines(step_lines: List[str], step_index: int, test_mode: bool) -> List[str]:
        """Optionally wrap step code lines in try/except for test mode error reporting."""
        if not test_mode or not step_lines:
            return step_lines
        wrapped = ["    try:"]
        for line in step_lines:
            if line == "":
                wrapped.append("")
            else:
                wrapped.append("    " + line)
        wrapped.append("    except StepExecutionError:")
        wrapped.append("        raise")
        wrapped.append("    except Exception as _e:")
        wrapped.append(f"        raise StepExecutionError(step_index={step_index}, original_error=str(_e))")
        return wrapped

    def _build_extract_result_key(self, step: Dict[str, Any], used_result_keys: Dict[str, int]) -> str:
        key = self._normalize_result_key(step.get("result_key"))
        if not key:
            fallback_count = used_result_keys.get("extract_text", 0) + 1
            used_result_keys["extract_text"] = fallback_count
            return f"extract_text_{fallback_count}"

        count = used_result_keys.get(key, 0) + 1
        used_result_keys[key] = count
        if count == 1:
            return key
        return f"{key}_{count}"

    def _build_extract_text_step_lines(
        self,
        *,
        step: Dict[str, Any],
        locator: str,
        scope_var: str,
        result_key: str,
        step_index: int,
        extraction_implementation: str,
    ) -> List[str]:
        extracted_fields = step.get("extracted_fields") or []
        step_lines = [f'    _results["{result_key}"] = {{"raw": "", "fields": {{}}}}']
        first_content_expr: Optional[str] = None

        if not extracted_fields:
            value_var = f"extract_text_value_{step_index + 1}"
            if 'get_by_text(' in locator or 'get_by_text("' in locator:
                step_lines.append("    try:")
                step_lines.append(f"        {value_var} = ((await {locator}.first.inner_text()) or '').strip()")
                step_lines.append("    except Exception as _e:")
                safe_locator = locator.replace("'", "\\'")
                step_lines.append(f"        elements = await {scope_var}.locator('{safe_locator}').count()")
                step_lines.append(f"        {value_var} = ''")
                step_lines.append("        for i in range(min(elements, 3)):")
                step_lines.append(f"            el_text = ((await {scope_var}.locator('{safe_locator}').nth(i).inner_text()) or '').strip()")
                step_lines.append("            if el_text and not any(x in str(el_text).lower() for x in ['fake', 'hidden', 'placeholder']):")
                step_lines.append(f"                {value_var} = el_text")
                step_lines.append("                break")
            else:
                step_lines.append(f"    {value_var} = ((await {locator}.inner_text()) or '').strip()")
            step_lines.append(
                f'    _results["{result_key}"]["fields"]["value"] = {{"label": "value", "content": {value_var}}}'
            )
            step_lines.append(f'    _results["{result_key}"]["raw"] = {value_var}')
            return step_lines

        for field_index, field in enumerate(extracted_fields, start=1):
            field_name = str(field.get("name") or field.get("label") or f"value_{field_index}")
            field_label = str(field.get("label") or field_name)
            safe_var = re.sub(r"[^a-z0-9_]", "_", field_name.lower()) or "value"
            field_var = f"_field_{safe_var}_{step_index + 1}"
            for code_line in self._build_extract_field_value_lines(
                step=step,
                field=field,
                field_var=field_var,
                locator=locator,
                scope_var=scope_var,
                extraction_implementation=extraction_implementation,
            ):
                step_lines.append(code_line)

            step_lines.append(
                f'    _results["{result_key}"]["fields"]["{self._escape(field_name)}"] = '
                f'{{"label": "{self._escape(field_label)}", "content": {field_var}}}'
            )
            if first_content_expr is None:
                first_content_expr = field_var

        if first_content_expr is not None:
            step_lines.append(f'    _results["{result_key}"]["raw"] = {first_content_expr}')

        return step_lines

    def _build_extract_field_value_lines(
        self,
        *,
        step: Dict[str, Any],
        field: Dict[str, Any],
        field_var: str,
        locator: str,
        scope_var: str,
        extraction_implementation: str,
    ) -> List[str]:
        label = str(field.get("label") or field.get("name") or "").strip()
        base_selector = self._extract_css_selector(step.get("target"))

        # Per-field candidate takes highest priority
        candidates = field.get("extract_candidates") or []
        selected = next((c for c in candidates if c.get("selected")), candidates[0] if candidates else None)
        if selected:
            kind = selected.get("kind", "")
            expr = str(selected.get("expression") or "")
            locator_payload = selected.get("locator_payload")
            if isinstance(locator_payload, dict) and locator_payload:
                payload_expr = self._build_locator_for_page(json.dumps(locator_payload, ensure_ascii=False), scope_var)
                return [f"    {field_var} = ((await {payload_expr}.inner_text()) or '').strip()"]
            if expr:
                if kind == "playwright_locator":
                    if expr.startswith("{") and expr.endswith("}"):
                        try:
                            payload = json.loads(expr)
                        except Exception:
                            payload = None
                        if isinstance(payload, dict):
                            payload_expr = self._build_locator_for_page(json.dumps(payload, ensure_ascii=False), scope_var)
                            return [f"    {field_var} = ((await {payload_expr}.inner_text()) or '').strip()"]
                    return [
                        f"    {field_var} = ((await {scope_var}.locator({json.dumps(expr)}).first.text_content()) or '').strip()"
                    ]
                if kind == "js_evaluate":
                    return [
                        f"    {field_var} = await {scope_var}.evaluate({self._as_python_multiline_string(expr)})",
                        f"    {field_var} = str({field_var} or '').strip()",
                    ]

        if extraction_implementation == "locator":
            selector = self._build_labeled_field_selector(base_selector, label)
            if selector:
                escaped_selector = self._escape(selector)
                return [
                    f'    {field_var} = ((await {scope_var}.locator("{escaped_selector}").text_content()) or \'\').strip()'
                ]

        if extraction_implementation == "js":
            js_code = self._build_label_lookup_js(base_selector, label)
            if js_code:
                return [
                    f'    {field_var} = await {scope_var}.evaluate({self._as_python_multiline_string(js_code)})',
                    f"    {field_var} = str({field_var} or '').strip()",
                ]

        if field.get("extract_js"):
            js_fn = str(field["extract_js"])
            return [
                f"    {field_var} = await {scope_var}.evaluate({self._as_python_multiline_string(js_fn)})",
                f"    {field_var} = str({field_var} or '').strip()",
            ]

        field_locator = field.get("locator")
        field_target = json.dumps(field_locator) if isinstance(field_locator, dict) and field_locator else None
        field_loc_expr = self._build_locator_for_page(field_target, scope_var) if field_target else locator
        return [f"    {field_var} = ((await {field_loc_expr}.inner_text()) or '').strip()"]

    def _normalize_result_key(self, raw_key: Any) -> str:
        text = str(raw_key or "").strip().lower()
        if not text:
            return ""
        text = re.sub(r"[^a-z0-9_]+", "_", text)
        text = re.sub(r"_+", "_", text).strip("_")
        if not text:
            return ""
        if text[0].isdigit():
            text = f"extract_{text}"
        return text[:64]

    @staticmethod
    def _normalize_extraction_implementation(value: Any) -> str:
        normalized = str(value or "auto").strip().lower()
        if normalized in {"locator", "js"}:
            return normalized
        return "auto"

    @staticmethod
    def _as_python_multiline_string(value: str) -> str:
        escaped = str(value or "").replace('"""', '\\"\\"\\"')
        return f'"""{escaped}"""'

    @staticmethod
    def _extract_css_selector(raw_locator: Any) -> Optional[str]:
        payload: Any = raw_locator
        if isinstance(raw_locator, str):
            try:
                payload = json.loads(raw_locator)
            except Exception:
                payload = None
        if isinstance(payload, dict) and payload.get("method") == "css":
            value = str(payload.get("value") or "").strip()
            return value or None
        return None

    @staticmethod
    def _build_labeled_field_selector(base_selector: Optional[str], label: str) -> Optional[str]:
        normalized_label = str(label or "").strip()
        normalized_base = str(base_selector or "").strip()
        if not normalized_label or not normalized_base:
            return None
        escaped_label = normalized_label.replace("\\", "\\\\").replace('"', '\\"')
        return f'{normalized_base}:has(span:text("{escaped_label}")) strong'

    @staticmethod
    def _build_label_lookup_js(base_selector: Optional[str], label: str) -> Optional[str]:
        normalized_label = str(label or "").strip()
        if not normalized_label:
            return None
        escaped_label = normalized_label.replace("\\", "\\\\").replace("'", "\\'")
        selector = str(base_selector or ".detail-item").strip() or ".detail-item"
        escaped_selector = selector.replace("\\", "\\\\").replace("'", "\\'")
        return "\n".join(
            [
                "() => {",
                f"    const items = Array.from(document.querySelectorAll('{escaped_selector}'));",
                f"    const label = '{escaped_label}';",
                "    for (const item of items) {",
                "        const span = item.querySelector('span');",
                "        if (!span) continue;",
                "        const text = (span.textContent || '').trim();",
                "        if (!text || !text.includes(label)) continue;",
                "        const valueEl = span.nextElementSibling || item.querySelector('strong, .value, [data-value]');",
                "        return valueEl ? (valueEl.textContent || '').trim() : null;",
                "    }",
                "    return null;",
                "}",
            ]
        )

    @classmethod
    def _infer_missing_tab_transitions(cls, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Backfill tab open/switch semantics for older recordings that only carry tab_id."""
        if not steps:
            return steps

        normalized: List[Dict[str, Any]] = []
        current_tab_id = steps[0].get("tab_id") or "tab-1"
        known_tabs = {current_tab_id}

        for original_step in steps:
            step = dict(original_step)
            step_tab_id = step.get("tab_id") or current_tab_id

            if step_tab_id != current_tab_id:
                previous_step = normalized[-1] if normalized else None
                if step_tab_id not in known_tabs and previous_step and previous_step.get("action") in {"click", "press", "open_tab_click"}:
                    signals = dict(previous_step.get("signals") or {})
                    popup_signal = dict(signals.get("popup") or {})
                    popup_signal.setdefault("source_tab_id", current_tab_id)
                    popup_signal["target_tab_id"] = step_tab_id
                    signals["popup"] = popup_signal
                    previous_step["signals"] = signals
                    previous_step["source_tab_id"] = current_tab_id
                    previous_step["target_tab_id"] = step_tab_id
                    known_tabs.add(step_tab_id)
                elif step_tab_id in known_tabs:
                    normalized.append(
                        {
                            "action": "switch_tab",
                            "tab_id": current_tab_id,
                            "target_tab_id": step_tab_id,
                            "description": "Switch tab",
                            "url": step.get("url", ""),
                        }
                    )
                else:
                    known_tabs.add(step_tab_id)

                current_tab_id = step_tab_id

            previous_step = normalized[-1] if normalized else None
            if (
                step.get("action") == "navigate"
                and previous_step
                and cls._popup_target_tab_id(previous_step) == step_tab_id
            ):
                continue

            normalized.append(step)

        return normalized

    @classmethod
    def _popup_target_tab_id(cls, step: Dict[str, Any]) -> str:
        signals = step.get("signals") or {}
        popup_signal = signals.get("popup") if isinstance(signals, dict) else None
        if isinstance(popup_signal, dict) and popup_signal.get("target_tab_id"):
            return str(popup_signal.get("target_tab_id"))
        return str(step.get("target_tab_id") or "")

    @staticmethod
    def _popup_signal(step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        signals = step.get("signals")
        if not isinstance(signals, dict):
            return None
        popup_signal = signals.get("popup")
        if isinstance(popup_signal, dict):
            return popup_signal
        return None

    @staticmethod
    def _download_signal(step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        signals = step.get("signals")
        if not isinstance(signals, dict):
            return None
        download_signal = signals.get("download")
        if isinstance(download_signal, dict):
            return download_signal
        return None

    @classmethod
    def _normalize_step_signals(cls, steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for original_step in steps:
            step = dict(original_step)
            signals = dict(step.get("signals") or {})
            action = step.get("action")

            if action == "open_tab_click":
                popup_signal = dict(signals.get("popup") or {})
                popup_signal.setdefault("source_tab_id", step.get("source_tab_id") or step.get("tab_id"))
                if step.get("target_tab_id"):
                    popup_signal.setdefault("target_tab_id", step.get("target_tab_id"))
                signals["popup"] = popup_signal
                step["action"] = "click"

            if action == "download_click":
                download_signal = dict(signals.get("download") or {})
                if step.get("value"):
                    download_signal.setdefault("filename", step.get("value"))
                if step.get("tab_id"):
                    download_signal.setdefault("tab_id", step.get("tab_id"))
                signals["download"] = download_signal
                step["action"] = "click"

            step["signals"] = signals

            if step.get("action") == "download" and cls._merge_standalone_download_step(normalized, step):
                continue

            normalized.append(step)

        return normalized

    @classmethod
    def _merge_standalone_download_step(cls, normalized_steps: List[Dict[str, Any]], download_step: Dict[str, Any]) -> bool:
        download_tab_id = str(download_step.get("tab_id") or "")
        download_name = str(download_step.get("value") or "file")

        for previous_step in reversed(normalized_steps):
            if previous_step.get("action") not in {"click", "press"}:
                continue
            previous_tab_id = str(previous_step.get("tab_id") or "")
            popup_target_tab_id = cls._popup_target_tab_id(previous_step)
            if download_tab_id and download_tab_id not in {previous_tab_id, popup_target_tab_id}:
                continue

            signals = dict(previous_step.get("signals") or {})
            download_signal = dict(signals.get("download") or {})
            if download_name:
                download_signal.setdefault("filename", download_name)
            if download_tab_id:
                download_signal.setdefault("tab_id", download_tab_id)
            signals["download"] = download_signal
            previous_step["signals"] = signals
            if download_name and not previous_step.get("value"):
                previous_step["value"] = download_name
            return True

        return False

    @classmethod
    def _should_materialize_popup(
        cls,
        steps: List[Dict[str, Any]],
        step_index: int,
        popup_signal: Dict[str, Any],
        download_signal: Optional[Dict[str, Any]],
    ) -> bool:
        target_tab_id = str(popup_signal.get("target_tab_id") or "")
        if not target_tab_id:
            return False
        if not download_signal:
            return True

        for future_step in steps[step_index + 1:]:
            future_tab_id = str(future_step.get("tab_id") or "")
            future_target_tab_id = str(future_step.get("target_tab_id") or "")
            if future_tab_id == target_tab_id or future_target_tab_id == target_tab_id:
                return True
            future_popup_tab_id = cls._popup_target_tab_id(future_step)
            if future_popup_tab_id == target_tab_id:
                return True
        return False

    @staticmethod
    def _deduplicate_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove consecutive duplicate actions (same action + same target).

        For fill actions on the same target, keep the LAST one (final typed value).
        """
        if not steps:
            return steps
        result = [steps[0]]
        for step in steps[1:]:
            prev = result[-1]
            # Same action and same target → replace with the later one
            # (keeps the final/complete value for fill actions)
            # BUT: never deduplicate AI steps (each AI instruction is unique)
            if (step.get("action") == prev.get("action")
                    and step.get("target") == prev.get("target")
                    and step.get("action") not in ("navigate", "ai_script")):
                result[-1] = step  # Replace previous with current (keep last)
                continue
            result.append(step)
        return result

    def _build_locator(self, target: str) -> str:
        """Convert a locator JSON object (from browser capture) to Playwright API call.

        The locator object has a 'method' field indicating the strategy:
          role     → page.get_by_role(role, name=name, exact=True)
          testid   → page.get_by_test_id(value)
          label    → page.get_by_label(value, exact=True)
          placeholder → page.get_by_placeholder(value, exact=True)
          alt      → page.get_by_alt_text(value, exact=True)
          title    → page.get_by_title(value, exact=True)
          css      → page.locator(css_selector)
        """
        try:
            loc = json.loads(target) if isinstance(target, str) else target
        except (json.JSONDecodeError, TypeError):
            # Fallback: treat as raw CSS selector
            if target:
                return f'page.locator("{self._escape(target)}")'
            return 'page.locator("body")'

        if not isinstance(loc, dict):
            return f'page.locator("{self._escape(str(target))}")'

        method = loc.get("method", "css")

        if method == "collection_item":
            collection = loc.get("collection", {"method": "css", "value": "body"})
            item = loc.get("item", {"method": "css", "value": "body"})
            ordinal = str(loc.get("ordinal") or "first")
            collection_loc = self._build_locator(json.dumps(collection) if isinstance(collection, dict) else str(collection))
            scoped_collection = self._apply_ordinal_to_locator(collection_loc, ordinal)
            item_loc = self._build_locator(json.dumps(item) if isinstance(item, dict) else str(item))
            if item_loc.startswith("page."):
                return f'{scoped_collection}{item_loc[len("page"):]}'
            return f'{scoped_collection}.locator("{self._escape(str(item))}")'

        if method == "role":
            role = loc.get("role", "button")
            name = self._escape(loc.get("name", ""))
            if name:
                return f'page.get_by_role("{role}", name="{name}", exact=True)'
            return f'page.get_by_role("{role}")'

        if method == "testid":
            val = self._escape(loc.get("value", ""))
            return f'page.get_by_test_id("{val}")'

        if method == "label":
            val = self._escape(loc.get("value", ""))
            return f'page.get_by_label("{val}", exact=True)'

        if method == "placeholder":
            val = self._escape(loc.get("value", ""))
            return f'page.get_by_placeholder("{val}", exact=True)'

        if method == "alt":
            val = self._escape(loc.get("value", ""))
            return f'page.get_by_alt_text("{val}", exact=True)'

        if method == "title":
            val = self._escape(loc.get("value", ""))
            return f'page.get_by_title("{val}", exact=True)'

        if method == "text":
            val = self._escape(loc.get("value", ""))
            return f'page.get_by_text("{val}", exact=True)'

        if method == "nested":
            # parent >> child locator chaining
            parent = loc.get("parent", {})
            child = loc.get("child", {})
            parent_loc = self._build_locator(json.dumps(parent) if isinstance(parent, dict) else str(parent))
            child_loc = self._build_locator(json.dumps(child) if isinstance(child, dict) else str(child))
            # Chain the child query directly on the parent locator.
            # Examples:
            #   page.locator("button")        -> parent.locator("button")
            #   page.get_by_role("link")      -> parent.get_by_role("link")
            # Wrapping everything in .locator(...) breaks non-CSS child locators.
            if child_loc.startswith("page."):
                return f'{parent_loc}{child_loc[len("page"):]}'
            return f'{parent_loc}.locator("{self._escape(str(child))}")'

        if method == "nth":
            base = loc.get("locator", loc.get("base", {"method": "css", "value": "body"}))
            base_loc = self._build_locator(json.dumps(base) if isinstance(base, dict) else str(base))
            try:
                index = max(int(loc.get("index", 0)), 0)
            except Exception:
                index = 0
            return f"{base_loc}.nth({index})"

        # css (default)
        val = self._escape(loc.get("value", "body"))
        return f'page.locator("{val}")'

    def _build_locator_for_page(self, target: str, page_var: str) -> str:
        locator = PlaywrightGenerator._build_locator(target)
        if page_var == "page":
            return locator
        if locator.startswith("page."):
            return f"{page_var}.{locator[len('page.'):]}"
        return locator

    @staticmethod
    def _enhance_locator_for_robustness(locator_expr: str, scope_var: str, action: str = "") -> str:
        """Enhance locators to use abstract/semantic strategies instead of concrete values.
        
        This method detects when a locator contains specific business data values
        (like names, IDs, phone numbers) and replaces them with abstract semantic
        lookups that remain valid even when the data changes.
        """
        import re as _re
        
        text_match = _re.search(r'get_by_text\(["\']([^"\']+)["\']\)', locator_expr)
        if not text_match:
            return locator_expr
        
        search_text = text_match.group(1)
        
        if _PlaywrightGenerator._is_concrete_data_value(search_text):
            if action == "extract_text":
                abstract_js = _PlaywrightGenerator._build_abstract_extract_js(search_text, scope_var)
                return f"await {scope_var}.evaluate({abstract_js})"
            else:
                abstract_locator = _PlaywrightGenerator._build_abstract_click_locator(search_text, scope_var)
                return abstract_locator
        
        click_enhanced = f"""{scope_var}.locator('div:not([class*="fake"]):not([class*="hidden"]):not([style*="display: none"]).filter(has_text="{search_text}").first"""
        return click_enhanced

    @staticmethod
    def _is_concrete_data_value(text: str) -> bool:
        """Detect if text looks like concrete business data rather than a label.
        
        Returns True for:
        - Chinese names (张伟, 李四, etc.)
        - Employee IDs (WX1383818, EMP001, etc.)
        - Phone numbers
        - Email addresses
        - Any text that's likely to change between sessions
        
        Returns False for:
        - Field labels (直接主管, 姓名, 电话, etc.)
        - UI text (提交, 取消, 确认, etc.)
        - Static labels
        """
        import re as _re
        
        patterns = [
            r'^[\u4e00-\u9fa5]{2,4}$',  # Chinese names (2-4 chars)
            r'^[A-Z]{2}\d{6,}$',  # Employee IDs like WX1383818
            r'^1[3-9]\d{9}$',  # Phone numbers
            r'^[\w.-]+@[\w.-]+\.\w+$',  # Emails
            r'^\d{4}-\d{2}-\d{2}',  # Dates
            r'^[A-Za-z0-9]{10,}$',  # Long alphanumeric codes
        ]
        
        label_indicators = [
            '直接主管', '姓名', '电话', '邮箱', '地址', '部门',
            '职位', '状态', '类型', '编号', '日期', '时间',
            '提交', '取消', '确认', '保存', '删除', '编辑',
            '查询', '搜索', '重置', '返回', '首页', '登录',
            '注册', '下一步', '上一步', '完成',
        ]
        
        is_label = any(indicator in text for indicator in label_indicators)
        if is_label:
            return False
        
        for pattern in patterns:
            if _re.match(pattern, text):
                return True
        
        return False

    @staticmethod
    def _build_abstract_extract_js(concrete_value: str, scope_var: str) -> str:
        """Build JS code that extracts value using semantic/structural lookup.
        
        Instead of using get_by_text("张伟"), this generates code that:
        1. Finds the field by its label or structural position
        2. Extracts whatever value is currently there
        """
        escaped = concrete_value.replace('\\', '\\\\').replace("'", "\\'").replace('"', '\\"')
        
        js_code = f'''() => {{
    const targetValue = '{escaped}';
    
    const formContainers = document.querySelectorAll(
        '[class*="form-item"], [class*="form-group"], [class*="field"], ' +
        '[data-prop], fieldset'
    );
    
    for (const container of formContainers) {{
        const valueEl = container.querySelector(
            '[class*="display-only__content"], [class*="__content"], ' +
            '[class*="value"], input:not([type="hidden"]), textarea, select'
        );
        
        if (!valueEl) continue;
        
        const currentValue = (valueEl.value || valueEl.textContent || '').trim();
        if (currentValue) return currentValue;
    }}
    
    const allElements = document.querySelectorAll('*');
    let bestMatch = null;
    let bestScore = 0;
    
    for (const el of allElements) {{
        if (el.childElementCount > 0) continue;
        const text = (el.textContent || '').trim();
        if (!text) continue;
        
        const parent = el.closest('[class*="content"], [class*="control"], [class*="value"]');
        if (parent && text.length > 0 && text.length < 200) {{
            if (!bestMatch || text.length < bestScore) {{
                bestMatch = text;
                bestScore = text.length;
            }}
        }}
    }}
    
    return bestMatch || '';
}}'''
        
        return js_code

    @staticmethod
    def _build_abstract_click_locator(concrete_value: str, scope_var: str) -> str:
        """Build an abstract locator for clicking that doesn't rely on specific values."""
        import re as _re
        
        escaped = concrete_value.replace('"', '\\"')
        
        return f"""{scope_var}.locator('button, [role="button"], [type="submit"], [type="button"], a[href]').filter(has_text=/{escaped}/).first"""

    def _build_adaptive_locator_for_step(self, step: Dict[str, Any], page_var: str) -> Optional[str]:
        ordinal = step.get("ordinal")
        if not ordinal:
            return None

        collection_hint = step.get("collection_hint") or {}
        item_hint = step.get("item_hint") or {}
        collection_locator = (collection_hint.get("container_hint") or {}).get("locator")
        item_locator = item_hint.get("locator")
        adaptive_target: Optional[Dict[str, Any]] = None
        if collection_locator and item_locator:
            adaptive_target = {
                "method": "collection_item",
                "collection": collection_locator,
                "item": item_locator,
                "ordinal": str(ordinal),
            }
        elif item_locator:
            adaptive_target = item_locator
        elif item_hint.get("role"):
            adaptive_target = {"method": "role", "role": item_hint["role"]}

        if not adaptive_target:
            return None

        locator = self._build_locator_for_page(json.dumps(adaptive_target), page_var)
        if adaptive_target.get("method") == "collection_item":
            return locator
        return self._apply_ordinal_to_locator(locator, str(ordinal))

    @staticmethod
    def _apply_ordinal_to_locator(locator: str, ordinal: str) -> str:
        normalized = (ordinal or "first").strip().lower()
        if normalized == "first":
            return f"{locator}.first"
        if normalized == "last":
            return f"{locator}.last"
        try:
            index = max(int(normalized) - 1, 0)
        except Exception:
            index = 0
        return f"{locator}.nth({index})"

    @staticmethod
    def _escape(s: str) -> str:
        """Escape and normalize a string for embedding in Python source code."""
        # Collapse all whitespace (newlines, tabs, multiple spaces) into single space
        import re
        s = re.sub(r'\s+', ' ', s).strip()
        return s.replace('\\', '\\\\').replace('"', '\\"')

    def _maybe_parameterize(self, value: str, params: Dict[str, Any]) -> str:
        """Check if value should be a parameter."""
        for param_name, param_info in params.items():
            if param_info.get("original_value") == value:
                if param_info.get("sensitive"):
                    # No default value for sensitive params
                    return f"kwargs['{param_name}']"
                return f"kwargs.get('{param_name}', '{value}')"
        safe = value.replace("'", "\\'")
        return f"'{safe}'"

    def _build_fill_value_expression(self, step: Dict[str, Any], value: str, params: Dict[str, Any]) -> str:
        fill_mappings = step.get("fill_mappings") or []
        if fill_mappings:
            mapping = fill_mappings[0]
            result_key = str(mapping.get("source_result_key") or "")
            field_name = str(mapping.get("param_name") or mapping.get("label") or "")
            if result_key and field_name:
                escaped_result_key = result_key.replace("\\", "\\\\").replace("'", "\\'")
                escaped_field_name = field_name.replace("\\", "\\\\").replace("'", "\\'")
                escaped_default = str(value or "").replace("\\", "\\\\").replace("'", "\\'")
                return (
                    f"_lookup_extracted_value(_results, '{escaped_result_key}', "
                    f"'{escaped_field_name}', '{escaped_default}')"
                )
        return self._maybe_parameterize(value, params)

    def _build_input_files_value(self, step: Dict[str, Any], value: str, params: Dict[str, Any]) -> str:
        signals = step.get("signals")
        files = None
        if isinstance(signals, dict):
            payload = signals.get("set_input_files")
            if isinstance(payload, dict) and isinstance(payload.get("files"), list):
                files = [str(item) for item in payload.get("files") if str(item)]

        if files and len(files) > 1:
            escaped = [item.replace("\\", "\\\\").replace("'", "\\'") for item in files]
            return "[" + ", ".join(f"'{item}'" for item in escaped) + "]"

        effective_value = files[0] if files else value
        return self._maybe_parameterize(str(effective_value or ""), params)

    @staticmethod
    def _sync_to_async(code: str) -> str:
        """Convert Playwright sync API code to async by adding await."""
        import re as _re
        lines = code.split("\n")
        result = []
        for line in lines:
            stripped = line.lstrip()
            indent = line[:len(line) - len(stripped)]
            if stripped and not stripped.startswith("#") and not stripped.startswith("def "):
                if stripped.startswith("page.") or _re.match(r'^(\w[\w\s,]*=\s*)(await\s+)?(page\..+)$', stripped):
                    if not stripped.startswith("await "):
                        # Check for assignment: var = page.xxx or var = await page.xxx
                        assign_match = _re.match(r'^(\w[\w\s,]*=\s*)(await\s+)?(page\..+)$', stripped)
                        if assign_match:
                            last_call = _re.search(r'\.(\w+)\([^)]*\)\s*$', assign_match.group(3))
                            if last_call and last_call.group(1) in PlaywrightGenerator._LOCATOR_BUILDER_METHODS:
                                result.append(f"{indent}{assign_match.group(1)}{assign_match.group(3)}")
                                continue
                            # If already has await, keep as-is; otherwise add await
                            if assign_match.group(2):  # already has await
                                result.append(line)
                            else:
                                result.append(f"{indent}{assign_match.group(1)}await {assign_match.group(3)}")
                            continue
                        result.append(f"{indent}await {stripped}")
                        continue
            result.append(line)
        return "\n".join(result)

    # Methods whose return value is not data (actions, not queries)
    _ACTION_METHODS = frozenset({
        'click', 'dblclick', 'fill', 'press', 'type', 'check', 'uncheck',
        'select_option', 'set_input_files', 'hover', 'focus', 'blur',
        'dispatch_event', 'scroll_into_view_if_needed',
        'goto', 'go_back', 'go_forward', 'reload',
        'wait_for_timeout', 'wait_for_load_state', 'wait_for_selector',
        'wait_for_url', 'wait_for_event', 'wait_for_function',
        'bring_to_front', 'close', 'set_content',
        'set_default_timeout', 'set_default_navigation_timeout',
        'add_init_script', 'expose_function', 'route', 'unroute',
    })

    _LOCATOR_BUILDER_METHODS = frozenset({
        'locator', 'frame_locator',
        'get_by_role', 'get_by_text', 'get_by_label', 'get_by_placeholder',
        'get_by_alt_text', 'get_by_title', 'get_by_test_id',
        'nth', 'first', 'last', 'filter',
    })

    _ASSIGN_RE = re.compile(r'^(\w+)\s*=\s*(?:await\s+)?page\.')
    _GENERIC_ASSIGN_RE = re.compile(r'^(?P<var>\w+)\s*=\s*(?:await\s+)?(?P<rhs>.+)$')
    _RESULT_CAPTURE_RE = re.compile(r'^_results\["[^"]+"\]\s*=\s*(?P<var>\w+)\s*$')

    @classmethod
    def _inject_result_capture(cls, code: str) -> str:
        """After data-extraction assignments, inject _results[var] = var."""
        lines = code.split('\n')
        result = []
        for line in lines:
            result.append(line)
            stripped = line.strip()
            m = cls._ASSIGN_RE.match(stripped)
            if not m:
                continue
            var_name = m.group(1)
            # Find the last method call in the line
            last_call = re.search(r'\.(\w+)\([^)]*\)\s*$', stripped)
            if last_call and last_call.group(1) in cls._LOCATOR_BUILDER_METHODS:
                continue
            if last_call and last_call.group(1) in cls._ACTION_METHODS:
                continue
            indent = line[:len(line) - len(line.lstrip())]
            result.append(f'{indent}_results["{var_name}"] = {var_name}')
        return '\n'.join(result)

    @classmethod
    def _strip_locator_result_capture(cls, code: str) -> str:
        """Drop `_results[...] = var` lines when `var` still points to a locator builder."""
        lines = code.split('\n')
        result = []
        locator_vars = set()
        for line in lines:
            stripped = line.strip()
            assign_match = cls._GENERIC_ASSIGN_RE.match(stripped)
            if assign_match:
                var_name = assign_match.group("var")
                rhs = assign_match.group("rhs")
                last_call = re.search(r'\.(\w+)\([^)]*\)\s*$', rhs)
                if last_call and last_call.group(1) in cls._LOCATOR_BUILDER_METHODS:
                    locator_vars.add(var_name)
                else:
                    locator_vars.discard(var_name)

            capture_match = cls._RESULT_CAPTURE_RE.match(stripped)
            if capture_match and capture_match.group("var") in locator_vars:
                continue

            result.append(line)
        return '\n'.join(result)
