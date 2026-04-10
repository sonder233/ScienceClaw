from __future__ import annotations

from typing import Any


def _candidate_kind(candidate: dict[str, Any]) -> str:
    locator_ast = candidate.get("locatorAst") or {}
    kind = locator_ast.get("kind")
    if kind:
        return str(kind)
    return str(candidate.get("kind") or "css")


def to_legacy_locator_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": _candidate_kind(candidate),
        "score": candidate.get("score", 0),
        "strict_match_count": candidate.get("matchCount", 0),
        "visible_match_count": candidate.get("visibleMatchCount", 0),
        "selected": bool(candidate.get("isSelected", False)),
        "locator": candidate.get("selector"),
        "reason": candidate.get("reason", ""),
        "engine": candidate.get("engine", ""),
    }


def _action_target_label(action: dict[str, Any]) -> str:
    snapshot = action.get("snapshot") or {}
    locator = action.get("locator") or {}
    for candidate in (
        snapshot.get("name"),
        snapshot.get("label"),
        snapshot.get("text"),
        snapshot.get("placeholder"),
        snapshot.get("title"),
        locator.get("selector"),
        snapshot.get("tag"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return "目标元素"


def _action_description(action: dict[str, Any]) -> str | None:
    existing = action.get("description")
    if existing:
        return str(existing)

    kind = str(action.get("kind") or action.get("action") or "").strip()
    input_payload = action.get("input") or {}
    snapshot = action.get("snapshot") or {}
    value = str(input_payload.get("value") or input_payload.get("text") or "").strip()
    url = str(input_payload.get("url") or snapshot.get("url") or action.get("url") or "").strip()
    target = _action_target_label(action)

    if kind == "navigate":
        return f"导航到 {url}" if url else "导航页面"
    if kind == "click":
        return f"点击 {target}"
    if kind == "fill":
        return f"输入 {value} 到 {target}" if value else f"输入到 {target}"
    if kind == "press":
        return f"按下 {value} 于 {target}" if value else f"按键于 {target}"
    if kind == "selectOption":
        return f"选择 {value} 于 {target}" if value else f"选择 {target}"
    if kind == "check":
        return f"勾选 {target}"
    if kind == "uncheck":
        return f"取消勾选 {target}"
    if kind == "openPage":
        return f"切换到页面 {action.get('pageAlias') or target}"
    if kind == "closePage":
        return f"关闭页面 {action.get('pageAlias') or target}"
    return None


def to_legacy_step(action: dict[str, Any]) -> dict[str, Any]:
    popup = (action.get("signals") or {}).get("popup") or {}
    input_payload = action.get("input") or {}
    snapshot = action.get("snapshot") or {}
    return {
        "id": action["id"],
        "action": action["kind"],
        "target": (action.get("locator") or {}).get("selector"),
        "frame_path": action.get("framePath", []),
        "locator_candidates": [
            to_legacy_locator_candidate(candidate)
            for candidate in action.get("locatorAlternatives", [])
        ],
        "validation": action.get("validation", {}),
        "signals": action.get("signals", {}),
        "element_snapshot": snapshot,
        "value": input_payload.get("value") or input_payload.get("text"),
        "screenshot_url": snapshot.get("screenshotUrl"),
        "description": _action_description(action),
        "tag": snapshot.get("tag"),
        "label": snapshot.get("label"),
        "url": snapshot.get("url") or action.get("url"),
        "source": "record",
        "tab_id": action.get("pageAlias"),
        "source_tab_id": action.get("pageAlias"),
        "target_tab_id": popup.get("targetPageAlias"),
    }


def to_legacy_tab(page: dict[str, Any], active_tab_id: str | None) -> dict[str, Any]:
    tab_id = page.get("alias") or page.get("id") or ""
    return {
        "tab_id": tab_id,
        "title": page.get("title", ""),
        "url": page.get("url", ""),
        "opener_tab_id": page.get("openerPageAlias"),
        "status": page.get("status", "open"),
        "active": tab_id == active_tab_id,
    }


def to_legacy_session(session: dict[str, Any]) -> dict[str, Any]:
    active_tab_id = session.get("activePageAlias") or session.get("active_tab_id")
    return {
        "id": session["id"],
        "user_id": session.get("userId") or session.get("user_id") or "",
        "status": session.get("status") or session.get("mode") or "recording",
        "steps": [to_legacy_step(action) for action in session.get("actions", [])],
        "sandbox_session_id": session.get("sandboxSessionId") or session.get("sandbox_session_id") or "",
        "paused": bool(session.get("paused", False)),
        "active_tab_id": active_tab_id,
    }


def to_legacy_tabs(session: dict[str, Any]) -> list[dict[str, Any]]:
    active_tab_id = session.get("activePageAlias") or session.get("active_tab_id")
    return [to_legacy_tab(page, active_tab_id) for page in session.get("pages", [])]
