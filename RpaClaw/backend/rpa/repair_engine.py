from __future__ import annotations

from typing import Any, Dict, List, Optional

from .repair_models import FailureContext, IntentAnalysis, RepairPatch, RepairProposal
from .repair_safety import highest_risk_level, side_effect_profiles_for_trace
from .trace_locator_utils import locator_has_unstable_identity


def _candidate_locator(candidate: Dict[str, Any]) -> Dict[str, Any]:
    locator = candidate.get("locator")
    return dict(locator) if isinstance(locator, dict) else {}


def _selected_candidate(trace: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for candidate in trace.get("locator_candidates") or []:
        if isinstance(candidate, dict) and candidate.get("selected"):
            return candidate
    return None


def _candidate_score(candidate: Dict[str, Any]) -> float:
    try:
        return float(candidate.get("score", 999))
    except (TypeError, ValueError):
        return 999.0


def categorize_failure(context: FailureContext) -> str:
    text = " ".join([context.error.message, *context.logs]).lower()
    if "strict mode violation" in text:
        return "locator-strict-violation"
    if "locator" in text and ("timeout" in text or "not found" in text or "waiting for" in text):
        return "locator-not-found"
    if "frame" in text or "iframe" in text:
        return "stale-frame-or-tab"
    if "navigation" in text or "net::err" in text:
        return "missing-navigation-wait"
    if "timeout" in text:
        return "page-slow"
    return "execution-failure"


class RepairEngine:
    def generate_proposals(
        self,
        context: FailureContext,
        intent: IntentAnalysis,
    ) -> List[RepairProposal]:
        proposals: List[RepairProposal] = []
        trace = context.failed_trace or {}
        failed_trace_id = context.failed_trace_id
        if not failed_trace_id:
            return proposals

        failure_category = categorize_failure(context)
        proposals.extend(self._locator_candidate_proposals(context, intent, trace, failure_category))
        return proposals

    def _locator_candidate_proposals(
        self,
        context: FailureContext,
        intent: IntentAnalysis,
        trace: Dict[str, Any],
        failure_category: str,
    ) -> List[RepairProposal]:
        if failure_category not in {"locator-not-found", "locator-strict-violation", "page-slow", "execution-failure"}:
            return []
        failed_trace_id = context.failed_trace_id
        if not failed_trace_id:
            return []

        current = _selected_candidate(trace)
        candidates = []
        for index, candidate in enumerate(trace.get("locator_candidates") or []):
            if not isinstance(candidate, dict) or candidate.get("selected"):
                continue
            locator = _candidate_locator(candidate)
            if locator and locator_has_unstable_identity(locator):
                continue
            entry = dict(candidate)
            entry["original_index"] = index
            candidates.append(entry)
        candidates.sort(key=lambda item: (
            0 if item.get("strict_match_count") == 1 else 1,
            _candidate_score(item),
        ))

        side_effects = side_effect_profiles_for_trace(trace)
        risk_level = highest_risk_level(side_effects)
        confidence = "medium" if any(candidate.get("strict_match_count") == 1 for candidate in candidates) else "low"
        proposals: List[RepairProposal] = []
        for candidate in candidates:
            original_index = int(candidate.get("original_index", 0))
            patch = RepairPatch(
                patch_type="select_existing_locator_candidate",
                trace_id=failed_trace_id,
                candidate_index=original_index,
                before={
                    "selected_candidate_index": trace.get("validation", {}).get("selected_candidate_index"),
                    "locator": _candidate_locator(current or {}),
                    "candidate": current or {},
                },
                after={
                    "selected_candidate_index": original_index,
                    "locator": _candidate_locator(candidate),
                    "candidate": candidate,
                },
                diff=[
                    {
                        "field": "locator_candidates.selected",
                        "before": trace.get("validation", {}).get("selected_candidate_index"),
                        "after": original_index,
                    },
                    {
                        "field": "validation.selected_candidate_kind",
                        "before": trace.get("validation", {}).get("selected_candidate_kind"),
                        "after": candidate.get("kind", ""),
                    },
                ],
                metadata={
                    "intent_summary": intent.intent_summary,
                    "source": "accepted_trace.locator_candidates",
                },
            )
            proposals.append(RepairProposal(
                context_id=context.context_id,
                failed_trace_id=failed_trace_id,
                failure_category=failure_category,
                reason_summary=(
                    "The failed trace has an unselected locator candidate. "
                    "Applying this proposal switches only that trace's selected locator "
                    "and keeps the accepted trace as the source of truth."
                ),
                repair_plan=[
                    "Select the existing accepted-trace locator candidate.",
                    "Record repair metadata on the trace validation payload.",
                    "Regenerate skill.py and run a full session replay.",
                ],
                patch_type="select_existing_locator_candidate",
                patch=patch,
                side_effects=side_effects,
                risk_level=risk_level,
                confidence=confidence,
                requires_user_confirmation=risk_level in {"high", "unknown"},
            ))
        return proposals
