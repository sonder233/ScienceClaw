import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from backend.rpa.compat_mapper import to_legacy_step


def test_to_legacy_step_backfills_navigate_description_from_url():
    step = to_legacy_step(
        {
            "id": "action-1",
            "kind": "navigate",
            "locator": {"selector": ""},
            "input": {"url": "https://example.com/docs"},
            "snapshot": {},
            "pageAlias": "page",
        }
    )

    assert step["description"] == "导航到 https://example.com/docs"


def test_to_legacy_step_backfills_click_description_from_snapshot_name():
    step = to_legacy_step(
        {
            "id": "action-2",
            "kind": "click",
            "locator": {"selector": 'internal:role=button[name="搜索"]'},
            "snapshot": {
                "name": "搜索",
                "tag": "button",
            },
            "pageAlias": "page",
        }
    )

    assert step["description"] == "点击 搜索"
