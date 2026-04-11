from __future__ import annotations

import re
from typing import Any, Dict, Optional


SCRIPT_STEP = "script_step"
AGENT_STEP = "agent_step"

_EN_IF_ELSE_RE = re.compile(r"\bif\b[\s\S]{0,200}\belse\b", re.IGNORECASE)
_ZH_IF_ELSE_RE = re.compile(r"如果[\s\S]{0,200}(否则|不然)")


def classify_candidate_step(
    prompt: str,
    structured_intent: Optional[Dict[str, Any]] = None,
    code: Optional[str] = None,
) -> str:
    """Classify candidate as script_step or agent_step with script-first bias."""
    normalized_prompt = (prompt or "").strip().lower()
    normalized_code = (code or "").strip().lower()
    action = str((structured_intent or {}).get("action", "")).strip().lower()

    if _contains_branching_signal(normalized_prompt) or _contains_branching_signal(normalized_code):
        return AGENT_STEP

    if action in {"navigate", "click", "fill", "extract_text", "press"}:
        return SCRIPT_STEP

    return SCRIPT_STEP


def _contains_branching_signal(text: str) -> bool:
    if not text:
        return False
    return bool(_EN_IF_ELSE_RE.search(text) or _ZH_IF_ELSE_RE.search(text))
