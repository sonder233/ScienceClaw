from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Optional


def safe_name_stem(filename: str, fallback: str = "file") -> str:
    basename = str(filename or fallback).rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip()
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", stem)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if re.search(r"[a-zA-Z0-9]", normalized):
        return normalized

    fallback_stem = re.sub(r"[^a-zA-Z0-9_]+", "_", str(fallback or "file"))
    fallback_stem = re.sub(r"_+", "_", fallback_stem).strip("_")
    if not re.search(r"[a-zA-Z0-9]", fallback_stem):
        fallback_stem = "file"
    digest_source = stem or basename or str(filename or fallback)
    digest = hashlib.sha1(digest_source.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{fallback_stem}_{digest}"


def download_result_key(filename: str) -> str:
    return f"download_{safe_name_stem(filename)}"


def safe_asset_filename(filename: str, fallback: str = "upload.bin") -> str:
    name = str(filename or fallback).rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip()
    if not name:
        name = fallback
    # Preserve the user's visible filename, including non-ASCII names. Only
    # strip path separators and characters that cannot safely live in a file
    # name across local filesystems.
    safe = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name)
    return safe or fallback


def default_asset_path(filename: str) -> str:
    return str(PurePosixPath("assets") / safe_asset_filename(filename))


def default_upload_source_from_staging(
    signals: Dict[str, Any],
    fallback_filename: str = "upload.bin",
) -> Dict[str, Any]:
    staging = signals.get("upload_staging") if isinstance(signals, dict) else None
    if not isinstance(staging, dict):
        return {}

    if staging.get("multi") and isinstance(staging.get("items"), list):
        items = []
        for item in staging["items"]:
            if not isinstance(item, dict):
                continue
            filename = str(item.get("original_filename") or item.get("stored_filename") or fallback_filename)
            items.append(
                {
                    "mode": "fixed",
                    "asset_path": default_asset_path(filename),
                    "original_filename": filename,
                }
            )
        return {"mode": "fixed", "multi": True, "items": items} if items else {}

    filename = str(staging.get("original_filename") or staging.get("stored_filename") or fallback_filename)
    return {
        "mode": "fixed",
        "asset_path": default_asset_path(filename),
        "original_filename": filename,
        "multi": False,
    }


def rpa_asset_helper_lines(indent: str = "    ") -> List[str]:
    return [
        f"{indent}def _rpa_asset_path(relative_path):",
        f"{indent}    import os as _os",
        f"{indent}    normalized = str(relative_path or '').replace('\\\\', '/')",
        f"{indent}    overrides = kwargs.get('_asset_overrides') or {{}}",
        f"{indent}    if normalized in overrides:",
        f"{indent}        return overrides[normalized]",
        f"{indent}    base_dir = kwargs.get('_skill_dir')",
        f"{indent}    if not base_dir:",
        f"{indent}        file_path = globals().get('__file__')",
        f"{indent}        base_dir = _os.path.dirname(file_path) if file_path else _os.getcwd()",
        f"{indent}    parts = [part for part in normalized.split('/') if part]",
        f"{indent}    return _os.path.join(base_dir, *parts)",
    ]


def _render_asset_expr(asset_path: str) -> str:
    return f"_rpa_asset_path({str(asset_path or '').replace(chr(0), '')!r})"


def _render_dataflow_expr(source: Dict[str, Any]) -> str:
    result_key = str(source.get("source_result_key") or "").strip()
    if not result_key:
        raise ValueError("Upload dataflow source is missing source_result_key")
    file_field = str(source.get("file_field") or "path")
    return f"_results[{result_key!r}][{file_field!r}]"


def _render_path_expr(source: Dict[str, Any]) -> str:
    path = str(source.get("path") or "").strip()
    if not path:
        raise ValueError("Upload path source is missing path")
    return repr(path.replace(chr(0), ""))


def _render_parameter_expr(source: Dict[str, Any], params: Dict[str, Any]) -> str:
    param_name = str(source.get("param_name") or "").strip()
    if not param_name:
        raise ValueError("Upload parameter source is missing param_name")

    param_info = params.get(param_name) if isinstance(params, dict) else None
    param_info = param_info if isinstance(param_info, dict) else {}
    required = bool(source.get("required") or param_info.get("required"))
    default_path = source.get("default_asset_path") or param_info.get("original_value")
    if isinstance(default_path, str) and default_path.strip():
        return f"kwargs.get({param_name!r}, {_render_asset_expr(default_path.strip())})"
    if required:
        return f"kwargs[{param_name!r}]"
    return f"kwargs.get({param_name!r}, '')"


def render_file_source_expr(upload_source: Dict[str, Any], params: Optional[Dict[str, Any]] = None) -> str:
    params = params or {}
    if not isinstance(upload_source, dict):
        raise ValueError("upload_source must be an object")

    if upload_source.get("multi") and isinstance(upload_source.get("items"), list):
        return "[" + ", ".join(render_file_source_expr(item, params) for item in upload_source["items"]) + "]"

    mode = str(upload_source.get("mode") or "").strip()
    if mode == "fixed":
        asset_path = str(upload_source.get("asset_path") or "").strip()
        if not asset_path:
            raise ValueError("Fixed upload source is missing asset_path")
        return _render_asset_expr(asset_path)
    if mode == "parameter":
        return _render_parameter_expr(upload_source, params)
    if mode == "path":
        return _render_path_expr(upload_source)
    if mode == "dataflow":
        return _render_dataflow_expr(upload_source)
    raise ValueError(f"Unsupported upload source mode: {mode or '<empty>'}")


def render_legacy_input_files_expr(files: Iterable[str], value: Any = "") -> str:
    normalized = [str(item) for item in files if str(item)]
    if normalized and len(normalized) > 1:
        return repr(normalized)
    effective = normalized[0] if normalized else str(value or "")
    return repr(effective)


def upload_source_uses_asset_helper(upload_source: Any) -> bool:
    if not isinstance(upload_source, dict):
        return False
    if upload_source.get("multi") and isinstance(upload_source.get("items"), list):
        return any(upload_source_uses_asset_helper(item) for item in upload_source["items"])
    mode = str(upload_source.get("mode") or "")
    if mode == "fixed":
        return bool(upload_source.get("asset_path"))
    if mode == "parameter":
        return bool(upload_source.get("default_asset_path"))
    return False


def step_upload_source(step: Dict[str, Any]) -> Dict[str, Any]:
    signals = step.get("signals") if isinstance(step, dict) else None
    if not isinstance(signals, dict):
        return {}
    source = signals.get("upload_source")
    if isinstance(source, dict):
        return source
    return default_upload_source_from_staging(signals, fallback_filename=str(step.get("value") or "upload.bin"))
