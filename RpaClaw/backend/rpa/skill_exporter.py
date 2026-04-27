import json
import logging
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from backend.storage import get_repository
from backend.config import settings

logger = logging.getLogger(__name__)


class SkillExporter:
    """Export recorded RPA skills to MongoDB or local filesystem."""

    @staticmethod
    def _json_default(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")

    @classmethod
    def _json_dumps(cls, value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            default=cls._json_default,
        )

    @staticmethod
    def _build_skill_meta(
        skill_name: str,
        description: str,
        params: Dict[str, Any],
        recording_meta: Dict[str, Any],
        projected_steps: list[Dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        legacy_steps = recording_meta.get("legacy_steps", [])
        mcp_steps = projected_steps if projected_steps is not None else recording_meta.get("mcp_steps", legacy_steps)
        return {
            "version": 2,
            "kind": "rpa-recording",
            "name": skill_name,
            "description": description,
            "entry_script": "skill.py",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "params": params,
            "recording_source": recording_meta.get("recording_source", "trace"),
            "recording": recording_meta,
            "steps": legacy_steps,
            "mcp_steps": mcp_steps,
            "artifacts": ["SKILL.md", "params.json", "skill.py"],
        }

    async def export_skill(
        self,
        user_id: str,
        skill_name: str,
        description: str,
        script: str,
        params: Dict[str, Any],
        recording_meta: Dict[str, Any] | None = None,
        steps: list[Dict[str, Any]] | None = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Export skill to MongoDB or local filesystem based on storage_backend.

        Returns the skill name on success.
        """
        # Generate input schema (exclude auto-injected params)
        input_schema = {
            "type": "object",
            "properties": {},
            "required": [],
        }
        has_auto_injected = False
        for param_name, param_info in params.items():
            # Sensitive params with credential_id are auto-injected — exclude from schema
            if param_info.get("sensitive") and param_info.get("credential_id"):
                has_auto_injected = True
                continue
            param_type = param_info.get("type", "string")
            if param_type == "file":
                prop = {
                    "type": "string",
                    "format": "file-path",
                    "x-rpa-type": "file",
                    "description": param_info.get("description", ""),
                }
            else:
                prop = {
                    "type": param_type,
                    "description": param_info.get("description", ""),
                }
            original = param_info.get("original_value", "")
            if original and original != "{{credential}}":
                prop["default"] = original
                has_auto_injected = True
            input_schema["properties"][param_name] = prop
            # Only required if no default value available
            if param_info.get("required", False) and not original:
                input_schema["required"].append(param_name)

        auto_inject_note = ""
        if has_auto_injected:
            # Build example from actual parameters with defaults
            examples = []
            for param_name, param_info in params.items():
                original = param_info.get("original_value", "")
                if original and original != "{{credential}}":
                    examples.append(f"`--{param_name}={original}`")
            example_text = ""
            if examples:
                example_text = f" For example: {', '.join(examples[:3])}"
            
            auto_inject_note = (
                "\nNote: Some parameters (credentials and defaults) are automatically "
                "injected at runtime. You can run this skill without providing them. "
                f"Pass `--param=value` only to override the pre-configured defaults.{example_text}\n"
            )

        file_param_note = ""
        if any(isinstance(info, dict) and info.get("type") == "file" for info in params.values()):
            file_param_note = (
                "\nFile parameters accept absolute paths, or paths relative to the "
                "session workspace when invoked through RpaClaw.\n"
            )

        skill_md = f"""---
name: {skill_name}
description: {description}
---

# {skill_name}

{description}

## Usage

To execute this skill, run:

```bash
python3 skill.py
```

The skill uses Playwright to automate browser interactions based on the recorded steps.
{auto_inject_note}
{file_param_note}
## Input Schema

```json
{json.dumps(input_schema, indent=2)}
```

## Implementation

The skill is implemented in `skill.py` using Playwright for browser automation.
"""
        skill_meta = self._build_skill_meta(
            skill_name=skill_name,
            description=description,
            params=params,
            recording_meta=recording_meta
            or {
                "recording_source": "legacy_step",
                "traces": [],
                "recorded_actions": [],
                "legacy_steps": steps or [],
                "runtime_results": {},
                "trace_diagnostics": [],
                "recording_diagnostics": [],
            },
            projected_steps=steps,
        )

        if settings.storage_backend == "local":
            # Save to filesystem
            skill_dir = Path(settings.external_skills_dir) / skill_name
            skill_dir.mkdir(parents=True, exist_ok=True)
            asset_steps = (recording_meta or {}).get("legacy_steps", steps or [])
            self._copy_upload_assets(skill_dir, asset_steps, session_id=session_id)

            (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
            (skill_dir / "skill.py").write_text(script, encoding="utf-8")
            # Save params config (includes credential_id for sensitive params)
            (skill_dir / "params.json").write_text(
                self._json_dumps(params), encoding="utf-8"
            )
            (skill_dir / "skill.meta.json").write_text(
                self._json_dumps(skill_meta), encoding="utf-8"
            )

            logger.info(f"Skill '{skill_name}' exported to {skill_dir}")
        else:
            # Save to MongoDB
            now = datetime.now(timezone.utc)
            col = get_repository("skills")
            await col.update_one(
                {"user_id": user_id, "name": skill_name},
                {
                    "$set": {
                        "files": {
                            "SKILL.md": skill_md,
                            "skill.py": script,
                            "skill.meta.json": self._json_dumps(skill_meta),
                        },
                        "description": description,
                        "params": params,
                        "updated_at": now,
                    },
                    "$setOnInsert": {
                        "user_id": user_id,
                        "name": skill_name,
                        "source": "rpa",
                        "blocked": False,
                        "created_at": now,
                    },
                },
                upsert=True,
            )
            logger.info(f"Skill '{skill_name}' exported to MongoDB for user {user_id}")

        return skill_name

    def _copy_upload_assets(
        self,
        skill_dir: Path,
        steps: List[Dict[str, Any]],
        *,
        session_id: Optional[str],
    ) -> None:
        for asset_path, source_path in self._iter_upload_assets(steps, session_id=session_id):
            try:
                source = Path(source_path)
                if not source.exists():
                    logger.warning("Upload asset source missing: %s", source)
                    continue
                target = skill_dir / asset_path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            except Exception as exc:
                logger.warning("Failed to copy upload asset %s: %s", asset_path, exc)

    def _iter_upload_assets(
        self,
        steps: List[Dict[str, Any]],
        *,
        session_id: Optional[str],
    ) -> List[Tuple[str, str]]:
        assets: List[Tuple[str, str]] = []
        for step in steps:
            signals = step.get("signals") if isinstance(step, dict) else None
            if not isinstance(signals, dict):
                continue
            source = signals.get("upload_source")
            if not isinstance(source, dict):
                continue
            staging = signals.get("upload_staging")

            def staging_path(item: Dict[str, Any]) -> str:
                explicit = item.get("path")
                if explicit:
                    return str(explicit)
                if not session_id:
                    return ""
                staging_id = str(item.get("staging_id") or "")
                stored = str(item.get("stored_filename") or item.get("original_filename") or "")
                if not staging_id or not stored:
                    return ""
                return str(Path(settings.rpa_uploads_dir) / session_id / staging_id / stored)

            def add(asset_path: Any, item: Dict[str, Any]) -> None:
                if not asset_path or not isinstance(item, dict):
                    return
                path = staging_path(item)
                if path:
                    assets.append((str(asset_path).replace("\\", "/"), path))

            if source.get("multi") and isinstance(source.get("items"), list) and isinstance(staging, dict):
                staging_items = staging.get("items") if isinstance(staging.get("items"), list) else []
                for index, item_source in enumerate(source["items"]):
                    if not isinstance(item_source, dict):
                        continue
                    staging_item = staging_items[index] if index < len(staging_items) and isinstance(staging_items[index], dict) else {}
                    add(item_source.get("asset_path") or item_source.get("default_asset_path"), staging_item)
                continue

            staging_item = {}
            if isinstance(staging, dict):
                items = staging.get("items")
                if isinstance(items, list) and items and isinstance(items[0], dict):
                    staging_item = items[0]
                else:
                    staging_item = staging
            add(source.get("asset_path") or source.get("default_asset_path"), staging_item)
        return assets
