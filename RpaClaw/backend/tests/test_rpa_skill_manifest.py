import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


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


if __name__ == "__main__":
    unittest.main()


class SaveSkillExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_skill_passes_steps_to_exporter(self):
        route_module = __import__("backend.route.rpa", fromlist=["dummy"])

        step_script = {"type": "script", "action": "click", "target": "#search"}
        step_agent = {"type": "agent", "action": "extract_text", "result_key": "title"}
        session = SimpleNamespace(
            steps=[
                SimpleNamespace(model_dump=lambda: step_script),
                SimpleNamespace(model_dump=lambda: step_agent),
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
            with patch.object(route_module.generator, "generate_script", Mock(return_value="# script")):
                with patch.object(route_module.exporter, "export_skill", AsyncMock(side_effect=_export_stub)):
                    result = await route_module.save_skill("session-1", request, current_user)

        self.assertEqual(result["status"], "success")
        self.assertEqual([step["type"] for step in captured["steps"]], ["script", "agent"])
