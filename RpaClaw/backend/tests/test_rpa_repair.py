from __future__ import annotations

import importlib
import json
import sys
import types

import pytest


def _load_route_module():
    if "backend.route.rpa" in sys.modules:
        return sys.modules["backend.route.rpa"]

    langchain_openai = types.ModuleType("langchain_openai")

    class ChatOpenAI:
        def __init__(self, *args, **kwargs):
            pass

    langchain_openai.ChatOpenAI = ChatOpenAI
    sys.modules["langchain_openai"] = langchain_openai

    chat_models = types.ModuleType("langchain_openai.chat_models")
    chat_models_base = types.ModuleType("langchain_openai.chat_models.base")
    chat_models_base._convert_dict_to_message = lambda value, *args, **kwargs: value
    chat_models_base._convert_message_to_dict = lambda value, *args, **kwargs: {}
    chat_models_base._convert_delta_to_message_chunk = (
        lambda value, default_class: default_class()
    )
    sys.modules["langchain_openai.chat_models"] = chat_models
    sys.modules["langchain_openai.chat_models.base"] = chat_models_base

    langchain_core = types.ModuleType("langchain_core")
    language_models = types.ModuleType("langchain_core.language_models")

    class BaseChatModel:
        pass

    language_models.BaseChatModel = BaseChatModel
    messages = types.ModuleType("langchain_core.messages")

    class BaseMessage:
        pass

    class AIMessage(BaseMessage):
        def __init__(self, *args, **kwargs):
            self.additional_kwargs = kwargs.get("additional_kwargs", {})

    messages.AIMessage = AIMessage
    messages.BaseMessage = BaseMessage
    sys.modules["langchain_core"] = langchain_core
    sys.modules["langchain_core.language_models"] = language_models
    sys.modules["langchain_core.messages"] = messages

    return importlib.import_module("backend.route.rpa")


ROUTE_MODULE = _load_route_module()

from backend.rpa.manager import RPASession
from backend.rpa.repair_models import (
    RepairAnalyzeRequest,
    RepairApplyRequest,
    RepairRestoreCheckRequest,
    SavedSkillRepairAnalyzeRequest,
)
from backend.rpa.repair_safety import highest_risk_level, side_effect_profiles_for_trace
from backend.rpa.trace_models import RPAAcceptedTrace, RPAPageState, RPATraceType


def _user(user_id: str = "u1"):
    return type("User", (), {"id": user_id})()


def _failed_click_trace(trace_id: str = "trace-click") -> RPAAcceptedTrace:
    return RPAAcceptedTrace(
        trace_id=trace_id,
        trace_type=RPATraceType.MANUAL_ACTION,
        source="manual",
        action="click",
        description="Open report details",
        before_page=RPAPageState(url="https://example.test/reports", title="Reports"),
        locator_candidates=[
            {
                "kind": "css",
                "locator": {"method": "css", "value": "#old"},
                "selected": True,
                "strict_match_count": 0,
                "score": 50,
            },
            {
                "kind": "role",
                "locator": {"method": "role", "role": "button", "name": "Details"},
                "selected": False,
                "strict_match_count": 1,
                "score": 1,
            },
        ],
        validation={"selected_candidate_index": 0, "status": "fallback"},
    )


@pytest.mark.asyncio
async def test_repair_analyze_maps_failed_trace_to_locator_proposal():
    manager = ROUTE_MODULE.rpa_manager
    session = RPASession(id="repair-analyze", user_id="u1", sandbox_session_id="sandbox")
    session.traces.append(
        RPAAcceptedTrace(
            trace_id="trace-open",
            trace_type=RPATraceType.NAVIGATION,
            source="manual",
            action="navigate",
        )
    )
    session.traces.append(_failed_click_trace())
    manager.sessions[session.id] = session

    try:
        response = await ROUTE_MODULE.analyze_session_repair(
            session.id,
            RepairAnalyzeRequest(
                test_result={
                    "result": {
                        "success": False,
                        "error": "locator.click: Timeout 60000ms exceeded",
                        "failed_trace_index": 1,
                    },
                    "logs": ["TRACE_ERROR 1: locator.click timeout"],
                },
            ),
            _user(),
        )

        assert response["status"] == "success"
        assert response["context"]["failed_trace_id"] == "trace-click"
        assert response["intent"]["intent_summary"] == "Open report details"
        assert len(response["proposals"]) == 1
        proposal = response["proposals"][0]
        assert proposal["failure_category"] == "locator-not-found"
        assert proposal["patch_type"] == "select_existing_locator_candidate"
        assert proposal["patch"]["candidate_index"] == 1
        assert proposal["validation_plan"]["full_replay_required"] is True
        assert proposal["requires_user_confirmation"] is True
    finally:
        manager.sessions.pop(session.id, None)


