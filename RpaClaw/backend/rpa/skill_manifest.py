from copy import deepcopy
from datetime import date, datetime
from typing import Any


def _to_json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _to_json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_json_safe(item) for item in value]
    return deepcopy(value)


def build_manifest(
    skill_name: str,
    description: str,
    params: dict[str, Any],
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_steps = []
    for step in steps:
        normalized_step = _to_json_safe(step)
        if isinstance(normalized_step, dict) and not normalized_step.get("type"):
            normalized_step["type"] = (
                "agent" if normalized_step.get("source") == "ai" else "script"
            )
        normalized_steps.append(normalized_step)

    return {
        "version": 2,
        "name": skill_name,
        "description": description,
        "goal": description,
        "params": _to_json_safe(params),
        "steps": normalized_steps,
    }
