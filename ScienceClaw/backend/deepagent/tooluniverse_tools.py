"""
ToolUniverse LangChain 工具 — 直接在后端进程内调用 ToolUniverse SDK。

架构：
  - 复用 route/tooluniverse.py 中的 ToolUniverse 单例（懒加载，进程内共享）
  - 搜索和规格查询在本地直接执行（无网络开销）
  - 工具执行（tu.run）调用外部 API（UniProt、FAERS 等），在后端进程内完成

工具列表：
  - tooluniverse_search: 关键词搜索科学工具
  - tooluniverse_info:   查看工具规格（参数、描述、返回值）
  - tooluniverse_run:    执行科学工具
"""
from __future__ import annotations

import inspect
import json
import logging
from typing import Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


def _get_tu():
    """获取 ToolUniverse 单例（复用 route/tooluniverse.py 的实例）。"""
    from backend.route.tooluniverse import _get_tu as _route_get_tu
    return _route_get_tu()


# ── 对外暴露的 LangChain Tool ────────────────────────────────────────

@tool
def tooluniverse_search(query: str, limit: int = 10) -> dict:
    """Find available scientific tool NAMES in ToolUniverse by keyword.

    IMPORTANT: This only returns tool names and descriptions, NOT actual data.
    You MUST then call tooluniverse_run with the tool name to retrieve real data.

    Workflow: tooluniverse_search → tooluniverse_info → tooluniverse_run

    Args:
        query: Natural language description of the capability needed.
               Examples: "protein structure prediction", "drug safety analysis",
               "gene expression", "literature search for CRISPR"
        limit: Maximum number of results to return (default 10).

    Returns:
        A list of tool metadata (name + description). Use the name with tooluniverse_run to get data.
    """
    tu = _get_tu()
    if tu is None:
        return {"error": "ToolUniverse is still loading, please retry"}

    try:
        result = tu.run({
            "name": "Tool_Finder_Keyword",
            "arguments": {"description": query, "limit": limit},
        })
        if inspect.isawaitable(result):
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(result)
        if isinstance(result, list):
            return {"tools": result}
        return result if isinstance(result, dict) else {"result": result}
    except Exception as exc:
        logger.error(f"[ToolUniverse] search failed: {exc}")
        return {"error": str(exc)}


@tool
def tooluniverse_info(tool_name: str) -> dict:
    """Get the detailed specification of a ToolUniverse tool, including parameter schema, description, and return type.

    Use this BEFORE calling tooluniverse_run to understand the required and optional parameters.

    Args:
        tool_name: The exact ToolUniverse tool name (e.g. "UniProt_get_function_by_accession").

    Returns:
        Tool specification in OpenAI function format with name, description, and parameters.
    """
    tu = _get_tu()
    if tu is None:
        return {"error": "ToolUniverse is still loading, please retry"}

    try:
        spec = tu.tool_specification(tool_name, format="openai")
        return spec if spec else {"error": f"Tool not found: {tool_name}"}
    except Exception as exc:
        logger.error(f"[ToolUniverse] info failed: {exc}")
        return {"error": str(exc)}


@tool
def tooluniverse_run(tool_name: str, arguments: str) -> dict:
    """Execute a ToolUniverse scientific tool and return real data results.

    This is the tool that actually retrieves data. You MUST call this to get real results.
    Use tooluniverse_search to find the tool name, tooluniverse_info to check parameters,
    then call this to get the actual data.

    Args:
        tool_name: The exact ToolUniverse tool name from search results
                   (e.g. "FAERS_count_reactions_by_drug_event").
        arguments: JSON string of tool arguments
                   (e.g. '{"medicinalproduct": "aspirin"}').

    Returns:
        The actual data from the scientific database/API (not just metadata).
    """
    tu = _get_tu()
    if tu is None:
        return {"error": "ToolUniverse is still loading, please retry"}

    try:
        parsed_args = json.loads(arguments)
    except json.JSONDecodeError:
        return {"error": f"Invalid JSON in arguments: {arguments}"}

    try:
        result = tu.run({"name": tool_name, "arguments": parsed_args})
        if inspect.isawaitable(result):
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(result)

        if isinstance(result, list):
            text = json.dumps(result, ensure_ascii=False, default=str)
            if len(text) > 30000:
                return {"result": result[:100], "truncated": True}
            return {"result": result}
        if isinstance(result, dict):
            text = json.dumps(result, ensure_ascii=False, default=str)
            if len(text) > 30000:
                return {"result": result, "truncated": True}
            return result
        return {"result": result}
    except Exception as exc:
        logger.error(f"[ToolUniverse] run failed: {tool_name} - {exc}")
        return {"error": str(exc)}
