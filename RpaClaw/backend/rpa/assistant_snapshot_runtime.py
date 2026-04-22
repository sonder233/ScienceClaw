from __future__ import annotations


SNAPSHOT_V2_JS = r"""() => {
    const ACTIONABLE = 'a,button,input,textarea,select,[role=button],[role=link],[role=menuitem],[role=menuitemradio],[role=tab],[role=checkbox],[role=radio],[role=combobox],[role=listbox],[role=option],[contenteditable=true]';
    const CONTENT = 'h1,h2,h3,h4,h5,h6,th,td,dt,dd,li,p,label,[role=heading],[role=cell],[role=rowheader],[role=columnheader]';
    const recorder = globalThis.__rpaPlaywrightRecorder || null;
    const result = { actionable_nodes: [], content_nodes: [], containers: [], page_blocks: [], field_pairs: [] };
    const containerMap = new Map();
    const blockMap = new Map();
    let actionableIndex = 1;
    let contentIndex = 1;
    let containerIndex = 1;
    let blockIndex = 1;
    let fieldPairIndex = 1;

    function normalizeText(value, limit) {
        return String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit || 160);
    }

    function isVisible(el, rect) {
        if (!rect || rect.width <= 0 || rect.height <= 0)
            return false;
        const style = getComputedStyle(el);
        if (!style)
            return false;
        return style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
    }

    function centerPoint(rect) {
        return {
            x: Math.round(rect.left + rect.width / 2),
            y: Math.round(rect.top + rect.height / 2),
        };
    }

    function hitTestOk(el, rect) {
        try {
            const point = centerPoint(rect);
            const hit = document.elementFromPoint(point.x, point.y);
            if (!hit)
                return false;
            return hit === el || el.contains(hit) || hit.contains(el);
        } catch (e) {
            return false;
        }
    }

    function bbox(rect) {
        return {
            x: Math.round(rect.left),
            y: Math.round(rect.top),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
        };
    }

    function isGuidLike(value) {
        const text = String(value || '').trim();
        if (!text || text.length < 8)
            return false;
        let transitions = 0;
        for (let i = 1; i < text.length; i++) {
            const prev = text[i - 1];
            const curr = text[i];
            const prevType = /[a-z]/i.test(prev) ? 'a' : /\d/.test(prev) ? 'n' : 'x';
            const currType = /[a-z]/i.test(curr) ? 'a' : /\d/.test(curr) ? 'n' : 'x';
            if (prevType !== currType)
                transitions += 1;
        }
        return transitions >= Math.floor(text.length / 4);
    }

    function stableAttrValue(value, limit) {
        const text = normalizeText(value || '', limit || 120);
        if (!text || isGuidLike(text))
            return '';
        return text;
    }

    function classTokens(el, limit) {
        return Array.from(el.classList || [])
            .map(token => stableAttrValue(token, 40))
            .filter(Boolean)
            .slice(0, limit || 4);
    }

    function stableAttrs(el) {
        if (!el)
            return {};
        const attrs = {};
        const id = stableAttrValue(el.getAttribute('id') || '', 80);
        const name = stableAttrValue(el.getAttribute('name') || '', 80);
        const testId = stableAttrValue(
            el.getAttribute('data-testid')
            || el.getAttribute('data-test-id')
            || el.getAttribute('data-qa')
            || '',
            80
        );
        const labelledBy = stableAttrValue(el.getAttribute('aria-labelledby') || '', 120);
        const describedBy = stableAttrValue(el.getAttribute('aria-describedby') || '', 120);
        if (id)
            attrs.id = id;
        if (name)
            attrs.name = name;
        if (testId)
            attrs.testid = testId;
        if (labelledBy)
            attrs.aria_labelledby = labelledBy;
        if (describedBy)
            attrs.aria_describedby = describedBy;

        const schemaAttrNames = ['data-prop', 'data-prop-id', 'data-field', 'data-field-id', 'data-schema-id', 'prop', 'fieldid', 'fieldname'];
        for (const attrName of schemaAttrNames) {
            const val = stableAttrValue(el.getAttribute(attrName) || '', 80);
            if (val && !/\s/.test(val) && !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(val)) {
                attrs[attrName] = val;
            }
        }
        const forAttr = stableAttrValue(el.getAttribute('for') || '', 80);
        if (forAttr)
            attrs['for'] = forAttr;
        return attrs;
    }

    function cssEsc(value) {
        try {
            return CSS.escape(String(value || ''));
        } catch (e) {
            return String(value || '').replace(/([\\"'\[\](){}|^$.*+?])/g, '\\$1');
        }
    }

    function fallbackRole(el) {
        const explicitRole = el.getAttribute('role');
        if (explicitRole)
            return explicitRole;
        const tag = el.tagName.toLowerCase();
        if (tag === 'button')
            return 'button';
        if (tag === 'select')
            return 'combobox';
        if (tag === 'textarea')
            return 'textbox';
        if (tag === 'input') {
            const type = (el.getAttribute('type') || '').toLowerCase();
            if (type === 'checkbox')
                return 'checkbox';
            if (type === 'radio')
                return 'radio';
            if (type === 'button' || type === 'submit')
                return 'button';
            return 'textbox';
        }
        if (tag === 'a' && el.hasAttribute('href'))
            return 'link';
        if (tag === 'option')
            return 'option';
        return '';
    }

    function getRole(el) {
        try {
            return recorder && recorder.getRole ? recorder.getRole(el) || '' : fallbackRole(el);
        } catch (e) {
            return fallbackRole(el);
        }
    }

    function getAccessibleName(el) {
        try {
            if (recorder && recorder.getAccessibleName)
                return normalizeText(recorder.getAccessibleName(el) || '', 160);
        } catch (e) {}
        return normalizeText(el.getAttribute('aria-label') || el.innerText || el.value || '', 160);
    }

    function detectContainerKind(el) {
        if (!el)
            return '';
        const tag = el.tagName.toLowerCase();
        const role = el.getAttribute('role') || '';
        if (tag === 'table' || role === 'table' || role === 'grid')
            return 'table';
        if (tag === 'ul' || tag === 'ol' || role === 'list')
            return 'list';
        if (role === 'toolbar')
            return 'toolbar';
        if (tag === 'form')
            return 'form_section';
        if (tag === 'section')
            return 'form_section';
        if (tag === 'article')
            return 'card_group';
        return '';
    }

    function detectContainerName(el) {
        if (!el)
            return '';
        const direct = normalizeText(
            el.getAttribute('aria-label') ||
            el.getAttribute('title') ||
            (el.querySelector('caption,h1,h2,h3,h4,legend,[role=heading]') || {}).innerText ||
            '',
            80
        );
        return direct;
    }

    function ensureContainer(el) {
        const containerEl = el.closest('table,[role=table],[role=grid],ul,ol,[role=list],form,[role=toolbar],section,article');
        if (!containerEl)
            return '';
        if (containerMap.has(containerEl))
            return containerMap.get(containerEl).container_id;
        const rect = containerEl.getBoundingClientRect();
        const container = {
            container_id: 'container-' + containerIndex++,
            frame_path: [],
            container_kind: detectContainerKind(containerEl) || 'container',
            name: detectContainerName(containerEl),
            bbox: bbox(rect),
            summary: normalizeText(containerEl.innerText || '', 120),
            child_actionable_ids: [],
            child_content_ids: [],
        };
        containerMap.set(containerEl, container);
        result.containers.push(container);
        return container.container_id;
    }

    function detectBlockKind(el) {
        if (!el)
            return '';
        const tag = el.tagName.toLowerCase();
        const classes = classTokens(el, 6).join(' ');
        if (tag === 'table' || tag === 'tbody')
            return 'table_section';
        if (tag === 'form')
            return 'form_section';
        if (tag === 'article')
            return 'card';
        if (tag === 'tr')
            return 'table_row';
        if (tag === 'li')
            return 'list_item';
        if (tag === 'dl')
            return 'description_list';
        if (/(detail|field|meta|info|summary|row|item)/.test(classes))
            return 'detail_section';
        if (tag === 'div' || tag === 'section')
            return 'content_group';
        return '';
    }

    function ensureBlock(el) {
        if (!el)
            return '';
        if (blockMap.has(el))
            return blockMap.get(el).block_id;
        const rect = el.getBoundingClientRect();
        const block = {
            block_id: 'block-' + blockIndex++,
            frame_path: [],
            block_kind: detectBlockKind(el) || 'content_group',
            tag: el.tagName.toLowerCase(),
            stable_attrs: stableAttrs(el),
            class_tokens: classTokens(el, 6),
            bbox: bbox(rect),
            text_summary: normalizeText(el.innerText || el.textContent || '', 240),
            child_block_ids: [],
        };
        blockMap.set(el, block);
        const parent = el.parentElement;
        if (parent && blockMap.has(parent)) {
            blockMap.get(parent).child_block_ids.push(block.block_id);
        }
        result.page_blocks.push(block);
        return block.block_id;
    }

    function buildFallbackLocator(el, role, name, text, placeholder, title) {
        if (role && name) {
            return {
                primary: { method: 'role', role, name },
                candidates: [{ kind: 'role', selected: true, locator: { method: 'role', role, name }, strict_match_count: 1, visible_match_count: 1, reason: 'fallback role candidate' }],
                validation: { status: 'fallback', details: 'fallback role candidate', selected_candidate_index: 0, selected_candidate_kind: 'role' },
            };
        }
        if (placeholder) {
            return {
                primary: { method: 'placeholder', value: placeholder },
                candidates: [{ kind: 'placeholder', selected: true, locator: { method: 'placeholder', value: placeholder }, strict_match_count: 1, visible_match_count: 1, reason: 'fallback placeholder candidate' }],
                validation: { status: 'fallback', details: 'fallback placeholder candidate', selected_candidate_index: 0, selected_candidate_kind: 'placeholder' },
            };
        }
        if (text || name) {
            const value = text || name;
            return {
                primary: { method: 'text', value },
                candidates: [{ kind: 'text', selected: true, locator: { method: 'text', value }, strict_match_count: 1, visible_match_count: 1, reason: 'fallback text candidate' }],
                validation: { status: 'fallback', details: 'fallback text candidate', selected_candidate_index: 0, selected_candidate_kind: 'text' },
            };
        }
        if (title) {
            return {
                primary: { method: 'title', value: title },
                candidates: [{ kind: 'title', selected: true, locator: { method: 'title', value: title }, strict_match_count: 1, visible_match_count: 1, reason: 'fallback title candidate' }],
                validation: { status: 'fallback', details: 'fallback title candidate', selected_candidate_index: 0, selected_candidate_kind: 'title' },
            };
        }
        const tag = el.tagName.toLowerCase();
        return {
            primary: { method: 'css', value: tag },
            candidates: [{ kind: 'css', selected: true, locator: { method: 'css', value: tag }, strict_match_count: 1, visible_match_count: 1, reason: 'fallback css candidate' }],
            validation: { status: 'fallback', details: 'fallback css candidate', selected_candidate_index: 0, selected_candidate_kind: 'css' },
        };
    }

    function buildLocatorBundle(el, role, name, text, placeholder, title) {
        try {
            if (recorder && recorder.buildLocatorBundle) {
                const bundle = recorder.buildLocatorBundle(el);
                if (bundle && bundle.primary)
                    return bundle;
            }
        } catch (e) {}
        return buildFallbackLocator(el, role, name, text, placeholder, title);
    }

    function appendCandidate(candidates, kind, locator, reason) {
        if (!locator)
            return;
        const key = JSON.stringify(locator);
        if (candidates.some(item => JSON.stringify(item.locator) === key))
            return;
        candidates.push({
            kind,
            selected: false,
            locator,
            strict_match_count: 1,
            visible_match_count: 1,
            reason,
        });
    }

    function buildFieldValueLocatorBundle(containerEl, labelEl, valueEl, labelText, valueText) {
        const candidates = [];
        const attrs = stableAttrs(valueEl);
        const containerAttrs = stableAttrs(containerEl);
        const valueClasses = classTokens(valueEl, 3);
        const containerClasses = classTokens(containerEl, 3);

        if (attrs.testid) {
            appendCandidate(candidates, 'testid', { method: 'testid', value: attrs.testid }, 'stable data-testid on value node');
        }
        if (attrs.id) {
            appendCandidate(candidates, 'css', { method: 'css', value: `#${cssEsc(attrs.id)}` }, 'stable id on value node');
        }
        if (attrs.name) {
            appendCandidate(
                candidates,
                'css',
                { method: 'css', value: `${valueEl.tagName.toLowerCase()}[name="${cssEsc(attrs.name)}"]` },
                'stable name on value node',
            );
        }
        if (containerAttrs.id && valueClasses.length) {
            appendCandidate(
                candidates,
                'css',
                { method: 'css', value: `#${cssEsc(containerAttrs.id)} .${valueClasses.map(cssEsc).join('.')}` },
                'stable container id with value class',
            );
        }
        if (containerAttrs.testid && valueClasses.length) {
            appendCandidate(
                candidates,
                'css',
                { method: 'css', value: `[data-testid="${cssEsc(containerAttrs.testid)}"] .${valueClasses.map(cssEsc).join('.')}` },
                'stable container testid with value class',
            );
        }
        if (containerClasses.length && valueClasses.length) {
            appendCandidate(
                candidates,
                'css',
                { method: 'css', value: `.${containerClasses.map(cssEsc).join('.')} .${valueClasses.map(cssEsc).join('.')}` },
                'container class with value class',
            );
        }
        if (!candidates.length) {
            const fallbackBundle = buildLocatorBundle(valueEl, getRole(valueEl), '', valueText, '', '');
            if (fallbackBundle && fallbackBundle.primary) {
                const fallbackCandidates = Array.isArray(fallbackBundle.candidates) ? fallbackBundle.candidates : [];
                for (const candidate of fallbackCandidates) {
                    appendCandidate(
                        candidates,
                        candidate.kind || (candidate.locator || {}).method || 'locator',
                        candidate.locator,
                        candidate.reason || 'fallback value locator',
                    );
                }
            }
        }
        if (!candidates.length && labelText) {
            appendCandidate(
                candidates,
                'css',
                { method: 'css', value: `${labelEl.tagName.toLowerCase()} + ${valueEl.tagName.toLowerCase()}` },
                'adjacent sibling fallback',
            );
        }
        if (!candidates.length) {
            appendCandidate(
                candidates,
                'text',
                { method: 'text', value: valueText },
                'text fallback for value node',
            );
        }
        if (candidates.length) {
            candidates[0].selected = true;
        }
        return {
            primary: candidates[0] ? candidates[0].locator : { method: 'text', value: valueText },
            candidates,
            validation: {
                status: candidates[0] ? 'ok' : 'fallback',
                details: candidates[0] ? candidates[0].reason : 'value node fallback',
                selected_candidate_index: 0,
                selected_candidate_kind: candidates[0] ? candidates[0].kind : 'text',
            },
        };
    }

    function actionKinds(el, role) {
        const tag = el.tagName.toLowerCase();
        const type = (el.getAttribute('type') || '').toLowerCase();
        const actions = new Set();
        if (tag === 'input' || tag === 'textarea' || el.isContentEditable)
            actions.add('fill');
        if (tag === 'select')
            actions.add('fill');
        if (tag === 'select')
            actions.add('select');
        if (role === 'combobox' || role === 'listbox')
            actions.add('fill');
        if (!actions.size || role === 'button' || role === 'link' || role === 'checkbox' || role === 'radio')
            actions.add('click');
        if (role === 'option')
            actions.add('click');
        if (role === 'textbox')
            actions.add('press');
        if (type === 'checkbox' || type === 'radio') {
            actions.delete('fill');
            actions.add('click');
        }
        return Array.from(actions);
    }

    function semanticKind(el, role) {
        const tag = el.tagName.toLowerCase();
        if (role === 'heading' || /^h[1-6]$/.test(tag))
            return 'heading';
        if (tag === 'td' || role === 'cell')
            return 'cell';
        if (tag === 'th' || role === 'rowheader' || role === 'columnheader')
            return 'header_cell';
        if (tag === 'li')
            return 'item';
        if (tag === 'label')
            return 'label';
        return 'text';
    }

    function contentLikeText(el) {
        if (!el)
            return '';
        const clone = el.cloneNode(true);
        for (const child of Array.from(clone.querySelectorAll(ACTIONABLE))) {
            child.remove();
        }
        return normalizeText(clone.innerText || clone.textContent || '', 200);
    }

    function isControl(el) {
        if (!el)
            return false;
        return el.matches('input,select,textarea,button,[role=button],[role=link],[role=checkbox],[role=radio],[role=combobox],[contenteditable=true]');
    }

    function labelLikeScore(el, text) {
        if (!el || !text)
            return 0;
        let score = 0;
        const tag = el.tagName.toLowerCase();
        if (tag === 'label' || tag === 'dt' || tag === 'th')
            score += 6;
        if (/(：|:)$/.test(text))
            score += 3;
        if (text.length <= 40)
            score += 2;
        if (!/\d{2,}/.test(text))
            score += 1;
        if (/(预算|金额|单位|时间|日期|状态|名称|编号|采购|联系人|电话|地址|备注)/.test(text))
            score += 5;
        return score;
    }

    function valueLikeScore(el, text) {
        if (!el || !text)
            return 0;
        let score = 0;
        const tag = el.tagName.toLowerCase();
        if (tag === 'dd' || tag === 'td')
            score += 6;
        if (text.length <= 120)
            score += 2;
        if (/\d/.test(text))
            score += 2;
        if (!/(：|:)$/.test(text))
            score += 1;
        return score;
    }

    function buildNodeDescriptor(el, role, text, locatorBundle) {
        return {
            tag: el.tagName.toLowerCase(),
            text: normalizeText(text || '', 200),
            role: role || getRole(el),
            stable_attrs: stableAttrs(el),
            class_tokens: classTokens(el, 4),
            locator: locatorBundle.primary,
            locator_candidates: locatorBundle.candidates || [],
            bbox: bbox(el.getBoundingClientRect()),
        };
    }

    function pushFieldPair(containerEl, labelEl, valueEl, relationKind) {
        if (!containerEl || !labelEl || !valueEl)
            return;
        if (labelEl === valueEl)
            return;
        const labelText = contentLikeText(labelEl);
        const valueText = contentLikeText(valueEl);
        if (!labelText || !valueText)
            return;
        if (labelText === valueText)
            return;
        const labelScore = labelLikeScore(labelEl, labelText);
        const valueScore = valueLikeScore(valueEl, valueText);
        if (labelScore < 2 || valueScore < 2)
            return;
        const labelRole = getRole(labelEl);
        const valueRole = getRole(valueEl);
        const labelBundle = buildLocatorBundle(labelEl, labelRole, '', labelText, '', '');
        const valueBundle = buildFieldValueLocatorBundle(containerEl, labelEl, valueEl, labelText, valueText);
        const containerRect = containerEl.getBoundingClientRect();
        const blockId = ensureBlock(containerEl);
        const pairKey = [
            blockId,
            labelText,
            valueText,
            Math.round(containerRect.x || containerRect.left || 0),
            Math.round(containerRect.y || containerRect.top || 0),
        ].join('|');
        if (result.field_pairs.some(item => item.pair_key === pairKey))
            return;
        result.field_pairs.push({
            pair_key: pairKey,
            field_pair_id: 'field-pair-' + fieldPairIndex++,
            frame_path: [],
            block_id: blockId,
            container: {
                tag: containerEl.tagName.toLowerCase(),
                stable_attrs: stableAttrs(containerEl),
                class_tokens: classTokens(containerEl, 6),
                bbox: bbox(containerRect),
            },
            label_text: labelText,
            value_text: valueText,
            relation: {
                kind: relationKind,
                direction: 'label_to_value',
                confidence: Math.min(0.99, 0.55 + labelScore * 0.04 + valueScore * 0.04),
            },
            label_node: buildNodeDescriptor(labelEl, labelRole, labelText, labelBundle),
            value_node: buildNodeDescriptor(valueEl, valueRole, valueText, valueBundle),
        });
    }

    function detectSiblingFieldPairs() {
        const candidates = Array.from(document.querySelectorAll('div,section,article,li,tr,dl'));
        for (const containerEl of candidates.slice(0, 400)) {
            const rect = containerEl.getBoundingClientRect();
            if (!isVisible(containerEl, rect))
                continue;
            const children = Array.from(containerEl.children || []).filter(child => {
                const childRect = child.getBoundingClientRect();
                return isVisible(child, childRect) && !isControl(child);
            });
            if (children.length < 2 || children.length > 6)
                continue;

            if (containerEl.tagName.toLowerCase() === 'tr') {
                if (children.length >= 2)
                    pushFieldPair(containerEl, children[0], children[1], 'same_row_cells');
                continue;
            }

            if (containerEl.tagName.toLowerCase() === 'dl') {
                const terms = Array.from(containerEl.querySelectorAll(':scope > dt'));
                for (const term of terms) {
                    const next = term.nextElementSibling;
                    if (next && next.tagName.toLowerCase() === 'dd')
                        pushFieldPair(containerEl, term, next, 'description_list_pair');
                }
                continue;
            }

            for (let index = 0; index < children.length - 1; index++) {
                pushFieldPair(containerEl, children[index], children[index + 1], 'siblings_same_container');
            }
        }
    }

    const actionableSeen = new Set();
    for (const el of Array.from(document.querySelectorAll(ACTIONABLE))) {
        const rect = el.getBoundingClientRect();
        if (!isVisible(el, rect))
            continue;
        if (el.disabled)
            continue;
        const role = getRole(el);
        const name = getAccessibleName(el);
        const text = normalizeText(el.innerText || '', 160);
        const placeholder = normalizeText(el.getAttribute('placeholder') || '', 80);
        const title = normalizeText(el.getAttribute('title') || '', 80);
        const key = [role, name, placeholder, title, bbox(rect).x, bbox(rect).y].join('|');
        if (actionableSeen.has(key))
            continue;
        actionableSeen.add(key);
        const containerId = ensureContainer(el);
        ensureBlock(el.closest('article,section,form,table,tbody,tr,ul,ol,li,dl,div') || el);
        const locatorBundle = buildLocatorBundle(el, role, name, text, placeholder, title);
        const node = {
            node_id: 'actionable-' + actionableIndex++,
            frame_path: [],
            container_id: containerId,
            tag: el.tagName.toLowerCase(),
            role,
            name,
            text,
            type: normalizeText(el.getAttribute('type') || '', 40),
            placeholder,
            title,
            bbox: bbox(rect),
            center_point: centerPoint(rect),
            is_visible: true,
            is_enabled: !el.disabled,
            hit_test_ok: hitTestOk(el, rect),
            action_kinds: actionKinds(el, role),
            locator: locatorBundle.primary,
            locator_candidates: locatorBundle.candidates || [],
            validation: locatorBundle.validation || { status: 'fallback', details: 'locator bundle unavailable' },
            stable_attrs: stableAttrs(el),
            class_tokens: classTokens(el, 4),
            element_snapshot: {
                tag: el.tagName.toLowerCase(),
                text,
                title,
                href: normalizeText(el.getAttribute('href') || '', 120),
            },
        };
        result.actionable_nodes.push(node);
        if (containerId) {
            const container = Array.from(containerMap.values()).find(item => item.container_id === containerId);
            if (container)
                container.child_actionable_ids.push(node.node_id);
        }
        if (result.actionable_nodes.length >= 120)
            break;
    }

    const contentSeen = new Set();
    for (const el of Array.from(document.querySelectorAll(CONTENT))) {
        const rect = el.getBoundingClientRect();
        if (!isVisible(el, rect))
            continue;
        const text = normalizeText(el.innerText || '', 200);
        if (!text)
            continue;
        const key = [text, bbox(rect).x, bbox(rect).y].join('|');
        if (contentSeen.has(key))
            continue;
        contentSeen.add(key);
        const role = getRole(el);
        const containerId = ensureContainer(el);
        const blockId = ensureBlock(el.closest('article,section,form,table,tbody,tr,ul,ol,li,dl,div') || el);
        const locatorBundle = buildLocatorBundle(el, role, '', text, '', '');
        const node = {
            node_id: 'content-' + contentIndex++,
            frame_path: [],
            container_id: containerId,
            block_id: blockId,
            semantic_kind: semanticKind(el, role),
            role,
            text,
            bbox: bbox(rect),
            stable_attrs: stableAttrs(el),
            class_tokens: classTokens(el, 4),
            locator: locatorBundle.primary,
            locator_candidates: locatorBundle.candidates || [],
            element_snapshot: {
                tag: el.tagName.toLowerCase(),
                text,
            },
        };
        result.content_nodes.push(node);
        if (containerId) {
            const container = Array.from(containerMap.values()).find(item => item.container_id === containerId);
            if (container)
                container.child_content_ids.push(node.node_id);
        }
        if (result.content_nodes.length >= 160)
            break;
    }

    detectSiblingFieldPairs();

    return JSON.stringify(result);
}"""
