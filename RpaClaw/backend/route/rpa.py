import json
import logging
import asyncio
from pathlib import Path
from urllib.parse import urlparse
from typing import Dict, Any
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request, UploadFile, File, Body
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
import websockets
from websockets.exceptions import ConnectionClosed
import httpx
from fastapi.responses import Response as FastAPIResponse

from backend.rpa.manager import rpa_manager
from backend.rpa.generator import PlaywrightGenerator
from backend.rpa.executor import ScriptExecutor
from backend.rpa.skill_exporter import SkillExporter
from backend.rpa.assistant import RPAAssistant, RPAReActAgent, _active_agents
from backend.rpa.recording_runtime_agent import RecordingRuntimeAgent, RecordingAgentResult
from backend.rpa.trace_skill_compiler import TraceSkillCompiler
from backend.rpa.cdp_connector import get_cdp_connector
from backend.rpa.screencast import SessionScreencastController
from backend.rpa.upload_source import default_upload_source_from_staging, download_result_key
from backend.rpa.file_transform import FileTransformService, transform_result_key
from backend.user.dependencies import get_current_user, User
from backend.config import settings
from backend.storage import get_repository
from backend.credential.vault import inject_credentials

logger = logging.getLogger(__name__)

RPA_TEST_TIMEOUT_S = 180.0
RPA_PAGE_TIMEOUT_MS = 60000

router = APIRouter(tags=["RPA"])
generator = PlaywrightGenerator()
executor = ScriptExecutor()
exporter = SkillExporter()
assistant = RPAAssistant()
trace_compiler = TraceSkillCompiler()
file_transform_service = FileTransformService()


class StartSessionRequest(BaseModel):
    sandbox_session_id: str


class GenerateRequest(BaseModel):
    params: Dict[str, Any] = {}


class SaveSkillRequest(BaseModel):
    skill_name: str
    description: str
    params: Dict[str, Any] = {}


class ChatRequest(BaseModel):
    message: str
    mode: str = "chat"


class ConfirmRequest(BaseModel):
    approved: bool


class NavigateRequest(BaseModel):
    url: str


class PromoteLocatorRequest(BaseModel):
    candidate_index: int


class FileTransformRequest(BaseModel):
    instruction: str
    source_result_key: str | None = None
    source_step_id: str | None = None
    output_filename: str | None = None
    output_result_key: str | None = None
    auto_link_next_upload: bool = True


def _looks_like_file_transform_request(message: str) -> bool:
    text = str(message or "").lower()
    file_terms = ("文件", "excel", "xlsx", "表格", "downloaded file", "download file", "spreadsheet")
    transform_terms = (
        "处理",
        "转换",
        "转成",
        "生成",
        "重新生成",
        "修改",
        "改为",
        "格式",
        "清洗",
        "整理",
        "convert",
        "transform",
        "reformat",
        "format",
        "clean",
    )
    source_terms = ("刚才下载", "下载的", "下载文件", "downloaded", "download")
    return (
        any(term in text for term in file_terms)
        and any(term in text for term in transform_terms)
        and any(term in text for term in source_terms)
    )


