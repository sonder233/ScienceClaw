from __future__ import annotations

import json


DEFAULT_DOM_VIEW_OPTIONS = {
    "maxTotalChars": 120000,
    "maxTableRows": 200,
    "maxCellChars": 800,
    "maxListDepth": 12,
    "maxShadowDepth": 2,
    "skeletonMinChars": 8000,
    "tableBudgetRatio": 0.45,
    "formBudgetRatio": 0.20,
    "listBudgetRatio": 0.20,
}


def _serialize_script(options: dict) -> str:
    opts = json.dumps(options, ensure_ascii=False)
    return f"""
() => {{
  const __DOM_DATA_VIEW__ = true;
  const opts = {opts};
  const maxTotalChars = Number(opts.maxTotalChars || 120000);
  const maxTableRows = Number(opts.maxTableRows || 200);
  const maxCellChars = Number(opts.maxCellChars || 800);
  const maxListDepth = Number(opts.maxListDepth || 12);
  const maxShadowDepth = Number(opts.maxShadowDepth || 2);
  const chunks = [];
  let totalChars = 0;

  const pushBlock = (text) => {{
    const value = String(text || '').trim();
    if (!value) return;
    if (totalChars >= maxTotalChars) return;
    const remaining = maxTotalChars - totalChars;
    const next = value.length > remaining ? value.slice(0, remaining) : value;
    if (!next.trim()) return;
    chunks.push(next);
    totalChars += next.length + 2;
  }};

  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const truncate = (value, limit=maxCellChars) => {{
    const text = normalize(value);
    return text.length > limit ? text.slice(0, limit) + '...' : text;
  }};

  const isVisible = (el) => {{
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return false;
    if (el.hidden || el.getAttribute('aria-hidden') === 'true') return false;
    const style = window.getComputedStyle(el);
    if (!style) return false;
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 || rect.height > 0 || normalize(el.innerText || el.textContent || '').length > 0;
  }};

  const labelForControl = (el) => {{
    if (!el) return '';
    const aria = el.getAttribute('aria-label');
    if (aria) return truncate(aria, 200);
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {{
      const parts = labelledBy.split(/\\s+/).map((id) => document.getElementById(id)).filter(Boolean);
      const text = parts.map((node) => normalize(node.innerText || node.textContent || '')).filter(Boolean).join(' ');
      if (text) return truncate(text, 200);
    }}
    if (el.id) {{
      const explicit = document.querySelector(`label[for="${{CSS.escape(el.id)}}"]`);
      if (explicit) {{
        const text = normalize(explicit.innerText || explicit.textContent || '');
        if (text) return truncate(text, 200);
      }}
    }}
    const wrapper = el.closest('label');
    if (wrapper) {{
      const text = normalize(wrapper.innerText || wrapper.textContent || '');
      if (text) return truncate(text, 200);
    }}
    return '';
  }};

  const listLines = (node, depth=0) => {{
    if (!node || depth > maxListDepth || !isVisible(node)) return [];
    const items = [];
    for (const child of Array.from(node.children || [])) {{
      if (!isVisible(child)) continue;
      if (child.tagName === 'LI' || child.tagName === 'DT' || child.tagName === 'DD') {{
        const clone = child.cloneNode(true);
        for (const nested of Array.from(clone.querySelectorAll('ul,ol,dl'))) nested.remove();
        const text = truncate(clone.innerText || clone.textContent || '', 400);
        if (text) items.push(`${{'  '.repeat(depth)}}- ${{text}}`);
        for (const nested of Array.from(child.children || [])) {{
          if (nested.tagName === 'UL' || nested.tagName === 'OL' || nested.tagName === 'DL') {{
            items.push(...listLines(nested, depth + 1));
          }}
        }}
      }}
    }}
    return items;
  }};

  const serializeTable = (table, index) => {{
    if (!isVisible(table)) return '';
    const rows = [];
    for (const tr of Array.from(table.querySelectorAll('tr')).slice(0, maxTableRows)) {{
      if (!isVisible(tr)) continue;
      const cells = Array.from(tr.children || [])
        .filter((cell) => ['TH', 'TD'].includes(cell.tagName) && isVisible(cell))
        .map((cell) => truncate(cell.innerText || cell.textContent || '', maxCellChars));
      if (cells.length) rows.push({{ header: Array.from(tr.children || []).some((cell) => cell.tagName === 'TH'), cells }});
    }}
    if (!rows.length) return '';
    let header = rows[0].cells;
    let body = rows.slice(1);
    if (!rows[0].header) {{
      body = rows;
      header = rows[0].cells.map((_, idx) => `col_${{idx + 1}}`);
    }}
    const width = Math.max(header.length, ...body.map((row) => row.cells.length), 0);
    if (!width) return '';
    const pad = (cells) => {{
      const copy = cells.slice(0, width);
      while (copy.length < width) copy.push('');
      return copy;
    }};
    const lines = [`TABLE ${{index}}`, `| ${{pad(header).join(' | ')}} |`, `| ${{Array(width).fill('---').join(' | ')}} |`];
    for (const row of body) lines.push(`| ${{pad(row.cells).join(' | ')}} |`);
    return lines.join('\\n');
  }};

  const serializeForm = (form, index) => {{
    if (!isVisible(form)) return '';
    const lines = [`FORM ${{index}}`];
    const controls = Array.from(form.querySelectorAll('input,select,textarea,button'));
    for (const el of controls) {{
      if (!isVisible(el)) continue;
      const bits = [];
      bits.push(el.tagName.toLowerCase());
      const label = labelForControl(el);
      if (label) bits.push(`label="${{label}}"`);
      const name = truncate(el.getAttribute('name') || '', 120);
      if (name) bits.push(`name="${{name}}"`);
      const controlId = truncate(el.getAttribute('id') || '', 120);
      if (controlId) bits.push(`id="${{controlId}}"`);
      const placeholder = truncate(el.getAttribute('placeholder') || '', 160);
      if (placeholder) bits.push(`placeholder="${{placeholder}}"`);
      const value = truncate(el.value || el.getAttribute('value') || el.innerText || '', 200);
      if (value) bits.push(`value="${{value}}"`);
      const testId = truncate(el.getAttribute('data-testid') || '', 120);
      if (testId) bits.push(`data-testid="${{testId}}"`);
      lines.push(`- ${{bits.join(' ')}}`);
    }}
    return lines.length > 1 ? lines.join('\\n') : '';
  }};

  const serializeLinksAndButtons = () => {{
    const lines = [];
    const nodes = Array.from(document.querySelectorAll('a,button,[role="button"],[role="link"]')).slice(0, 80);
    for (const el of nodes) {{
      if (!isVisible(el)) continue;
      const text = truncate(el.innerText || el.textContent || el.getAttribute('aria-label') || '', 200);
      if (!text) continue;
      const parts = [el.tagName.toLowerCase(), `"${{text}}"`];
      const testId = truncate(el.getAttribute('data-testid') || '', 120);
      if (testId) parts.push(`data-testid="${{testId}}"`);
      const href = truncate(el.getAttribute('href') || '', 200);
      if (href) parts.push(`href="${{href}}"`);
      lines.push(`- ${{parts.join(' ')}}`);
    }}
    return lines.length ? ['INTERACTIVE', ...lines].join('\\n') : '';
  }};

  const looksStable = (value) => {{
    const text = normalize(value);
    if (!text) return false;
    if (text.length < 3) return true;
    let transitions = 0;
    for (let i = 1; i < text.length; i++) {{
      const prev = /[a-z]/i.test(text[i - 1]) ? 'a' : /\\d/.test(text[i - 1]) ? 'n' : 'x';
      const curr = /[a-z]/i.test(text[i]) ? 'a' : /\\d/.test(text[i]) ? 'n' : 'x';
      if (prev !== curr) transitions += 1;
    }}
    return transitions < Math.floor(text.length / 4);
  }};

  const stableAttrs = (el) => {{
    const bits = [];
    const id = normalize(el.getAttribute('id') || '');
    const name = normalize(el.getAttribute('name') || '');
    const testId = normalize(el.getAttribute('data-testid') || el.getAttribute('data-test-id') || '');
    if (id && looksStable(id)) bits.push(`id="${{truncate(id, 80)}}"`);
    if (name && looksStable(name)) bits.push(`name="${{truncate(name, 80)}}"`);
    if (testId && looksStable(testId)) bits.push(`data-testid="${{truncate(testId, 80)}}"`);
    return bits.join(' ');
  }};

  const serializeFieldGroups = () => {{
    const blocks = [];
    const candidates = Array.from(document.querySelectorAll('div,section,article,tr,dl,li')).slice(0, 240);
    let index = 1;
    for (const el of candidates) {{
      if (!isVisible(el)) continue;
      const children = Array.from(el.children || []).filter(isVisible);
      if (children.length < 2 || children.length > 6) continue;
      const pairs = [];
      if (el.tagName === 'DL') {{
        for (const dt of Array.from(el.querySelectorAll(':scope > dt')).slice(0, 6)) {{
          const dd = dt.nextElementSibling;
          if (!dd || dd.tagName !== 'DD' || !isVisible(dd)) continue;
          const label = truncate(dt.innerText || dt.textContent || '', 120);
          const value = truncate(dd.innerText || dd.textContent || '', 120);
          if (label && value && label !== value) {{
            pairs.push(`- label="${{label}}" -> value="${{value}}" ${{stableAttrs(dd)}}`.trim());
          }}
        }}
      }} else {{
        for (let i = 0; i < children.length - 1; i++) {{
          const left = children[i];
          const right = children[i + 1];
          if (!left || !right) continue;
          const label = truncate(left.innerText || left.textContent || '', 120);
          const value = truncate(right.innerText || right.textContent || '', 120);
          if (!label || !value || label === value) continue;
          if (label.length > 60 || value.length > 160) continue;
          pairs.push(`- label="${{label}}" -> value="${{value}}" ${{stableAttrs(right)}}`.trim());
        }}
      }}
      if (!pairs.length) continue;
      const tag = el.tagName.toLowerCase();
      const attrs = stableAttrs(el);
      blocks.push([`FIELD_GROUP ${{index++}}`, `container=${{tag}}${{attrs ? ' ' + attrs : ''}}`, ...pairs].join('\\n'));
      if (blocks.length >= 40) break;
    }}
    return blocks.join('\\n\\n');
  }};

  const serializeFrame = (root, prefix, shadowDepth) => {{
    if (!root) return;
    const headings = Array.from(root.querySelectorAll ? root.querySelectorAll('h1,h2,h3,h4,h5,h6') : []).filter(isVisible).slice(0, 80);
    if (headings.length) {{
      pushBlock([`${{prefix}}HEADINGS`, ...headings.map((el) => `- ${{el.tagName}}: ${{truncate(el.innerText || el.textContent || '', 300)}}`)].join('\\n'));
    }}

    const tables = Array.from(root.querySelectorAll ? root.querySelectorAll('table') : []).slice(0, 20);
    let tableIndex = 1;
    for (const table of tables) {{
      const block = serializeTable(table, tableIndex++);
      if (block) pushBlock(block);
    }}

    const forms = Array.from(root.querySelectorAll ? root.querySelectorAll('form') : []).slice(0, 20);
    let formIndex = 1;
    for (const form of forms) {{
      const block = serializeForm(form, formIndex++);
      if (block) pushBlock(block);
    }}

    const lists = Array.from(root.querySelectorAll ? root.querySelectorAll('ul,ol,dl') : []).slice(0, 20);
    let listIndex = 1;
    for (const list of lists) {{
      if (!isVisible(list)) continue;
      const lines = listLines(list, 0);
      if (lines.length) pushBlock([`LIST ${{listIndex++}}`, ...lines].join('\\n'));
    }}

    const interactive = serializeLinksAndButtons();
    if (interactive) pushBlock(interactive);

    const fieldGroups = serializeFieldGroups();
    if (fieldGroups) pushBlock(fieldGroups);

    if (shadowDepth >= maxShadowDepth) return;
    const shadowHosts = Array.from(root.querySelectorAll ? root.querySelectorAll('*') : []).filter((el) => el.shadowRoot);
    for (const host of shadowHosts.slice(0, 20)) {{
      pushBlock(`SHADOW_ROOT\\n- host: ${{host.tagName.toLowerCase()}}`);
      serializeFrame(host.shadowRoot, 'SHADOW_', shadowDepth + 1);
    }}
  }};

  pushBlock(`PAGE\\n- title: ${{truncate(document.title || '', 300)}}\\n- url: ${{truncate(location.href || '', 400)}}`);
  serializeFrame(document, '', 0);

  for (const iframe of Array.from(document.querySelectorAll('iframe')).slice(0, 20)) {{
    if (!isVisible(iframe)) continue;
    try {{
      const doc = iframe.contentDocument;
      if (!doc) {{
        pushBlock(`IFRAME\\n- title: ${{truncate(iframe.getAttribute('title') || '', 120)}}\\n- status: skipped`);
        continue;
      }}
      pushBlock(`IFRAME\\n- title: ${{truncate(iframe.getAttribute('title') || '', 120)}}\\n- status: accessible`);
      serializeFrame(doc, 'IFRAME_', 0);
    }} catch (err) {{
      pushBlock(`IFRAME\\n- title: ${{truncate(iframe.getAttribute('title') || '', 120)}}\\n- status: skipped`);
    }}
  }}

  return chunks.join('\\n\\n');
}}
"""


