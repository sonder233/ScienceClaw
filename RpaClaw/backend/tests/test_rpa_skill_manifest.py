import importlib.util
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from backend.rpa.manager import RPAStep
from backend.rpa.skill_exporter import SkillExporter

MANIFEST_PATH = Path(__file__).resolve().parents[1] / "rpa" / "skill_manifest.py"


class SkillManifestTests(unittest.TestCase):
    def test_build_manifest_includes_script_and_agent_steps(self):
        spec = importlib.util.spec_from_file_location("skill_manifest_module", MANIFEST_PATH)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        manifest_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(manifest_module)

        steps = [
            {"type": "script", "action": "click", "target": "#search"},
            {"type": "agent", "action": "extract_text", "result_key": "title"},
        ]
        params = {
            "query": {"type": "string", "description": "Search term", "required": True}
        }

        manifest = manifest_module.build_manifest(
            skill_name="search_skill",
            description="Search and extract title",
            params=params,
            steps=steps,
        )

        self.assertEqual(manifest["version"], 2)
        self.assertEqual(manifest["name"], "search_skill")
        self.assertEqual(manifest["description"], "Search and extract title")
        self.assertEqual(manifest["goal"], "Search and extract title")
        self.assertEqual(manifest["params"], params)
        self.assertEqual([step["type"] for step in manifest["steps"]], ["script", "agent"])

class SaveSkillExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_skill_builds_runtime_entry_and_export_steps(self):
        route_module = __import__("backend.route.rpa", fromlist=["dummy"])

        session = SimpleNamespace(
            steps=[
                RPAStep(
                    id="step-script",
                    action="click",
                    timestamp=datetime(2026, 4, 11, 10, 30, 0),
                    source="record",
                    type="script",
                ),
                RPAStep(
                    id="step-agent",
                    action="extract_text",
                    timestamp=datetime(2026, 4, 11, 10, 31, 0),
                    source="ai",
                    type="agent",
                ),
            ],
            status="recording",
        )
        current_user = SimpleNamespace(id="user-1")
        request = route_module.SaveSkillRequest(
            skill_name="search_skill",
            description="Search and extract title",
            params={},
        )

        captured = {}

        async def _export_stub(**kwargs):
            captured.update(kwargs)
            return kwargs["skill_name"]

        with patch.object(route_module.rpa_manager, "get_session", AsyncMock(return_value=session)):
            with patch.object(route_module.generator, "build_export_steps", Mock(return_value=[
                {"type": "script", "action": "click", "script_fragment": "await current_page.click()"},
                {"type": "agent", "action": "ai_script", "goal": "Finish the task"},
            ])):
                with patch.object(route_module.generator, "generate_runtime_entry", Mock(return_value="# runtime using SkillRuntime")):
                    with patch.object(route_module.exporter, "export_skill", AsyncMock(side_effect=_export_stub)):
                        result = await route_module.save_skill("session-1", request, current_user)

        self.assertEqual(result["status"], "success")
        self.assertEqual([step["type"] for step in captured["steps"]], ["script", "agent"])
        self.assertIn("SkillRuntime", captured["script"])
        self.assertIn("script_fragment", captured["steps"][0])


class SkillExporterManifestTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_skill_local_writes_manifest_with_real_step_types_and_json_timestamps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("backend.rpa.skill_exporter.settings.storage_backend", "local"):
                with patch("backend.rpa.skill_exporter.settings.external_skills_dir", temp_dir):
                    steps = [
                        RPAStep(
                            id="step-script",
                            action="click",
                            timestamp=datetime(2026, 4, 11, 10, 30, 0),
                            source="record",
                            type="script",
                        ).model_dump(),
                        RPAStep(
                            id="step-agent",
                            action="extract_text",
                            timestamp=datetime(2026, 4, 11, 10, 31, 0),
                            source="ai",
                            type="agent",
                        ).model_dump(),
                    ]

                    await SkillExporter().export_skill(
                        user_id="user-1",
                        skill_name="search_skill",
                        description="Search and extract title",
                        script="# runtime using SkillRuntime",
                        params={},
                        steps=[
                            {
                                **steps[0],
                                "script_fragment": 'await current_page.get_by_role("button", name="Save", exact=True).click()',
                            },
                            {
                                **steps[1],
                                "goal": "If positive do X else Y",
                            },
                        ],
                    )

            manifest_path = Path(temp_dir) / "search_skill" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            skill_py = (Path(temp_dir) / "search_skill" / "skill.py").read_text(encoding="utf-8")
            self.assertEqual([step["type"] for step in manifest["steps"]], ["script", "agent"])
            self.assertIsInstance(manifest["steps"][0]["timestamp"], str)
            self.assertIn("script_fragment", manifest["steps"][0])
            self.assertIn("goal", manifest["steps"][1])
            self.assertIn("SkillRuntime", skill_py)

    async def test_export_skill_mongodb_stores_manifest_with_real_step_types_and_json_timestamps(self):
        fake_repo = AsyncMock()
        captured_set = {}

        async def _update_one_stub(_filter, update_doc, upsert):
            captured_set.update(update_doc["$set"])
            return None

        fake_repo.update_one.side_effect = _update_one_stub

        with patch("backend.rpa.skill_exporter.settings.storage_backend", "docker"):
            with patch("backend.rpa.skill_exporter.get_repository", Mock(return_value=fake_repo)):
                steps = [
                    RPAStep(
                        id="step-script",
                        action="click",
                        timestamp=datetime(2026, 4, 11, 10, 30, 0),
                        source="record",
                        type="script",
                    ).model_dump(),
                    RPAStep(
                        id="step-agent",
                        action="extract_text",
                        timestamp=datetime(2026, 4, 11, 10, 31, 0),
                        source="ai",
                        type="agent",
                    ).model_dump(),
                ]

                await SkillExporter().export_skill(
                    user_id="user-1",
                    skill_name="search_skill",
                    description="Search and extract title",
                    script="# script",
                    params={},
                    steps=steps,
                )

        manifest_text = captured_set["files"]["manifest.json"]
        manifest = json.loads(manifest_text)
        self.assertEqual([step["type"] for step in manifest["steps"]], ["script", "agent"])
        self.assertIsInstance(manifest["steps"][0]["timestamp"], str)


if __name__ == "__main__":
    unittest.main()