async def _create_file_transform_for_session(
    *,
    session_id: str,
    current_user: User,
    request: FileTransformRequest,
) -> Dict[str, Any]:
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")

    instruction = request.instruction.strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="instruction is required")

    try:
        source = rpa_manager.resolve_file_result_source(
            session_id,
            source_result_key=request.source_result_key,
            source_step_id=request.source_step_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    input_path_text = str(source.get("path") or "")
    if not input_path_text:
        raise HTTPException(status_code=400, detail="Selected file source has no persisted path")
    input_path = Path(input_path_text)
    if not input_path.exists():
        raise HTTPException(status_code=400, detail=f"Selected file does not exist: {input_path}")

    output_filename = request.output_filename or f"{input_path.stem}_converted.xlsx"
    if not output_filename.lower().endswith(".xlsx"):
        output_filename = f"{Path(output_filename).stem or input_path.stem}_converted.xlsx"
    output_result_key = request.output_result_key or transform_result_key(output_filename)
    model_config = await _resolve_user_model_config(str(current_user.id))

    try:
        transform = await file_transform_service.create_transform(
            session_id=session_id,
            input_path=input_path,
            instruction=instruction,
            output_filename=output_filename,
            output_key=output_result_key,
            model_config=model_config,
        )
        step = await rpa_manager.add_file_transform_step(
            session_id,
            source_result_key=str(source.get("result_key") or ""),
            source_step_id=str(source.get("step_id") or ""),
            instruction=instruction,
            output_filename=str(transform["output_filename"]),
            output_result_key=str(transform["output_result_key"]),
            code=str(transform["code"]),
            script_path=str(transform["script_path"]),
            output_path=str(transform["output_path"]),
            result=transform["result"],
            auto_link_next_upload=request.auto_link_next_upload,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("file transform failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "step": step,
        "artifact": transform["result"],
        "result_key": transform["output_result_key"],
    }


def _generate_session_script(session, params: Dict[str, Any], *, test_mode: bool = False) -> str:
    _ensure_default_upload_sources(session)
    if getattr(session, "traces", None):
        return trace_compiler.generate_script(
            session.traces,
            params,
            is_local=(settings.storage_backend == "local"),
            test_mode=test_mode,
        )
    steps = [step.model_dump() for step in session.steps]
    return generator.generate_script(
        steps,
        params,
        is_local=(settings.storage_backend == "local"),
        test_mode=test_mode,
    )


def _ensure_default_upload_sources(session) -> None:
    trace_by_id = {
        trace.trace_id: trace
        for trace in (getattr(session, "traces", None) or [])
    }
    for step in getattr(session, "steps", []) or []:
        step_signals = step.signals if isinstance(step.signals, dict) else {}
        download_signal = step_signals.get("download")
        if not isinstance(download_signal, dict):
            continue
        trace = trace_by_id.get(f"trace-{step.id}")
        if trace is None:
            continue
        trace_signals = dict(trace.signals or {})
        trace_signals["download"] = download_signal
        trace.signals = trace_signals
        trace.value = step.value or trace.value
        if getattr(step, "description", None):
            trace.description = step.description

    for step in getattr(session, "steps", []) or []:
        if getattr(step, "action", "") != "set_input_files":
            continue
        signals = step.signals if isinstance(step.signals, dict) else {}
        if isinstance(signals.get("upload_source"), dict):
            converted = _download_dataflow_source_for_upload_path(session, signals["upload_source"])
            if converted:
                signals = dict(signals)
                signals["upload_source"] = converted
                step.signals = signals
                trace = trace_by_id.get(f"trace-{step.id}")
                if trace is not None:
                    trace_signals = dict(trace.signals or {})
                    trace_signals["upload_source"] = converted
                    trace.signals = trace_signals
            continue
        source = default_upload_source_from_staging(signals, fallback_filename=str(step.value or "upload.bin"))
        if not source:
            continue
        source = _download_dataflow_source_for_upload_path(session, source) or source
        signals = dict(signals)
        signals["upload_source"] = source
        step.signals = signals
        trace = trace_by_id.get(f"trace-{step.id}")
        if trace is not None:
            trace_signals = dict(trace.signals or {})
            trace_signals["upload_source"] = source
            trace.signals = trace_signals

    for trace in getattr(session, "traces", None) or []:
        if getattr(trace, "action", "") != "set_input_files":
            continue
        signals = trace.signals if isinstance(trace.signals, dict) else {}
        if isinstance(signals.get("upload_source"), dict):
            converted = _download_dataflow_source_for_upload_path(session, signals["upload_source"])
            if converted:
                trace_signals = dict(signals)
                trace_signals["upload_source"] = converted
                trace.signals = trace_signals
            continue
        source = default_upload_source_from_staging(signals, fallback_filename=str(trace.value or "upload.bin"))
        source = _download_dataflow_source_for_upload_path(session, source) or source
        if source:
            trace_signals = dict(signals)
            trace_signals["upload_source"] = source
            trace.signals = trace_signals


def _download_dataflow_source_for_upload_path(session, upload_source: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(upload_source, dict) or upload_source.get("mode") != "path":
        return {}
    raw_path = str(upload_source.get("path") or "").strip()
    if not raw_path:
        return {}
    normalized_path = raw_path.replace("\\", "/")
    session_download_dir = (Path(settings.workspace_dir) / "rpa_downloads" / session.id).as_posix()
    session_transform_dir = (Path(settings.workspace_dir) / "rpa_transforms" / session.id).as_posix()
    if session_download_dir not in normalized_path and session_transform_dir not in normalized_path:
        return {}

    filename = Path(raw_path).name or str(upload_source.get("original_filename") or "")
    for step in getattr(session, "steps", []) or []:
        signals = step.signals if isinstance(step.signals, dict) else {}
        download = signals.get("download")
        if not isinstance(download, dict):
            continue
        download_filename = str(download.get("filename") or step.value or "")
        download_path = str(download.get("path") or download.get("saved_path") or "").replace("\\", "/")
        if (download_path and download_path == normalized_path) or (filename and download_filename == filename):
            result_key = str(download.get("result_key") or download_result_key(download_filename or filename))
            return {
                "mode": "dataflow",
                "source_step_id": step.id,
                "source_result_key": result_key,
                "file_field": "path",
                "original_filename": filename or download_filename,
            }
    for step in getattr(session, "steps", []) or []:
        signals = step.signals if isinstance(step.signals, dict) else {}
        transform = signals.get("file_transform")
        if not isinstance(transform, dict):
            continue
        transform_filename = str(transform.get("output_filename") or step.value or "")
        transform_path = str(transform.get("output_path") or "").replace("\\", "/")
        if (transform_path and transform_path == normalized_path) or (filename and transform_filename == filename):
            result_key = str(transform.get("output_result_key") or step.result_key or "")
            if result_key:
                return {
                    "mode": "dataflow",
                    "source_step_id": step.id,
                    "source_result_key": result_key,
                    "file_field": "path",
                    "original_filename": filename or transform_filename,
                }
    return {}


def _staging_file_path(session_id: str, staging: Dict[str, Any]) -> str:
    explicit = staging.get("path")
    if explicit:
        return str(explicit)
    staging_id = str(staging.get("staging_id") or "")
    stored = str(staging.get("stored_filename") or staging.get("original_filename") or "")
    if not staging_id or not stored:
        return ""
    return str(Path(settings.rpa_uploads_dir) / session_id / staging_id / stored)


def _first_staging_item(signals: Dict[str, Any]) -> Dict[str, Any]:
    staging = signals.get("upload_staging")
    if not isinstance(staging, dict):
        return {}
    items = staging.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    return staging


def _collect_asset_overrides(session) -> Dict[str, str]:
    overrides: Dict[str, str] = {}
    for step in getattr(session, "steps", []) or []:
        signals = step.signals if isinstance(step.signals, dict) else {}
        source = signals.get("upload_source")
        if not isinstance(source, dict):
            continue
        staging = signals.get("upload_staging")

        def add_override(asset_path: str, staging_item: Dict[str, Any]) -> None:
            if not asset_path or not isinstance(staging_item, dict):
                return
            path = _staging_file_path(session.id, staging_item)
            if path and Path(path).exists():
                overrides[str(asset_path).replace("\\", "/")] = path

        if source.get("multi") and isinstance(source.get("items"), list) and isinstance(staging, dict):
            staging_items = staging.get("items") if isinstance(staging.get("items"), list) else []
            for index, item_source in enumerate(source["items"]):
                if not isinstance(item_source, dict):
                    continue
                staging_item = staging_items[index] if index < len(staging_items) and isinstance(staging_items[index], dict) else {}
                asset_path = item_source.get("asset_path") or item_source.get("default_asset_path")
                add_override(str(asset_path or ""), staging_item)
            continue

        staging_item = _first_staging_item(signals)
        asset_path = source.get("asset_path") or source.get("default_asset_path")
        add_override(str(asset_path or ""), staging_item)
    return overrides


def _resolve_file_param_values(session, params: Dict[str, Any], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(kwargs)
    overrides = _collect_asset_overrides(session)
    if overrides:
        result.setdefault("_asset_overrides", overrides)
    for param_name, param_info in (params or {}).items():
        if not isinstance(param_info, dict) or param_info.get("type") != "file":
            continue
        if param_name not in result:
            continue
        raw_value = str(result.get(param_name) or "")
        if not raw_value:
            continue
        normalized = raw_value.replace("\\", "/")
        if normalized in overrides:
            result[param_name] = overrides[normalized]
            continue
        value_path = Path(raw_value)
        if value_path.is_absolute():
            result[param_name] = str(value_path)
        else:
            result[param_name] = str(Path(settings.workspace_dir) / raw_value)
    return result


async def _apply_recording_agent_result(session_id: str, result: RecordingAgentResult) -> None:
    for diagnostic in result.diagnostics:
        await rpa_manager.append_trace_diagnostic(session_id, diagnostic)
    if result.trace:
        await rpa_manager.append_trace(session_id, result.trace)
    if result.output_key:
        rpa_manager.write_runtime_result(session_id, result.output_key, result.output)


async def _get_ws_user(websocket: WebSocket) -> User | None:
    """Resolve the current user for a WebSocket request.

    Browser WebSocket APIs cannot attach custom Authorization headers in the
    same way axios does, so we accept a bearer token via query param as a
    fallback and keep the existing local-mode shortcut.
    """
    if settings.storage_backend == "local":
        return User(id="local_admin", username="admin", role="admin")

    if getattr(settings, "auth_provider", "local") == "none":
        return User(id="anonymous", username="Anonymous", role="user")

    session_id = (
        websocket.query_params.get("token")
        or websocket.cookies.get(settings.session_cookie)
    )
    if not session_id:
        return None

    repo = get_repository("user_sessions")
    session_doc = await repo.find_one({"_id": session_id})
    if not session_doc:
        return None

    import time
    if session_doc.get("expires_at", 0) < time.time():
        await repo.delete_one({"_id": session_id})
        return None

    return User(
        id=str(session_doc["user_id"]),
        username=session_doc["username"],
        role=session_doc.get("role", "user"),
    )


async def _get_http_user(request: Request) -> User | None:
    """Resolve the current user for normal HTTP requests.

    This mirrors websocket auth so iframe-based noVNC pages can use either
    the session cookie or a `token` query param.
    """
    if settings.storage_backend == "local":
        return User(id="local_admin", username="admin", role="admin")

    if getattr(settings, "auth_provider", "local") == "none":
        return User(id="anonymous", username="Anonymous", role="user")

    session_id = (
        request.query_params.get("token")
        or request.cookies.get(settings.session_cookie)
    )
    if not session_id:
        return None

    repo = get_repository("user_sessions")
    session_doc = await repo.find_one({"_id": session_id})
    if not session_doc:
        return None

    import time
    if session_doc.get("expires_at", 0) < time.time():
        await repo.delete_one({"_id": session_id})
        return None

    return User(
        id=str(session_doc["user_id"]),
        username=session_doc["username"],
        role=session_doc.get("role", "user"),
    )


def _get_sandbox_vnc_ws_url() -> str:
    """Return the configured upstream sandbox VNC WebSocket URL."""
    return settings.sandbox_vnc_ws_url.rstrip("/")


def _get_sandbox_vnc_http_url(path: str) -> str:
    sandbox_base = settings.sandbox_base_url.rstrip("/")
    return f"{sandbox_base}/vnc/{path.lstrip('/')}"


def _get_sandbox_novnc_ws_url() -> str:
    sandbox_base = settings.sandbox_base_url.rstrip("/")
    parsed = urlparse(sandbox_base)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    return parsed._replace(scheme=ws_scheme, path="/websockify", query="", fragment="").geturl()


def _get_sandbox_proxy_headers() -> list[tuple[str, str]] | None:
    """Parse optional proxy request headers from env.

    Expected format:
      SANDBOX_PROXY_HEADERS={"Authorization":"Bearer xxx","X-API-Key":"yyy"}
    """
    raw = (getattr(settings, "sandbox_proxy_headers", "") or "").strip()
    if not raw:
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid SANDBOX_PROXY_HEADERS JSON; ignoring proxy headers")
        return None

    if not isinstance(parsed, dict):
        logger.warning("SANDBOX_PROXY_HEADERS must be a JSON object; ignoring proxy headers")
        return None

    headers: list[tuple[str, str]] = []
    for key, value in parsed.items():
        if value is None:
            continue
        headers.append((str(key), str(value)))
    return headers or None


def _get_sandbox_proxy_headers_dict() -> dict[str, str]:
    headers = _get_sandbox_proxy_headers() or []
    return {key: value for key, value in headers}


def _filter_proxy_query(params: dict[str, str] | Any) -> dict[str, str]:
    return {str(k): str(v) for k, v in dict(params).items() if k != "token"}


def _rewrite_vnc_html(html: str, session_id: str) -> str:
    proxy_prefix = f"/api/v1/rpa/vnc/page/{session_id}/"
    rewritten = html.replace('href="/vnc/', f'href="{proxy_prefix}')
    rewritten = rewritten.replace('src="/vnc/', f'src="{proxy_prefix}')
    rewritten = rewritten.replace('action="/vnc/', f'action="{proxy_prefix}')
    rewritten = rewritten.replace('url: "/vnc/', f'url: "{proxy_prefix}')
    rewritten = rewritten.replace("url: '/vnc/", f"url: '{proxy_prefix}")
    rewritten = rewritten.replace('path: "websockify"', f'path: "{proxy_prefix}websockify"')
    rewritten = rewritten.replace("path: 'websockify'", f"path: '{proxy_prefix}websockify'")
    rewritten = rewritten.replace('path = "websockify"', f'path = "{proxy_prefix}websockify"')
    rewritten = rewritten.replace("path = 'websockify'", f"path = '{proxy_prefix}websockify'")
    if "<head>" in rewritten:
        rewritten = rewritten.replace("<head>", f'<head><base href="{proxy_prefix}">', 1)
    return rewritten


async def _resolve_user_model_config(user_id: str) -> dict | None:
    """Resolve the user's model config for the RPA assistant.

    Priority: user's own models → system models → env defaults (None).
    """
    # Try user's own active model first, then system models
    docs = await get_repository("models").find_many(
        {"$or": [{"user_id": user_id}, {"is_system": True}], "is_active": True, "api_key": {"$nin": ["", None]}},
        sort=[("is_system", 1), ("updated_at", -1)],  # user models first
        limit=1,
    )
    doc = docs[0] if docs else None
    if doc:
        return {
            "model_name": doc.get("model_name"),
            "base_url": doc.get("base_url"),
            "api_key": doc.get("api_key"),
            "context_window": doc.get("context_window"),
            "provider": doc.get("provider", ""),
        }
    # Fall back to env defaults
    if (getattr(settings, "model_ds_api_key", None) or "").strip():
        return None  # get_llm_model(config=None) uses env defaults
    return None


@router.post("/session/start")
async def start_rpa_session(
    request: StartSessionRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        session = await rpa_manager.create_session(
            user_id=str(current_user.id),
            sandbox_session_id=request.sandbox_session_id,
        )
        return {"status": "success", "session": session}
    except Exception as e:
        logger.error(f"Failed to start RPA session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/session/{session_id}")
async def get_rpa_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")
    return {"status": "success", "session": session}


@router.get("/session/{session_id}/tabs")
async def list_rpa_tabs(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")
    return {
        "status": "success",
        "tabs": rpa_manager.list_tabs(session_id),
        "active_tab_id": session.active_tab_id,
    }


@router.post("/session/{session_id}/tabs/{tab_id}/activate")
async def activate_rpa_tab(
    session_id: str,
    tab_id: str,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")
    try:
        result = await rpa_manager.activate_tab(session_id, tab_id, source="user")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "status": "success",
        "result": result,
        "tabs": rpa_manager.list_tabs(session_id),
        "active_tab_id": session.active_tab_id,
    }


@router.post("/session/{session_id}/navigate")
async def navigate_rpa_session(
    session_id: str,
    request: NavigateRequest,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")
    try:
        result = await rpa_manager.navigate_active_tab(session_id, request.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": "success",
        "result": result,
        "tabs": rpa_manager.list_tabs(session_id),
        "active_tab_id": session.active_tab_id,
    }


@router.post("/session/{session_id}/stop")
async def stop_rpa_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")
    await rpa_manager.stop_session(session_id)
    return {"status": "success", "session": session}


@router.delete("/session/{session_id}/step/{step_index}")
async def delete_step(
    session_id: str,
    step_index: int,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")
    success = await rpa_manager.delete_step(session_id, step_index)
    if not success:
        raise HTTPException(status_code=400, detail="Invalid step index")
    return {"status": "success"}


@router.post("/session/{session_id}/step/{step_index}/locator")
async def promote_step_locator(
    session_id: str,
    step_index: int,
    request: PromoteLocatorRequest,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        step = await rpa_manager.select_step_locator_candidate(
            session_id,
            step_index,
            request.candidate_index,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"status": "success", "step": step}


@router.get("/session/{session_id}/downloadable_steps")
async def list_downloadable_steps(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")
    try:
        steps = rpa_manager.downloadable_steps(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "success", "steps": steps}


@router.put("/session/{session_id}/step/{step_index}/upload_source")
async def update_step_upload_source(
    session_id: str,
    step_index: int,
    upload_source: Dict[str, Any] = Body(...),
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")
    try:
        step = await rpa_manager.update_step_upload_source(session_id, step_index, upload_source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "step": step}


@router.post("/session/{session_id}/step/{step_index}/asset")
async def upload_step_asset(
    session_id: str,
    step_index: int,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    try:
        step = await rpa_manager.attach_step_asset(
            session_id,
            step_index,
            filename=file.filename or "upload.bin",
            content=content,
            mime=file.content_type or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    source = step.signals.get("upload_source") if isinstance(step.signals, dict) else {}
    asset_path = ""
    if isinstance(source, dict):
        asset_path = source.get("asset_path") or source.get("default_asset_path") or ""
    return {
        "status": "success",
        "step": step,
        "asset_path": asset_path,
    }


@router.post("/session/{session_id}/step/{step_index}/promote_staging")
async def promote_step_upload_staging(
    session_id: str,
    step_index: int,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")
    try:
        step = await rpa_manager.promote_upload_staging(session_id, step_index)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    source = step.signals.get("upload_source") if isinstance(step.signals, dict) else {}
    return {
        "status": "success",
        "step": step,
        "asset_path": source.get("asset_path") if isinstance(source, dict) else "",
    }


@router.post("/session/{session_id}/file-transform")
async def create_file_transform_step(
    session_id: str,
    request: FileTransformRequest,
    current_user: User = Depends(get_current_user),
):
    result = await _create_file_transform_for_session(
        session_id=session_id,
        current_user=current_user,
        request=request,
    )
    return {
        "status": "success",
        **result,
    }


@router.post("/session/{session_id}/generate")
async def generate_script(
    session_id: str,
    request: GenerateRequest = GenerateRequest(),
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    script = _generate_session_script(session, request.params)
    return {"status": "success", "script": script}


@router.post("/session/{session_id}/test")
async def test_script(
    session_id: str,
    request: GenerateRequest = GenerateRequest(),
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    steps = [step.model_dump() for step in session.steps]
    script = _generate_session_script(session, request.params, test_mode=True)

    logs = []
    browser = await get_cdp_connector().get_browser(
        session_id=session.sandbox_session_id,
        user_id=str(current_user.id),
    )

    downloads_dir = str(Path(settings.workspace_dir) / "rpa_downloads" / session_id)
    connector = get_cdp_connector()
    pw_loop_runner = getattr(connector, "run_in_pw_loop", None)

    # 本地模式：通过 pw_loop_runner 确保 Playwright 操作在正确的事件循环里执行
    if settings.storage_backend == "local":
        test_kwargs: Dict[str, Any] = {"_downloads_dir": downloads_dir}
        if request.params:
            test_kwargs.update(await inject_credentials(str(current_user.id), request.params, {}))
        test_kwargs = _resolve_file_param_values(session, request.params, test_kwargs)
        result = await executor.execute(
            browser,
            script,
            on_log=lambda msg: logs.append(msg),
            timeout=RPA_TEST_TIMEOUT_S,
            session_id=session_id,
            page_registry=rpa_manager._pages,
            session_manager=rpa_manager,
            kwargs=test_kwargs,
            downloads_dir=downloads_dir,
            pw_loop_runner=pw_loop_runner,
        )
    else:
        # Docker 模式：使用原有逻辑
        docker_kwargs: Dict[str, Any] = {}
        if request.params:
            docker_kwargs = await inject_credentials(
                str(current_user.id), request.params, {}
            )
        docker_kwargs = _resolve_file_param_values(session, request.params, docker_kwargs)
        result = await executor.execute(
            browser,
            script,
            on_log=lambda msg: logs.append(msg),
            timeout=RPA_TEST_TIMEOUT_S,
            session_id=session_id,
            page_registry=rpa_manager._pages,
            session_manager=rpa_manager,
            kwargs=docker_kwargs,
            downloads_dir=downloads_dir,
        )

    # Extract failed step candidates for locator retry
    deduped_failed_index = result.get("failed_step_index")
    failed_step_index = None
    failed_step_candidates = []
    if deduped_failed_index is not None:
        deduped = generator._deduplicate_steps(steps)
        deduped = generator._infer_missing_tab_transitions(deduped)
        deduped = generator._normalize_step_signals(deduped)
        if 0 <= deduped_failed_index < len(deduped):
            failed_step = deduped[deduped_failed_index]
            # Map deduped index back to original steps index via step id
            failed_step_id = failed_step.get("id")
            if failed_step_id:
                for orig_i, orig_step in enumerate(steps):
                    if orig_step.get("id") == failed_step_id:
                        failed_step_index = orig_i
                        break
            if failed_step_index is None:
                failed_step_index = min(deduped_failed_index, len(steps) - 1)
            candidates = failed_step.get("locator_candidates", [])
            filtered = []
            for orig_idx, c in enumerate(candidates):
                if not c.get("selected"):
                    entry = dict(c)
                    entry["original_index"] = orig_idx
                    filtered.append(entry)
            failed_step_candidates = sorted(
                filtered,
                key=lambda c: (
                    0 if c.get("strict_match_count") == 1 else 1,
                    c.get("score", 999),
                ),
            )

    return {
        "status": "success" if result.get("success") else "failed",
        "result": result,
        "logs": logs,
        "script": script,
        "failed_step_index": failed_step_index,
        "failed_step_candidates": failed_step_candidates,
    }


@router.post("/session/{session_id}/save")
async def save_skill(
    session_id: str,
    request: SaveSkillRequest,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    script = _generate_session_script(session, request.params)

    skill_name = await exporter.export_skill(
        user_id=str(current_user.id),
        skill_name=request.skill_name,
        description=request.description,
        script=script,
        params=request.params,
        steps=[step.model_dump() for step in session.steps],
        session_id=session_id,
    )

    session.status = "saved"
    return {"status": "success", "skill_name": skill_name}


@router.post("/session/{session_id}/chat")
async def chat_with_assistant(
    session_id: str,
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
):
    session = await rpa_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized")

    # Resolve user's model config
    model_config = await _resolve_user_model_config(str(current_user.id))

    if _looks_like_file_transform_request(request.message):
        async def file_transform_event_generator():
            try:
                rpa_manager.pause_recording(session_id)
                yield {
                    "event": "agent_thought",
                    "data": json.dumps({"text": "识别到文件处理请求，正在处理最近的文件产物。"}, ensure_ascii=False),
                }
                result = await _create_file_transform_for_session(
                    session_id=session_id,
                    current_user=current_user,
                    request=FileTransformRequest(
                        instruction=request.message,
                        auto_link_next_upload=True,
                    ),
                )
                step = result["step"]
                artifact = result["artifact"]
                yield {
                    "event": "trace_added",
                    "data": json.dumps(
                        {
                            "trace_type": "file_transform",
                            "description": getattr(step, "description", ""),
                            "signals": getattr(step, "signals", {}),
                            "value": getattr(step, "value", ""),
                            "output_key": result["result_key"],
                            "output": artifact,
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                }
                yield {
                    "event": "agent_step_done",
                    "data": json.dumps(
                        {
                            "success": True,
                            "description": getattr(step, "description", ""),
                            "output": artifact,
                            "step": step.model_dump(mode="json") if hasattr(step, "model_dump") else {},
                        },
                        ensure_ascii=False,
                        default=str,
                    ),
                }
                yield {
                    "event": "agent_done",
                    "data": json.dumps(
                        {
                            "message": f"文件已处理完成：{artifact.get('filename')}",
                            "total_steps": len(session.traces),
                            "trace_count": len(session.traces),
                        },
                        ensure_ascii=False,
                    ),
                }
            except Exception as e:
                logger.error(f"File transform chat error: {e}")
                yield {"event": "error", "data": json.dumps({"message": str(e)}, ensure_ascii=False)}
                yield {
                    "event": "agent_aborted",
                    "data": json.dumps({"reason": str(e), "diagnostics": []}, ensure_ascii=False),
                }
            finally:
                rpa_manager.resume_recording(session_id)

        return EventSourceResponse(file_transform_event_generator())

    # Get the page object for this session
    page = rpa_manager.get_page(session_id)
    if not page:
        raise HTTPException(status_code=400, detail="No active page for this session")

    steps = [step.model_dump() for step in session.steps]

    async def event_generator():
        try:
            rpa_manager.pause_recording(session_id)

            if request.mode == "legacy_react":
                # Reuse existing agent for this session to preserve history across turns
                agent = _active_agents.get(session_id)
                if agent is None:
                    agent = RPAReActAgent()
                    _active_agents[session_id] = agent
                try:
                    async for event in agent.run(
                        session_id=session_id,
                        page=page,
                        goal=request.message,
                        existing_steps=steps,
                        model_config=model_config,
                        page_provider=lambda: rpa_manager.get_page(session_id),
                    ):
                        evt_type = event.get("event", "message")
                        evt_data = event.get("data", {})
                        if evt_type == "agent_step_done" and evt_data.get("step"):
                            await rpa_manager.add_step(session_id, evt_data["step"])
                        if evt_type == "agent_aborted":
                            _active_agents.pop(session_id, None)
                        yield {
                            "event": evt_type,
                            "data": json.dumps(evt_data, ensure_ascii=False),
                        }
                except Exception:
                    _active_agents.pop(session_id, None)
                    raise
            elif request.mode == "legacy_chat":
                async for event in assistant.chat(
                    session_id=session_id,
                    page=page,
                    message=request.message,
                    steps=steps,
                    model_config=model_config,
                    page_provider=lambda: rpa_manager.get_page(session_id),
                ):
                    evt_type = event.get("event", "message")
                    evt_data = event.get("data", {})
                    if evt_type == "result" and evt_data.get("success") and evt_data.get("step"):
                        await rpa_manager.add_step(session_id, evt_data["step"])
                    yield {
                        "event": evt_type,
                        "data": json.dumps(evt_data, ensure_ascii=False),
                    }
            else:
                yield {
                    "event": "agent_thought",
                    "data": json.dumps({"text": "Planning one trace-first recording command."}, ensure_ascii=False),
                }
                agent = RecordingRuntimeAgent(model_config=model_config)
                result = await agent.run(
                    page=page,
                    instruction=request.message,
                    runtime_results=session.runtime_results.values,
                    debug_context={"session_id": session_id},
                )
                await _apply_recording_agent_result(session_id, result)

                if result.trace:
                    code = result.trace.ai_execution.code if result.trace.ai_execution else ""
                    yield {
                        "event": "agent_action",
                        "data": json.dumps(
                            {"description": result.trace.description, "code": code},
                            ensure_ascii=False,
                        ),
                    }
                    yield {
                        "event": "trace_added",
                        "data": json.dumps(result.trace.model_dump(mode="json"), ensure_ascii=False),
                    }
                    yield {
                        "event": "agent_step_done",
                        "data": json.dumps(
                            {
                                "success": result.success,
                                "description": result.trace.description,
                                "output": result.output,
                                "trace": result.trace.model_dump(mode="json"),
                            },
                            ensure_ascii=False,
                        ),
                    }

                if result.success:
                    yield {
                        "event": "agent_done",
                        "data": json.dumps(
                            {
                                "message": result.message,
                                "total_steps": len(session.traces),
                                "trace_count": len(session.traces),
                            },
                            ensure_ascii=False,
                        ),
                    }
                else:
                    yield {
                        "event": "agent_aborted",
                        "data": json.dumps(
                            {
                                "reason": result.message,
                                "diagnostics": [d.model_dump(mode="json") for d in result.diagnostics],
                            },
                            ensure_ascii=False,
                        ),
                    }
        except Exception as e:
            logger.error(f"Chat error: {e}")
            yield {"event": "error", "data": json.dumps({"message": str(e)}, ensure_ascii=False)}
            yield {"event": "done", "data": "{}"}
        finally:
            rpa_manager.resume_recording(session_id)

    return EventSourceResponse(event_generator())


@router.post("/session/{session_id}/agent/confirm")
async def agent_confirm(
    session_id: str,
    body: ConfirmRequest,
    current_user: User = Depends(get_current_user),
):
    agent = _active_agents.get(session_id)
    if agent:
        agent.resolve_confirm(body.approved)
    return {"ok": True}


@router.post("/session/{session_id}/agent/abort")
async def agent_abort(
    session_id: str,
    current_user: User = Depends(get_current_user),
):
    agent = _active_agents.get(session_id)
    if agent:
        agent.abort()
    return {"ok": True}


@router.websocket("/session/{session_id}/steps")
async def steps_stream(websocket: WebSocket, session_id: str):
    """Stream real-time step updates to frontend."""
    await websocket.accept()

    session = await rpa_manager.get_session(session_id)
    if not session:
        await websocket.close(code=1008, reason="Session not found")
        return

    rpa_manager.register_ws(session_id, websocket)

    try:
        for step in session.steps:
            await websocket.send_json({"type": "step", "data": step.model_dump(mode="json")})
        for trace in getattr(session, "traces", []):
            await websocket.send_json({"type": "trace_added", "data": trace.model_dump(mode="json")})

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        rpa_manager.unregister_ws(session_id, websocket)


@router.websocket("/screencast/{session_id}")
async def rpa_screencast(websocket: WebSocket, session_id: str):
    """Session-scoped CDP screencast with active-tab switching."""
    logger.info(
        "Screencast websocket connect session=%s client=%s query=%s",
        session_id,
        getattr(websocket.client, "host", None),
        dict(websocket.query_params),
    )
    user = await _get_ws_user(websocket)
    await websocket.accept()
    if not user:
        logger.warning("Screencast websocket unauthenticated session=%s", session_id)
        await websocket.close(code=1008, reason="Not authenticated")
        return

    session = await rpa_manager.get_session(session_id)
    if not session:
        logger.warning("Screencast websocket missing session=%s user=%s", session_id, user.username)
        await websocket.close(code=1008, reason="Session not found")
        return
    if session.user_id != str(user.id):
        logger.warning(
            "Screencast websocket forbidden session=%s request_user=%s owner=%s",
            session_id,
            user.id,
            session.user_id,
        )
        await websocket.close(code=1008, reason="Not authorized")
        return

    active_page = rpa_manager.get_page(session_id)
    if active_page:
        logger.info(
            "Screencast websocket ready session=%s user=%s page_id=%s url=%s",
            session_id,
            user.username,
            id(active_page),
            getattr(active_page, "url", ""),
        )
    else:
        logger.info(
            "Screencast websocket waiting for active page session=%s user=%s",
            session_id,
            user.username,
        )

    screencast = SessionScreencastController(
        page_provider=lambda: rpa_manager.get_page(session_id),
        tabs_provider=lambda: rpa_manager.list_tabs(session_id),
        session_id=session_id,
    )
    try:
        await screencast.start(websocket)
    except WebSocketDisconnect:
        logger.info("Screencast websocket disconnected session=%s", session_id)
    except Exception as e:
        logger.exception("Screencast error session=%s: %s", session_id, e)
        try:
            await websocket.close(code=1011, reason="Screencast failed")
        except Exception:
            pass
    finally:
        await screencast.stop()


@router.get("/vnc/page/{session_id}")
@router.get("/vnc/page/{session_id}/{path:path}")
async def proxy_vnc_page(session_id: str, request: Request, path: str = "index.html"):
    logger.info(
        "noVNC page proxy request session=%s path=%s query=%s",
        session_id,
        path or "index.html",
        dict(request.query_params),
    )
    user = await _get_http_user(request)
    if not user:
        logger.warning("noVNC page proxy unauthenticated session=%s", session_id)
        raise HTTPException(status_code=401, detail="Not authenticated")

    session = await rpa_manager.get_session(session_id)
    if session and session.user_id != str(user.id):
        logger.warning(
            "noVNC page proxy forbidden session=%s request_user=%s owner=%s",
            session_id,
            user.id,
            session.user_id,
        )
        raise HTTPException(status_code=403, detail="Not authorized")

    upstream_url = _get_sandbox_vnc_http_url(path or "index.html")
    query = _filter_proxy_query(request.query_params)
    logger.info(
        "noVNC page proxy upstream session=%s upstream=%s filtered_query=%s",
        session_id,
        upstream_url,
        query,
    )

    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        upstream = await client.get(
            upstream_url,
            params=query,
            headers=_get_sandbox_proxy_headers_dict(),
        )
    logger.info(
        "noVNC page proxy response session=%s status=%s content_type=%s",
        session_id,
        upstream.status_code,
        upstream.headers.get("content-type", ""),
    )

    excluded_headers = {"content-length", "transfer-encoding", "connection", "content-encoding"}
    headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in excluded_headers
    }

    content_type = upstream.headers.get("content-type", "")
    content = upstream.content
    if "text/html" in content_type:
        content = _rewrite_vnc_html(upstream.text, session_id).encode("utf-8")
        headers["content-type"] = "text/html; charset=utf-8"

    return FastAPIResponse(
        content=content,
        status_code=upstream.status_code,
        headers=headers,
        media_type=None,
    )


@router.websocket("/vnc/page/{session_id}/websockify")
async def proxy_vnc_page_websocket(websocket: WebSocket, session_id: str):
    logger.info(
        "noVNC websocket proxy request session=%s query=%s client=%s",
        session_id,
        dict(websocket.query_params),
        getattr(websocket.client, "host", None),
    )
    user = await _get_ws_user(websocket)

    requested_protocols = [
        p.strip()
        for p in (websocket.headers.get("sec-websocket-protocol") or "").split(",")
        if p.strip()
    ]
    accepted_subprotocol = requested_protocols[0] if requested_protocols else None

    await websocket.accept(subprotocol=accepted_subprotocol)
    if not user:
        logger.warning("noVNC websocket proxy unauthenticated session=%s", session_id)
        await websocket.close(code=1008, reason="Not authenticated")
        return

    session = await rpa_manager.get_session(session_id)
    if session and session.user_id != str(user.id):
        logger.warning(
            "noVNC websocket proxy forbidden session=%s request_user=%s owner=%s",
            session_id,
            user.id,
            session.user_id,
        )
        await websocket.close(code=1008, reason="Not authorized")
        return

    upstream_url = _get_sandbox_novnc_ws_url()
    query = _filter_proxy_query(websocket.query_params)
    if query:
        from urllib.parse import urlencode
        upstream_url = f"{upstream_url}?{urlencode(query)}"

    logger.info(
        "Opening proxied noVNC websocket for user=%s session=%s upstream=%s subprotocols=%s",
        user.username,
        session_id,
        upstream_url,
        requested_protocols,
    )

    try:
        async with websockets.connect(
            upstream_url,
            subprotocols=requested_protocols or None,
            additional_headers=_get_sandbox_proxy_headers(),
            ping_interval=20,
            ping_timeout=20,
            max_size=None,
        ) as upstream:
            logger.info(
                "Proxied noVNC websocket upstream connected session=%s upstream_subprotocol=%s",
                session_id,
                getattr(upstream, "subprotocol", None),
            )

            async def client_to_upstream():
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        logger.info("noVNC websocket client disconnected session=%s", session_id)
                        break
                    if message.get("bytes") is not None:
                        await upstream.send(message["bytes"])
                    elif message.get("text") is not None:
                        await upstream.send(message["text"])

            async def upstream_to_client():
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            relay_tasks = {
                asyncio.create_task(client_to_upstream()),
                asyncio.create_task(upstream_to_client()),
            }
            done, pending = await asyncio.wait(
                relay_tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.gather(*done, return_exceptions=True)
    except ConnectionClosed as exc:
        logger.info("Proxied noVNC websocket closed session=%s detail=%s", session_id, exc)
    except WebSocketDisconnect:
        logger.info("Proxied noVNC websocket local disconnect session=%s", session_id)
        pass
    except Exception as exc:
        logger.exception("Proxied noVNC websocket error session=%s: %s", session_id, exc)
        try:
            await websocket.close(code=1011, reason="noVNC proxy failed")
        except Exception:
            pass


@router.websocket("/vnc/{session_id}")
async def vnc_proxy(websocket: WebSocket, session_id: str):
    """Proxy frontend VNC WebSocket traffic through the backend.

    This keeps the sandbox or local browser endpoint private to the backend,
    so the browser only talks to `/api/v1/rpa/vnc/...`.
    """
    user = await _get_ws_user(websocket)

    requested_protocols = [
        p.strip()
        for p in (websocket.headers.get("sec-websocket-protocol") or "").split(",")
        if p.strip()
    ]
    accepted_subprotocol = requested_protocols[0] if requested_protocols else None

    await websocket.accept(subprotocol=accepted_subprotocol)
    if not user:
        await websocket.close(code=1008, reason="Not authenticated")
        return

    upstream_url = _get_sandbox_vnc_ws_url()
    logger.info(
        "Opening VNC proxy for user=%s session=%s upstream=%s",
        user.username,
        session_id,
        upstream_url,
    )

    try:
        async with websockets.connect(
            upstream_url,
            subprotocols=requested_protocols or None,
            additional_headers=_get_sandbox_proxy_headers(),
            ping_interval=20,
            ping_timeout=20,
            max_size=None,
        ) as upstream:

            async def client_to_upstream():
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        break
                    if message.get("bytes") is not None:
                        await upstream.send(message["bytes"])
                    elif message.get("text") is not None:
                        await upstream.send(message["text"])

            async def upstream_to_client():
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            relay_tasks = {
                asyncio.create_task(client_to_upstream()),
                asyncio.create_task(upstream_to_client()),
            }
            done, pending = await asyncio.wait(
                relay_tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.gather(*done, return_exceptions=True)
    except ConnectionClosed as exc:
        logger.info("VNC proxy closed: %s", exc)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("VNC proxy error: %s", exc)
        try:
            await websocket.close(code=1011, reason="VNC proxy failed")
        except Exception:
            pass
