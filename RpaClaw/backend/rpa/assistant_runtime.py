from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .extracted_fields import parse_extracted_fields
from .assistant_snapshot_runtime import SNAPSHOT_V2_JS
from .frame_selectors import build_frame_path

RPA_VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
PLAYWRIGHT_RECORDER_RUNTIME_JS = (RPA_VENDOR_DIR / "playwright_recorder_runtime.js").read_text(encoding="utf-8")


EXTRACT_ELEMENTS_JS = r"""() => {
    const INTERACTIVE = 'a,button,input,textarea,select,[role=button],[role=link],[role=menuitem],[role=menuitemradio],[role=tab],[role=checkbox],[role=radio],[role=combobox],[role=listbox],[role=option],[contenteditable=true]';
    const ROLE_MAP = { A: 'link', BUTTON: 'button', TEXTAREA: 'textbox', SELECT: 'combobox' };
    const els = document.querySelectorAll(INTERACTIVE);
    const results = [];
    let index = 1;
    const seen = new Set();
    function cssEsc(s) {
        try { return CSS.escape(s); } catch (e) {
            return String(s || '').replace(/([\\"'\[\](){}|^$.*+?])/g, '\\$1');
        }
    }
    function charType(c) {
        if (c >= 'a' && c <= 'z') return 1;
        if (c >= 'A' && c <= 'Z') return 2;
        if (c >= '0' && c <= '9') return 3;
        return 4;
    }
    function isGuidLike(id) {
        if (!id) return false;
        let transitions = 0;
        for (let i = 1; i < id.length; i++) {
            if (charType(id[i - 1]) !== charType(id[i])) transitions++;
        }
        return transitions >= id.length / 4;
    }
    function labelForControl(el) {
        if (!el) return '';
        const aria = (el.getAttribute('aria-label') || '').trim();
        if (aria) return aria;
        const labelledBy = (el.getAttribute('aria-labelledby') || '').trim();
        if (labelledBy) {
            const text = labelledBy
                .split(/\s+/)
                .map(id => document.getElementById(id))
                .filter(Boolean)
                .map(node => (node.innerText || node.textContent || '').trim().replace(/\s+/g, ' '))
                .filter(Boolean)
                .join(' ');
            if (text) return text.substring(0, 120);
        }
        if (el.id) {
            try {
                const explicit = document.querySelector(`label[for="${cssEsc(el.id)}"]`);
                const text = (explicit?.innerText || explicit?.textContent || '').trim().replace(/\s+/g, ' ');
                if (text) return text.substring(0, 120);
            } catch (e) {}
        }
        const wrapper = el.closest('label');
        const wrappedText = (wrapper?.innerText || wrapper?.textContent || '').trim().replace(/\s+/g, ' ');
        if (wrappedText) return wrappedText.substring(0, 120);
        return '';
    }
    function getRole(el) {
        if (el.getAttribute('role')) return el.getAttribute('role');
        if (el.tagName === 'INPUT') {
            const type = (el.getAttribute('type') || 'text').toLowerCase();
            if (type === 'number' || type === 'range') return 'spinbutton';
            if (type === 'checkbox') return 'checkbox';
            if (type === 'radio') return 'radio';
            if (type === 'button' || type === 'submit' || type === 'reset') return 'button';
            return 'textbox';
        }
        return ROLE_MAP[el.tagName] || '';
    }
    function stableSelector(el) {
        const tag = el.tagName.toLowerCase();
        if (el.id && !isGuidLike(el.id)) return `${tag}#${cssEsc(el.id)}`;
        const classes = Array.from(el.classList || []).filter(cls => cls && !isGuidLike(cls)).slice(0, 2);
        if (classes.length) return `${tag}.${classes.map(cssEsc).join('.')}`;
        const role = el.getAttribute('role');
        if (role) return `${tag}[role="${cssEsc(role)}"]`;
        return tag;
    }
    function selectorIsUnique(sel) {
        try { return document.querySelectorAll(sel).length === 1; } catch (e) { return false; }
    }
    function buildRelativeSelector(ancestor, el) {
        const parts = [];
        let current = el;
        while (current && current !== ancestor) {
            parts.unshift(stableSelector(current));
            current = current.parentElement;
        }
        if (current !== ancestor) return '';
        return parts.join(' ');
    }
    function countSiblingMatches(el) {
        if (!el || !el.parentElement) return 0;
        const sel = stableSelector(el);
        let count = 0;
        for (const child of Array.from(el.parentElement.children)) {
            try {
                if (child.matches(sel)) count++;
            } catch (e) {}
        }
        return count;
    }
    function findUniqueAnchor(start) {
        let current = start;
        for (let depth = 0; current && current !== document.body && depth < 4; depth++, current = current.parentElement) {
            const sel = stableSelector(current);
            if (selectorIsUnique(sel)) return { node: current, selector: sel };
        }
        return null;
    }
    function findCollectionContext(el) {
        let repeatedRoot = null;
        let current = el.parentElement;
        for (let depth = 0; current && current !== document.body && depth < 6; depth++, current = current.parentElement) {
            if (countSiblingMatches(current) >= 2) {
                repeatedRoot = current;
                break;
            }
        }
        if (!repeatedRoot) return null;
        const itemSelector = buildRelativeSelector(repeatedRoot, el);
        if (!itemSelector) return null;
        const anchor = findUniqueAnchor(repeatedRoot.parentElement || repeatedRoot);
        let containerSelector = stableSelector(repeatedRoot);
        if (anchor && anchor.node !== repeatedRoot) {
            const relativeContainer = buildRelativeSelector(anchor.node, repeatedRoot);
            if (relativeContainer) containerSelector = `${anchor.selector} ${relativeContainer}`;
        }
        return {
            container_selector: containerSelector,
            item_selector: itemSelector,
            item_count: countSiblingMatches(repeatedRoot),
        };
    }
    for (const el of els) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;
        if (el.disabled) continue;
        const style = getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;

        const tag = el.tagName.toLowerCase();
        const role = getRole(el);
        const label = labelForControl(el);
        const name = (el.getAttribute('aria-label') || label || el.innerText || '').trim().replace(/\s+/g, ' ').substring(0, 80);
        const placeholder = el.getAttribute('placeholder') || '';
        const href = el.getAttribute('href') || '';
        const value = el.value || '';
        const type = el.getAttribute('type') || '';

        const key = tag + role + name + label + placeholder + href;
        if (seen.has(key)) continue;
        seen.add(key);

        const info = { index, tag };
        if (role) info.role = role;
        if (name) info.name = name;
        if (label) info.label = label;
        if (placeholder) info.placeholder = placeholder;
        if (href) info.href = href.substring(0, 120);
        if (value) info.value = value.substring(0, 80);
        if (type) info.type = type;
        const collection = findCollectionContext(el);
        if (collection) {
            info.collection_container_selector = collection.container_selector;
            info.collection_item_selector = collection.item_selector;
            info.collection_item_count = collection.item_count;
        }

        results.push(info);
        index++;
        if (results.length >= 80) break;
    }
    return JSON.stringify(results);
}"""


