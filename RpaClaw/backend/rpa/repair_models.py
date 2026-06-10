from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


RepairScope = Literal["session-test", "recording", "saved-skill-run"]
RepairConfidence = Literal["low", "medium", "high"]
RepairRiskLevel = Literal["low", "medium", "high", "unknown"]
RepairStatus = Literal["draft", "applied", "validation_pending", "validated", "rejected"]
RepairPatchType = Literal[
    "replace_locator",
    "select_existing_locator_candidate",
    "user_reselect_target_element",
    "add_wait_condition",
    "adjust_navigation_expectation",
    "adjust_tab_or_frame_context",
    "repair_dataflow_ref",
    "preserve_runtime_ai_instruction",
    "replace_failed_recording_trace",
    "mark_requires_user_confirmation",
]
SideEffectType = Literal[
    "navigation",
    "popup",
    "download",
    "upload",
    "form_submit",
    "data_mutation",
    "delete",
    "payment",
    "message_send",
    "permission_change",
    "unknown",
]


class RepairError(BaseModel):
    type: str = ""
    message: str = ""
    raw: Any = None


class RepairPageState(BaseModel):
    url: str = ""
    title: str = ""
    tab_id: str = ""
    frame_path: List[str] = Field(default_factory=list)


class FailureContext(BaseModel):
    context_id: str = Field(default_factory=lambda: f"repairctx-{uuid4().hex}")
    scope: RepairScope = "session-test"
    session_id: Optional[str] = None
    skill_name: Optional[str] = None
    skill_version: Optional[str] = None
    run_id: Optional[str] = None
    failed_trace_index: Optional[int] = None
    failed_trace_id: Optional[str] = None
    error: RepairError = Field(default_factory=RepairError)
    logs: List[str] = Field(default_factory=list)
    failed_trace: Dict[str, Any] = Field(default_factory=dict)
    previous_results: Dict[str, Any] = Field(default_factory=dict)
    current_page: RepairPageState = Field(default_factory=RepairPageState)
    raw_snapshot_ref: Optional[str] = None
    compact_snapshot: Dict[str, Any] = Field(default_factory=dict)
    generated_code_excerpt: str = ""
    side_effects_observed: List[Dict[str, Any]] = Field(default_factory=list)
    user_intent: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


class IntentAnalysis(BaseModel):
    intent_id: str = Field(default_factory=lambda: f"intent-{uuid4().hex}")
    context_id: str
    intent_summary: str = ""
    target_semantics: Dict[str, Any] = Field(default_factory=dict)
    expected_outcome: Dict[str, Any] = Field(default_factory=dict)
    allowed_side_effects: List[str] = Field(default_factory=list)
    disallowed_side_effects: List[str] = Field(default_factory=list)
    repair_constraints: List[str] = Field(default_factory=list)
    confidence: RepairConfidence = "low"
    requires_user_confirmation: bool = False


class SideEffectProfile(BaseModel):
    type: SideEffectType = "unknown"
    target: str = ""
    scope: str = "current_page"
    reversibility: str = "unknown"
    persistence: str = "unknown"
    data_impact: str = "none"
    confidence: RepairConfidence = "low"
    evidence: List[str] = Field(default_factory=list)
    risk_level: RepairRiskLevel = "unknown"


class RepairValidationPlan(BaseModel):
    mode: str = "full-session-replay"
    expected: List[str] = Field(default_factory=lambda: [
        "recompiled skill succeeds",
        "full session replay succeeds",
        "output shape stays compatible",
    ])
    full_replay_required: bool = True


class RepairPatch(BaseModel):
    patch_type: RepairPatchType
    trace_id: str
    candidate_index: Optional[int] = None
    before: Dict[str, Any] = Field(default_factory=dict)
    after: Dict[str, Any] = Field(default_factory=dict)
    diff: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RepairProposal(BaseModel):
    proposal_id: str = Field(default_factory=lambda: f"repair-{uuid4().hex}")
    context_id: str
    failed_trace_id: str
    failure_category: str = "execution-failure"
    reason_summary: str = ""
    repair_plan: List[str] = Field(default_factory=list)
    patch_type: RepairPatchType
    patch: RepairPatch
    side_effects: List[SideEffectProfile] = Field(default_factory=list)
    risk_level: RepairRiskLevel = "unknown"
    confidence: RepairConfidence = "low"
    requires_user_confirmation: bool = True
    validation_plan: RepairValidationPlan = Field(default_factory=RepairValidationPlan)
    status: RepairStatus = "draft"
    created_at: datetime = Field(default_factory=datetime.now)
    applied_at: Optional[datetime] = None


class RepairAnalyzeRequest(BaseModel):
    test_result: Dict[str, Any] = Field(default_factory=dict)
    logs: List[str] = Field(default_factory=list)
    params: Dict[str, Any] = Field(default_factory=dict)
    user_intent_override: str = ""
    failed_trace_index: Optional[int] = None
    failed_trace_id: Optional[str] = None
    generated_code_excerpt: str = ""
    current_page: Dict[str, Any] = Field(default_factory=dict)


class RepairApplyRequest(BaseModel):
    confirmed: bool = False


class RepairValidateRequest(BaseModel):
    params: Dict[str, Any] = Field(default_factory=dict)


class RepairRestoreCheckRequest(BaseModel):
    test_result: Dict[str, Any] = Field(default_factory=dict)
    failed_trace_index: Optional[int] = None
    failed_trace_id: Optional[str] = None
    current_page: Dict[str, Any] = Field(default_factory=dict)


class RepairRestoreCheckResult(BaseModel):
    mode: Literal["current-page-match", "replay-prefix", "user-restore", "not-restorable"]
    failed_trace_id: Optional[str] = None
    failed_trace_index: Optional[int] = None
    can_replay_prefix: bool = False
    requires_user_confirmation: bool = False
    risk_level: RepairRiskLevel = "unknown"
    reason: str = ""
    evidence: List[str] = Field(default_factory=list)
    blocking_side_effects: List[SideEffectProfile] = Field(default_factory=list)


class SavedSkillRepairAnalyzeRequest(BaseModel):
    run_result: Dict[str, Any] = Field(default_factory=dict)
    logs: List[str] = Field(default_factory=list)
    user_intent_override: str = ""
    failed_trace_index: Optional[int] = None
    failed_trace_id: Optional[str] = None
    current_page: Dict[str, Any] = Field(default_factory=dict)
