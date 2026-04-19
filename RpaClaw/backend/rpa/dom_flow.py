from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage

from backend.deepagent.engine import get_llm_model
from backend.rpa.dom_data_view import collect_targeted_dom_segments, serialize_structured_data_view_async

INTENT_CLASSIFIER_PROMPT = """你是浏览器 Agent 的意图分类器。

任务：把用户指令分类为以下两类之一，并且只返回 JSON：
- {"intent":"operation"}：点击、输入、跳转、提交、滚动、打开页面、操作控件
- {"intent":"data_extraction"}：总结页面、读取列表、提取信息、汇总内容、查询当前页数据

规则：
1. 只能输出严格 JSON，不要输出 markdown，不要解释。
2. 不确定时返回 {"intent":"operation"}。
"""

INTENT_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_object(text: str) -> Dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        return {}
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        match = INTENT_JSON_RE.search(stripped)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}


async def classify_nl_intent(text: str, model_config: Optional[Dict[str, Any]] = None) -> str:
    try:
        model = get_llm_model(config=model_config, max_tokens_override=40, streaming=False)
        response = await model.ainvoke(
            [
                SystemMessage(content=INTENT_CLASSIFIER_PROMPT),
                HumanMessage(content=f"用户指令：{text}"),
            ]
        )
        content = getattr(response, "content", "")
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    block_text = block.get("text") or block.get("content")
                    if block_text:
                        text_parts.append(str(block_text))
                elif isinstance(block, str):
                    text_parts.append(block)
            content = "".join(text_parts)
        parsed = _parse_json_object(str(content))
        intent = str(parsed.get("intent", "")).strip().lower()
        if intent in {"operation", "data_extraction"}:
            return intent
    except Exception:
        pass
    return "operation"


def build_candidate_block(candidates: List[Dict[str, Any]]) -> str:
    if not candidates:
        return "当前页面 DOM 上下文（用于页面操作）：\n(未发现明显可操作元素)"
    lines = ["当前页面 DOM 上下文（用于页面操作）："]
    for candidate in candidates:
        parts = [f"[{candidate.get('index', '?')}] {candidate.get('tag', 'element')}"]
        if candidate.get("role"):
            parts.append(f"role={candidate['role']}")
        if candidate.get("name"):
            parts.append(f'name="{candidate["name"]}"')
        if candidate.get("placeholder"):
            parts.append(f'placeholder="{candidate["placeholder"]}"')
        if candidate.get("data_testid"):
            parts.append(f'data-testid="{candidate["data_testid"]}"')
        lines.append(" ".join(parts))
    return "\n".join(lines)


async def collect_dom_candidates(page) -> List[Dict[str, Any]]:
    script = """
() => {
  const els = Array.from(document.querySelectorAll('a,button,input,textarea,select,[role="button"],[role="link"]'));
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const results = [];
  let index = 1;
  for (const el of els) {
    if (results.length >= 80) break;
    if (el.hidden) continue;
    const style = window.getComputedStyle(el);
    if (!style || style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 && rect.height <= 0) continue;
    results.push({
      index,
      tag: el.tagName.toLowerCase(),
      role: el.getAttribute('role') || '',
      name: normalize(el.innerText || el.textContent || el.getAttribute('aria-label') || ''),
      placeholder: el.getAttribute('placeholder') || '',
      data_testid: el.getAttribute('data-testid') || '',
    });
    index += 1;
  }
  return JSON.stringify(results);
}
"""
    raw = await page.evaluate(script)
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    return parsed if isinstance(parsed, list) else []


def dom_coverage_score(structured: str, targeted: str) -> float:
    structured_text = (structured or "").strip()
    targeted_text = (targeted or "").strip()
    if not structured_text and not targeted_text:
        return 0.0
    base = min(len(structured_text) / 8000.0, 1.0) * 0.7
    targeted_boost = min(len(targeted_text) / 3000.0, 1.0) * 0.15
    structure_bonus = 0.0
    upper = structured_text.upper()
    for token, bonus in (("TABLE", 0.06), ("FORM", 0.04), ("LIST", 0.04), ("INTERACTIVE", 0.01)):
        if token in upper:
            structure_bonus += bonus
    return round(min(base + targeted_boost + structure_bonus, 1.0), 4)


async def collect_full_dom_html_with_truncation(page, max_chars: int = 120000) -> Tuple[str, bool, int]:
    raw_html = await page.content()
    if len(raw_html) <= max_chars:
        return raw_html, False, 0
    head_chars = max_chars // 2
    tail_chars = max_chars - head_chars
    omitted_chars = max(len(raw_html) - max_chars, 0)
    truncated = raw_html[:head_chars] + f"\n<!-- OMITTED {omitted_chars} CHARS -->\n" + raw_html[-tail_chars:]
    return truncated, True, omitted_chars


def compose_full_dom_block(structured: str, targeted: str, fallback: str) -> str:
    parts = [
        "当前任务偏向数据获取。以下为结构保留页面视图（表格行列、表单 label/name/value、列表层级已对齐）；请优先据此抽取，不要臆造。若含 FALLBACK_RAW_HTML 则仅在结构化视图过短时作为补充。",
        structured or "(结构化视图为空)",
    ]
    if (targeted or "").strip():
        parts.append(targeted.strip())
    if (fallback or "").strip():
        parts.append("<!-- FALLBACK_RAW_HTML -->\n" + fallback.strip())
    return "\n\n".join(parts)


async def build_full_dom_context(page) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
    structured = await serialize_structured_data_view_async(page, maxTotalChars=120000)
    targeted = await collect_targeted_dom_segments(page)
    coverage_score = dom_coverage_score(structured, targeted)

    reasons: List[str] = []
    if len(structured.strip()) < 400:
        reasons.append("structured_too_short")
    if coverage_score < 0.42:
        reasons.append("coverage_low")

    fallback = ""
    dom_truncated = False
    omitted_chars = 0
    if reasons:
        fallback, dom_truncated, omitted_chars = await collect_full_dom_html_with_truncation(page, max_chars=120000)

    dom_context_block = compose_full_dom_block(structured=structured, targeted=targeted, fallback=fallback)
    dom_debug = {
        "dom_mode": "full",
        "dom_strategy_version": "v2-layered",
        "dom_truncated": dom_truncated,
        "omitted_chars": omitted_chars,
        "coverage_score": coverage_score,
        "fallback_trigger_reason": ",".join(reasons),
    }
    return [], dom_context_block, dom_debug


async def get_dom_context_for_agent(page, dom_mode: str) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
    if dom_mode == "full":
        return await build_full_dom_context(page)
    candidates = await collect_dom_candidates(page)
    return candidates, build_candidate_block(candidates), {
        "dom_mode": "compact",
        "coverage_score": 1.0,
        "dom_truncated": False,
        "omitted_chars": 0,
        "fallback_trigger_reason": "",
    }


async def prepare_dom_context(
    page,
    user_text: str,
    model_config: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str, List[Dict[str, Any]], str, Dict[str, Any]]:
    intent = await classify_nl_intent(user_text, model_config=model_config)
    dom_mode = "full" if intent == "data_extraction" else "compact"
    candidates, dom_context_block, dom_debug = await get_dom_context_for_agent(page, dom_mode)
    dom_debug = {**dom_debug, "intent": intent}
    return intent, dom_mode, candidates, dom_context_block, dom_debug