async def _extract_frame_elements(frame) -> List[Dict[str, Any]]:
    raw = await frame.evaluate(EXTRACT_ELEMENTS_JS)
    data = json.loads(raw) if isinstance(raw, str) else raw
    return data if isinstance(data, list) else []


async def _extract_frame_snapshot_v2(frame) -> Dict[str, Any]:
    try:
        ready = await frame.evaluate("() => !!globalThis.__rpaPlaywrightRecorder")
        if not ready:
            await frame.evaluate(PLAYWRIGHT_RECORDER_RUNTIME_JS)
        raw = await frame.evaluate(SNAPSHOT_V2_JS)
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        data = None
    if isinstance(data, dict):
        return {
            "actionable_nodes": list(data.get("actionable_nodes") or []),
            "content_nodes": list(data.get("content_nodes") or []),
            "containers": list(data.get("containers") or []),
            "page_blocks": list(data.get("page_blocks") or []),
            "field_pairs": list(data.get("field_pairs") or []),
        }

    elements = await _extract_frame_elements(frame)
    return {
        "actionable_nodes": [],
        "content_nodes": [],
        "containers": [],
        "page_blocks": [],
        "field_pairs": [],
        "elements": elements,
    }


def _is_detached_frame_error(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return "frame has been detached" in text or "frame was detached" in text


def _detect_collections(elements: List[Dict[str, Any]], frame_path: List[str]) -> List[Dict[str, Any]]:
    collections: List[Dict[str, Any]] = []
    grouped: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
    for element in elements:
        container_selector = element.get("collection_container_selector")
        item_selector = element.get("collection_item_selector")
        if not container_selector or not item_selector:
            continue
        grouped.setdefault((str(container_selector), str(item_selector)), []).append(element)

    for (container_selector, item_selector), items in grouped.items():
        if len(items) < 2:
            continue
        roles = {item.get("role") for item in items if item.get("role")}
        item_hint: Dict[str, Any] = {"locator": {"method": "css", "value": item_selector}}
        if len(roles) == 1:
            item_hint["role"] = next(iter(roles))
        collections.append(
            {
                "kind": "repeated_items",
                "frame_path": list(frame_path),
                "container_hint": {"locator": {"method": "css", "value": container_selector}},
                "item_hint": item_hint,
                "item_count": len(items),
                "items": items[:25],
            }
        )

    links = [el for el in elements if el.get("role") == "link" or el.get("tag") == "a"]
    if len(links) >= 2:
        collections.append(
            {
                "kind": "search_results",
                "frame_path": list(frame_path),
                "container_hint": {"role": "list"},
                "item_hint": {"role": "link"},
                "item_count": len(links),
                "items": links[:10],
            }
        )
    return collections


async def _resolve_frame_path(frame, frame_path_builder: Callable[[Any], Any]) -> List[str]:
    value = frame_path_builder(frame)
    if inspect.isawaitable(value):
        value = await value
    if not value:
        return []
    return list(value)


async def build_frame_path_from_frame(frame) -> List[str]:
    return await build_frame_path(frame)


async def build_page_snapshot(page, frame_path_builder: Callable[[Any], Any]) -> Dict[str, Any]:
    frames: List[Dict[str, Any]] = []
    actionable_nodes: List[Dict[str, Any]] = []
    content_nodes: List[Dict[str, Any]] = []
    containers: List[Dict[str, Any]] = []
    page_blocks: List[Dict[str, Any]] = []
    field_pairs: List[Dict[str, Any]] = []

    async def walk(frame) -> None:
        try:
            frame_path = await _resolve_frame_path(frame, frame_path_builder)
            snapshot_v2 = await _extract_frame_snapshot_v2(frame)
            elements = list(snapshot_v2.get("elements") or [])
            if not elements:
                elements = await _extract_frame_elements(frame)
        except Exception as exc:
            if _is_detached_frame_error(exc):
                return
            raise
        frame_actionable_nodes = [
            {
                **node,
                "frame_path": list(node.get("frame_path") or frame_path),
            }
            for node in list(snapshot_v2.get("actionable_nodes") or [])
        ]
        frame_content_nodes = [
            {
                **node,
                "frame_path": list(node.get("frame_path") or frame_path),
            }
            for node in list(snapshot_v2.get("content_nodes") or [])
        ]
        frame_containers = [
            {
                **container,
                "frame_path": list(container.get("frame_path") or frame_path),
            }
            for container in list(snapshot_v2.get("containers") or [])
        ]
        frame_page_blocks = [
            {
                **block,
                "frame_path": list(block.get("frame_path") or frame_path),
            }
            for block in list(snapshot_v2.get("page_blocks") or [])
        ]
        frame_field_pairs = [
            {
                **pair,
                "frame_path": list(pair.get("frame_path") or frame_path),
            }
            for pair in list(snapshot_v2.get("field_pairs") or [])
        ]
        collections = _detect_collections(elements, frame_path)
        frames.append(
            {
                "frame_path": frame_path,
                "url": getattr(frame, "url", ""),
                "frame_hint": "main document" if not frame_path else " -> ".join(frame_path),
                "elements": elements or frame_actionable_nodes,
                "collections": collections,
                "page_blocks": frame_page_blocks,
                "field_pairs": frame_field_pairs,
            }
        )
        actionable_nodes.extend(frame_actionable_nodes)
        content_nodes.extend(frame_content_nodes)
        containers.extend(frame_containers)
        page_blocks.extend(frame_page_blocks)
        field_pairs.extend(frame_field_pairs)
        for child in getattr(frame, "child_frames", []):
            await walk(child)

    await walk(page.main_frame)
    return {
        "url": page.url,
        "title": await page.title(),
        "frames": frames,
        "actionable_nodes": actionable_nodes,
        "content_nodes": content_nodes,
        "containers": containers,
        "page_blocks": page_blocks,
        "field_pairs": field_pairs,
    }


def resolve_collection_target(snapshot: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, Any]:
    ordinal = _normalize_ordinal(intent)
    if ordinal == "first":
        index = 0
    elif ordinal == "last":
        index = -1
    else:
        try:
            index = max(int(ordinal) - 1, 0)
        except Exception:
            index = 0

    candidates: List[Dict[str, Any]] = []
    for frame in snapshot.get("frames", []):
        for collection in frame.get("collections", []):
            items = collection.get("items", [])
            if not items:
                continue
            if index == -1:
                resolved_item = items[-1]
            else:
                resolved_item = items[min(index, len(items) - 1)]
            candidates.append(
                {
                    "frame_path": collection.get("frame_path", []),
                    "resolved_target": resolved_item,
                    "collection": collection,
                    "ordinal": ordinal,
                    "score": _collection_score(collection, intent),
                }
            )
    if not candidates:
        raise ValueError("No collection target matched")
    candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
    best = candidates[0]
    return {
        "frame_path": best["frame_path"],
        "resolved_target": best["resolved_target"],
        "collection": best["collection"],
        "ordinal": best["ordinal"],
    }


def _build_locator_candidates_for_element(element: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    role = element.get("role")
    name = element.get("name") or element.get("label")
    placeholder = element.get("placeholder")
    href = element.get("href")
    if role and name:
        candidates.append(
            {
                "kind": "role",
                "selected": True,
                "locator": {"method": "role", "role": role, "name": name},
            }
        )
    elif placeholder:
        candidates.append(
            {
                "kind": "placeholder",
                "selected": True,
                "locator": {"method": "placeholder", "value": placeholder},
            }
        )
    elif name:
        candidates.append(
            {
                "kind": "text",
                "selected": True,
                "locator": {"method": "text", "value": name},
            }
        )
    elif href:
        candidates.append(
            {
                "kind": "css",
                "selected": True,
                "locator": {"method": "css", "value": f"a[href*='{href}']"},
            }
        )
    else:
        candidates.append(
            {
                "kind": "css",
                "selected": True,
                "locator": {"method": "css", "value": element.get("tag", "*")},
            }
        )
    return candidates


def _field_pair_locator_bundle(field_pair: Dict[str, Any]) -> tuple[Dict[str, Any], List[Dict[str, Any]], str]:
    value_node = field_pair.get("value_node") or {}
    locator_candidates = list(value_node.get("locator_candidates") or [])
    locator = value_node.get("locator")
    if locator_candidates and locator:
        for candidate in locator_candidates:
            if candidate.get("selected"):
                return locator, locator_candidates, str(candidate.get("kind") or "")
        return locator, locator_candidates, str(locator_candidates[0].get("kind") or "")
    fallback_candidates = _build_locator_candidates_for_element(value_node)
    return _candidate_locator_payload(fallback_candidates), fallback_candidates, str(
        fallback_candidates[0].get("kind") or ""
    )


def _extract_candidate_locator_payload(candidate: Dict[str, Any]) -> Dict[str, Any]:
    payload = candidate.get("locator_payload")
    if isinstance(payload, dict) and payload:
        return payload
    payload = candidate.get("locator")
    return payload if isinstance(payload, dict) else {}


def _locator_payload_priority(payload: Dict[str, Any]) -> int:
    method = _normalize_hint(payload.get("method"))
    value = str(payload.get("value") or "")
    if method == "testid":
        return 100
    if method == "css":
        if value.startswith("#"):
            return 95
        if "[data-testid=" in value or "[name=" in value:
            return 90
        return 80
    if method == "label":
        return 70
    if method == "placeholder":
        return 60
    if method == "role":
        return 50
    if method == "text":
        return 10
    return 20


def _playwright_expression_for_locator(payload: Dict[str, Any], scope_var: str = "page") -> str:
    method = _normalize_hint(payload.get("method"))
    if method == "role":
        role = str(payload.get("role") or "button")
        name = str(payload.get("name") or "")
        if name:
            escaped_name = name.replace("\\", "\\\\").replace('"', '\\"')
            return f'{scope_var}.get_by_role("{role}", name="{escaped_name}")'
        return f'{scope_var}.get_by_role("{role}")'
    if method == "testid":
        value = str(payload.get("value") or "").replace("\\", "\\\\").replace('"', '\\"')
        return f'{scope_var}.get_by_test_id("{value}")'
    if method == "label":
        value = str(payload.get("value") or "").replace("\\", "\\\\").replace('"', '\\"')
        return f'{scope_var}.get_by_label("{value}")'
    if method == "placeholder":
        value = str(payload.get("value") or "").replace("\\", "\\\\").replace('"', '\\"')
        return f'{scope_var}.get_by_placeholder("{value}")'
    if method == "text":
        value = str(payload.get("value") or "").replace("\\", "\\\\").replace('"', '\\"')
        return f'{scope_var}.get_by_text("{value}")'
    value = str(payload.get("value") or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'{scope_var}.locator("{value}")'


def _candidate_description(payload: Dict[str, Any]) -> str:
    method = _normalize_hint(payload.get("method"))
    if method == "testid":
        return "按 data-testid 定位"
    if method == "css":
        value = str(payload.get("value") or "")
        if value.startswith("#"):
            return "按稳定 ID 定位"
        if "[data-testid=" in value:
            return "按容器 testid + 结构定位"
        if "[name=" in value:
            return "按 name 属性定位"
        return "按 CSS 结构定位"
    if method == "label":
        return "按标签定位"
    if method == "placeholder":
        return "按 placeholder 定位"
    if method == "role":
        return "按角色定位"
    if method == "text":
        return "按文本值兜底"
    return f"按 {method or 'locator'} 定位"


def _candidate_locator_payload(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    for candidate in candidates:
        if candidate.get("selected"):
            return candidate["locator"]
    return candidates[0]["locator"]


def _node_locator_bundle(node: Dict[str, Any]) -> tuple[Dict[str, Any], List[Dict[str, Any]], str]:
    locator_candidates = list(node.get("locator_candidates") or [])
    locator = node.get("locator")
    if locator_candidates and locator:
        for candidate in locator_candidates:
            if candidate.get("selected"):
                return locator, locator_candidates, str(candidate.get("kind") or "")
        return locator, locator_candidates, str(locator_candidates[0].get("kind") or "")
    fallback_candidates = _build_locator_candidates_for_element(node)
    return _candidate_locator_payload(fallback_candidates), fallback_candidates, str(fallback_candidates[0].get("kind") or "")


def _resolve_content_node(snapshot: Dict[str, Any], intent: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    nodes = list(snapshot.get("content_nodes", []))
    if not nodes:
        return None
    scored = [{"node": node, "score": _content_node_score(node, intent)} for node in nodes]
    scored.sort(key=lambda item: (-item["score"],) + _node_sort_key(item["node"]))
    if _intent_requests_ordinal(intent):
        max_score = scored[0]["score"]
        ordinal_pool = [item["node"] for item in scored if item["score"] >= max_score - 1]
        return _select_ordinal_node(ordinal_pool, intent)
    return scored[0]["node"]


def _field_pair_search_text(field_pair: Dict[str, Any]) -> str:
    container = field_pair.get("container") or {}
    label_node = field_pair.get("label_node") or {}
    value_node = field_pair.get("value_node") or {}
    bits = [
        field_pair.get("label_text"),
        field_pair.get("value_text"),
        (field_pair.get("relation") or {}).get("kind"),
        label_node.get("text"),
        value_node.get("text"),
        " ".join(container.get("class_tokens") or []),
        " ".join(label_node.get("class_tokens") or []),
        " ".join(value_node.get("class_tokens") or []),
    ]
    stable_values = []
    for node in (container, label_node, value_node):
        attrs = node.get("stable_attrs") or {}
        stable_values.extend(str(value) for value in attrs.values() if value)
    bits.extend(stable_values)
    return " ".join(str(bit or "") for bit in bits).lower()


def _field_pair_score(field_pair: Dict[str, Any], intent: Dict[str, Any]) -> int:
    target_hint = _coerce_target_hint(intent.get("target_hint"))
    expected_name = _normalize_hint(
        target_hint.get("label") or target_hint.get("name") or target_hint.get("text") or target_hint.get("value")
    )
    prompt = " ".join(
        part for part in [intent.get("prompt"), intent.get("description")] if isinstance(part, str) and part.strip()
    )
    prompt_tokens = _tokenize_text(prompt)
    expected_tokens = _tokenize_text(expected_name)
    haystack = _field_pair_search_text(field_pair)
    haystack_tokens = _tokenize_text(haystack)
    score = 0
    if expected_name:
        if expected_name in haystack:
            score += 10
        overlap = len(expected_tokens & haystack_tokens)
        score += min(overlap * 3, 12)
        if not overlap and expected_name not in haystack and expected_tokens:
            score -= 4
    prompt_overlap = len(prompt_tokens & haystack_tokens)
    score += min(prompt_overlap * 2, 6)
    relation_kind = _normalize_hint((field_pair.get("relation") or {}).get("kind"))
    if relation_kind in {"siblings_same_container", "description_list_pair", "same_row_cells"}:
        score += 4
    confidence = (field_pair.get("relation") or {}).get("confidence")
    if isinstance(confidence, (int, float)):
        score += int(round(float(confidence) * 4))
    value_node = field_pair.get("value_node") or {}
    stable_attrs = value_node.get("stable_attrs") or {}
    if stable_attrs.get("id") or stable_attrs.get("testid") or stable_attrs.get("name"):
        score += 5
    return score


def _resolve_field_pair(snapshot: Dict[str, Any], intent: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    pairs = list(snapshot.get("field_pairs") or [])
    if not pairs:
        return None
    scored = [{"field_pair": pair, "score": _field_pair_score(pair, intent)} for pair in pairs]
    scored.sort(
        key=lambda item: (
            -item["score"],
            int(((item["field_pair"].get("value_node") or {}).get("bbox") or {}).get("y", 0) or 0),
            int(((item["field_pair"].get("value_node") or {}).get("bbox") or {}).get("x", 0) or 0),
        )
    )
    best = scored[0]
    if best["score"] <= 0:
        return None
    return best["field_pair"]


def _node_sort_key(node: Dict[str, Any]) -> tuple[int, int]:
    bbox = node.get("bbox") or {}
    return int(bbox.get("y", 0) or 0), int(bbox.get("x", 0) or 0)


def _sort_nodes_by_visual_position(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(nodes, key=_node_sort_key)


def _annotate_visual_order(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered = _sort_nodes_by_visual_position(nodes)
    last_y: Optional[int] = None
    row_index = 0
    annotated: List[Dict[str, Any]] = []
    for node in ordered:
        bbox = node.get("bbox") or {}
        current_y = int(bbox.get("y", 0) or 0)
        if last_y is None or abs(current_y - last_y) > 8:
            row_index += 1
            last_y = current_y
        annotated.append({**node, "row_index": row_index})
    return annotated


def _normalize_hint(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _tokenize_text(value: Optional[str]) -> set[str]:
    if not value:
        return set()
    lowered = str(value).lower()
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", lowered)
        if len(token) >= 2 and not token.isdigit()
    }
    if any(term in lowered for term in ["项目", "条目", "事项"]):
        tokens.update({"item", "project"})
    if "结果" in lowered:
        tokens.update({"result", "item"})
    if "列表" in lowered:
        tokens.update({"list"})
    if any(term in lowered for term in ["标题", "名称"]):
        tokens.update({"title", "name"})
    if "链接" in lowered:
        tokens.update({"link"})
    if any(term in lowered for term in ["按钮", "操作"]):
        tokens.update({"button", "action", "control"})
    return tokens


def _selector_semantic_tokens(selector: Optional[str]) -> set[str]:
    if not selector:
        return set()
    lowered = str(selector).lower()
    tokens = _tokenize_text(lowered)
    if re.search(r"(^|[\s>+~(])h[1-6]($|[\s>+~.#:\[])", lowered):
        tokens.update({"heading", "title"})
    if re.search(r"(^|[\s>+~(])a($|[\s>+~.#:\[])", lowered):
        tokens.add("link")
    if re.search(r"(^|[\s>+~(])(button|input|select|textarea)($|[\s>+~.#:\[])", lowered) or "btn" in lowered:
        tokens.update({"button", "action", "control"})
    if "article" in lowered or ".card" in lowered or "[data-card" in lowered:
        tokens.update({"item", "card"})
    if re.search(r"(^|[\s>+~(])(li|dt|dd)($|[\s>+~.#:\[])", lowered) or "listitem" in lowered:
        tokens.update({"item", "list"})
    if re.search(r"(^|[\s>+~(])(tr|td|th)($|[\s>+~.#:\[])", lowered) or "row" in lowered:
        tokens.update({"row", "cell"})
    if any(term in lowered for term in ["action", "toolbar", "menu", "control", "icon"]):
        tokens.update({"action", "control"})
    return tokens


def _coerce_target_hint(raw: Any) -> Dict[str, Any]:
    """Normalize target_hint to a dict. Handles string shorthand from LLM responses."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        return {"text": raw}
    return {}


def _intent_semantic_tokens(intent: Dict[str, Any]) -> set[str]:
    target_hint = _coerce_target_hint(intent.get("target_hint"))
    parts = [
        target_hint.get("name"),
        target_hint.get("text"),
        target_hint.get("value"),
        intent.get("prompt"),
        intent.get("description"),
    ]
    tokens: set[str] = set()
    for part in parts:
        tokens.update(_tokenize_text(part))
    if target_hint.get("role"):
        tokens.update(_tokenize_text(str(target_hint.get("role"))))
    return tokens


def _sample_name_tokens(collection: Dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for item in (collection.get("items") or [])[:3]:
        tokens.update(_tokenize_text(item.get("name")))
    return tokens


def _average_item_name_length(collection: Dict[str, Any]) -> float:
    names = [str(item.get("name", "")).strip() for item in (collection.get("items") or [])[:5] if item.get("name")]
    if not names:
        return 0.0
    return sum(len(name) for name in names) / len(names)


def _semantic_collection_score(collection: Dict[str, Any], intent: Dict[str, Any]) -> int:
    intent_tokens = _intent_semantic_tokens(intent)
    if not intent_tokens:
        return 0

    container_hint = collection.get("container_hint", {}) or {}
    item_hint = collection.get("item_hint", {}) or {}
    selector_tokens = set()
    selector_tokens.update(_selector_semantic_tokens((container_hint.get("locator") or {}).get("value")))
    selector_tokens.update(_selector_semantic_tokens((item_hint.get("locator") or {}).get("value")))
    name_tokens = _sample_name_tokens(collection)

    score = min(len(intent_tokens & selector_tokens) * 2, 8)
    score += min(len(intent_tokens & name_tokens), 3)

    primary_item_requested = bool(intent_tokens & {"item", "result", "project", "repository", "entry", "record"})
    control_requested = bool(intent_tokens & {"button", "action", "control", "menu", "toggle"})
    title_requested = bool(intent_tokens & {"title", "heading", "headline", "name"})
    heading_collection = bool(selector_tokens & {"heading", "title"})
    control_collection = bool(selector_tokens & {"action", "control", "button"})

    if heading_collection and (primary_item_requested or title_requested):
        score += 4
    if control_collection and (primary_item_requested or title_requested) and not control_requested:
        score -= 3

    average_name_length = _average_item_name_length(collection)
    if average_name_length:
        if average_name_length <= 24 and (primary_item_requested or title_requested):
            score += 1
        elif average_name_length >= 40 and not control_requested:
            score -= 1

    return score


def _has_structural_template(collection: Dict[str, Any]) -> bool:
    container_hint = collection.get("container_hint", {}) or {}
    item_hint = collection.get("item_hint", {}) or {}
    return bool(container_hint.get("locator") and item_hint.get("locator"))


def _normalize_ordinal(intent: Dict[str, Any]) -> str:
    prompt_text = " ".join(
        part for part in [intent.get("prompt"), intent.get("description")] if isinstance(part, str) and part.strip()
    ).lower()
    if any(token in prompt_text for token in ["\u7b2c\u4e00\u4e2a", "\u9996\u4e2a", "\u7b2c1\u4e2a", "first", "1st"]):
        return "first"
    if any(token in prompt_text for token in ["\u6700\u540e\u4e00\u4e2a", "\u6700\u540e1\u4e2a", "last"]):
        return "last"
    ordinal = str(intent.get("ordinal", "first")).strip()
    return ordinal or "first"


def _intent_requests_ordinal(intent: Dict[str, Any]) -> bool:
    prompt_text = " ".join(
        part for part in [intent.get("prompt"), intent.get("description")] if isinstance(part, str) and part.strip()
    ).lower()
    if intent.get("ordinal") not in (None, ""):
        return True
    return any(token in prompt_text for token in ["\u7b2c\u4e00\u4e2a", "\u9996\u4e2a", "\u7b2c1\u4e2a", "\u6700\u540e\u4e00\u4e2a", "first", "last"])


def _select_ordinal_node(nodes: List[Dict[str, Any]], intent: Dict[str, Any]) -> Dict[str, Any]:
    ordered = _sort_nodes_by_visual_position(nodes)
    ordinal = _normalize_ordinal(intent)
    if not ordered:
        raise ValueError("No ordinal node candidates")
    if ordinal == "first":
        return ordered[0]
    if ordinal == "last":
        return ordered[-1]
    try:
        index = max(int(ordinal) - 1, 0)
    except Exception:
        index = 0
    return ordered[min(index, len(ordered) - 1)]


def _node_search_text(node: Dict[str, Any]) -> str:
    return " ".join(
        [
            str(node.get("name") or ""),
            str(node.get("text") or ""),
            str(node.get("label") or ""),
            str(node.get("title") or ""),
            str(node.get("placeholder") or ""),
            str(node.get("type") or ""),
        ]
    ).lower()


def _expected_target_hints(intent: Dict[str, Any]) -> Dict[str, str]:
    target_hint = _coerce_target_hint(intent.get("target_hint"))
    return {
        "role": _normalize_hint(target_hint.get("role")),
        "name": _normalize_hint(target_hint.get("name")),
        "text": _normalize_hint(target_hint.get("text")),
        "value": _normalize_hint(target_hint.get("value")),
        "placeholder": _normalize_hint(target_hint.get("placeholder")),
        "label": _normalize_hint(target_hint.get("label")),
        "title": _normalize_hint(target_hint.get("title")),
    }


def _score_node_against_hints(node: Dict[str, Any], intent: Dict[str, Any]) -> int:
    expected = _expected_target_hints(intent)
    actual = {
        "name": _normalize_hint(node.get("name")),
        "text": _normalize_hint(node.get("text")),
        "value": _normalize_hint(node.get("value")),
        "placeholder": _normalize_hint(node.get("placeholder")),
        "label": _normalize_hint(node.get("label")),
        "title": _normalize_hint(node.get("title")),
    }

    score = 0
    direct_weights = {
        "name": 12,
        "text": 10,
        "value": 8,
        "placeholder": 18,
        "label": 16,
        "title": 10,
    }
    cross_fields = ("name", "text", "label", "title", "placeholder", "value")

    for field, expected_value in expected.items():
        if field == "role" or not expected_value:
            continue
        matched = False
        direct_value = actual.get(field, "")
        if direct_value:
            if expected_value == direct_value:
                score += direct_weights.get(field, 8)
                matched = True
            elif expected_value in direct_value:
                score += max(direct_weights.get(field, 8) - 4, 3)
                matched = True
        if matched:
            continue
        for candidate_field in cross_fields:
            candidate_value = actual.get(candidate_field, "")
            if not candidate_value:
                continue
            if expected_value == candidate_value:
                score += 6
                matched = True
                break
            if expected_value in candidate_value:
                score += 3
                matched = True
                break
        if not matched:
            score -= 4

    freeform_expected = next(
        (
            expected[key]
            for key in ("name", "text", "label", "title", "placeholder", "value")
            if expected.get(key)
        ),
        "",
    )
    freeform_tokens = _tokenize_text(freeform_expected)
    node_search_text = _node_search_text(node)
    haystack_tokens = _tokenize_text(node_search_text)
    if freeform_expected:
        if freeform_expected in node_search_text:
            score += 6
        overlap = len(freeform_tokens & haystack_tokens)
        score += min(overlap * 2, 6)
        if overlap == 0 and freeform_expected not in node_search_text and freeform_tokens:
            score -= 2

    return score


def _is_fill_value_compatible(node: Dict[str, Any], value: Any) -> bool:
    input_type = _normalize_hint(node.get("type"))
    if not input_type:
        return True
    text = str(value or "").strip()
    if not text:
        return True

    if input_type == "date":
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", text))
    if input_type == "datetime-local":
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}t\d{2}:\d{2}(:\d{2})?", text.lower()))
    if input_type == "month":
        return bool(re.fullmatch(r"\d{4}-\d{2}", text))
    if input_type == "time":
        return bool(re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", text))
    if input_type in {"number", "range"}:
        return bool(re.fullmatch(r"[-+]?\d+(\.\d+)?", text))
    if input_type == "email":
        return "@" in text and "." in text.split("@")[-1]
    if input_type == "url":
        return text.startswith(("http://", "https://"))
    return True


def _fill_type_error(node: Dict[str, Any], value: Any) -> str:
    label = (
        node.get("label")
        or node.get("name")
        or node.get("placeholder")
        or node.get("title")
        or node.get("text")
        or "target field"
    )
    return (
        f"Resolved fill target '{label}' expects input type '{node.get('type')}', "
        f"but received incompatible value '{value}'."
    )


def _actionable_node_score(node: Dict[str, Any], intent: Dict[str, Any], action: str) -> Optional[int]:
    expected = _expected_target_hints(intent)
    expected_role = expected.get("role", "")
    node_role = _normalize_hint(node.get("role"))
    action_kinds = {str(kind).lower() for kind in (node.get("action_kinds") or [])}
    if action in {"click", "fill", "press"} and action_kinds:
        allowed_actions = set(action_kinds)
        if action == "fill" and "select" in allowed_actions:
            allowed_actions.add("fill")
        if action not in allowed_actions:
            return None
    if expected_role and node_role != expected_role:
        return None
    if action == "fill" and not _is_fill_value_compatible(node, intent.get("value", "")):
        return None

    score = 0
    if expected_role and node_role == expected_role:
        score += 5
    score += _score_node_against_hints(node, intent)
    validation = node.get("validation") or {}
    if validation.get("status") == "ok":
        score += 4
    elif validation.get("status"):
        score += 1
    if node.get("hit_test_ok"):
        score += 3
    if node.get("is_visible", True):
        score += 2
    if node.get("is_enabled", True):
        score += 1
    return score


def _resolve_actionable_node(snapshot: Dict[str, Any], intent: Dict[str, Any], action: str) -> Optional[Dict[str, Any]]:
    scored: List[Dict[str, Any]] = []
    for node in snapshot.get("actionable_nodes", []):
        score = _actionable_node_score(node, intent, action)
        if score is None:
            continue
        scored.append({"node": node, "score": score})

    if not scored:
        return None

    scored.sort(key=lambda item: (-item["score"],) + _node_sort_key(item["node"]))
    if _intent_requests_ordinal(intent):
        max_score = scored[0]["score"]
        ordinal_pool = [item["node"] for item in scored if item["score"] >= max_score - 1]
        return _select_ordinal_node(ordinal_pool, intent)
    return scored[0]["node"]


def _content_node_score(node: Dict[str, Any], intent: Dict[str, Any]) -> int:
    target_hint = _coerce_target_hint(intent.get("target_hint"))
    expected_name = _normalize_hint(target_hint.get("name") or target_hint.get("text") or target_hint.get("value"))
    expected_tokens = _tokenize_text(expected_name)
    haystack = " ".join(
        [
            str(node.get("text") or ""),
            str(node.get("semantic_kind") or ""),
            str(node.get("role") or ""),
        ]
    ).lower()
    haystack_tokens = _tokenize_text(haystack)
    score = 0
    if expected_name:
        if expected_name in haystack:
            score += 6
        score += min(len(expected_tokens & haystack_tokens) * 2, 6)
        if "title" in expected_tokens and _normalize_hint(node.get("semantic_kind")) in {"heading", "title"}:
            score += 3
    if node.get("bbox"):
        score += 1
    return score


def _collection_score(collection: Dict[str, Any], intent: Dict[str, Any]) -> int:
    score = 0
    collection_hint = intent.get("collection_hint", {}) or {}
    target_hint = _coerce_target_hint(intent.get("target_hint"))
    requested_kind = _normalize_hint(collection_hint.get("kind"))
    collection_kind = _normalize_hint(collection.get("kind"))
    requested_role = _normalize_hint(target_hint.get("role"))
    item_hint = collection.get("item_hint", {}) or {}
    item_role = _normalize_hint(item_hint.get("role"))

    if requested_kind and requested_kind == collection_kind:
        score += 3

    if requested_role and requested_role == item_role:
        score += 3

    if _has_structural_template(collection):
        score += 6

    score += min(int(collection.get("item_count", 0) or 0), 5)
    score += _semantic_collection_score(collection, intent)
    return score


def resolve_structured_intent(snapshot: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, Any]:
    action = _normalize_hint(intent.get("action"))
    collection_hint = intent.get("collection_hint", {}) or {}
    ordinal = intent.get("ordinal")

    if action == "navigate":
        return {
            **intent,
            "resolved": {
                "frame_path": [],
                "locator": None,
                "locator_candidates": [],
                "collection_hint": {},
                "item_hint": {},
                "ordinal": None,
                "selected_locator_kind": "navigate",
                "url": intent.get("value", ""),
            },
        }

    if ordinal or collection_hint:
        try:
            resolved = resolve_collection_target(snapshot, intent)
            item = resolved["resolved_target"]
            locator, locator_candidates, selected_locator_kind = _node_locator_bundle(item)
            collection = resolved["collection"]
            return {
                **intent,
                "resolved": {
                    "frame_path": resolved["frame_path"],
                    "locator": locator,
                    "locator_candidates": locator_candidates,
                    "collection_hint": {
                        "kind": collection.get("kind", ""),
                        "container_hint": collection.get("container_hint", {}),
                    },
                    "item_hint": collection.get("item_hint", {}),
                    "ordinal": resolved.get("ordinal"),
                    "selected_locator_kind": selected_locator_kind,
                },
            }
        except ValueError:
            pass

    if action == "extract_text":
        field_pair = _resolve_field_pair(snapshot, intent)
        if field_pair:
            locator, locator_candidates, selected_locator_kind = _field_pair_locator_bundle(field_pair)
            return {
                **intent,
                "resolved": {
                    "frame_path": list(field_pair.get("frame_path") or []),
                    "locator": locator,
                    "locator_candidates": locator_candidates,
                    "collection_hint": {},
                    "item_hint": {},
                    "ordinal": None,
                    "selected_locator_kind": selected_locator_kind,
                    "field_pair": field_pair,
                },
            }
        content_node = _resolve_content_node(snapshot, intent)
        if content_node:
            locator = content_node.get("locator") or {"method": "text", "value": content_node.get("text", "")}
            return {
                **intent,
                "resolved": {
                    "frame_path": list(content_node.get("frame_path") or []),
                    "locator": locator,
                    "locator_candidates": list(content_node.get("locator_candidates") or []),
                    "collection_hint": {},
                    "item_hint": {},
                    "ordinal": None,
                    "selected_locator_kind": str((content_node.get("locator_candidates") or [{}])[0].get("kind") or locator.get("method", "")),
                    "content_node": content_node,
                },
            }

    best_actionable = _resolve_actionable_node(snapshot, intent, action)
    if best_actionable:
        locator, locator_candidates, selected_locator_kind = _node_locator_bundle(best_actionable)
        return {
            **intent,
            "resolved": {
                "frame_path": list(best_actionable.get("frame_path") or []),
                "locator": locator,
                "locator_candidates": locator_candidates,
                "collection_hint": {},
                "item_hint": {},
                "ordinal": _normalize_ordinal(intent) if _intent_requests_ordinal(intent) else None,
                "selected_locator_kind": selected_locator_kind,
                "actionable_node": best_actionable,
            },
        }

    expected = _expected_target_hints(intent)
    expected_role = expected.get("role", "")
    best_match: Optional[Dict[str, Any]] = None
    best_frame_path: List[str] = []
    best_score: Optional[int] = None
    search_nodes = list(snapshot.get("actionable_nodes") or [])
    if search_nodes:
        for element in search_nodes:
            role = _normalize_hint(element.get("role"))
            if expected_role and role != expected_role:
                continue
            if action == "fill" and not _is_fill_value_compatible(element, intent.get("value", "")):
                continue
            score = _score_node_against_hints(element, intent)
            if best_score is None or score > best_score:
                best_match = element
                best_frame_path = list(element.get("frame_path") or [])
                best_score = score
    for frame in snapshot.get("frames", []):
        for element in frame.get("elements", []):
            role = _normalize_hint(element.get("role"))
            if expected_role and role != expected_role:
                continue
            if action == "fill" and not _is_fill_value_compatible(element, intent.get("value", "")):
                continue
            score = _score_node_against_hints(element, intent)
            if best_score is None or score > best_score:
                best_match = element
                best_frame_path = frame.get("frame_path", [])
                best_score = score

    if not best_match:
        raise ValueError("No frame-aware target matched the structured intent")
    if any(expected.get(key) for key in ("name", "text", "label", "title", "placeholder", "value")) and (
        best_score is None or best_score <= 0
    ):
        raise ValueError("No sufficiently specific target matched the structured intent")

    locator, locator_candidates, selected_locator_kind = _node_locator_bundle(best_match)
    return {
        **intent,
        "resolved": {
            "frame_path": best_frame_path,
            "locator": locator,
            "locator_candidates": locator_candidates,
            "collection_hint": {},
            "item_hint": {},
            "ordinal": None,
            "selected_locator_kind": selected_locator_kind,
            "actionable_node": best_match,
        },
    }


def _locator_from_payload(scope, payload):
    method = payload.get("method")
    if method == "role":
        kwargs = {"name": payload.get("name")} if payload.get("name") else {}
        return scope.get_by_role(payload["role"], **kwargs)
    if method == "text":
        return scope.get_by_text(payload["value"])
    if method == "placeholder":
        return scope.get_by_placeholder(payload["value"])
    return scope.locator(payload.get("value", ""))


async def _select_custom_combobox_value(scope, locator, value: Any) -> None:
    text_value = str(value)
    await locator.click()

    option_locator = scope.get_by_role("option", name=text_value)
    try:
        await option_locator.click()
        return
    except Exception:
        pass

    try:
        await locator.fill(text_value)
    except Exception:
        pass

    option_locator = scope.get_by_role("option", name=text_value)
    try:
        await option_locator.click()
        return
    except Exception:
        pass

    try:
        await locator.press("Enter")
        return
    except Exception as exc:
        raise ValueError(f"Unable to select combobox option '{text_value}'.") from exc


def _build_collection_item_payload(collection_hint: Dict[str, Any], item_hint: Dict[str, Any], ordinal: Optional[str]) -> Optional[Dict[str, Any]]:
    if not ordinal:
        return None
    container_locator = (collection_hint.get("container_hint") or {}).get("locator")
    item_locator = item_hint.get("locator")
    if not container_locator or not item_locator:
        return None

    return {
        "method": "collection_item",
        "collection": container_locator,
        "ordinal": ordinal,
        "item": item_locator,
    }


def _summarize_resolved_element(resolved: Dict[str, Any], output: Any = None) -> Dict[str, Any]:
    node = (
        (resolved.get("field_pair") or {}).get("value_node")
        or (resolved.get("field_pair") or {}).get("label_node")
        or {}
    ) or (
        resolved.get("content_node")
        or resolved.get("resolved_target")
        or resolved.get("actionable_node")
        or {}
    )
    if not isinstance(node, dict):
        node = {}

    summary: Dict[str, Any] = {}
    for key in ("tag", "role", "name", "text", "placeholder", "title", "semantic_kind", "type", "href", "value"):
        value = node.get(key)
        if value not in (None, "", []):
            summary[key] = value

    locator = resolved.get("locator")
    if locator:
        summary["locator"] = locator

    frame_path = resolved.get("frame_path") or []
    if frame_path:
        summary["frame_path"] = frame_path

    field_pair = resolved.get("field_pair")
    if isinstance(field_pair, dict):
        summary["field_pair"] = {
            "field_pair_id": field_pair.get("field_pair_id"),
            "label_text": field_pair.get("label_text"),
            "value_text": field_pair.get("value_text"),
            "relation": field_pair.get("relation") or {},
            "container": field_pair.get("container") or {},
        }

    if output not in (None, "", "ok", "None"):
        summary["output"] = output

    return summary


def _build_extracted_fields(intent: Dict[str, Any], resolved: Dict[str, Any], output: Any) -> List[Dict[str, Any]]:
    field_pair = resolved.get("field_pair")
    if isinstance(field_pair, dict):
        value_node = field_pair.get("value_node") or {}
        label_text = str(field_pair.get("label_text") or "").strip()
        value_text = str(output or field_pair.get("value_text") or "").strip()
        relation = field_pair.get("relation") or {}
        field_name = _coerce_target_hint(intent.get("target_hint")).get("name") or label_text or intent.get("result_key") or "value"
        normalized_name = parse_extracted_fields(
            value_text,
            locator=value_node.get("locator") if isinstance(value_node.get("locator"), dict) else {},
            frame_path=resolved.get("frame_path") or [],
            result_key=str(intent.get("result_key") or ""),
            hint_label=label_text,
        )
        if normalized_name:
            field = dict(normalized_name[0])
        else:
            field = {
                "name": str(field_name),
                "label": label_text or str(field_name),
                "content": value_text,
                "locator": value_node.get("locator") if isinstance(value_node.get("locator"), dict) else {},
                "frame_path": list(resolved.get("frame_path") or []),
                "source_result_key": str(intent.get("result_key") or ""),
            }
        field["locator"] = value_node.get("locator") if isinstance(value_node.get("locator"), dict) else field.get("locator", {})
        field["frame_path"] = list(resolved.get("frame_path") or [])
        field["relation"] = relation
        field["container"] = field_pair.get("container") or {}
        locator_candidates = list(value_node.get("locator_candidates") or [])
        if locator_candidates:
            normalized_candidates = []
            for candidate in locator_candidates[:4]:
                payload = candidate.get("locator")
                if not isinstance(payload, dict) or not payload:
                    continue
                normalized_candidates.append(
                    {
                        "kind": "playwright_locator",
                        "selected": bool(candidate.get("selected")),
                        "expression": _playwright_expression_for_locator(payload) + ".text_content()",
                        "description": _candidate_description(payload),
                        "locator_payload": payload,
                    }
                )
            if normalized_candidates:
                best_index = max(
                    range(len(normalized_candidates)),
                    key=lambda index: (
                        _locator_payload_priority(_extract_candidate_locator_payload(normalized_candidates[index])),
                        1 if normalized_candidates[index].get("selected") else 0,
                    ),
                )
                for index, candidate in enumerate(normalized_candidates):
                    candidate["selected"] = index == best_index
                best_payload = _extract_candidate_locator_payload(normalized_candidates[best_index])
                if best_payload:
                    field["locator"] = best_payload
                field["extract_candidates"] = normalized_candidates
        return [field]

    locator = resolved.get("locator") if isinstance(resolved.get("locator"), dict) else {}
    frame_path = resolved.get("frame_path") or []
    target_hint = _coerce_target_hint(intent.get("target_hint"))
    hint_label = str(target_hint.get("text") or target_hint.get("name") or "").strip()
    return parse_extracted_fields(
        output,
        locator=locator,
        frame_path=frame_path,
        result_key=str(intent.get("result_key") or ""),
        hint_label=hint_label,
    )


async def execute_structured_intent(page, intent: Dict[str, Any]) -> Dict[str, Any]:
    resolved = intent["resolved"]
    action = intent["action"]
    output = "ok"
    output_meta: Dict[str, Any] = {}
    if action == "navigate":
        url = resolved.get("url") or intent.get("value", "")
        await page.goto(url)
        await page.wait_for_load_state("domcontentloaded")
        step = {
            "action": "navigate",
            "source": "ai",
            "target": "",
            "url": url,
            "frame_path": [],
            "locator_candidates": [],
            "validation": {"status": "ok", "details": "assistant structured action"},
            "collection_hint": {},
            "item_hint": {},
            "ordinal": None,
            "assistant_diagnostics": {
                "resolved_frame_path": [],
                "selected_locator_kind": "navigate",
                "collection_kind": "",
            },
            "description": intent.get("description", action),
            "prompt": intent.get("prompt"),
            "value": intent.get("value"),
        }
        return {"success": True, "step": step, "output": output}

    frame_path = resolved.get("frame_path", [])
    scope = page
    for frame_selector in frame_path:
        scope = scope.frame_locator(frame_selector)

    locator_payload = resolved["locator"]
    locator = _locator_from_payload(scope, locator_payload)
    if action == "click":
        await locator.click()
    elif action == "extract_text":
        output = await locator.inner_text()
        output_meta = _summarize_resolved_element(resolved, output)
        extracted_fields = _build_extracted_fields(intent, resolved, output)
        if extracted_fields:
            output_meta["extracted_fields"] = extracted_fields
    elif action == "fill":
        fill_target = resolved.get("actionable_node") or resolved.get("resolved_target") or {}
        if isinstance(intent.get("value"), dict):
            raise ValueError("Structured fill action expects a scalar value, but received an object.")
        if fill_target and not _is_fill_value_compatible(fill_target, intent.get("value", "")):
            raise ValueError(_fill_type_error(fill_target, intent.get("value", "")))
        fill_value = intent.get("value", "")
        target_tag = _normalize_hint(fill_target.get("tag"))
        target_role = _normalize_hint(fill_target.get("role"))
        if target_tag == "select":
            try:
                await locator.select_option(label=str(fill_value))
            except Exception:
                await locator.select_option(value=str(fill_value))
        elif target_role == "combobox":
            await _select_custom_combobox_value(scope, locator, fill_value)
        else:
            await locator.fill(fill_value)
    elif action == "press":
        await locator.press(intent.get("value", "Enter"))
    else:
        raise ValueError(f"Unsupported action: {action}")

    step_target = _build_collection_item_payload(
        resolved.get("collection_hint", {}) or {},
        resolved.get("item_hint", {}) or {},
        resolved.get("ordinal"),
    ) or locator_payload

    step = {
        "action": action,
        "source": "ai",
        "target": json.dumps(step_target, ensure_ascii=False),
        "frame_path": frame_path,
        "locator_candidates": resolved.get("locator_candidates", []),
        "validation": {"status": "ok", "details": "assistant structured action"},
        "collection_hint": resolved.get("collection_hint", {}),
        "item_hint": resolved.get("item_hint", {}),
        "ordinal": resolved.get("ordinal"),
        "assistant_diagnostics": {
            **(resolved.get("assistant_diagnostics", {}) or {}),
            "resolved_frame_path": frame_path,
            "selected_locator_kind": resolved.get("selected_locator_kind", ""),
            "collection_kind": resolved.get("collection_hint", {}).get("kind", ""),
        },
        "element_snapshot": output_meta,
        "result_key": intent.get("result_key"),
        "extracted_fields": output_meta.get("extracted_fields", []),
        "description": intent.get("description", action),
        "prompt": intent.get("prompt"),
        "value": intent.get("value"),
    }
    return {"success": True, "step": step, "output": output, "output_meta": output_meta}
