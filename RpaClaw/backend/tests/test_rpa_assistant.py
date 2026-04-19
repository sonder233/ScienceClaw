import importlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


ASSISTANT_MODULE = importlib.import_module("backend.rpa.assistant")
ASSISTANT_RUNTIME_MODULE = importlib.import_module("backend.rpa.assistant_runtime")
DOM_FLOW_MODULE = importlib.import_module("backend.rpa.dom_flow")
DOM_DATA_VIEW_MODULE = importlib.import_module("backend.rpa.dom_data_view")


class _FakeModel:
    def __init__(self, response):
        self._response = response

    async def ainvoke(self, _messages):
        return self._response


class _FakeStreamingModel:
    def __init__(self, chunks):
        self._chunks = chunks

    async def astream(self, _messages):
        for chunk in self._chunks:
            yield chunk


class _FakePage:
    url = "https://example.com"

    async def title(self):
        return "Example"

    async def content(self):
        return "<html><body><main><h1>Example</h1></main></body></html>"

    async def evaluate(self, _script):
        return "[]"


class _FakeSnapshotFrame:
    def __init__(self, name, url, frame_path, elements=None, child_frames=None):
        self.name = name
        self.url = url
        self._frame_path = frame_path
        self._elements = elements or []
        self.child_frames = child_frames or []

    async def evaluate(self, _script):
        return json.dumps(self._elements)


class _FakeSnapshotPage:
    url = "https://example.com"

    def __init__(self, main_frame):
        self.main_frame = main_frame

    async def title(self):
        return "Example"


class _FakeDomPage(_FakePage):
    def __init__(
        self,
        *,
        structured: str = "",
        targeted: str = "",
        candidates=None,
        html: str = "<html><body><main><h1>Fallback</h1></main></body></html>",
    ):
        self._structured = structured
        self._targeted = targeted
        self._candidates = candidates or []
        self._html = html

    async def evaluate(self, script):
        text = str(script)
        if "__DOM_DATA_VIEW__" in text:
            return self._structured
        if "__TARGETED_DOM_SEGMENTS__" in text:
            return self._targeted
        return json.dumps(self._candidates)

    async def content(self):
        return self._html


class _FakeLocator:
    def __init__(self, text=""):
        self.click_calls = 0
        self.text = text
        self.fill_values = []
        self.selected_labels = []
        self.selected_values = []
        self.press_values = []

    async def click(self):
        self.click_calls += 1

    async def inner_text(self):
        return self.text

    async def fill(self, value):
        self.fill_values.append(value)

    async def press(self, value):
        self.text = value
        self.press_values.append(value)

    async def select_option(self, *, label=None, value=None):
        if label is not None:
            self.selected_labels.append(label)
        if value is not None:
            self.selected_values.append(value)


class _FakeFrameScope:
    def __init__(self):
        self.locator_calls = []
        self.locator_obj = _FakeLocator("Resolved text")

    def locator(self, selector):
        self.locator_calls.append(selector)
        return self.locator_obj

    def frame_locator(self, selector):
        self.locator_calls.append(f"frame:{selector}")
        return self

    def get_by_role(self, role, **kwargs):
        self.locator_calls.append(f"role:{role}:{kwargs.get('name', '')}")
        return self.locator_obj

    def get_by_text(self, value):
        self.locator_calls.append(f"text:{value}")
        return self.locator_obj

    def get_by_placeholder(self, value):
        self.locator_calls.append(f"placeholder:{value}")
        return self.locator_obj


class _FakeActionPage(_FakePage):
    def __init__(self):
        self.scope = _FakeFrameScope()
        self.goto_calls = []
        self.load_state_calls = []

    def frame_locator(self, selector):
        self.scope.locator_calls.append(f"frame:{selector}")
        return self.scope

    def locator(self, selector):
        self.scope.locator_calls.append(selector)
        return self.scope.locator_obj

    def get_by_role(self, role, **kwargs):
        self.scope.locator_calls.append(f"role:{role}:{kwargs.get('name', '')}")
        return self.scope.locator_obj

    def get_by_text(self, value):
        self.scope.locator_calls.append(f"text:{value}")
        return self.scope.locator_obj

    def get_by_placeholder(self, value):
        self.scope.locator_calls.append(f"placeholder:{value}")
        return self.scope.locator_obj

    async def goto(self, url):
        self.goto_calls.append(url)

    async def wait_for_load_state(self, state):
        self.load_state_calls.append(state)


class DomFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_classify_nl_intent_routes_operation_and_data_extraction(self):
        operation_model = _FakeModel(SimpleNamespace(content='{"intent":"operation"}', additional_kwargs={}))
        extract_model = _FakeModel(SimpleNamespace(content='{"intent":"data_extraction"}', additional_kwargs={}))

        with patch.object(DOM_FLOW_MODULE, "get_llm_model", return_value=operation_model):
            operation = await DOM_FLOW_MODULE.classify_nl_intent("帮我点击搜索按钮")
        with patch.object(DOM_FLOW_MODULE, "get_llm_model", return_value=extract_model):
            extraction = await DOM_FLOW_MODULE.classify_nl_intent("总结当前页面内容")

        self.assertEqual(operation, "operation")
        self.assertEqual(extraction, "data_extraction")

    async def test_classify_nl_intent_falls_back_to_operation_on_invalid_json(self):
        broken_model = _FakeModel(SimpleNamespace(content="not-json", additional_kwargs={}))

        with patch.object(DOM_FLOW_MODULE, "get_llm_model", return_value=broken_model):
            result = await DOM_FLOW_MODULE.classify_nl_intent("总结当前页面内容")

        self.assertEqual(result, "operation")

    async def test_build_full_dom_context_uses_structured_block_without_fallback_when_coverage_is_enough(self):
        structured = "\n".join(
            [
                "PAGE",
                "TABLE 1",
                "| Name | Price |",
                "| --- | --- |",
                "| iPhone 15 | $799 |",
                "FORM 1",
                '- input label="Search" name="q" value="iphone"',
                "LIST 1",
                "- item a",
            ]
        ) * 30
        page = _FakeDomPage(structured=structured, targeted="TARGETED_HEADINGS\n- iPhone 15")

        candidates, block, debug = await DOM_FLOW_MODULE.build_full_dom_context(page)

        self.assertEqual(candidates, [])
        self.assertIn("TABLE 1", block)
        self.assertIn("FORM 1", block)
        self.assertIn("LIST 1", block)
        self.assertNotIn("<!-- FALLBACK_RAW_HTML -->", block)
        self.assertEqual(debug["dom_mode"], "full")
        self.assertGreaterEqual(debug["coverage_score"], 0.42)

    async def test_serialize_structured_data_view_async_returns_table_form_and_list_sections(self):
        structured = "\n".join(
            [
                "PAGE",
                "TABLE 1",
                "| Name | Price |",
                "| --- | --- |",
                "| iPhone 15 | $799 |",
                "FORM 1",
                '- input label="Search" name="q" value="iphone"',
                "LIST 1",
                "- item a",
            ]
        )
        page = _FakeDomPage(structured=structured)

        result = await DOM_DATA_VIEW_MODULE.serialize_structured_data_view_async(page)

        self.assertIn("TABLE 1", result)
        self.assertIn("FORM 1", result)
        self.assertIn("LIST 1", result)

    async def test_build_full_dom_context_triggers_fallback_when_structured_is_too_short(self):
        page = _FakeDomPage(
            structured="TABLE 1\n| A |\n| --- |\n| B |",
            targeted="",
            html="<html><body>" + ("x" * 150000) + "</body></html>",
        )

        _candidates, block, debug = await DOM_FLOW_MODULE.build_full_dom_context(page)

        self.assertIn("FALLBACK_RAW_HTML", block)
        self.assertIn("OMITTED", block)
        self.assertTrue(debug["dom_truncated"])
        self.assertIn("structured_too_short", debug["fallback_trigger_reason"])

    async def test_prepare_dom_context_injects_full_mode_for_data_extraction(self):
        page = _FakeDomPage(
            structured="TABLE 1\n| Name | Price |\n| --- | --- |\n| iPhone 15 | $799 |" * 40,
            targeted="TARGETED_HEADINGS\n- Products",
        )

        with patch.object(
            DOM_FLOW_MODULE,
            "classify_nl_intent",
            new=AsyncMock(return_value="data_extraction"),
        ):
            intent, dom_mode, candidates, block, debug = await DOM_FLOW_MODULE.prepare_dom_context(
                page,
                "总结当前页面内容",
            )

        self.assertEqual(intent, "data_extraction")
        self.assertEqual(dom_mode, "full")
        self.assertEqual(candidates, [])
        self.assertIn("当前任务偏向数据获取", block)
        self.assertEqual(debug["intent"], "data_extraction")


class RPAReActAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_llm_preserves_whitespace_between_stream_chunks(self):
        response_text = 'await page.goto("https://github.com/trending?since=weekly")\n'
        stream_chunks = [
            SimpleNamespace(content="await", additional_kwargs={}),
            SimpleNamespace(content=" page", additional_kwargs={}),
            SimpleNamespace(content='.goto("https://github.com/trending?since=weekly")\n', additional_kwargs={}),
        ]

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeStreamingModel(stream_chunks),
        ):
            chunks = []
            async for chunk in ASSISTANT_MODULE.RPAReActAgent._stream_llm([]):
                chunks.append(chunk)

        self.assertEqual(chunks, [response_text])

    async def test_stream_llm_extracts_text_from_stream_content_blocks(self):
        response_text = (
            '{"thought":"task done","action":"done","code":"","description":"done","risk":"none","risk_reason":""}'
        )
        stream_chunks = [
            SimpleNamespace(
                content=[
                    {"type": "thinking", "thinking": "inspect the page"},
                    {"type": "text", "text": response_text},
                ],
                additional_kwargs={},
            ),
        ]

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeStreamingModel(stream_chunks),
        ):
            chunks = []
            async for chunk in ASSISTANT_MODULE.RPAReActAgent._stream_llm([]):
                chunks.append(chunk)

        self.assertEqual(chunks, [response_text])

    async def test_stream_llm_falls_back_to_stream_reasoning_content(self):
        response_text = (
            '{"thought":"task done","action":"done","code":"","description":"done","risk":"none","risk_reason":""}'
        )
        stream_chunks = [
            SimpleNamespace(
                content="",
                additional_kwargs={"reasoning_content": response_text},
            ),
        ]

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeStreamingModel(stream_chunks),
        ):
            chunks = []
            async for chunk in ASSISTANT_MODULE.RPAReActAgent._stream_llm([]):
                chunks.append(chunk)

        self.assertEqual(chunks, [response_text])

    async def test_stream_llm_extracts_text_from_content_blocks(self):
        response_text = (
            '{"thought":"task done","action":"done","code":"","description":"done","risk":"none","risk_reason":""}'
        )
        fake_response = SimpleNamespace(
            content=[
                {"type": "thinking", "thinking": "inspect the page"},
                {"type": "text", "text": response_text},
            ],
            additional_kwargs={},
        )

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeModel(fake_response),
        ):
            chunks = []
            async for chunk in ASSISTANT_MODULE.RPAReActAgent._stream_llm([]):
                chunks.append(chunk)

        self.assertEqual(chunks, [response_text])

    async def test_run_falls_back_to_reasoning_content_when_text_is_empty(self):
        response_text = (
            '{"thought":"task done","action":"done","code":"","description":"done","risk":"none","risk_reason":""}'
        )
        fake_response = SimpleNamespace(
            content="",
            additional_kwargs={"reasoning_content": response_text},
        )
        agent = ASSISTANT_MODULE.RPAReActAgent()

        with patch.object(
            ASSISTANT_MODULE,
            "get_llm_model",
            return_value=_FakeModel(fake_response),
        ), patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value={"url": "https://example.com", "title": "Example", "frames": []}),
        ):
            events = []
            async for event in agent.run(
                session_id="session-1",
                page=_FakePage(),
                goal="finish the task",
                existing_steps=[],
            ):
                events.append(event)

        self.assertEqual(
            [event["event"] for event in events[-2:]],
            ["agent_thought", "agent_done"],
        )

    async def test_react_agent_build_observation_lists_frames_and_collections(self):
        snapshot = {
            "url": "https://example.com",
            "title": "Example",
            "frames": [
                {
                    "frame_hint": "main document",
                    "frame_path": [],
                    "elements": [{"index": 1, "tag": "button", "role": "button", "name": "Search"}],
                    "collections": [],
                },
                {
                    "frame_hint": "iframe title=results",
                    "frame_path": ["iframe[title='results']"],
                    "elements": [{"index": 1, "tag": "a", "role": "link", "name": "Result A"}],
                    "collections": [{"kind": "search_results", "item_count": 2}],
                },
            ],
        }

        content = ASSISTANT_MODULE.RPAReActAgent._build_observation(snapshot, 0)

        self.assertIn("Frame: main document", content)
        self.assertIn("Frame: iframe title=results", content)
        self.assertIn("Collection: search_results (2 items)", content)

    async def test_react_agent_build_observation_lists_snapshot_v2_containers(self):
        snapshot = {
            "url": "https://example.com",
            "title": "Example",
            "frames": [],
            "actionable_nodes": [],
            "content_nodes": [],
            "containers": [
                {
                    "container_id": "table-1",
                    "frame_path": [],
                    "container_kind": "table",
                    "name": "合同列表",
                    "summary": "合同下载列表",
                    "child_actionable_ids": ["a-1", "a-2"],
                    "child_content_ids": ["c-1", "c-2"],
                }
            ],
        }

        content = ASSISTANT_MODULE.RPAReActAgent._build_observation(snapshot, 0)

        self.assertIn("Container: table 合同列表", content)
        self.assertIn("actionable=2", content)
        self.assertIn("content=2", content)

    async def test_react_agent_build_observation_includes_dom_context_block_for_full_mode(self):
        snapshot = {
            "url": "https://example.com",
            "title": "Example",
            "frames": [],
        }

        content = ASSISTANT_MODULE.RPAReActAgent._build_observation(
            snapshot,
            0,
            "full",
            "当前任务偏向数据获取。\n\nTABLE 1\n| Name | Price |",
            {"dom_mode": "full", "coverage_score": 0.88},
        )

        self.assertIn("当前页面 DOM 上下文（用于读取/提取数据）", content)
        self.assertIn("TABLE 1", content)
        self.assertIn('"dom_mode": "full"', content)

    async def test_react_agent_executes_structured_collection_action_with_frame_context(self):
        agent = ASSISTANT_MODULE.RPAReActAgent()
        page = _FakeActionPage()
        snapshot = {
            "url": "https://example.com",
            "title": "Example",
            "frames": [
                {
                    "frame_path": ["iframe[title='results']"],
                    "frame_hint": "iframe title=results",
                    "elements": [],
                    "collections": [
                        {
                            "kind": "repeated_items",
                            "frame_path": ["iframe[title='results']"],
                            "container_hint": {"locator": {"method": "css", "value": "main article.card"}},
                            "item_hint": {"role": "link", "locator": {"method": "css", "value": "h2 a"}},
                            "item_count": 2,
                            "items": [
                                {"index": 1, "tag": "a", "role": "link", "name": "Result A"},
                                {"index": 2, "tag": "a", "role": "link", "name": "Result B"},
                            ],
                        }
                    ],
                }
            ],
        }
        responses = [
            json.dumps(
                {
                    "thought": "click the first item",
                    "action": "execute",
                    "operation": "click",
                    "description": "点击列表中的第一个项目",
                    "target_hint": {"role": "link", "name": "item"},
                    "collection_hint": {"kind": "search_results"},
                    "ordinal": "first",
                    "risk": "none",
                    "risk_reason": "",
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "thought": "done",
                    "action": "done",
                    "description": "done",
                    "risk": "none",
                    "risk_reason": "",
                },
                ensure_ascii=False,
            ),
        ]

        async def fake_stream(_history, _model_config=None):
            yield responses.pop(0)

        agent._stream_llm = fake_stream

        with patch.object(
            ASSISTANT_MODULE,
            "build_page_snapshot",
            new=AsyncMock(return_value=snapshot),
        ):
            events = []
            async for event in agent.run(
                session_id="session-1",
                page=page,
                goal="点击列表中的第一个项目",
                existing_steps=[],
            ):
                events.append(event)

        step_done = next(event for event in events if event["event"] == "agent_step_done")
        self.assertEqual(page.scope.locator_calls[0], "frame:iframe[title='results']")
        self.assertEqual(
            json.loads(step_done["data"]["step"]["target"]),
            {
                "method": "collection_item",
                "collection": {"method": "css", "value": "main article.card"},
                "ordinal": "first",
                "item": {"method": "css", "value": "h2 a"},
            },
        )

class RPAAssistantFrameAwareSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_build_page_snapshot_v2_includes_actionable_content_and_containers(self):
        main = _FakeSnapshotFrame(
            name="main",
            url="https://example.com",
            frame_path=[],
            elements=[{"index": 1, "tag": "button", "role": "button", "name": "Search"}],
        )
        page = _FakeSnapshotPage(main)

        with patch.object(
            ASSISTANT_RUNTIME_MODULE,
            "_extract_frame_snapshot_v2",
            new=AsyncMock(
                return_value={
                    "actionable_nodes": [
                        {
                            "node_id": "act-1",
                            "frame_path": [],
                            "container_id": "table-1",
                            "role": "link",
                            "name": "ContractList20260411124156",
                            "action_kinds": ["click"],
                            "locator": {"method": "role", "role": "link", "name": "ContractList20260411124156"},
                            "locator_candidates": [
                                {
                                    "kind": "role",
                                    "selected": True,
                                    "locator": {
                                        "method": "role",
                                        "role": "link",
                                        "name": "ContractList20260411124156",
                                    },
                                }
                            ],
                            "validation": {"status": "ok"},
                            "bbox": {"x": 10, "y": 20, "width": 120, "height": 24},
                            "center_point": {"x": 70, "y": 32},
                            "is_visible": True,
                            "is_enabled": True,
                            "hit_test_ok": True,
                            "element_snapshot": {"tag": "a", "text": "ContractList20260411124156"},
                        }
                    ],
                    "content_nodes": [
                        {
                            "node_id": "content-1",
                            "frame_path": [],
                            "container_id": "table-1",
                            "semantic_kind": "cell",
                            "text": "已归档",
                            "bbox": {"x": 300, "y": 20, "width": 80, "height": 24},
                            "locator": {"method": "text", "value": "已归档"},
                            "element_snapshot": {"tag": "td", "text": "已归档"},
                        }
                    ],
                    "containers": [
                        {
                            "container_id": "table-1",
                            "frame_path": [],
                            "container_kind": "table",
                            "name": "合同列表",
                            "bbox": {"x": 0, "y": 0, "width": 800, "height": 600},
                            "summary": "合同下载列表",
                            "child_actionable_ids": ["act-1"],
                            "child_content_ids": ["content-1"],
                        }
                    ],
                }
            ),
        ):
            snapshot = await ASSISTANT_MODULE.build_page_snapshot(
                page,
                frame_path_builder=lambda frame: frame._frame_path,
            )

        self.assertIn("actionable_nodes", snapshot)
        self.assertIn("content_nodes", snapshot)
        self.assertIn("containers", snapshot)
        self.assertEqual(snapshot["actionable_nodes"][0]["locator"]["method"], "role")
        self.assertEqual(snapshot["content_nodes"][0]["semantic_kind"], "cell")
        self.assertEqual(snapshot["containers"][0]["container_kind"], "table")

    async def test_build_page_snapshot_includes_iframe_elements_and_collections(self):
        iframe = _FakeSnapshotFrame(
            name="editor",
            url="https://example.com/editor",
            frame_path=["iframe[title='editor']"],
            elements=[
                {"index": 1, "tag": "a", "role": "link", "name": "Quarterly Report"},
                {"index": 2, "tag": "a", "role": "link", "name": "Annual Report"},
            ],
        )
        main = _FakeSnapshotFrame(
            name="main",
            url="https://example.com",
            frame_path=[],
            elements=[{"index": 1, "tag": "button", "role": "button", "name": "Search"}],
            child_frames=[iframe],
        )
        page = _FakeSnapshotPage(main)

        snapshot = await ASSISTANT_MODULE.build_page_snapshot(
            page,
            frame_path_builder=lambda frame: frame._frame_path,
        )

        self.assertEqual(snapshot["title"], "Example")
        self.assertEqual(len(snapshot["frames"]), 2)
        self.assertEqual(snapshot["frames"][1]["frame_path"], ["iframe[title='editor']"])
        self.assertEqual(snapshot["frames"][1]["elements"][0]["name"], "Quarterly Report")
        self.assertEqual(snapshot["frames"][1]["collections"][0]["item_count"], 2)

    async def test_build_page_snapshot_skips_detached_child_frame(self):
        detached = _FakeSnapshotFrame(
            name="detached",
            url="https://example.com/detached",
            frame_path=["iframe[title='detached']"],
            elements=[{"index": 1, "tag": "a", "role": "link", "name": "Detached Link"}],
        )
        main = _FakeSnapshotFrame(
            name="main",
            url="https://example.com",
            frame_path=[],
            elements=[{"index": 1, "tag": "button", "role": "button", "name": "Search"}],
            child_frames=[detached],
        )
        page = _FakeSnapshotPage(main)

        async def flaky_frame_path_builder(frame):
            if frame is detached:
                raise RuntimeError("Frame.frame_element: Frame has been detached.")
            return frame._frame_path

        snapshot = await ASSISTANT_MODULE.build_page_snapshot(
            page,
            frame_path_builder=flaky_frame_path_builder,
        )

        self.assertEqual(len(snapshot["frames"]), 1)
        self.assertEqual(snapshot["frames"][0]["frame_path"], [])

    async def test_detect_collections_builds_structured_template_from_repeated_context(self):
        collections = ASSISTANT_RUNTIME_MODULE._detect_collections(
            [
                {"index": 1, "tag": "a", "role": "link", "name": "Skip to content", "href": "#start-of-content"},
                {
                    "index": 2,
                    "tag": "a",
                    "role": "link",
                    "name": "Item A",
                    "collection_container_selector": "main article.card",
                    "collection_item_selector": "h2 a",
                },
                {
                    "index": 3,
                    "tag": "a",
                    "role": "link",
                    "name": "Item B",
                    "collection_container_selector": "main article.card",
                    "collection_item_selector": "h2 a",
                },
            ],
            [],
        )

        self.assertGreaterEqual(len(collections), 1)
        self.assertEqual(collections[0]["kind"], "repeated_items")
        self.assertEqual(collections[0]["container_hint"]["locator"], {"method": "css", "value": "main article.card"})
        self.assertEqual(collections[0]["item_hint"]["locator"], {"method": "css", "value": "h2 a"})
        self.assertEqual(collections[0]["items"][0]["name"], "Item A")
        self.assertEqual(collections[0]["items"][1]["name"], "Item B")

    async def test_pick_first_item_uses_collection_scope_not_global_page_order(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": [],
                    "elements": [{"name": "Sidebar Link", "role": "link"}],
                    "collections": [],
                },
                {
                    "frame_path": ["iframe[title='results']"],
                    "elements": [],
                    "collections": [
                        {
                            "kind": "search_results",
                            "frame_path": ["iframe[title='results']"],
                            "container_hint": {"role": "list"},
                            "item_hint": {"role": "link"},
                            "items": [
                                {"name": "Result A", "role": "link"},
                                {"name": "Result B", "role": "link"},
                            ],
                        }
                    ],
                },
            ]
        }

        resolved = ASSISTANT_MODULE.resolve_collection_target(
            snapshot,
            {"action": "click", "ordinal": "first"},
        )

        self.assertEqual(resolved["frame_path"], ["iframe[title='results']"])
        self.assertEqual(resolved["resolved_target"]["name"], "Result A")

    async def test_sort_nodes_by_visual_position_orders_top_to_bottom_then_left_to_right(self):
        nodes = [
            {"node_id": "download-2", "name": "文件二", "bbox": {"x": 40, "y": 60, "width": 80, "height": 20}},
            {"node_id": "download-1", "name": "文件一", "bbox": {"x": 20, "y": 20, "width": 80, "height": 20}},
            {"node_id": "download-3", "name": "文件三", "bbox": {"x": 100, "y": 20, "width": 80, "height": 20}},
        ]

        ordered = ASSISTANT_RUNTIME_MODULE._sort_nodes_by_visual_position(nodes)

        self.assertEqual([node["name"] for node in ordered], ["文件一", "文件三", "文件二"])


class RPAAssistantStructuredExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_structured_intent_uses_bbox_order_for_first_match_in_single_pass(self):
        snapshot = {
            "frames": [],
            "actionable_nodes": [
                {
                    "node_id": "download-1",
                    "frame_path": [],
                    "container_id": "table-1",
                    "role": "link",
                    "name": "ContractList20260411124156",
                    "action_kinds": ["click"],
                    "locator": {"method": "text", "value": "ContractList20260411124156"},
                    "locator_candidates": [{"kind": "text", "selected": True, "locator": {"method": "text", "value": "ContractList20260411124156"}}],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                    "is_visible": True,
                    "is_enabled": True,
                    "bbox": {"x": 20, "y": 20, "width": 80, "height": 20},
                },
                {
                    "node_id": "download-2",
                    "frame_path": [],
                    "container_id": "table-1",
                    "role": "link",
                    "name": "ContractList20260411124157",
                    "action_kinds": ["click"],
                    "locator": {"method": "text", "value": "ContractList20260411124157"},
                    "locator_candidates": [{"kind": "text", "selected": True, "locator": {"method": "text", "value": "ContractList20260411124157"}}],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                    "is_visible": True,
                    "is_enabled": True,
                    "bbox": {"x": 20, "y": 60, "width": 80, "height": 20},
                },
            ],
            "content_nodes": [],
            "containers": [
                {
                    "container_id": "table-1",
                    "frame_path": [],
                    "container_kind": "table",
                    "name": "合同列表",
                    "bbox": {"x": 0, "y": 0, "width": 800, "height": 600},
                    "summary": "合同下载列表",
                    "child_actionable_ids": ["download-1", "download-2"],
                    "child_content_ids": [],
                }
            ],
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击第一个文件下载",
                "prompt": "点击第一个文件下载",
                "target_hint": {"role": "link", "name": "contractlist"},
                "ordinal": "first",
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["value"], "ContractList20260411124156")
        self.assertEqual(resolved["resolved"]["ordinal"], "first")
        self.assertNotIn("assistant_diagnostics", resolved["resolved"])

    async def test_resolve_structured_intent_prefers_snapshot_locator_bundle_for_actionable_node(self):
        snapshot = {
            "frames": [],
            "actionable_nodes": [
                {
                    "node_id": "download-1",
                    "frame_path": [],
                    "container_id": "table-1",
                    "role": "link",
                    "name": "ContractList20260411124156",
                    "action_kinds": ["click"],
                    "locator": {"method": "text", "value": "ContractList20260411124156"},
                    "locator_candidates": [
                        {
                            "kind": "role",
                            "selected": False,
                            "locator": {"method": "role", "role": "link", "name": "ContractList20260411124156"},
                        },
                        {
                            "kind": "text",
                            "selected": True,
                            "locator": {"method": "text", "value": "ContractList20260411124156"},
                        },
                    ],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                }
            ],
            "content_nodes": [],
            "containers": [],
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击第一个文件下载",
                "target_hint": {"role": "link", "name": "contractlist"},
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["method"], "text")
        self.assertTrue(resolved["resolved"]["locator_candidates"][1]["selected"])

    async def test_resolve_structured_intent_extract_text_prefers_content_nodes(self):
        snapshot = {
            "frames": [],
            "actionable_nodes": [
                {
                    "node_id": "button-1",
                    "frame_path": [],
                    "container_id": "card-1",
                    "role": "button",
                    "name": "复制标题",
                    "action_kinds": ["click"],
                    "locator": {"method": "role", "role": "button", "name": "复制标题"},
                    "locator_candidates": [
                        {
                            "kind": "role",
                            "selected": True,
                            "locator": {"method": "role", "role": "button", "name": "复制标题"},
                        }
                    ],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                }
            ],
            "content_nodes": [
                {
                    "node_id": "title-1",
                    "frame_path": [],
                    "container_id": "card-1",
                    "semantic_kind": "heading",
                    "role": "heading",
                    "text": "Quarterly Report",
                    "bbox": {"x": 20, "y": 20, "width": 200, "height": 24},
                    "locator": {"method": "text", "value": "Quarterly Report"},
                    "element_snapshot": {"tag": "h2", "text": "Quarterly Report"},
                }
            ],
            "containers": [],
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "extract_text",
                "description": "提取报表标题",
                "prompt": "提取报表标题",
                "target_hint": {"name": "report title"},
                "result_key": "report_title",
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["method"], "text")
        self.assertEqual(resolved["resolved"]["content_node"]["semantic_kind"], "heading")

    async def test_execute_structured_click_does_not_mark_local_expansion_in_single_pass_mode(self):
        page = _FakeActionPage()
        intent = {
            "action": "click",
            "description": "点击第一个文件下载",
            "prompt": "点击第一个文件下载",
            "resolved": {
                "frame_path": [],
                "locator": {"method": "text", "value": "ContractList20260411124156"},
                "locator_candidates": [
                    {
                        "kind": "text",
                        "selected": True,
                        "locator": {"method": "text", "value": "ContractList20260411124156"},
                    }
                ],
                "collection_hint": {},
                "item_hint": {},
                "ordinal": "first",
                "selected_locator_kind": "text",
            },
        }

        result = await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(page.scope.locator_calls[0], "text:ContractList20260411124156")
        self.assertNotIn("used_local_expansion", result["step"]["assistant_diagnostics"])

    async def test_execute_structured_click_uses_frame_locator_chain(self):
        page = _FakeActionPage()
        intent = {
            "action": "click",
            "description": "点击发送按钮",
            "prompt": "点击发送按钮",
            "resolved": {
                "frame_path": ["iframe[title='editor']"],
                "locator": {"method": "role", "role": "button", "name": "Send"},
                "locator_candidates": [
                    {
                        "kind": "role",
                        "selected": True,
                        "locator": {"method": "role", "role": "button", "name": "Send"},
                    }
                ],
                "selected_locator_kind": "role",
            },
        }

        result = await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(page.scope.locator_calls[0], "frame:iframe[title='editor']")
        self.assertEqual(result["step"]["frame_path"], ["iframe[title='editor']"])
        self.assertEqual(result["step"]["source"], "ai")
        self.assertEqual(
            result["step"]["target"],
            '{"method": "role", "role": "button", "name": "Send"}',
        )

    async def test_execute_structured_click_persists_adaptive_collection_target_for_first_collection_item(self):
        page = _FakeActionPage()
        intent = {
            "action": "click",
            "description": "点击第一个卡片项目",
            "prompt": "点击列表中的第一个项目",
            "resolved": {
                "frame_path": [],
                "locator": {"method": "role", "role": "link", "name": "Item A"},
                "locator_candidates": [
                    {
                        "kind": "role",
                        "selected": True,
                        "locator": {"method": "role", "role": "link", "name": "Item A"},
                    }
                ],
                "collection_hint": {
                    "kind": "repeated_items",
                    "container_hint": {"locator": {"method": "css", "value": "main article.card"}},
                },
                "item_hint": {"role": "link", "locator": {"method": "css", "value": "h2 a"}},
                "ordinal": "first",
                "selected_locator_kind": "role",
            },
        }

        result = await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(
            json.loads(result["step"]["target"]),
            {
                "method": "collection_item",
                "collection": {"method": "css", "value": "main article.card"},
                "ordinal": "first",
                "item": {"method": "css", "value": "h2 a"},
            },
        )
        self.assertEqual(result["step"]["collection_hint"]["kind"], "repeated_items")
        self.assertEqual(result["step"]["item_hint"]["locator"], {"method": "css", "value": "h2 a"})
        self.assertEqual(result["step"]["ordinal"], "first")

    async def test_execute_structured_navigate_uses_page_goto(self):
        page = _FakeActionPage()
        intent = {
            "action": "navigate",
            "description": "打开 GitHub Trending 页面",
            "prompt": "打开 GitHub Trending 页面",
            "value": "https://github.com/trending",
            "resolved": {
                "frame_path": [],
                "locator": None,
                "locator_candidates": [],
                "collection_hint": {},
                "item_hint": {},
                "ordinal": None,
                "selected_locator_kind": "navigate",
                "url": "https://github.com/trending",
            },
        }

        result = await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(page.goto_calls, ["https://github.com/trending"])
        self.assertEqual(page.load_state_calls, ["domcontentloaded"])
        self.assertEqual(result["step"]["action"], "navigate")
        self.assertEqual(result["step"]["url"], "https://github.com/trending")

    async def test_execute_structured_extract_text_persists_result_key(self):
        page = _FakeActionPage()
        intent = {
            "action": "extract_text",
            "description": "提取最近一条 issue 的标题",
            "prompt": "提取最近一条 issue 的标题",
            "result_key": "latest_issue_title",
            "resolved": {
                "frame_path": [],
                "locator": {"method": "role", "role": "link", "name": "Issue Title"},
                "locator_candidates": [
                    {
                        "kind": "role",
                        "selected": True,
                        "locator": {"method": "role", "role": "link", "name": "Issue Title"},
                    }
                ],
                "collection_hint": {},
                "item_hint": {},
                "ordinal": None,
                "selected_locator_kind": "role",
            },
        }

        result = await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(result["output"], "Resolved text")
        self.assertEqual(result["output_meta"]["output"], "Resolved text")
        self.assertEqual(result["output_meta"]["locator"]["method"], "role")
        self.assertEqual(result["step"]["action"], "extract_text")
        self.assertEqual(result["step"]["element_snapshot"]["output"], "Resolved text")
        self.assertEqual(result["step"]["result_key"], "latest_issue_title")

    async def test_resolve_structured_intent_prefers_collection_item_inside_iframe(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [{"index": 1, "tag": "a", "role": "link", "name": "Sidebar"}],
                    "collections": [],
                },
                {
                    "frame_path": ["iframe[title='results']"],
                    "frame_hint": "iframe title=results",
                    "elements": [],
                    "collections": [
                        {
                            "kind": "search_results",
                            "frame_path": ["iframe[title='results']"],
                            "container_hint": {"role": "list"},
                            "item_hint": {"role": "link"},
                            "item_count": 2,
                            "items": [
                                {"index": 1, "tag": "a", "role": "link", "name": "Result A"},
                                {"index": 2, "tag": "a", "role": "link", "name": "Result B"},
                            ],
                        }
                    ],
                },
            ]
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击第一个结果",
                "collection_hint": {"kind": "search_results"},
                "ordinal": "first",
            },
        )

        self.assertEqual(resolved["resolved"]["frame_path"], ["iframe[title='results']"])
        self.assertEqual(resolved["resolved"]["locator"]["method"], "role")
        self.assertEqual(resolved["resolved"]["locator"]["name"], "Result A")

    async def test_resolve_structured_intent_prefers_structured_collection_over_flat_links(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [
                        {"index": 1, "tag": "a", "role": "link", "name": "Skip to content", "href": "#start-of-content"},
                        {"index": 2, "tag": "a", "role": "link", "name": "Homepage", "href": "/"},
                        {"index": 3, "tag": "a", "role": "link", "name": "Item A"},
                        {"index": 4, "tag": "a", "role": "link", "name": "Item B"},
                    ],
                    "collections": [
                        {
                            "kind": "search_results",
                            "frame_path": [],
                            "container_hint": {"role": "list"},
                            "item_hint": {"role": "link"},
                            "item_count": 4,
                            "items": [
                                {"index": 1, "tag": "a", "role": "link", "name": "Skip to content", "href": "#start-of-content"},
                                {"index": 2, "tag": "a", "role": "link", "name": "Homepage", "href": "/"},
                                {"index": 3, "tag": "a", "role": "link", "name": "Item A"},
                                {"index": 4, "tag": "a", "role": "link", "name": "Item B"},
                            ],
                        },
                        {
                            "kind": "repeated_items",
                            "frame_path": [],
                            "container_hint": {"locator": {"method": "css", "value": "main article.card"}},
                            "item_hint": {"role": "link", "locator": {"method": "css", "value": "h2 a"}},
                            "item_count": 2,
                            "items": [
                                {"index": 3, "tag": "a", "role": "link", "name": "Item A"},
                                {"index": 4, "tag": "a", "role": "link", "name": "Item B"},
                            ],
                        },
                    ],
                }
            ]
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击列表中的第一个项目",
                "prompt": "点击列表中的第一个项目",
                "target_hint": {"role": "link", "name": "item"},
                "collection_hint": {"kind": "search_results"},
                "ordinal": "first",
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["name"], "Item A")
        self.assertEqual(resolved["resolved"]["collection_hint"]["kind"], "repeated_items")

    async def test_resolve_structured_intent_normalizes_first_ordinal_from_prompt(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [],
                    "collections": [
                        {
                            "kind": "repeated_items",
                            "frame_path": [],
                            "container_hint": {"locator": {"method": "css", "value": "main article.card"}},
                            "item_hint": {"role": "link", "locator": {"method": "css", "value": "h2 a"}},
                            "item_count": 2,
                            "items": [
                                {"index": 1, "tag": "a", "role": "link", "name": "Item A"},
                                {"index": 2, "tag": "a", "role": "link", "name": "Item B"},
                            ],
                        },
                    ],
                }
            ]
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击列表中的第一个项目",
                "prompt": "点击列表中的第一个项目",
                "target_hint": {"role": "link", "name": "item"},
                "collection_hint": {"kind": "search_results"},
                "ordinal": "25",
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["name"], "Item A")
        self.assertEqual(resolved["resolved"]["ordinal"], "first")

    async def test_resolve_structured_intent_falls_back_to_direct_target_when_collection_hint_has_no_match(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [
                        {"index": 1, "tag": "input", "role": "textbox", "name": "Search", "placeholder": "Search"}
                    ],
                    "collections": [],
                }
            ]
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "fill",
                "description": "在搜索框中输入关键词",
                "prompt": "在搜索框中输入关键词",
                "target_hint": {"role": "textbox", "name": "search"},
                "collection_hint": {"kind": "cards"},
                "ordinal": "1",
                "value": "github",
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["method"], "role")
        self.assertEqual(resolved["resolved"]["locator"]["name"], "Search")
        self.assertEqual(resolved["resolved"]["collection_hint"], {})

    async def test_resolve_structured_intent_prefers_placeholder_match_inside_iframe(self):
        snapshot = {
            "frames": [],
            "actionable_nodes": [
                {
                    "node_id": "purchase-date",
                    "frame_path": ["iframe[title='订单填写 iframe']"],
                    "role": "textbox",
                    "name": "采购时间",
                    "type": "date",
                    "action_kinds": ["fill"],
                    "locator": {"method": "role", "role": "textbox", "name": "采购时间"},
                    "locator_candidates": [
                        {
                            "kind": "role",
                            "selected": True,
                            "locator": {"method": "role", "role": "textbox", "name": "采购时间"},
                        }
                    ],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                },
                {
                    "node_id": "procurement-purpose",
                    "frame_path": ["iframe[title='订单填写 iframe']"],
                    "role": "textbox",
                    "name": "采购用途",
                    "placeholder": "可填写到货要求、型号说明等",
                    "tag": "textarea",
                    "action_kinds": ["fill"],
                    "locator": {"method": "placeholder", "value": "可填写到货要求、型号说明等"},
                    "locator_candidates": [
                        {
                            "kind": "placeholder",
                            "selected": True,
                            "locator": {"method": "placeholder", "value": "可填写到货要求、型号说明等"},
                        }
                    ],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                },
            ],
            "content_nodes": [],
            "containers": [],
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "fill",
                "description": "在当前 iframe 中的采购用途文本域中填写测试",
                "prompt": "采购用途，填写测试",
                "target_hint": {
                    "role": "textbox",
                    "placeholder": "可填写到货要求、型号说明等",
                },
                "value": "测试",
            },
        )

        self.assertEqual(resolved["resolved"]["frame_path"], ["iframe[title='订单填写 iframe']"])
        self.assertEqual(resolved["resolved"]["locator"]["method"], "placeholder")
        self.assertEqual(resolved["resolved"]["locator"]["value"], "可填写到货要求、型号说明等")

    async def test_execute_structured_fill_rejects_incompatible_date_value(self):
        page = _FakeActionPage()
        intent = {
            "action": "fill",
            "description": "填写采购时间",
            "prompt": "采购时间填写测试",
            "value": "测试",
            "resolved": {
                "frame_path": ["iframe[title='订单填写 iframe']"],
                "locator": {"method": "role", "role": "textbox", "name": "采购时间"},
                "locator_candidates": [
                    {
                        "kind": "role",
                        "selected": True,
                        "locator": {"method": "role", "role": "textbox", "name": "采购时间"},
                    }
                ],
                "collection_hint": {},
                "item_hint": {},
                "ordinal": None,
                "selected_locator_kind": "role",
                "actionable_node": {
                    "role": "textbox",
                    "name": "采购时间",
                    "type": "date",
                },
            },
        }

        with self.assertRaisesRegex(ValueError, "expects input type 'date'"):
            await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertEqual(page.scope.locator_obj.fill_values, [])

    async def test_execute_structured_fill_uses_select_option_for_native_select(self):
        page = _FakeActionPage()
        intent = {
            "action": "fill",
            "description": "选择物资类别",
            "prompt": "选择物资类别",
            "value": "办公设备",
            "resolved": {
                "frame_path": [],
                "locator": {"method": "role", "role": "combobox", "name": "物资类别"},
                "locator_candidates": [
                    {
                        "kind": "role",
                        "selected": True,
                        "locator": {"method": "role", "role": "combobox", "name": "物资类别"},
                    }
                ],
                "collection_hint": {},
                "item_hint": {},
                "ordinal": None,
                "selected_locator_kind": "role",
                "actionable_node": {
                    "role": "combobox",
                    "name": "物资类别",
                    "tag": "select",
                },
            },
        }

        result = await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(page.scope.locator_obj.selected_labels, ["办公设备"])
        self.assertEqual(page.scope.locator_obj.fill_values, [])

    async def test_execute_structured_fill_uses_fill_for_custom_combobox(self):
        page = _FakeActionPage()
        intent = {
            "action": "fill",
            "description": "选择紧急程度",
            "prompt": "选择紧急程度",
            "value": "普通",
            "resolved": {
                "frame_path": [],
                "locator": {"method": "role", "role": "combobox", "name": "紧急程度"},
                "locator_candidates": [
                    {
                        "kind": "role",
                        "selected": True,
                        "locator": {"method": "role", "role": "combobox", "name": "紧急程度"},
                    }
                ],
                "collection_hint": {},
                "item_hint": {},
                "ordinal": None,
                "selected_locator_kind": "role",
                "actionable_node": {
                    "role": "combobox",
                    "name": "紧急程度",
                    "tag": "input",
                },
            },
        }

        result = await ASSISTANT_MODULE.execute_structured_intent(page, intent)

        self.assertTrue(result["success"])
        self.assertEqual(page.scope.locator_calls, ["role:combobox:紧急程度", "role:option:普通"])
        self.assertEqual(page.scope.locator_obj.click_calls, 2)
        self.assertEqual(page.scope.locator_obj.fill_values, [])
        self.assertEqual(page.scope.locator_obj.selected_labels, [])

    async def test_resolve_structured_intent_uses_label_for_fallback_textarea_target(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": ["iframe[title='订单填写 iframe']"],
                    "frame_hint": "iframe title=订单填写 iframe",
                    "elements": [
                        {"index": 1, "tag": "input", "role": "textbox", "label": "采购人", "name": "采购人"},
                        {"index": 2, "tag": "input", "role": "textbox", "label": "采购时间", "name": "采购时间", "type": "date"},
                        {
                            "index": 3,
                            "tag": "textarea",
                            "role": "textbox",
                            "label": "采购用途",
                            "name": "采购用途",
                            "placeholder": "可填写到货要求、型号说明等",
                        },
                    ],
                    "collections": [],
                }
            ]
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击采购用途文本域以聚焦并准备输入",
                "prompt": "采购用途填写测试",
                "target_hint": {
                    "role": "textbox",
                    "name": "采购用途",
                },
                "ordinal": "first",
            },
        )

        self.assertEqual(resolved["resolved"]["frame_path"], ["iframe[title='订单填写 iframe']"])
        self.assertEqual(resolved["resolved"]["locator"]["method"], "role")
        self.assertEqual(resolved["resolved"]["locator"]["name"], "采购用途")

    async def test_resolve_structured_intent_rejects_low_confidence_generic_inputs(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": ["iframe[title='订单填写 iframe']"],
                    "frame_hint": "iframe title=订单填写 iframe",
                    "elements": [
                        {"index": 1, "tag": "input", "role": "textbox", "type": "text"},
                        {"index": 2, "tag": "input", "role": "textbox", "type": "date"},
                        {"index": 3, "tag": "input", "role": "textbox", "type": "text", "placeholder": "请输入供应商名称"},
                    ],
                    "collections": [],
                }
            ]
        }

        with self.assertRaisesRegex(ValueError, "No sufficiently specific target"):
            ASSISTANT_MODULE.resolve_structured_intent(
                snapshot,
                {
                    "action": "click",
                    "description": "点击采购用途文本域以聚焦并准备输入",
                    "prompt": "采购用途填写测试",
                    "target_hint": {
                        "role": "textbox",
                        "name": "采购用途",
                    },
                    "ordinal": "first",
                },
            )

    async def test_resolve_structured_intent_prefers_primary_collection_items_over_repeated_controls(self):
        snapshot = {
            "frames": [
                {
                    "frame_path": [],
                    "frame_hint": "main document",
                    "elements": [],
                    "collections": [
                        {
                            "kind": "repeated_items",
                            "frame_path": [],
                            "container_hint": {"locator": {"method": "css", "value": "main article.card"}},
                            "item_hint": {"role": "link", "locator": {"method": "css", "value": "div.actions a"}},
                            "item_count": 2,
                            "items": [
                                {"index": 1, "tag": "a", "role": "link", "name": "Star project A"},
                                {"index": 2, "tag": "a", "role": "link", "name": "Star project B"},
                            ],
                        },
                        {
                            "kind": "repeated_items",
                            "frame_path": [],
                            "container_hint": {"locator": {"method": "css", "value": "main article.card"}},
                            "item_hint": {"role": "link", "locator": {"method": "css", "value": "h2 a"}},
                            "item_count": 2,
                            "items": [
                                {"index": 3, "tag": "a", "role": "link", "name": "Project A"},
                                {"index": 4, "tag": "a", "role": "link", "name": "Project B"},
                            ],
                        },
                    ],
                }
            ]
        }

        resolved = ASSISTANT_MODULE.resolve_structured_intent(
            snapshot,
            {
                "action": "click",
                "description": "点击列表中的第一个项目链接",
                "prompt": "点击列表中的第一个项目",
                "target_hint": {"role": "link", "name": "project title link"},
                "collection_hint": {"kind": "search_results"},
                "ordinal": "first",
            },
        )

        self.assertEqual(resolved["resolved"]["locator"]["name"], "Project A")
        self.assertEqual(
            resolved["resolved"]["item_hint"]["locator"],
            {"method": "css", "value": "h2 a"},
        )


