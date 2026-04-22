import json
import re
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field


SCHEMA_ID_ATTR_NAMES = (
    "data-prop",
    "data-prop-id",
    "data-field",
    "data-field-id",
    "data-fieldid",
    "data-schema-id",
    "prop",
    "fieldid",
    "fieldname",
)

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_SESSION_PREFIXES = ("sess_", "tok_", "cmp_", "usr_", "row_", "rec_")


@dataclass
class PatternMatch:
    name: str
    confidence: float
    context: Dict[str, Any] = field(default_factory=dict)
    framework_hint: Optional[str] = None


@dataclass
class ExtractionCandidate:
    kind: str
    selected: bool = False
    expression: str = ""
    description: str = ""
    confidence: float = 0.0
    strategy_name: str = ""
    pattern_name: str = ""
    required_context: Dict[str, Any] = field(default_factory=dict)


def _normalize_whitespace(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _escape_js_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")


def _looks_like_schema_id(value: str) -> bool:
    """Return True if the attribute value looks like a stable per-field schema id
    rather than a per-row/per-session token."""
    if not value:
        return False
    text = value.strip()
    if len(text) < 3 or len(text) > 80:
        return False
    if re.search(r"\s", text):
        return False
    if _UUID_RE.match(text):
        return False
    lowered = text.lower()
    if any(lowered.startswith(prefix) for prefix in _SESSION_PREFIXES):
        return False
    return True


def _collect_attrs_from_snapshot(snapshot: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """Flatten all attr sources in the snapshot into (source, attr_name, value)."""
    bag: List[Tuple[str, str, str]] = []
    source_keys = ("containerAttrs", "valueAttrs", "labelAttrs", "attrs", "stable_attrs")
    for key in source_keys:
        attrs = snapshot.get(key)
        if not isinstance(attrs, dict):
            continue
        for name, value in attrs.items():
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            bag.append((key, str(name), text))
    return bag


def _extract_schema_id_candidates(snapshot: Dict[str, Any]) -> List[Dict[str, str]]:
    """Identify stable field-id attributes suitable as primary locators.

    Rules:
      - attr name matches a known schema-id key OR starts with `data-` (but
        excludes Vue scoped markers and testing attrs)
      - value passes `_looks_like_schema_id`
      - `for` on a label is excluded — that is handled by label_for_association
        strategy, not as a container anchor
      - labelAttrs source is skipped so label-only anchors don't masquerade
        as container schema ids
    """
    seen: set = set()
    candidates: List[Dict[str, str]] = []
    for source, name, value in _collect_attrs_from_snapshot(snapshot):
        if source == "labelAttrs":
            continue
        lname = name.lower()
        if lname == "for":
            continue
        if lname.startswith("data-v-"):
            continue
        is_known = lname in SCHEMA_ID_ATTR_NAMES
        is_schema_data = lname.startswith("data-") and not lname.startswith("data-test")
        if not (is_known or is_schema_data):
            continue
        if not _looks_like_schema_id(value):
            continue
        key = (name.lower(), value)
        if key in seen:
            continue
        seen.add(key)
        candidates.append({"attr": name, "value": value, "source": source})
    return candidates


def detect_ui_pattern(dom_info: Dict[str, Any]) -> List[PatternMatch]:
    patterns = []

    schema_id_candidates = dom_info.get("schema_id_candidates") or []
    if schema_id_candidates:
        patterns.append(PatternMatch(
            name="SCHEMA_ID_ATTR",
            confidence=0.99,
            context={"schema_id_candidates": schema_id_candidates},
        ))

    has_label_for = bool(dom_info.get("has_label_for"))
    has_form_container = bool(dom_info.get("has_form_container"))
    framework_classes = dom_info.get("framework_classes") or []
    
    if has_label_for and has_form_container:
        confidence = 0.95
        if any("aui" in c for c in framework_classes):
            confidence = 0.98
            framework_hint = "aui"
        elif any("el-" in c or "el-form" in c for c in framework_classes):
            confidence = 0.93
            framework_hint = "element-plus"
        elif any("ant-" in c for c in framework_classes):
            confidence = 0.94
            framework_hint = "ant-design"
        else:
            framework_hint = "standard-html"
        
        patterns.append(PatternMatch(
            name="FORM_FIELD_LABEL_VALUE",
            confidence=confidence,
            context={
                "has_label_for": True,
                "has_form_container": True,
            },
            framework_hint=framework_hint,
        ))
    
    if has_form_container and not has_label_for:
        patterns.append(PatternMatch(
            name="FORM_FIELD_LABEL_VALUE",
            confidence=0.82,
            context={
                "has_label_for": False,
                "has_form_container": True,
            },
            framework_hint=next((c.split("-")[0] for c in framework_classes if "-" in c), None),
        ))
    
    has_table_structure = dom_info.get("has_table_structure")
    if has_table_structure:
        patterns.append(PatternMatch(
            name="TABLE_CELL_PAIR",
            confidence=0.88,
            context={"has_table_structure": True},
        ))
    
    has_dl_structure = dom_info.get("has_dl_structure")
    if has_dl_structure:
        patterns.append(PatternMatch(
            name="DEFINITION_LIST",
            confidence=0.90,
            context={"has_dl_structure": True},
        ))
    
    if not patterns:
        patterns.append(PatternMatch(
            name="GENERIC_SIBLING",
            confidence=0.60,
            context={},
        ))
    
    patterns.sort(key=lambda p: p.confidence, reverse=True)
    return patterns


def build_semantic_extract_js(label: str, pattern: PatternMatch) -> ExtractionCandidate:
    escaped = _escape_js_string(label)

    if pattern.name == "SCHEMA_ID_ATTR":
        return _build_schema_id_extract_js(escaped, pattern)
    if pattern.name == "FORM_FIELD_LABEL_VALUE" and pattern.context.get("has_label_for"):
        return _build_label_for_extract_js(escaped, pattern)
    elif pattern.name == "FORM_FIELD_LABEL_VALUE":
        return _build_container_search_extract_js(escaped, pattern)
    elif pattern.name in ("TABLE_CELL_PAIR", "DEFINITION_LIST"):
        return _build_structured_pair_extract_js(escaped, pattern)
    else:
        return _build_generic_sibling_extract_js(escaped)


def _build_schema_id_extract_js(escaped_label: str, pattern: PatternMatch) -> ExtractionCandidate:
    raw_candidates = pattern.context.get("schema_id_candidates") or []
    if not raw_candidates:
        return _build_container_search_extract_js(escaped_label, pattern)

    attrs_json = json.dumps(
        [{"attr": c["attr"], "value": c["value"]} for c in raw_candidates],
        ensure_ascii=False,
    )
    primary_attr = raw_candidates[0]["attr"]

    js_code = f"""() => {{
    const candidates = {attrs_json};
    const targetLabel = '{escaped_label}';

    for (const c of candidates) {{
        const nodes = document.querySelectorAll(`[${{c.attr}}="${{c.value}}"]`);
        for (const node of nodes) {{
            if (_matchesLabel(node, targetLabel)) {{
                const value = _extractValueFromContainer(node, targetLabel);
                if (value) return value;
            }}
        }}
        for (const node of nodes) {{
            const value = _extractValueFromContainer(node, targetLabel);
            if (value) return value;
        }}
    }}
    return '';
}}

function _matchesLabel(container, target) {{
    if (!target) return true;
    const labelEl = container.querySelector(
        'label, [class*="__label"], [class*="field-header"], [class*="form-label"], legend, dt, th'
    );
    if (!labelEl) return false;
    const text = (labelEl.textContent || '').trim().replace(/^\\s*\\*\\s*/, '').replace(/[:：]$/, '');
    if (!text) return false;
    return text === target || text.endsWith(target) || text.includes(target);
}}

function _extractValueFromContainer(container, excludeLabel) {{
    const valueSelectors = [
        '[class*="display-only__content"]',
        'input:not([type="hidden"]):not([type="submit"]):not([type="button"])',
        'textarea',
        'select',
        '[contenteditable="true"]',
        'span[class*="display-only"]',
        '[class*="__content"]:not([class*="label"])',
        'output',
    ];
    for (const sel of valueSelectors) {{
        const els = container.querySelectorAll(sel);
        for (const el of els) {{
            if (_isInLabelArea(el, excludeLabel)) continue;
            const val = _getElValue(el);
            if (val) return val;
        }}
    }}
    const content = container.querySelector('[class*="content"], [class*="control"]');
    if (content) {{
        const text = (content.textContent || '').trim();
        if (text && text !== excludeLabel && !text.endsWith(excludeLabel)) return text;
    }}
    return '';
}}

function _isInLabelArea(el, label) {{
    let parent = el.parentElement;
    for (let i = 0; i < 5 && parent; i++) {{
        if (parent.matches && parent.matches('[class*="label"], label, legend')) {{
            const text = (parent.textContent || '').trim();
            if (!label || text.includes(label)) return true;
        }}
        parent = parent.parentElement;
    }}
    return false;
}}

function _getElValue(el) {{
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') {{
        return (el.value || '').trim();
    }}
    const text = (el.textContent || '').trim();
    return text && text.length < 500 ? text : '';
}}"""

    return ExtractionCandidate(
        kind="js_evaluate",
        selected=True,
        expression=js_code,
        description=f"schema-id 属性定位 (attr={primary_attr})",
        confidence=0.995,
        strategy_name="schema_id_attr",
        pattern_name="SCHEMA_ID_ATTR",
        required_context={
            "schema_attrs": [{"attr": c["attr"], "value": c["value"]} for c in raw_candidates],
        },
    )


def _build_label_for_extract_js(escaped_label: str, pattern: PatternMatch) -> ExtractionCandidate:
    js_code = f"""() => {{
    const targetLabel = '{escaped_label}';
    
    const allLabels = document.querySelectorAll('label[for]');
    let bestLabelEl = null;
    let bestScore = 0;
    
    for (const labelEl of allLabels) {{
        const textNodes = _getTextNodes(labelEl);
        for (const node of textNodes) {{
            const text = (node.textContent || '').trim();
            if (!text) continue;
            
            let score = 0;
            if (text === targetLabel) score = 3;
            else if (text.endsWith(targetLabel)) score = 2;
            else if (text.includes(targetLabel)) score = 1;
            
            if (score > bestScore) {{
                bestScore = score;
                bestLabelEl = labelEl;
            }}
            if (score >= 3) break;
        }}
        if (bestScore >= 3) break;
    }}
    
    if (!bestLabelEl || bestScore < 1) return '';
    
    const dataProp = bestLabelEl.getAttribute('for');
    if (!dataProp) return '';
    
    const container = document.querySelector(`[data-prop="${{dataProp}}"], [id="${{dataProp}}"], #${{dataProp}}`);
    if (!container) {{
        const input = document.getElementById(dataProp);
        if (input) return _extractValueFromElement(input);
        return '';
    }}
    
    return _extractValueFromContainer(container, targetLabel);
}}

function _getTextNodes(el) {{
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null, false);
    const nodes = [];
    while (walker.nextNode()) {{
        const text = (walker.currentNode.textContent || '').trim();
        if (text) nodes.push(walker.currentNode.parentElement || walker.currentNode);
    }}
    return nodes;
}}

function _extractValueFromContainer(container, excludeLabel) {{
    const valueSelectors = [
        'span[class*="display-only__content"]',
        'input:not([type="hidden"]):not([type="submit"]):not([type="button"])',
        'textarea',
        'select',
        '[contenteditable="true"]',
        '[class*="__content"]',
        '[class*="input"]',
        '[class*="value"]',
        '.aui-input-display-only__content',
        '.el-input__inner',
        '.ant-input',
        '.form-control',
    ];
    
    for (const selector of valueSelectors) {{
        const el = container.querySelector(selector);
        if (el && !_isLabelArea(el, excludeLabel)) {{
            return _extractValueFromElement(el);
        }}
    }}
    
    const contentArea = container.querySelector('[class*="content"], [class*="control"]');
    if (contentArea) {{
        const text = (contentArea.textContent || '').trim();
        if (text && text !== excludeLabel) return text;
    }}
    
    return '';
}}

function _isLabelArea(el, label) {{
    const parent = el.closest('[class*="label"], label, [role="presentation"]');
    if (parent) {{
        const text = (parent.textContent || '').trim();
        if (text.includes(label)) return true;
    }}
    return false;
}}

function _extractValueFromElement(el) {{
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') {{
        return el.value || '';
    }}
    return (el.textContent || '').trim();
}}"""

    return ExtractionCandidate(
        kind="js_evaluate",
        selected=True,
        expression=js_code,
        description=f"label[for] 关联定位 ({pattern.framework_hint or '通用'})",
        confidence=0.99,
        strategy_name="label_for_association",
        pattern_name=pattern.name,
        required_context={"has_label_for": True},
    )


def _build_container_search_extract_js(escaped_label: str, pattern: PatternMatch) -> ExtractionCandidate:
    js_code = f"""() => {{
    const targetLabel = '{escaped_label}';
    
    const containerSelectors = [
        '[class*="form-item"]',
        '[class*="field"]',
        '[class*="form-group"]',
        '.mb-3',
        'fieldset',
        '[data-prop]',
    ];
    
    for (const containerSel of containerSelectors) {{
        const containers = document.querySelectorAll(containerSel);
        
        for (const container of containers) {{
            const labelEl = container.querySelector(
                'label, [class*="label"], [class*="__label"], .field-header, legend'
            );
            
            if (!labelEl) continue;
            
            const labelText = (labelEl.textContent || '').trim();
            const matchScore = _calcMatchScore(labelText, targetLabel);
            
            if (matchScore < 1) continue;
            
            const value = _extractValueFromContainer(container, targetLabel);
            if (value) return value;
        }}
    }}
    
    return _fallbackToSiblingSearch(targetLabel);
}}

function _calcMatchScore(text, target) {{
    if (text === target) return 3;
    if (text.endsWith(target)) return 2;
    if (text.includes(target)) return 1;
    return 0;
}}

function _extractValueFromContainer(container, excludeLabel) {{
    const valueSelectors = [
        '[class*="__content"]',
        '[class*="control"]',
        'input:not([type="hidden"]):not([type="submit"])',
        'textarea',
        'select',
        '[contenteditable="true"]',
        'span[class*="display-only"]',
        'output',
    ];
    
    for (const selector of valueSelectors) {{
        const elements = container.querySelectorAll(selector);
        for (const el of elements) {{
            if (_isInLabelArea(el, excludeLabel)) continue;
            const val = _getElValue(el);
            if (val) return val;
        }}
    }}
    
    return '';
}}

function _isInLabelArea(el, label) {{
    let parent = el.parentElement;
    for (let i = 0; i < 5 && parent; i++) {{
        if (parent.matches && parent.matches('[class*="label"], label')) {{
            return (parent.textContent || '').includes(label);
        }}
        parent = parent.parentElement;
    }}
    return false;
}}

function _getElValue(el) {{
    if (el.value !== undefined) return el.value;
    const text = (el.textContent || '').trim();
    return text && text.length < 500 ? text : '';
}}

function _fallbackToSiblingSearch(label) {{
    const allElements = document.querySelectorAll('*');
    let bestEl = null;
    let bestScore = 0;
    
    for (const el of allElements) {{
        if (el.childElementCount > 0) continue;
        const text = (el.textContent || '').trim();
        const score = _calcMatchScore(text, label);
        if (score > bestScore) {{
            bestScore = score;
            bestEl = el;
        }}
    }}
    
    if (!bestEl || bestScore < 1) return '';
    
    const parent = bestEl.parentElement;
    if (!parent) return '';
    
    const children = Array.from(parent.children);
    const valueTags = ['STRONG', 'B', 'DD', 'TD', 'OUTPUT', 'SPAN', 'DIV', 'INPUT'];
    
    for (const child of children) {{
        if (child === bestEl) continue;
        if (valueTags.indexOf(child.tagName) >= 0) {{
            const val = _getElValue(child);
            if (val) return val;
        }}
    }}
    
    const idx = children.indexOf(bestEl);
    if (idx >= 0 && idx + 1 < children.length) {{
        return _getElValue(children[idx + 1]);
    }}
    
    return '';
}}"""

    return ExtractionCandidate(
        kind="js_evaluate",
        selected=False,
        expression=js_code,
        description=f"容器语义搜索 ({pattern.framework_hint or '通用'})",
        confidence=0.92,
        strategy_name="container_search",
        pattern_name=pattern.name,
        required_context={"has_form_container": True},
    )


def _build_structured_pair_extract_js(escaped_label: str, pattern: PatternMatch) -> ExtractionCandidate:
    if pattern.name == "TABLE_CELL_PAIR":
        pair_selector = "td, th"
        parent_selector = "tr"
    else:
        pair_selector = "dt, dd"
        parent_selector = "dl"
    
    js_code = f"""() => {{
    const targetLabel = '{escaped_label}';
    const parents = document.querySelectorAll('{parent_selector}');
    
    for (const parent of parents) {{
        const cells = parent.querySelectorAll('{pair_selector}');
        for (let i = 0; i < cells.length - 1; i++) {{
            const cellText = (cells[i].textContent || '').trim();
            if (cellText === targetLabel || cellText.endsWith(targetLabel)) {{
                return (cells[i + 1].textContent || '').trim();
            }}
        }}
    }}
    
    return '';
}}"""

    return ExtractionCandidate(
        kind="js_evaluate",
        selected=False,
        expression=js_code,
        description=f"{pattern.name.replace('_', ' ')} 结构化提取",
        confidence=0.88,
        strategy_name="structured_pair",
        pattern_name=pattern.name,
        required_context={"has_structured_pair": True},
    )


def _build_generic_sibling_extract_js(escaped_label: str) -> ExtractionCandidate:
    js_code = f"""() => {{
    var q='{escaped_label}',b=null,bs=0;
    for(var e of document.querySelectorAll('*')){{
        if(e.childElementCount)continue;
        var t=(e.innerText||e.textContent||'').trim();
        var s=t===q?2:t.indexOf(q)===0?1:0;
        if(s>bs){{bs=s;b=e;}}
    }}
    if(!b)return'';
    var p=b.parentElement,cs=p?Array.from(p.children):[];
    var VT=['STRONG','B','DD','TD','OUTPUT'];
    for(var c of cs){{if(c!==b&&VT.indexOf(c.tagName)>=0)return(c.innerText||c.textContent||'').trim();}}
    var i=cs.indexOf(b);
    if(i>=0&&i+1<cs.length)return(cs[i+1].innerText||cs[i+1].textContent||'').trim();
    var n=p&&p.nextElementSibling;
    return n?(n.innerText||n.textContent||'').trim():'';
}}"""

    return ExtractionCandidate(
        kind="js_evaluate",
        selected=False,
        expression=js_code,
        description="通用兄弟节点降级",
        confidence=0.75,
        strategy_name="generic_sibling",
        pattern_name="GENERIC_SIBLING",
    )


def build_enhanced_extract_candidates(
    label: str,
    dom_info: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    candidates: List[ExtractionCandidate] = []

    if dom_info:
        patterns = detect_ui_pattern(dom_info)
        seen_expressions: set = set()
        for pattern in patterns:
            candidate = build_semantic_extract_js(label, pattern)
            if candidate.expression in seen_expressions:
                continue
            seen_expressions.add(candidate.expression)
            candidates.append(candidate)

    generic_candidate = _build_generic_sibling_extract_js(_escape_js_string(label))
    if not any(c.expression == generic_candidate.expression for c in candidates):
        candidates.append(generic_candidate)

    xpath_candidate = ExtractionCandidate(
        kind="playwright_locator",
        selected=False,
        expression=_build_xpath_selector(label),
        description="Playwright XPath 定位器",
        confidence=0.80,
        strategy_name="xpath_fallback",
        pattern_name="universal",
    )

    if dom_info is not None:
        ordered = _verify_and_rerank(candidates, dom_info)
    else:
        ordered = candidates

    insert_index = 1 if ordered else 0
    ordered.insert(insert_index, xpath_candidate)

    for i, candidate in enumerate(ordered):
        candidate.selected = (i == 0)

    return [_candidate_to_dict(c) for c in ordered]


def verify_candidate_against_snapshot(
    candidate: ExtractionCandidate | Dict[str, Any],
    snapshot: Dict[str, Any] | None,
) -> bool:
    """Return True when the snapshot provides the anchors the candidate needs.

    Static check — does not execute the candidate JS. Purpose is to flag
    candidates whose required anchors are absent from the captured DOM, so we
    can demote them before writing to the skill file.
    """
    if not snapshot:
        return True

    if isinstance(candidate, ExtractionCandidate):
        strategy = candidate.strategy_name
        required = candidate.required_context or {}
    else:
        strategy = candidate.get("strategy_name", "")
        required = candidate.get("required_context") or {}

    if strategy == "schema_id_attr":
        required_attrs = required.get("schema_attrs") or []
        if not required_attrs:
            return False
        present_pairs: set = set()
        for item in snapshot.get("schema_id_candidates") or []:
            if isinstance(item, dict):
                present_pairs.add((str(item.get("attr", "")).lower(), str(item.get("value", ""))))
        for _, name, value in _collect_attrs_from_snapshot(snapshot):
            present_pairs.add((name.lower(), value))
        for item in required_attrs:
            key = (str(item.get("attr", "")).lower(), str(item.get("value", "")))
            if key in present_pairs:
                return True
        return False

    if strategy == "label_for_association":
        if snapshot.get("has_label_for") is not None:
            return bool(snapshot.get("has_label_for"))
        return bool(analyze_dom_for_pattern(snapshot).get("has_label_for"))

    if strategy == "container_search":
        if snapshot.get("has_form_container") is not None:
            return bool(snapshot.get("has_form_container"))
        return bool(analyze_dom_for_pattern(snapshot).get("has_form_container"))

    if strategy == "structured_pair":
        if snapshot.get("has_table_structure") is not None or snapshot.get("has_dl_structure") is not None:
            return bool(snapshot.get("has_table_structure") or snapshot.get("has_dl_structure"))
        info = analyze_dom_for_pattern(snapshot)
        return bool(info.get("has_table_structure") or info.get("has_dl_structure"))

    return True


def _verify_and_rerank(
    candidates: List[ExtractionCandidate],
    snapshot: Dict[str, Any],
) -> List[ExtractionCandidate]:
    verified: List[ExtractionCandidate] = []
    demoted: List[ExtractionCandidate] = []
    for candidate in candidates:
        if verify_candidate_against_snapshot(candidate, snapshot):
            verified.append(candidate)
        else:
            candidate.selected = False
            candidate.confidence = min(candidate.confidence, 0.5)
            if "锚点缺失" not in candidate.description:
                candidate.description = f"{candidate.description} (锚点缺失，已降权)"
            demoted.append(candidate)

    verified.sort(key=lambda c: c.confidence, reverse=True)
    demoted.sort(key=lambda c: c.confidence, reverse=True)
    return verified + demoted


def _candidate_to_dict(candidate: ExtractionCandidate) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "kind": candidate.kind,
        "selected": candidate.selected,
        "expression": candidate.expression,
        "description": candidate.description,
        "confidence": candidate.confidence,
        "strategy_name": candidate.strategy_name,
        "pattern_name": candidate.pattern_name,
    }
    if candidate.required_context:
        payload["required_context"] = candidate.required_context
    return payload


def _build_xpath_selector(label: str) -> str:
    escaped = label.replace("\\", "\\\\").replace('"', '\\"')
    return f'xpath=//*[normalize-space(.)="{escaped}"]/following-sibling::*[1]'


def analyze_dom_for_pattern(element_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    classes = element_snapshot.get("classList") or []
    container_classes = element_snapshot.get("containerClasses") or []
    combined_classes = list(classes) + list(container_classes)
    class_str = " ".join(combined_classes).lower()

    has_label_for = (
        element_snapshot.get("tagName") == "LABEL"
        and bool(element_snapshot.get("htmlFor"))
    ) or any(
        child.get("tagName") == "LABEL" and child.get("htmlFor")
        for child in (element_snapshot.get("children") or [])
    )
    if not has_label_for:
        label_attrs = element_snapshot.get("labelAttrs") or {}
        if isinstance(label_attrs, dict) and label_attrs.get("for"):
            has_label_for = True

    form_indicators = [
        "form-item", "form-group", "form-field",
        "field", "control",
        "data-prop",
    ]
    has_form_container = any(indicator in class_str for indicator in form_indicators)

    framework_classes = [cls for cls in combined_classes if any(
        prefix in cls.lower()
        for prefix in ["aui-", "el-", "ant-", "form-", "field-"]
    )]

    has_table_structure = (
        element_snapshot.get("tagName") in ("TR", "TBODY")
        or element_snapshot.get("containerTag", "").upper() in ("TR", "TBODY", "TABLE")
        or "table" in class_str
        or any(child.get("tagName") in ("TD", "TH") for child in (element_snapshot.get("children") or []))
    )

    has_dl_structure = (
        element_snapshot.get("tagName") in ("DL",)
        or element_snapshot.get("containerTag", "").upper() == "DL"
        or any(child.get("tagName") in ("DT", "DD") for child in (element_snapshot.get("children") or []))
    )

    schema_id_candidates = _extract_schema_id_candidates(element_snapshot)

    return {
        "has_label_for": has_label_for,
        "has_form_container": has_form_container,
        "framework_classes": framework_classes,
        "has_table_structure": has_table_structure,
        "has_dl_structure": has_dl_structure,
        "class_str": class_str,
        "schema_id_candidates": schema_id_candidates,
        "section_title": element_snapshot.get("sectionTitle", ""),
        "tab_title": element_snapshot.get("tabTitle", ""),
        "dialog_title": element_snapshot.get("dialogTitle", ""),
        "sibling_labels": element_snapshot.get("siblingLabels") or [],
    }