@pytest.mark.asyncio
async def test_repair_apply_requires_confirmation_then_records_trace_metadata():
    manager = ROUTE_MODULE.rpa_manager
    session = RPASession(id="repair-apply", user_id="u1", sandbox_session_id="sandbox")
    session.traces.append(_failed_click_trace())
    manager.sessions[session.id] = session

    try:
        analyze_response = await ROUTE_MODULE.analyze_session_repair(
            session.id,
            RepairAnalyzeRequest(
                test_result={
                    "result": {
                        "success": False,
                        "error": "locator.click: Timeout 60000ms exceeded",
                        "failed_trace_index": 0,
                    },
                },
            ),
            _user(),
        )
        proposal_id = analyze_response["proposals"][0]["proposal_id"]

        with pytest.raises(ROUTE_MODULE.HTTPException) as exc_info:
            await ROUTE_MODULE.apply_session_repair_proposal(
                session.id,
                proposal_id,
                RepairApplyRequest(confirmed=False),
                _user(),
            )
        assert exc_info.value.status_code == 409

        response = await ROUTE_MODULE.apply_session_repair_proposal(
            session.id,
            proposal_id,
            RepairApplyRequest(confirmed=True),
            _user(),
        )

        trace = session.traces[0]
        assert response["status"] == "success"
        assert [candidate["selected"] for candidate in trace.locator_candidates] == [False, True]
        assert trace.validation["selected_candidate_index"] == 1
        assert trace.validation["last_repair"]["proposal_id"] == proposal_id
        assert trace.validation["last_repair"]["confirmed"] is True
        assert response["proposal"]["status"] == "applied"
    finally:
        manager.sessions.pop(session.id, None)


def test_side_effect_profile_treats_unknown_and_destructive_steps_as_high_risk():
    unknown_profiles = side_effect_profiles_for_trace({
        "action": "click",
        "description": "Open the next panel",
    })
    destructive_profiles = side_effect_profiles_for_trace({
        "action": "click",
        "description": "Delete selected records",
    })

    assert highest_risk_level(unknown_profiles) == "unknown"
    assert highest_risk_level(destructive_profiles) == "high"
    assert any(profile.type == "delete" for profile in destructive_profiles)


@pytest.mark.asyncio
async def test_restore_check_refuses_automatic_prefix_replay_after_high_risk_step():
    manager = ROUTE_MODULE.rpa_manager
    session = RPASession(id="repair-restore-check", user_id="u1", sandbox_session_id="sandbox")
    session.traces.extend(
        [
            RPAAcceptedTrace(
                trace_id="trace-delete",
                trace_type=RPATraceType.MANUAL_ACTION,
                source="manual",
                action="click",
                description="Delete selected records",
            ),
            _failed_click_trace("trace-later"),
        ]
    )
    manager.sessions[session.id] = session

    try:
        response = await ROUTE_MODULE.check_session_repair_restore(
            session.id,
            RepairRestoreCheckRequest(
                test_result={
                    "result": {
                        "success": False,
                        "error": "locator.click: Timeout 60000ms exceeded",
                        "failed_trace_index": 1,
                    },
                },
                current_page={"url": "https://example.test/error", "title": "Error"},
            ),
            _user(),
        )

        restore = response["restore"]
        assert restore["mode"] == "user-restore"
        assert restore["can_replay_prefix"] is False
        assert restore["requires_user_confirmation"] is True
        assert restore["risk_level"] in {"high", "unknown"}
        assert restore["blocking_side_effects"]
    finally:
        manager.sessions.pop(session.id, None)


@pytest.mark.asyncio
async def test_saved_skill_repair_analyze_returns_read_only_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(ROUTE_MODULE.settings, "storage_backend", "local")
    monkeypatch.setattr(ROUTE_MODULE.settings, "external_skills_dir", str(tmp_path))
    skill_dir = tmp_path / "saved-report-skill"
    skill_dir.mkdir()
    trace = _failed_click_trace("trace-saved-failed")
    meta = {
        "kind": "rpa-recording",
        "name": "saved-report-skill",
        "generated_at": "2026-06-09T10:00:00+00:00",
        "recording": {
            "recording_source": "trace",
            "traces": [trace.model_dump(mode="json")],
            "runtime_results": {},
        },
    }
    meta_path = skill_dir / "skill.meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    response = await ROUTE_MODULE.analyze_saved_skill_repair(
        "saved-report-skill",
        SavedSkillRepairAnalyzeRequest(
            run_result={
                "result": {
                    "success": False,
                    "error": "locator.click: Timeout 60000ms exceeded",
                    "failed_trace_index": 0,
                }
            }
        ),
        _user(),
    )

    assert response["status"] == "success"
    assert response["context"]["scope"] == "saved-skill-run"
    assert response["context"]["skill_name"] == "saved-report-skill"
    assert response["repair_draft"]["will_modify_original_skill"] is False
    assert response["repair_draft"]["live_retry_allowed_by_default"] is False
    assert response["proposals"][0]["failed_trace_id"] == "trace-saved-failed"
    assert json.loads(meta_path.read_text(encoding="utf-8")) == meta
