from __future__ import annotations

from typing import Any, Dict, List, Optional

from .repair_models import (
    FailureContext,
    RepairAnalyzeRequest,
    RepairError,
    RepairPageState,
    RepairRestoreCheckRequest,
    RepairRestoreCheckResult,
)
from .repair_safety import highest_risk_level, side_effect_profiles_for_trace
from .trace_ordering import order_traces_by_recording_time


def _model_dump_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_result_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(payload.get("result"), dict):
        return dict(payload["result"])
    return dict(payload or {})


def _extract_logs(request_logs: List[str], test_result: Dict[str, Any]) -> List[str]:
    if request_logs:
        return [str(item) for item in request_logs]
    logs = test_result.get("logs")
    if isinstance(logs, list):
        return [str(item) for item in logs]
    return []


def _find_failed_trace(session: Any, failed_trace_index: Optional[int], failed_trace_id: Optional[str]) -> tuple[Optional[int], Optional[Any]]:
    traces = order_traces_by_recording_time(list(getattr(session, "traces", None) or []))
    if failed_trace_id:
        for index, trace in enumerate(traces):
            if getattr(trace, "trace_id", None) == failed_trace_id:
                return index, trace
    if failed_trace_index is not None and 0 <= failed_trace_index < len(traces):
        return failed_trace_index, traces[failed_trace_index]
    return failed_trace_index, None


def _request_current_page(request_page: Dict[str, Any], failed_trace: Any) -> RepairPageState:
    if isinstance(request_page, dict) and request_page:
        return RepairPageState(
            url=str(request_page.get("url") or ""),
            title=str(request_page.get("title") or ""),
            tab_id=str(request_page.get("tab_id") or ""),
            frame_path=list(request_page.get("frame_path") or []),
        )
    before_page = getattr(failed_trace, "before_page", None)
    if before_page is not None:
        return RepairPageState(
            url=str(getattr(before_page, "url", "") or ""),
            title=str(getattr(before_page, "title", "") or ""),
            frame_path=list(getattr(failed_trace, "frame_path", None) or []),
        )
    return RepairPageState()


def build_failure_context(
    session: Any,
    request: RepairAnalyzeRequest,
    *,
    scope: str = "session-test",
) -> FailureContext:
    test_result = dict(request.test_result or {})
    result_payload = _extract_result_payload(test_result)
    failed_trace_index = (
        request.failed_trace_index
        if request.failed_trace_index is not None
        else _coerce_int(test_result.get("failed_trace_index", result_payload.get("failed_trace_index")))
    )
    failed_trace_id = (
        request.failed_trace_id
        or test_result.get("failed_trace_id")
        or result_payload.get("failed_trace_id")
    )
    failed_trace_index, failed_trace = _find_failed_trace(session, failed_trace_index, failed_trace_id)
    if failed_trace is not None:
        failed_trace_id = getattr(failed_trace, "trace_id", None)

    failed_trace_payload = _model_dump_json(failed_trace) if failed_trace is not None else {}
    runtime_results = _model_dump_json(getattr(session, "runtime_results", {})) or {}
    compact_snapshot = {}
    before_page = getattr(failed_trace, "before_page", None)
    if before_page is not None:
        compact_snapshot = dict(getattr(before_page, "snapshot_summary", None) or {})

    error_message = str(result_payload.get("error") or result_payload.get("output") or "")
    error_type = str(result_payload.get("error_type") or "")
    if not error_type and error_message:
        error_type = error_message.split(":", 1)[0][:80]

    return FailureContext(
        scope=scope,  # type: ignore[arg-type]
        session_id=getattr(session, "id", None),
        failed_trace_index=failed_trace_index,
        failed_trace_id=failed_trace_id,
        error=RepairError(
            type=error_type,
            message=error_message,
            raw=result_payload,
        ),
        logs=_extract_logs(request.logs, test_result),
        failed_trace=failed_trace_payload if isinstance(failed_trace_payload, dict) else {},
        previous_results=dict(runtime_results.get("values") or runtime_results or {}),
        current_page=_request_current_page(request.current_page, failed_trace),
        compact_snapshot=compact_snapshot,
        generated_code_excerpt=request.generated_code_excerpt or str(test_result.get("script") or "")[:4000],
        user_intent=request.user_intent_override,
    )


def build_restore_check(
    session: Any,
    request: RepairRestoreCheckRequest,
    *,
    current_page: Optional[Dict[str, Any]] = None,
) -> RepairRestoreCheckResult:
    analyze_request = RepairAnalyzeRequest(
        test_result=request.test_result,
        failed_trace_index=request.failed_trace_index,
        failed_trace_id=request.failed_trace_id,
        current_page=current_page or request.current_page,
    )
    context = build_failure_context(session, analyze_request)
    failed_trace_id = context.failed_trace_id
    failed_trace_index = context.failed_trace_index
    traces = order_traces_by_recording_time(list(getattr(session, "traces", None) or []))
    failed_trace = None
    if failed_trace_id:
        failed_trace = next((trace for trace in traces if trace.trace_id == failed_trace_id), None)
    if failed_trace is None or failed_trace_index is None:
        return RepairRestoreCheckResult(
            mode="not-restorable",
            failed_trace_id=failed_trace_id,
            failed_trace_index=failed_trace_index,
            reason="No failed trace could be mapped from the replay result.",
            evidence=["missing failed_trace_id"],
        )

    before_page = getattr(failed_trace, "before_page", None)
    before_url = str(getattr(before_page, "url", "") or "")
    before_title = str(getattr(before_page, "title", "") or "")
    page_url = str((current_page or request.current_page or {}).get("url") or context.current_page.url or "")
    page_title = str((current_page or request.current_page or {}).get("title") or context.current_page.title or "")
    evidence: List[str] = []
    if before_url and page_url and before_url.rstrip("/") == page_url.rstrip("/"):
        evidence.append("current URL matches failed trace before_page.url")
    if before_title and page_title and before_title == page_title:
        evidence.append("current title matches failed trace before_page.title")

    if evidence:
        return RepairRestoreCheckResult(
            mode="current-page-match",
            failed_trace_id=failed_trace_id,
            failed_trace_index=failed_trace_index,
            can_replay_prefix=False,
            requires_user_confirmation=False,
            risk_level="low",
            reason="Current page appears to match the failed step start state.",
            evidence=evidence,
        )

    prefix_traces = traces[:failed_trace_index]
    blocking = []
    for trace in prefix_traces:
        profiles = side_effect_profiles_for_trace(_model_dump_json(trace) or {})
        if highest_risk_level(profiles) in {"high", "unknown"}:
            blocking.extend(profiles)

    if blocking:
        return RepairRestoreCheckResult(
            mode="user-restore",
            failed_trace_id=failed_trace_id,
            failed_trace_index=failed_trace_index,
            can_replay_prefix=False,
            requires_user_confirmation=True,
            risk_level=highest_risk_level(blocking),
            reason="Prefix replay is not automatic because earlier steps may have high-risk side effects.",
            evidence=["prefix contains high or unknown risk side effects"],
            blocking_side_effects=blocking,
        )

    return RepairRestoreCheckResult(
        mode="replay-prefix",
        failed_trace_id=failed_trace_id,
        failed_trace_index=failed_trace_index,
        can_replay_prefix=True,
        requires_user_confirmation=bool(prefix_traces),
        risk_level="medium" if prefix_traces else "low",
        reason="Failed step start state is not currently visible, but the prefix has no high-risk side effects.",
        evidence=["safe prefix replay is available" if prefix_traces else "failed trace is the first step"],
    )
