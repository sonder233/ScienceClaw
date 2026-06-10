from __future__ import annotations

from typing import Any, Dict, Iterable, List

from .repair_models import RepairRiskLevel, SideEffectProfile


HIGH_RISK_TOKENS = (
    "submit",
    "save",
    "delete",
    "remove",
    "pay",
    "payment",
    "transfer",
    "upload",
    "send",
    "approve",
    "publish",
    "export",
    "download",
    "提交",
    "保存",
    "删除",
    "移除",
    "付款",
    "支付",
    "转账",
    "上传",
    "发送",
    "审批",
    "发布",
    "导出",
    "下载",
)
DELETE_TOKENS = ("delete", "remove", "删除", "移除")
PAYMENT_TOKENS = ("pay", "payment", "transfer", "付款", "支付", "转账")
UPLOAD_TOKENS = ("upload", "上传")
MESSAGE_TOKENS = ("send", "发送")
PERMISSION_TOKENS = ("permission", "role", "授权", "权限")

RISK_RANK = {"low": 0, "medium": 1, "high": 2, "unknown": 3}


def _string_values(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _string_values(item)
        return
    if isinstance(value, list):
        for item in value:
            yield from _string_values(item)
        return
    if isinstance(value, str):
        yield value


def _trace_text(trace: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in ("action", "description", "user_instruction", "value"):
        parts.append(str(trace.get(key) or ""))
    for candidate in trace.get("locator_candidates") or []:
        parts.extend(_string_values(candidate))
    return " ".join(parts).lower()


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token.lower() in text for token in tokens)


def highest_risk_level(profiles: List[SideEffectProfile]) -> RepairRiskLevel:
    if not profiles:
        return "low"
    return max((profile.risk_level for profile in profiles), key=lambda item: RISK_RANK.get(item, 3))  # type: ignore[return-value]


def side_effect_profiles_for_trace(trace: Dict[str, Any]) -> List[SideEffectProfile]:
    signals = trace.get("signals") if isinstance(trace.get("signals"), dict) else {}
    action = str(trace.get("action") or "").lower()
    text = _trace_text(trace)
    profiles: List[SideEffectProfile] = []

    if isinstance(signals.get("download"), dict) or _contains_any(text, ("download", "export", "下载", "导出")):
        profiles.append(SideEffectProfile(
            type="download",
            target="file",
            scope="local_file",
            reversibility="unknown",
            persistence="persistent",
            data_impact="export",
            confidence="medium",
            evidence=["download/export signal or visible text"],
            risk_level="high",
        ))
    if isinstance(signals.get("popup"), dict):
        profiles.append(SideEffectProfile(
            type="popup",
            target="browser tab or window",
            scope="current_page",
            reversibility="reversible",
            persistence="session",
            data_impact="none",
            confidence="medium",
            evidence=["trace signals.popup"],
            risk_level="medium",
        ))
    if isinstance(signals.get("navigation"), dict) or action == "navigate":
        profiles.append(SideEffectProfile(
            type="navigation",
            target="page",
            scope="current_page",
            reversibility="reversible",
            persistence="session",
            data_impact="none",
            confidence="medium",
            evidence=["navigation action or signal"],
            risk_level="medium" if action != "navigate" else "low",
        ))
    if "set_input_files" in text or _contains_any(text, UPLOAD_TOKENS):
        profiles.append(SideEffectProfile(
            type="upload",
            target="file input",
            scope="external_service",
            reversibility="unknown",
            persistence="persistent",
            data_impact="create",
            confidence="medium",
            evidence=["upload action or visible text"],
            risk_level="high",
        ))
    if _contains_any(text, DELETE_TOKENS):
        profiles.append(SideEffectProfile(
            type="delete",
            target="remote record",
            scope="remote_record",
            reversibility="unknown",
            persistence="persistent",
            data_impact="delete",
            confidence="medium",
            evidence=["delete-like visible text"],
            risk_level="high",
        ))
    if _contains_any(text, PAYMENT_TOKENS):
        profiles.append(SideEffectProfile(
            type="payment",
            target="external payment or transfer",
            scope="external_service",
            reversibility="unknown",
            persistence="persistent",
            data_impact="update",
            confidence="medium",
            evidence=["payment-like visible text"],
            risk_level="high",
        ))
    if _contains_any(text, MESSAGE_TOKENS):
        profiles.append(SideEffectProfile(
            type="message_send",
            target="external message",
            scope="external_service",
            reversibility="unknown",
            persistence="persistent",
            data_impact="create",
            confidence="medium",
            evidence=["send-like visible text"],
            risk_level="high",
        ))
    if _contains_any(text, PERMISSION_TOKENS):
        profiles.append(SideEffectProfile(
            type="permission_change",
            target="account permissions",
            scope="account_data",
            reversibility="partially_reversible",
            persistence="persistent",
            data_impact="update",
            confidence="low",
            evidence=["permission-like visible text"],
            risk_level="high",
        ))
    if action == "fill":
        profiles.append(SideEffectProfile(
            type="data_mutation",
            target="form field",
            scope="current_page",
            reversibility="reversible",
            persistence="transient",
            data_impact="update",
            confidence="medium",
            evidence=["fill action"],
            risk_level="medium",
        ))

    if not profiles and action in {"extract_text", "extract", "read"}:
        profiles.append(SideEffectProfile(
            type="navigation",
            target="page",
            scope="current_page",
            reversibility="reversible",
            persistence="transient",
            data_impact="read",
            confidence="medium",
            evidence=["read-only extraction action"],
            risk_level="low",
        ))
    if not profiles and action == "click":
        risk = "high" if _contains_any(text, HIGH_RISK_TOKENS) else "unknown"
        profiles.append(SideEffectProfile(
            type="unknown",
            target="clicked element",
            scope="current_page",
            reversibility="unknown",
            persistence="unknown",
            data_impact="update" if risk == "high" else "none",
            confidence="low",
            evidence=["click action without enough side-effect evidence"],
            risk_level=risk,  # type: ignore[arg-type]
        ))
    if not profiles:
        profiles.append(SideEffectProfile(
            type="unknown",
            target="failed step",
            scope="current_page",
            reversibility="unknown",
            persistence="unknown",
            data_impact="none",
            confidence="low",
            evidence=["side effect could not be determined"],
            risk_level="unknown",
        ))
    return profiles