class RPAAssistantPromptFormattingTests(unittest.TestCase):
    def test_system_prompt_allows_direct_answer_for_data_extraction_dom_context(self):
        prompt = ASSISTANT_MODULE.SYSTEM_PROMPT

        self.assertIn("DOM Context For Data Extraction", prompt)
        self.assertIn("answer directly in natural language", prompt)

    def test_data_extraction_mode_uses_dedicated_system_prompt(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        messages = assistant._build_messages(
            "获取当前页面信息",
            [],
            {"frames": []},
            [],
            "full",
            "当前任务偏向数据获取。\n\nTABLE 1\n| 字段 | 值 |",
            {"dom_mode": "full", "coverage_score": 0.9},
        )

        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("RPA page-reading assistant", messages[0]["content"])
        self.assertIn("Summarize the current page directly in natural language", messages[0]["content"])

    def test_build_messages_lists_frames_and_collections(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        snapshot = {
            "frames": [
                {
                    "frame_hint": "main document",
                    "frame_path": [],
                    "elements": [{"index": 1, "tag": "button", "role": "button", "name": "Search"}],
                    "collections": [],
                },
                {
                    "frame_hint": "iframe title=results",
                    "frame_path": ["iframe[title='results']"],
                    "elements": [{"index": 1, "tag": "a", "role": "link", "name": "Result A"}],
                    "collections": [{"kind": "search_results", "item_count": 2}],
                },
            ]
        }

        messages = assistant._build_messages(
            "点击第一个结果",
            [],
            snapshot,
            [],
            "full",
            "当前任务偏向数据获取。\n\n- Result A\n- Result B",
            {"dom_mode": "full", "coverage_score": 0.77},
        )
        content = messages[-1]["content"]

        self.assertIn("Frame: main document", content)
        self.assertIn("Frame: iframe title=results", content)
        self.assertIn("Collection: search_results (2 items)", content)
        self.assertIn("## DOM Context For Data Extraction", content)
        self.assertIn("Result A", content)
        self.assertIn("## DOM Debug", content)

    def test_react_system_prompt_requires_explicit_extraction_before_done(self):
        prompt = ASSISTANT_MODULE.REACT_SYSTEM_PROMPT

        self.assertIn("For extraction tasks, use operation=extract_text", prompt)
        self.assertIn('"result_key": "short_ascii_snake_case_key_for_extracted_value"', prompt)
        self.assertIn("Do not mark the task done just because the data is visible on the page.", prompt)
        self.assertIn("Execute the extraction step first and return the extracted value.", prompt)


class RPAAssistantDirectAnswerTests(unittest.IsolatedAsyncioTestCase):
    def test_expand_mapping_fill_intent_splits_fields_across_controls(self):
        snapshot = {
            "frames": [],
            "actionable_nodes": [
                {
                    "node_id": "requester",
                    "frame_path": [],
                    "role": "textbox",
                    "name": "采购人",
                    "action_kinds": ["fill"],
                    "tag": "input",
                },
                {
                    "node_id": "purchase-date",
                    "frame_path": [],
                    "role": "textbox",
                    "name": "采购时间",
                    "action_kinds": ["fill"],
                    "tag": "input",
                    "type": "date",
                },
                {
                    "node_id": "category",
                    "frame_path": [],
                    "role": "combobox",
                    "name": "物资类别",
                    "action_kinds": ["fill"],
                    "tag": "select",
                },
            ],
            "content_nodes": [],
            "containers": [],
        }
        intent = {
            "action": "fill",
            "prompt": "把获取的数据填写到当前页面",
            "target_hint": {"role": "form"},
            "value": {
                "采购人": "张敏",
                "采购时间": "2026-04-20",
                "物资类别": "办公设备",
                "备注": "",
            },
        }

        expanded = ASSISTANT_MODULE._expand_mapping_fill_intent(snapshot, intent)

        self.assertEqual(len(expanded), 3)
        self.assertEqual(expanded[0]["target_hint"]["role"], "textbox")
        self.assertEqual(expanded[1]["target_hint"]["name"], "采购时间")
        self.assertEqual(expanded[2]["target_hint"]["role"], "combobox")
        self.assertEqual(expanded[2]["value"], "办公设备")

    def test_expand_mapping_fill_intent_accepts_select_action_kind(self):
        snapshot = {
            "frames": [],
            "actionable_nodes": [
                {
                    "node_id": "category",
                    "frame_path": [],
                    "role": "combobox",
                    "name": "物资类别",
                    "action_kinds": ["select"],
                    "tag": "select",
                },
            ],
            "content_nodes": [],
            "containers": [],
        }
        intent = {
            "action": "fill",
            "prompt": "把获取的数据填写到当前页面",
            "value": {"物资类别": "办公设备"},
        }

        expanded = ASSISTANT_MODULE._expand_mapping_fill_intent(snapshot, intent)

        self.assertEqual(len(expanded), 1)
        self.assertEqual(expanded[0]["target_hint"]["role"], "combobox")

    def test_extract_structured_intents_supports_multiple_json_objects(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        text = '\n\n'.join(
            [
                json.dumps(
                    {
                        "action": "fill",
                        "description": "填写采购人",
                        "target_hint": {"role": "textbox", "name": "采购人"},
                        "value": "张敏",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "action": "click",
                        "description": "点击保存订单",
                        "target_hint": {"role": "button", "name": "保存订单"},
                    },
                    ensure_ascii=False,
                ),
            ]
        )

        intents = assistant._extract_structured_intents(text)

        self.assertEqual(len(intents), 2)
        self.assertEqual(intents[0]["action"], "fill")
        self.assertEqual(intents[1]["action"], "click")

    async def test_execute_single_response_executes_multiple_structured_intents(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        page = _FakeActionPage()
        snapshot = {
            "frames": [],
            "actionable_nodes": [
                {
                    "node_id": "requester",
                    "frame_path": [],
                    "role": "textbox",
                    "name": "采购人",
                    "action_kinds": ["fill"],
                    "locator": {"method": "role", "role": "textbox", "name": "采购人"},
                    "locator_candidates": [
                        {
                            "kind": "role",
                            "selected": True,
                            "locator": {"method": "role", "role": "textbox", "name": "采购人"},
                        }
                    ],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                },
                {
                    "node_id": "save-order",
                    "frame_path": [],
                    "role": "button",
                    "name": "保存订单",
                    "action_kinds": ["click"],
                    "locator": {"method": "role", "role": "button", "name": "保存订单"},
                    "locator_candidates": [
                        {
                            "kind": "role",
                            "selected": True,
                            "locator": {"method": "role", "role": "button", "name": "保存订单"},
                        }
                    ],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                },
            ],
            "content_nodes": [],
            "containers": [],
        }
        response = '\n\n'.join(
            [
                json.dumps(
                    {
                        "action": "fill",
                        "description": "填写采购人",
                        "prompt": "把刚才获取的内容填写到当前页面的表单",
                        "target_hint": {"role": "textbox", "name": "采购人"},
                        "value": "张敏",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "action": "click",
                        "description": "点击保存订单按钮提交表单",
                        "prompt": "把刚才获取的内容填写到当前页面的表单",
                        "target_hint": {"role": "button", "name": "保存订单"},
                    },
                    ensure_ascii=False,
                ),
            ]
        )

        result, code, resolution = await assistant._execute_single_response(page, snapshot, response)

        self.assertTrue(result["success"])
        self.assertIsNone(code)
        self.assertIn("resolved_batch", resolution)
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(result["steps"][0]["action"], "fill")
        self.assertEqual(result["steps"][1]["action"], "click")
        self.assertEqual(page.scope.locator_calls[0], "role:textbox:采购人")
        self.assertEqual(page.scope.locator_calls[1], "role:button:保存订单")
        self.assertEqual(page.scope.locator_obj.fill_values, ["张敏"])

    async def test_execute_single_response_expands_mapping_fill_intent(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        page = _FakeActionPage()
        snapshot = {
            "frames": [],
            "actionable_nodes": [
                {
                    "node_id": "requester",
                    "frame_path": [],
                    "role": "textbox",
                    "name": "采购人",
                    "action_kinds": ["fill"],
                    "locator": {"method": "role", "role": "textbox", "name": "采购人"},
                    "locator_candidates": [
                        {"kind": "role", "selected": True, "locator": {"method": "role", "role": "textbox", "name": "采购人"}}
                    ],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                    "tag": "input",
                },
                {
                    "node_id": "category",
                    "frame_path": ["iframe[title='订单填写 iframe']"],
                    "role": "combobox",
                    "name": "物资类别",
                    "action_kinds": ["fill"],
                    "locator": {"method": "role", "role": "combobox", "name": "物资类别"},
                    "locator_candidates": [
                        {"kind": "role", "selected": True, "locator": {"method": "role", "role": "combobox", "name": "物资类别"}}
                    ],
                    "validation": {"status": "ok"},
                    "hit_test_ok": True,
                    "tag": "select",
                },
            ],
            "content_nodes": [],
            "containers": [],
        }
        response = json.dumps(
            {
                "action": "fill",
                "description": "将已获取的采购需求数据填写到当前订单填写表单中",
                "prompt": "把获取的数据填写到当前页面",
                "target_hint": {"role": "form"},
                "value": {
                    "采购人": "张敏",
                    "物资类别": "办公设备",
                },
            },
            ensure_ascii=False,
        )

        result, code, resolution = await assistant._execute_single_response(page, snapshot, response)

        self.assertTrue(result["success"])
        self.assertIsNone(code)
        self.assertEqual(len(result["steps"]), 2)
        self.assertIn("resolved_batch", resolution)
        self.assertEqual(page.scope.locator_calls[0], "role:textbox:采购人")
        self.assertEqual(page.scope.locator_calls[1], "frame:iframe[title='订单填写 iframe']")
        self.assertEqual(page.scope.locator_calls[2], "role:combobox:物资类别")
        self.assertEqual(page.scope.locator_obj.fill_values, ["张敏"])
        self.assertEqual(page.scope.locator_obj.selected_labels, ["办公设备"])

    async def test_execute_single_response_accepts_direct_answer_without_action(self):
        assistant = ASSISTANT_MODULE.RPAAssistant()
        result, code, resolution = await assistant._execute_single_response(
            _FakePage(),
            {"url": "https://example.com", "title": "Example", "frames": []},
            "当前页面是采购需求申请单页面，包含申请标题、基础信息区域，以及待补充的明细表格。",
        )

        self.assertTrue(result["success"])
        self.assertIn("采购需求申请单", result["output"])
        self.assertEqual(result["output_meta"], {})
        self.assertIsNone(code)
        self.assertIsNone(resolution)


if __name__ == "__main__":
    unittest.main()
