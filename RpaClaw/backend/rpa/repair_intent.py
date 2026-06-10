from __future__ import annotations

from typing import Any, Dict, List

from .repair_models import FailureContext, IntentAnalysis


READ_TOKENS = ("read", "extract", "get", "query", "view", "读取", "获取", "提取", "查询", "查看")
DOWNLOAD_TOKENS = ("download", "export", "下载", "导出")
SUBMIT_TOKENS = ("submit", "save", "send", "approve", "提交", "保存", "发送", "审批", "发布")


def _trace_text(trace: Dict[str, Any]) -> str:
    parts = [
        trace.get("user_instruction"),
        trace.get("description"),
        trace.get("action"),
    ]
    return " ".join(str(part or "") for part in parts).lower()


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token.lower() in text for token in tokens)


def analyze_failure_intent(context: FailureContext) -> IntentAnalysis:
    trace = context.failed_trace or {}
    text = _trace_text(trace)
    action = str(trace.get("action") or trace.get("trace_type") or "")
    description = str(trace.get("user_instruction") or trace.get("description") or action or "failed RPA step")

    allowed_side_effects: List[str] = ["navigation"]
    if _contains_any(text, READ_TOKENS):
        allowed_side_effects.extend(["read"])
    if _contains_any(text, DOWNLOAD_TOKENS):
        allowed_side_effects.extend(["download"])
    if _contains_any(text, SUBMIT_TOKENS):
        allowed_side_effects.extend(["form_submit", "data_mutation"])

    disallowed = [
        "delete",
        "payment",
        "upload",
        "message_send",
        "permission_change",
    ]
    if "download" not in allowed_side_effects:
        disallowed.append("download")
    if "form_submit" not in allowed_side_effects:
        disallowed.append("form_submit")
    if "data_mutation" not in allowed_side_effects:
        disallowed.append("data_mutation")

    constraints = [
        "Only repair the failed trace or its draft configuration.",
        "Do not invent selectors without trace or current-page evidence.",
        "Do not execute free-form Playwright code on the live page.",
        "Run a full session replay before allowing save or publish.",
    ]
    if context.failed_trace_id:
        constraints.append(f"Patch target is limited to trace {context.failed_trace_id}.")

    return IntentAnalysis(
        context_id=context.context_id,
        intent_summary=description,
        target_semantics={
            "action": action,
            "object": trace.get("description") or trace.get("user_instruction") or "",
            "selection_rule": "derive from accepted trace evidence and runtime parameters",
        },
        expected_outcome={
            "page_state": "matches the original step outcome or reaches an equivalent replay state",
            "output_keys": [trace.get("output_key")] if trace.get("output_key") else [],
        },
        allowed_side_effects=sorted(set(allowed_side_effects)),
        disallowed_side_effects=sorted(set(disallowed)),
        repair_constraints=constraints,
        confidence="medium" if description else "low",
        requires_user_confirmation=False,
    )
