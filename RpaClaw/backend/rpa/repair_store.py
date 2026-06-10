from __future__ import annotations

from typing import Dict, List, Optional

from .repair_models import FailureContext, IntentAnalysis, RepairProposal


class RPARepairStore:
    def __init__(self) -> None:
        self._contexts: Dict[str, FailureContext] = {}
        self._intents: Dict[str, IntentAnalysis] = {}
        self._proposals: Dict[str, Dict[str, RepairProposal]] = {}

    def replace_session_analysis(
        self,
        session_id: str,
        context: FailureContext,
        intent: IntentAnalysis,
        proposals: List[RepairProposal],
    ) -> None:
        self._contexts[session_id] = context
        self._intents[session_id] = intent
        self._proposals[session_id] = {proposal.proposal_id: proposal for proposal in proposals}

    def get_session_context(self, session_id: str) -> Optional[FailureContext]:
        return self._contexts.get(session_id)

    def get_session_intent(self, session_id: str) -> Optional[IntentAnalysis]:
        return self._intents.get(session_id)

    def list_session_proposals(self, session_id: str) -> List[RepairProposal]:
        return list(self._proposals.get(session_id, {}).values())

    def get_proposal(self, session_id: str, proposal_id: str) -> Optional[RepairProposal]:
        return self._proposals.get(session_id, {}).get(proposal_id)

    def upsert_proposal(self, session_id: str, proposal: RepairProposal) -> None:
        self._proposals.setdefault(session_id, {})[proposal.proposal_id] = proposal


repair_store = RPARepairStore()
