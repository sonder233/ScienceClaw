import importlib
import json
import unittest
from unittest.mock import AsyncMock, patch


ASSISTANT_MODULE = importlib.import_module("backend.rpa.assistant")


class RPAStepClassifierTests(unittest.TestCase):
    def test_extract_text_style_candidate_defaults_to_script_step(self):
        classifier_module = importlib.import_module("backend.rpa.step_classifier")

        step_type = classifier_module.classify_candidate_step(
            prompt="提取最新评论内容",
            structured_intent={
                "action": "extract_text",
                "description": "提取最新评论内容",
                "prompt": "提取最新评论内容",
                "result_key": "latest_comment_text",
            },
            code=None,
        )

        self.assertEqual(step_type, "script_step")

    def test_semantic_branching_prompt_becomes_agent_step(self):
        classifier_module = importlib.import_module("backend.rpa.step_classifier")

        step_type = classifier_module.classify_candidate_step(
            prompt="判断评论情绪，如果积极就回复，否则归档",
            structured_intent={
                "action": "extract_text",
                "description": "读取评论文本",
                "prompt": "判断评论情绪，如果积极就回复，否则归档",
                "result_key": "comment_text",
            },
            code=None,
        )

        self.assertEqual(step_type, "agent_step")

    def test_ordinary_script_prompt_with_for_stays_script_step(self):
        classifier_module = importlib.import_module("backend.rpa.step_classifier")

        step_type = classifier_module.classify_candidate_step(
            prompt="Click the button for details",
            structured_intent={
                "action": "click",
                "description": "Click the details button",
                "prompt": "Click the button for details",
            },
            code=None,
        )

        self.assertEqual(step_type, "script_step")

    def test_extract_prompt_with_genju_stays_script_step(self):
        classifier_module = importlib.import_module("backend.rpa.step_classifier")

        step_type = classifier_module.classify_candidate_step(
            prompt="根据标题提取摘要",
            structured_intent={
                "action": "extract_text",
                "description": "根据标题提取摘要",
                "prompt": "根据标题提取摘要",
                "result_key": "summary",
            },
            code=None,
        )

        self.assertEqual(step_type, "script_step")


class RPAAssistantCandidateGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_candidate_step_returns_classified_candidate_payload(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        snapshot = {"url": "https://example.com", "title": "Example", "frames": []}
        llm_response = json.dumps(
            {
                "action": "extract_text",
                "description": "提取最新评论内容",
                "prompt": "提取最新评论内容",
                "result_key": "latest_comment_text",
                "target_hint": {"role": "article", "name": "comment"},
            },
            ensure_ascii=False,
        )

        async def fake_stream(_messages, _model_config=None):
            yield llm_response

        assistant._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ):
            candidate = await assistant.generate_candidate_step(
                session_id="session-1",
                page=object(),
                message="提取最新评论内容",
                steps=[],
            )

        self.assertEqual(candidate["raw_response"], llm_response)
        self.assertEqual(candidate["structured_intent"]["action"], "extract_text")
        self.assertIsNone(candidate["code"])
        self.assertEqual(candidate["snapshot"], snapshot)
        self.assertEqual(candidate["step_type"], "script_step")
        self.assertTrue(isinstance(candidate["messages"], list))

    async def test_generate_candidate_step_does_not_invoke_execution_helpers(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        snapshot = {"url": "https://example.com", "title": "Example", "frames": []}
        llm_response = json.dumps(
            {
                "action": "extract_text",
                "description": "Extract the latest comment text",
                "prompt": "Extract the latest comment text",
                "result_key": "latest_comment_text",
            }
        )

        async def fake_stream(_messages, _model_config=None):
            yield llm_response

        assistant._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ), patch.object(
            ASSISTANT_MODULE,
            "execute_structured_intent",
            new=AsyncMock(),
        ) as execute_mock, patch.object(
            ASSISTANT_MODULE.RPAAssistant,
            "_execute_on_page",
            new=AsyncMock(),
        ) as execute_on_page_mock:
            await assistant.generate_candidate_step(
                session_id="session-1",
                page=object(),
                message="Extract the latest comment text",
                steps=[],
            )

        execute_mock.assert_not_called()
        execute_on_page_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
