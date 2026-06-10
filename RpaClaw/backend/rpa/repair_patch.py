from __future__ import annotations

from datetime import datetime
from typing import Any

from .repair_models import RepairProposal


class RepairConfirmationRequired(ValueError):
    pass


class UnsupportedRepairPatch(ValueError):
    pass


def _append_repair_metadata(trace: Any, proposal: RepairProposal, *, confirmed: bool) -> None:
    validation = dict(getattr(trace, "validation", None) or {})
    history = list(validation.get("repair_history") or [])
    applied_at = datetime.now().isoformat()
    entry = {
        "proposal_id": proposal.proposal_id,
        "context_id": proposal.context_id,
        "patch_type": proposal.patch_type,
        "failure_category": proposal.failure_category,
        "risk_level": proposal.risk_level,
        "confidence": proposal.confidence,
        "confirmed": confirmed,
        "applied_at": applied_at,
        "reason_summary": proposal.reason_summary,
    }
    history.append(entry)
    validation["repair_history"] = history
    validation["last_repair"] = entry
    trace.validation = validation


async def apply_repair_patch(
    manager: Any,
    session_id: str,
    proposal: RepairProposal,
    *,
    confirmed: bool = False,
) -> Any:
    if proposal.requires_user_confirmation and not confirmed:
        raise RepairConfirmationRequired("User confirmation is required before applying this repair proposal")

    if proposal.patch_type in {"select_existing_locator_candidate", "replace_locator"}:
        candidate_index = proposal.patch.candidate_index
        if candidate_index is None:
            raise UnsupportedRepairPatch("Locator repair proposal is missing candidate_index")
        trace = await manager.select_trace_locator_candidate(
            session_id,
            proposal.failed_trace_id,
            candidate_index,
        )
        _append_repair_metadata(trace, proposal, confirmed=confirmed)
        proposal.status = "applied"
        proposal.applied_at = datetime.now()
        return trace

    raise UnsupportedRepairPatch(f"Unsupported repair patch type: {proposal.patch_type}")
