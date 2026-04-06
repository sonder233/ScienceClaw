"""
ToolUniverse 工具元数据批量翻译脚本。

用法：
  cd RpaClaw/backend
  python -m scripts.translate_tools

功能：
  - 从 ToolUniverse 加载全部工具元数据
  - 按 batch 调用 LLM 翻译 description、参数描述、类别名
  - 增量翻译：已翻译的跳过
  - 结果写入 translations/tu_zh.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from langchain_openai import ChatOpenAI

TRANSLATIONS_DIR = Path(__file__).resolve().parent.parent / "translations"
OUTPUT_FILE = TRANSLATIONS_DIR / "tu_zh.json"
BATCH_SIZE = 15


def _get_llm() -> ChatOpenAI:
    api_key = os.environ.get("DS_API_KEY", "")
    base_url = os.environ.get("DS_URL", "")
    model = os.environ.get("DS_MODEL", "DeepSeekV3")
    if not api_key or not base_url:
        print("ERROR: DS_API_KEY and DS_URL must be set")
        sys.exit(1)
    return ChatOpenAI(model=model, base_url=base_url, api_key=api_key, max_tokens=8000, temperature=0.1)


def _load_existing() -> Dict[str, Any]:
    if OUTPUT_FILE.exists():
        return json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    return {}


def _save(data: Dict[str, Any]) -> None:
    TRANSLATIONS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_tool_info(tool: dict) -> dict:
    """Extract translatable fields from a tool dict."""
    name = tool.get("name", "")
    desc = tool.get("description", "")
    category = tool.get("category", "") or tool.get("type", "")
    params = tool.get("parameter", tool.get("parameters", {}))
    param_descs = {}
    if isinstance(params, dict):
        for pname, pinfo in params.get("properties", {}).items():
            if isinstance(pinfo, dict) and pinfo.get("description"):
                param_descs[pname] = pinfo["description"]
    return {"name": name, "description": desc, "category": category, "params": param_descs}


def _build_translate_prompt(tools_batch: List[dict]) -> str:
    """Build a prompt to translate a batch of tools."""
    items = []
    for t in tools_batch:
        item = {"name": t["name"], "description": t["description"]}
        if t["params"]:
            item["params"] = t["params"]
        if t["category"]:
            item["category"] = t["category"]
        items.append(item)

    return f"""你是一个专业的科学工具翻译助手。请将以下科学工具的元数据从英文翻译为中文。

翻译规则：
1. name 字段保持原样不翻译
2. description 翻译为流畅的中文，保留专业术语（如蛋白质、基因、药物等可用中文）
3. params 中的每个参数描述翻译为中文
4. category 翻译为简短的中文类别名
5. 保持 JSON 格式不变

输入：
{json.dumps(items, ensure_ascii=False, indent=2)}

请直接返回翻译后的 JSON 数组（不要添加 markdown 标记）："""


def _parse_llm_response(text: str) -> List[dict]:
    """Parse LLM response, handling markdown code blocks."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        start = 1 if lines[0].startswith("```") else 0
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[start:end])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("["):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
    return []


def main():
    print("Loading ToolUniverse...")
    from tooluniverse import ToolUniverse
    tu = ToolUniverse()
    tu.load_tools()
    print(f"Loaded {len(tu.all_tools)} tools")

    existing = _load_existing()
    tools_data = existing.get("tools", {})
    categories_data = existing.get("categories", {})

    all_tools_info = []
    for tool in tu.all_tools:
        if not isinstance(tool, dict):
            continue
        info = _extract_tool_info(tool)
        if info["name"] and info["name"] not in tools_data:
            all_tools_info.append(info)

    print(f"Already translated: {len(tools_data)}, Need translation: {len(all_tools_info)}")

    if not all_tools_info:
        print("Nothing to translate. Done.")
        return

    llm = _get_llm()
    total_batches = (len(all_tools_info) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_idx in range(total_batches):
        start = batch_idx * BATCH_SIZE
        batch = all_tools_info[start:start + BATCH_SIZE]
        print(f"\nBatch {batch_idx + 1}/{total_batches} ({len(batch)} tools)...")

        prompt = _build_translate_prompt(batch)
        try:
            response = llm.invoke(prompt)
            translated = _parse_llm_response(response.content)

            if not translated:
                print(f"  WARNING: Failed to parse response, skipping batch")
                continue

            for orig, trans in zip(batch, translated):
                name = orig["name"]
                tools_data[name] = {
                    "description": trans.get("description", orig["description"]),
                    "params": trans.get("params", {}),
                }
                cat = orig.get("category", "")
                if cat and cat not in categories_data and trans.get("category"):
                    categories_data[cat] = trans["category"]

            _save({"tools": tools_data, "categories": categories_data})
            print(f"  Translated {len(translated)} tools (total: {len(tools_data)})")

        except Exception as exc:
            print(f"  ERROR: {exc}")
            _save({"tools": tools_data, "categories": categories_data})
            time.sleep(2)
            continue

        time.sleep(0.5)

    print(f"\nDone! Translated {len(tools_data)} tools, {len(categories_data)} categories")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