def _targeted_script() -> str:
    return """
() => {
  const __TARGETED_DOM_SEGMENTS__ = true;
  const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
  const isVisible = (el) => {
    if (!el) return false;
    if (el.hidden) return false;
    const style = window.getComputedStyle(el);
    if (!style) return false;
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 || rect.height > 0 || normalize(el.innerText || el.textContent || '').length > 0;
  };
  const blocks = [];
  const main = document.querySelector('main, article, [role="main"]');
  if (main && isVisible(main)) {
    const text = normalize(main.innerText || main.textContent || '').slice(0, 3000);
    if (text) blocks.push('TARGETED_MAIN\\n' + text);
  }
  const headings = Array.from(document.querySelectorAll('h1,h2,h3')).filter(isVisible).slice(0, 20).map((el) => normalize(el.innerText || el.textContent || '')).filter(Boolean);
  if (headings.length) blocks.push('TARGETED_HEADINGS\\n' + headings.map((text) => '- ' + text).join('\\n'));
  return blocks.join('\\n\\n');
}
"""


async def serialize_structured_data_view_async(page, **opts) -> str:
    options = {**DEFAULT_DOM_VIEW_OPTIONS, **opts}
    result = await page.evaluate(_serialize_script(options))
    return str(result or "").strip()


async def collect_targeted_dom_segments(page) -> str:
    result = await page.evaluate(_targeted_script())
    return str(result or "").strip()
