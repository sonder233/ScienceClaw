import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
CAPTURE_JS_PATH = REPO_ROOT / "RpaClaw" / "backend" / "rpa" / "vendor" / "playwright_recorder_capture.js"


def _run_capture_probe(scenario: str) -> dict:
    if not shutil.which("node"):
        pytest.skip("node is required to validate recorder capture JavaScript")

    probe = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');

        function makeElement(options = {{}}) {{
          const attributes = options.attributes || {{}};
          const element = {{
            tagName: options.tagName || 'BUTTON',
            className: options.className || '',
            id: options.id || '',
            textContent: options.textContent || '',
            parentElement: options.parentElement || null,
            nextElementSibling: options.nextElementSibling || null,
            firstElementChild: options.firstElementChild || null,
            children: options.children || [],
            isContentEditable: false,
            contains(other) {{
              if (other === this) return true;
              const stack = [...this.children];
              while (stack.length) {{
                const node = stack.pop();
                if (node === other) return true;
                if (node && node.children) stack.push(...node.children);
              }}
              return false;
            }},
            querySelector(selector) {{
              if (options.querySelector && Object.prototype.hasOwnProperty.call(options.querySelector, selector)) {{
                return options.querySelector[selector];
              }}
              const stack = [...this.children];
              while (stack.length) {{
                const node = stack.pop();
                if (!node) continue;
                if (selector === 'input[type="file"]'
                    && node.tagName === 'INPUT'
                    && (node.getAttribute && node.getAttribute('type') === 'file')) {{
                  return node;
                }}
                if (node.children) stack.push(...node.children);
              }}
              return null;
            }},
            getAttribute(name) {{
              return Object.prototype.hasOwnProperty.call(attributes, name) ? attributes[name] : null;
            }},
            hasAttribute(name) {{
              return Object.prototype.hasOwnProperty.call(attributes, name);
            }},
            closest(selector) {{
              if (options.closest && Object.prototype.hasOwnProperty.call(options.closest, selector)) {{
                return options.closest[selector];
              }}
              return null;
            }},
          }};
          for (const child of element.children) child.parentElement = element;
          return element;
        }}

        const elementsBySelector = {{}};
        let installedOptions = null;
        const emitted = [];
        const context = {{
          console,
          CSS: {{ escape: value => String(value) }},
          location: {{ href: 'https://example.test/login' }},
          document: {{
            querySelector(selector) {{
              return elementsBySelector[selector] || null;
            }},
          }},
          window: {{
            __rpa_tab_id: 'tab-1',
            __rpa_emit: payload => emitted.push(JSON.parse(payload)),
            __rpaPlaywrightRecorder: {{
              buildLocatorBundle(element) {{
                return {{
                  primary: {{
                    method: 'role',
                    role: (element.getAttribute('role') || element.tagName.toLowerCase()),
                    name: element.textContent || '',
                  }},
                  candidates: [],
                  validation: {{ status: 'ok' }},
                }};
              }},
              getRole(element) {{
                return element.getAttribute('role') || element.tagName.toLowerCase();
              }},
              getAccessibleName(element) {{
                return element.textContent || '';
              }},
            }},
            __rpaPlaywrightActions: {{
              install(options) {{
                installedOptions = options;
              }},
            }},
          }},
        }};
        context.globalThis = context.window;

        vm.createContext(context);
        vm.runInContext(fs.readFileSync({json.dumps(str(CAPTURE_JS_PATH))}, 'utf8'), context);

        try {{
          const scenario = {json.dumps(scenario)};
          if (scenario === 'plain-button-click') {{
            const button = makeElement({{
              tagName: 'BUTTON',
              textContent: 'Login',
              attributes: {{ role: 'button' }},
            }});
            installedOptions.emitAction('click', button, {{}});
          }} else if (scenario === 'menu-item-click') {{
            const menu = makeElement({{
              tagName: 'UL',
              attributes: {{ role: 'menu' }},
              className: 'dropdown-menu',
            }});
            const item = makeElement({{
              tagName: 'A',
              textContent: 'Export CSV',
              attributes: {{ role: 'menuitem' }},
              closest: {{ '[role="menu"], [role="listbox"]': menu }},
            }});
            installedOptions.emitAction('click', item, {{}});
          }} else if (scenario === 'dropdown-container-click-retargets-trigger') {{
            const button = makeElement({{
              tagName: 'BUTTON',
              textContent: 'Download',
              attributes: {{ role: 'button', 'aria-controls': 'dropdown-menu-9221', 'aria-haspopup': 'list' }},
            }});
            const menu = makeElement({{
              tagName: 'UL',
              id: 'dropdown-menu-9221',
              textContent: 'All Docs Export all',
              attributes: {{ role: 'list', id: 'dropdown-menu-9221' }},
              className: 'el-dropdown-menu el-popper dropdown-menu-list',
            }});
            elementsBySelector['[aria-controls="dropdown-menu-9221"]'] = button;
            installedOptions.emitAction('click', menu, {{}});
          }} else if (scenario === 'hover-menu-trigger') {{
            const menu = makeElement({{
              tagName: 'UL',
              attributes: {{ role: 'menu' }},
              className: 'dropdown-menu',
            }});
            const button = makeElement({{
              tagName: 'BUTTON',
              textContent: 'Export',
              attributes: {{ role: 'button', 'aria-haspopup': 'menu' }},
              nextElementSibling: menu,
            }});
            installedOptions.emitAction('hover', button, {{}});
          }} else if (scenario === 'hover-list-popup-trigger') {{
            const button = makeElement({{
              tagName: 'BUTTON',
              textContent: 'Download',
              attributes: {{ role: 'button', 'aria-haspopup': 'list' }},
            }});
            installedOptions.emitAction('hover', button, {{}});
          }} else if (scenario === 'plain-button-hover') {{
            const button = makeElement({{
              tagName: 'BUTTON',
              textContent: 'Login',
              attributes: {{ role: 'button' }},
            }});
            installedOptions.emitAction('hover', button, {{}});
          }} else if (scenario === 'dropdown-trigger-with-sibling-file-upload') {{
            // Mirrors a Vue/Element-style toolbar: a dropdown trigger and an
            // upload widget live as siblings under the same outer container.
            // Clicking the dropdown trigger must NOT retarget into the outer
            // container (which contains an input[type="file"]), otherwise the
            // recorded locator would target a wrapper whose click handler
            // opens the OS file picker.
            const fileInput = makeElement({{
              tagName: 'INPUT',
              attributes: {{ type: 'file', name: 'file' }},
            }});
            const uploadZone = makeElement({{
              tagName: 'DIV',
              className: 'aui-upload aui-upload--text',
              attributes: {{ tabindex: '0' }},
              children: [fileInput],
            }});
            const importDialog = makeElement({{
              tagName: 'DIV',
              className: 'gtm-import-c',
              children: [uploadZone],
            }});
            const triggerLabel = makeElement({{
              tagName: 'I',
              className: 'gtm-drop-button-fa gtm-fa-chevron-down',
            }});
            const trigger = makeElement({{
              tagName: 'DIV',
              textContent: 'Import',
              className: 'gtm-drop-buttons-enter gtm-drop-buttons-actived',
              children: [triggerLabel],
            }});
            const dropdownGroup = makeElement({{
              tagName: 'DIV',
              className: 'gtm-drop-buttons-main gtm-drop-buttons_mini',
              children: [trigger],
            }});
            makeElement({{
              tagName: 'DIV',
              className: 'gtm-Import-group toolbar-item',
              children: [dropdownGroup, importDialog],
            }});
            installedOptions.emitAction('click', trigger, {{}});
          }} else if (scenario === 'menu-item-hover') {{
            const menu = makeElement({{
              tagName: 'UL',
              attributes: {{ role: 'menu' }},
              className: 'dropdown-menu',
            }});
            const item = makeElement({{
              tagName: 'A',
              textContent: 'Export CSV',
              attributes: {{ role: 'menuitem' }},
              closest: {{ '[role="menu"], [role="listbox"]': menu }},
            }});
            installedOptions.emitAction('hover', item, {{}});
          }}
          process.stdout.write(JSON.stringify({{ ok: true, emitted }}));
        }} catch (error) {{
          process.stdout.write(JSON.stringify({{
            ok: false,
            name: error.name,
            message: error.message,
            stack: String(error.stack || ''),
          }}));
          process.exitCode = 1;
        }}
        """
    )
    completed = subprocess.run(
        ["node", "-e", probe],
        check=False,
        text=True,
        capture_output=True,
    )
    output = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
    assert output, completed.stderr
    payload = json.loads(output)
    assert completed.returncode == 0, payload
    assert payload["ok"] is True
    return payload


def test_plain_button_click_does_not_throw_or_get_menu_context():
    payload = _run_capture_probe("plain-button-click")

    event = payload["emitted"][0]
    assert event["action"] == "click"
    assert event["locator"]["name"] == "Login"
    assert "menu_context" not in event.get("signals", {})


def test_menu_item_click_keeps_menu_context_signal():
    payload = _run_capture_probe("menu-item-click")

    event = payload["emitted"][0]
    assert event["action"] == "click"
    assert event["signals"]["menu_context"]["is_menu_item"] is True


def test_dropdown_container_click_retargets_to_controlling_trigger():
    payload = _run_capture_probe("dropdown-container-click-retargets-trigger")

    event = payload["emitted"][0]
    assert event["action"] == "click"
    assert event["locator"]["role"] == "button"
    assert event["locator"]["name"] == "Download"
    assert event["element_snapshot"]["tag"] == "button"
    assert "menu_context" not in event.get("signals", {})


def test_hover_menu_trigger_keeps_trigger_signal():
    payload = _run_capture_probe("hover-menu-trigger")

    event = payload["emitted"][0]
    assert event["action"] == "hover"
    assert event["signals"]["hover"]["is_menu_trigger_candidate"] is True


def test_hover_list_popup_trigger_keeps_trigger_signal():
    payload = _run_capture_probe("hover-list-popup-trigger")

    event = payload["emitted"][0]
    assert event["action"] == "hover"
    assert event["signals"]["hover"]["is_menu_trigger_candidate"] is True


def test_plain_button_hover_does_not_get_trigger_signal():
    payload = _run_capture_probe("plain-button-hover")

    event = payload["emitted"][0]
    assert event["action"] == "hover"
    assert "hover" not in event.get("signals", {})


def test_dropdown_trigger_with_sibling_file_upload_does_not_retarget_outward():
    payload = _run_capture_probe("dropdown-trigger-with-sibling-file-upload")

    event = payload["emitted"][0]
    assert event["action"] == "click"
    # The locator must describe the dropdown trigger itself, not an outer
    # wrapper that also contains the hidden file-input upload zone.
    assert "Import" in (event["locator"].get("name") or "")
    snapshot_classes = event["element_snapshot"].get("classes", [])
    assert "gtm-drop-buttons-enter" in snapshot_classes
    assert "gtm-Import-group" not in snapshot_classes
    assert "aui-upload" not in snapshot_classes


def test_menu_item_hover_gets_menu_context_signal():
    payload = _run_capture_probe("menu-item-hover")

    event = payload["emitted"][0]
    assert event["action"] == "hover"
    assert event["signals"]["menu_context"]["is_menu_item"] is True
