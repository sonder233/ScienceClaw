(() => {
    if (globalThis.__rpaPlaywrightActions) return;

    var SUPPRESSION_WINDOW_MS = 250;

    function now() {
        return Date.now();
    }

    function isElement(node) {
        return !!(node && node.nodeType === Node.ELEMENT_NODE);
    }

    function closestElement(node, selector) {
        if (!isElement(node) || !node.closest) return null;
        return node.closest(selector);
    }

    function asCheckbox(node) {
        if (!isElement(node) || node.nodeName !== 'INPUT') return null;
        var input = node;
        var type = (input.type || '').toLowerCase();
        return type === 'checkbox' || type === 'radio' ? input : null;
    }

    function associatedControl(node) {
        if (!isElement(node)) return null;
        if (node.nodeName === 'LABEL' && node.control) return node.control;
        var label = closestElement(node, 'label');
        return label && label.control ? label.control : null;
    }

    function roleToggleTarget(node) {
        return closestElement(node, '[role="checkbox"], [role="radio"], [role="switch"]');
    }

    function logicalToggleTarget(node) {
        return asCheckbox(node) || roleToggleTarget(node);
    }

    function logicalActionTarget(node, retarget) {
        return logicalToggleTarget(node) || retarget(node);
    }

    function hoverMenuTriggerTarget(node, retarget) {
        var target = closestElement(node, 'button, a, [role="button"], [role="link"]') || retarget(node);
        if (!isElement(target)) return null;
        var role = (target.getAttribute('role') || '').toLowerCase();
        if (target.nodeName === 'BUTTON' || target.nodeName === 'A') return target;
        if (role === 'button' || role === 'link') return target;
        return null;
    }

    function toggleState(node) {
        var checkbox = asCheckbox(node);
        if (checkbox) return !!checkbox.checked;
        if (!isElement(node)) return false;
        return (node.getAttribute('aria-checked') || '').toLowerCase() === 'true';
    }

    function isFileInput(node) {
        return isElement(node) && node.nodeName === 'INPUT' && (node.type || '').toLowerCase() === 'file';
    }

    function isRangeInput(node) {
        return isElement(node) && node.nodeName === 'INPUT' && (node.type || '').toLowerCase() === 'range';
    }

    function isDateLikeInput(node) {
        if (!isElement(node) || node.nodeName !== 'INPUT') return false;
        var type = (node.type || '').toLowerCase();
        return type === 'date' || type === 'datetime-local' || type === 'month' || type === 'time' || type === 'week';
    }

    var MAX_UPLOAD_STAGING_BYTES = 25 * 1024 * 1024;

    function fileMeta(file) {
        return {
            name: file.name,
            size: file.size,
            mime: file.type,
            last_modified: file.lastModified,
        };
    }

    function readFilePayload(file) {
        var meta = fileMeta(file);
        if (file.size > MAX_UPLOAD_STAGING_BYTES) {
            meta.missing_reason = 'too_large';
            return Promise.resolve(meta);
        }
        return new Promise(function(resolve) {
            var reader = new FileReader();
            reader.onload = function() {
                var result = String(reader.result || '');
                var comma = result.indexOf(',');
                meta.data_base64 = comma >= 0 ? result.slice(comma + 1) : '';
                resolve(meta);
            };
            reader.onerror = function() {
                meta.missing_reason = 'read_error';
                resolve(meta);
            };
            reader.readAsDataURL(file);
        });
    }

    function sameOrRelatedTarget(a, b) {
        if (!a || !b) return false;
        if (a === b) return true;
        if (isElement(a) && isElement(b)) {
            if ((a.contains && a.contains(b)) || (b.contains && b.contains(a))) return true;
        }
        var aControl = associatedControl(a) || a;
        var bControl = associatedControl(b) || b;
        return aControl === bControl;
    }

    function shouldGenerateKeyPressFor(event, target) {
        if (typeof event.key !== 'string') return false;
        if (event.key === 'Enter' && ((target && target.nodeName === 'TEXTAREA') || (target && target.isContentEditable))) return false;
        if (['Backspace', 'Delete', 'AltGraph'].indexOf(event.key) >= 0) return false;
        if (event.key === '@' && event.code === 'KeyL') return false;
        if (navigator.platform.includes('Mac')) {
            if (event.key === 'v' && event.metaKey) return false;
        } else {
            if (event.key === 'v' && event.ctrlKey) return false;
            if (event.key === 'Insert' && event.shiftKey) return false;
        }
        if (['Shift', 'Control', 'Meta', 'Alt', 'Process'].indexOf(event.key) >= 0) return false;
        var hasModifier = event.ctrlKey || event.altKey || event.metaKey;
        if (event.key.length === 1 && !hasModifier) return !!logicalToggleTarget(target);
        return true;
    }

    function install(options) {
        var doc = options.document || document;
        var isPaused = options.isPaused || function() { return false; };
        var retarget = options.retarget || function(node) { return node; };
        var emitAction = options.emitAction;
        if (typeof emitAction !== 'function') throw new Error('emitAction is required');

        var activeTarget = null;
        var lastHoverTarget = null;
        var recentAction = null;
        var recentFileInput = new WeakMap();
        var listeners = [];

        function addListener(type, handler) {
            doc.addEventListener(type, handler, true);
            listeners.push(function() {
                doc.removeEventListener(type, handler, true);
            });
        }

        function rememberActiveTarget(node) {
            activeTarget = logicalActionTarget(node, retarget);
            return activeTarget;
        }

        function markAction(action, target) {
            recentAction = {
                action: action,
                target: target,
                time: now(),
            };
        }

        function shouldSuppress(action, target) {
            if (!recentAction) return false;
            if (now() - recentAction.time > SUPPRESSION_WINDOW_MS) return false;
            if (!sameOrRelatedTarget(target, recentAction.target)) return false;
            if (recentAction.action === action) return true;
            if (recentAction.action === 'click') {
                return action === 'check' || action === 'uncheck';
            }
            if (recentAction.action === 'check' || recentAction.action === 'uncheck') {
                return action === 'click' || action === 'fill' || action === 'select';
            }
            if (recentAction.action === 'select') {
                return action === 'click' || action === 'fill' || action === 'select';
            }
            if (recentAction.action === 'set_input_files') {
                return action === 'set_input_files' || action === 'fill';
            }
            return false;
        }

        function wrongTarget(node) {
            var logicalTarget = logicalActionTarget(node, retarget);
            return !!(activeTarget && logicalTarget && !sameOrRelatedTarget(activeTarget, logicalTarget));
        }

        function emitLogicalAction(action, node, payload) {
            var target = logicalActionTarget(node, retarget);
            if (!target) return;
            if (shouldSuppress(action, target)) return;
            emitAction(action, target, payload || {});
            markAction(action, target);
        }

        function fileInputSignature(target) {
            var inputFiles = target.files || [];
            var parts = [];
            for (var i = 0; i < inputFiles.length; i++) {
                parts.push([inputFiles[i].name, inputFiles[i].size, inputFiles[i].lastModified].join(':'));
            }
            return parts.join('|');
        }

        function shouldSuppressFileInput(target) {
            var signature = fileInputSignature(target);
            var previous = recentFileInput.get(target);
            if (previous && previous.signature === signature && now() - previous.time < 600) return true;
            recentFileInput.set(target, { signature: signature, time: now() });
            return false;
        }

        function emitFileInputAction(target) {
            if (shouldSuppressFileInput(target)) return;
            var files = [];
            var meta = [];
            var payloadPromises = [];
            var inputFiles = target.files || [];
            for (var i = 0; i < inputFiles.length; i++) {
                files.push(inputFiles[i].name);
                meta.push(fileMeta(inputFiles[i]));
                payloadPromises.push(readFilePayload(inputFiles[i]));
            }
            Promise.all(payloadPromises).then(function(uploadPayloads) {
                emitLogicalAction('set_input_files', target, {
                    value: files[0] || '',
                    signals: {
                        set_input_files: { files: files, meta: meta },
                        upload_payloads: uploadPayloads,
                    },
                });
            });
        }

        addListener('focusin', function(event) {
            if (!event.isTrusted || isPaused()) return;
            rememberActiveTarget(event.target);
        });

        addListener('focusout', function(event) {
            if (activeTarget && sameOrRelatedTarget(activeTarget, logicalActionTarget(event.target, retarget))) {
                activeTarget = null;
            }
        });

        addListener('mouseover', function(event) {
            if (!event.isTrusted || isPaused()) return;
            var target = hoverMenuTriggerTarget(event.target, retarget);
            if (!target) return;
            if (lastHoverTarget && sameOrRelatedTarget(lastHoverTarget, target)) return;
            lastHoverTarget = target;
            emitLogicalAction('hover', target, {});
        });

        addListener('click', function(event) {
            if (!event.isTrusted || isPaused()) return;
            var target = event.target;
            rememberActiveTarget(target);
            if (!isElement(target)) return;
            if (target.nodeName === 'SELECT' || target.nodeName === 'OPTION') return;
            if (isRangeInput(target) || isDateLikeInput(target)) return;

            var toggleTarget = logicalToggleTarget(target);
            if (toggleTarget) {
                emitLogicalAction(toggleState(toggleTarget) ? 'check' : 'uncheck', toggleTarget, {});
                return;
            }

            var associated = associatedControl(target);
            if (associated && asCheckbox(associated)) {
                emitLogicalAction('click', target, {});
                return;
            }

            emitLogicalAction('click', target, {});
        });

        addListener('input', function(event) {
            if (!event.isTrusted || isPaused()) return;
            var target = event.target;
            rememberActiveTarget(target);
            if (!isElement(target)) return;

            if (isFileInput(target)) {
                if (window.__rpaSuppressFileInputEventsUntil && Date.now() < window.__rpaSuppressFileInputEventsUntil) return;
                emitFileInputAction(target);
                return;
            }

            if (isRangeInput(target)) {
                emitLogicalAction('fill', target, { value: target.value || '' });
                return;
            }

            if (target.nodeName === 'SELECT') {
                if (wrongTarget(target)) return;
                emitLogicalAction('select', target, { value: target.value || '' });
                return;
            }

            if ((target.nodeName === 'INPUT' || target.nodeName === 'TEXTAREA' || target.isContentEditable)) {
                if (asCheckbox(target)) return;
                if (wrongTarget(target)) return;
                var isPassword = target.nodeName === 'INPUT' && (target.type || '').toLowerCase() === 'password';
                emitLogicalAction('fill', target, {
                    value: isPassword ? '{{credential}}' : (target.isContentEditable ? (target.innerText || '') : (target.value || '')),
                    sensitive: isPassword,
                });
            }
        });

        addListener('change', function(event) {
            if (!event.isTrusted || isPaused()) return;
            var target = event.target;
            rememberActiveTarget(target);
            if (!isElement(target)) return;
            if (asCheckbox(target)) {
                emitLogicalAction(toggleState(target) ? 'check' : 'uncheck', target, {});
                return;
            }
            if (target.nodeName === 'SELECT') {
                if (wrongTarget(target)) return;
                emitLogicalAction('select', target, { value: target.value || '' });
                return;
            }
            if (isFileInput(target)) {
                if (window.__rpaSuppressFileInputEventsUntil && Date.now() < window.__rpaSuppressFileInputEventsUntil) return;
                emitFileInputAction(target);
            }
        });

        addListener('keydown', function(event) {
            if (!event.isTrusted || isPaused()) return;
            var target = logicalActionTarget(event.target, retarget);
            if (!target || !shouldGenerateKeyPressFor(event, target)) return;
            if (wrongTarget(event.target)) return;

            if (event.key === ' ') {
                var toggleTarget = logicalToggleTarget(event.target);
                if (toggleTarget) {
                    emitLogicalAction(toggleState(toggleTarget) ? 'uncheck' : 'check', toggleTarget, {});
                    return;
                }
            }

            emitLogicalAction('press', target, { value: event.key });
        });

        return {
            uninstall: function() {
                while (listeners.length) listeners.pop()();
            },
        };
    }

    globalThis.__rpaPlaywrightActions = {
        install: install,
    };
})();
