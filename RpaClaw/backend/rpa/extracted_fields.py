import json
import re
from typing import Any, Dict, List


def _normalize_whitespace(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _snake_case_ascii(value: Any) -> str:
    text = _normalize_whitespace(value).lower()
    if not text:
        return ""
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        return ""
    if text[0].isdigit():
        text = f"field_{text}"
    return text[:64]


def _preferred_field_name(label: str, result_key: str, index: int) -> str:
    return _snake_case_ascii(label) or _snake_case_ascii(result_key) or f"field_{index}"


def build_playwright_extract_selector(label: str) -> str:
    """Generate a Playwright XPath selector anchored on the label element.

    Finds the element whose (normalized) text equals the label and returns its
    next sibling — which is typically the value element. Works for common
    patterns:
      <span>Label</span><strong>Value</strong>
      <td>Label</td><td>Value</td>
      <dt>Label</dt><dd>Value</dd>
    The value itself is never encoded into the selector, so the locator stays
    stable across data changes.
    """
    escaped = label.replace("\\", "\\\\").replace('"', '\\"')
    return f'xpath=//*[normalize-space(.)="{escaped}"]/following-sibling::*[1]'


def build_label_extract_js(label: str) -> str:
    """Generate a JS () => string function that finds a DOM element whose leaf text
    matches `label` and returns the text of the adjacent value element.

    Matching priority: exact (score 2) > starts-with (score 1).
    This handles labels like "预算" matching DOM text "预算（元）".

    Handles common patterns:
      - <span>Label</span><strong>Value</strong> (siblings in same parent)
      - <td>Label</td><td>Value</td> (table row)
      - <dt>Label</dt><dd>Value</dd>
      - <div>Label</div><div>Value</div> (parent's next sibling)

    Within a parent, prefers <strong>/<b>/<dd>/<td>/<output> over plain siblings
    so value-typed tags win when present.
    """
    escaped = label.replace("\\", "\\\\").replace("'", "\\'")
    return (
        "() => {"
        f"var q='{escaped}',b=null,bs=0;"
        "for(var e of document.querySelectorAll('*')){"
        "if(e.childElementCount)continue;"
        "var t=(e.innerText||e.textContent||'').trim();"
        "var s=t===q?2:t.indexOf(q)===0?1:0;"
        "if(s>bs){bs=s;b=e;}"
        "}"
        "if(!b)return'';"
        "var p=b.parentElement,cs=p?Array.from(p.children):[];"
        "var VT=['STRONG','B','DD','TD','OUTPUT'];"
        "for(var c of cs){if(c!==b&&VT.indexOf(c.tagName)>=0)return(c.innerText||c.textContent||'').trim();}"
        "var i=cs.indexOf(b);"
        "if(i>=0&&i+1<cs.length)return(cs[i+1].innerText||cs[i+1].textContent||'').trim();"
        "var n=p&&p.nextElementSibling;"
        "return n?(n.innerText||n.textContent||'').trim():'';"
        "}"
    )


def build_extract_candidates(label: str) -> List[Dict[str, Any]]:
    """Build the two standard extraction candidates for a field label."""
    return [
        {
            "kind": "playwright_locator",
            "selected": True,
            "expression": build_playwright_extract_selector(label),
            "description": "Playwright XPath 定位器",
        },
        {
            "kind": "js_evaluate",
            "selected": False,
            "expression": build_label_extract_js(label),
            "description": "JavaScript evaluate",
        },
    ]


def _strip_value_from_label(label: Any, content: str) -> str:
    """Remove a trailing value from a label so the selector doesn't capture the data.

    Example: label="预算（元） 6800", content="6800" → "预算（元）"
    Only strips when the label clearly ends with the content; separators that
    get peeled afterward are the punctuation commonly used between label/value
    in Chinese and English UIs.
    """
    label_text = _normalize_whitespace(label)
    if not content or label_text == content:
        return label_text
    if label_text.endswith(content):
        label_text = label_text[: -len(content)].rstrip(" \t:：·-—,.、")
    return label_text


def _split_label_value(text: str) -> tuple[str, str]:
    """Split a single combined "label value" string on the last whitespace.

    Used when the upstream hint gives us the outer element's innerText (e.g.
    "预算（元） 6800" from `<div class="detail-item">`) without a colon — there's
    no separator we can trust, so peel off the trailing token and call that the
    value. Callers must only invoke this when they already know label and
    content collapsed into one string.
    """
    parts = text.rsplit(None, 1)
    if len(parts) == 2:
        head, tail = parts[0].strip(), parts[1].strip()
        if head and tail:
            return head, tail
    return text, ""


def parse_extracted_fields(
    output: Any,
    *,
    locator: Dict[str, Any] | None = None,
    frame_path: List[str] | None = None,
    result_key: str = "",
    hint_label: str = "",
) -> List[Dict[str, Any]]:
    text = _normalize_whitespace(output)
    if not text:
        return []

    fields: List[Dict[str, Any]] = []
    seen_names: set[str] = set()

    def add_field(label: str, content: Any) -> None:
        normalized_content = _normalize_whitespace(content)
        if not normalized_content:
            return
        cleaned_label = _strip_value_from_label(label, normalized_content)
        base_name = _preferred_field_name(cleaned_label, result_key, len(fields) + 1)
        name = base_name
        suffix = 2
        while name in seen_names:
            name = f"{base_name}_{suffix}"
            suffix += 1
        seen_names.add(name)
        normalized_label = cleaned_label or name
        field: Dict[str, Any] = {
            "name": name,
            "label": normalized_label,
            "content": normalized_content,
            "locator": locator or {},
            "frame_path": list(frame_path or []),
            "source_result_key": result_key,
        }
        if cleaned_label and normalized_label != name:
            field["extract_js"] = build_label_extract_js(cleaned_label)
            field["extract_candidates"] = build_extract_candidates(cleaned_label)
        fields.append(field)

    try:
        parsed = json.loads(text)
    except Exception:
        parsed = None

    if isinstance(parsed, dict):
        for key, value in parsed.items():
            if isinstance(value, (str, int, float, bool)):
                add_field(str(key), value)
        if fields:
            return fields

    for line in str(output).splitlines():
        matched = re.match(r"^\s*([^:：]{1,80})\s*[:：]\s*(.+?)\s*$", line)
        if matched:
            add_field(matched.group(1), matched.group(2))
    if fields:
        return fields

    normalized_hint = _normalize_whitespace(hint_label)
    if normalized_hint and normalized_hint == text:
        head, tail = _split_label_value(text)
        if tail:
            add_field(head, tail)
            return fields

    add_field(hint_label or result_key or "提取值", text)
    return fields


def infer_fill_mappings(
    previous_steps: List[Any],
    incoming_value: Any,
) -> List[Dict[str, Any]]:
    normalized_value = _normalize_whitespace(incoming_value)
    if not normalized_value:
        return []

    matches: List[Dict[str, Any]] = []
    for step in reversed(previous_steps):
        extracted_fields = getattr(step, "extracted_fields", None) or []
        for field in extracted_fields:
            if not isinstance(field, dict):
                continue
            if _normalize_whitespace(field.get("content")) != normalized_value:
                continue
            matches.append(
                {
                    "param_name": field.get("name") or "value",
                    "label": field.get("label") or field.get("name") or "提取值",
                    "content": field.get("content") or "",
                    "source_result_key": field.get("source_result_key") or getattr(step, "result_key", "") or "",
                    "source_step_id": getattr(step, "id", ""),
                }
            )
        if matches:
            return matches
    return []
