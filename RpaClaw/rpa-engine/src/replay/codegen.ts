import type { RecordedAction } from '../action-model.js';

type ParamInfo = {
  original_value?: unknown;
  sensitive?: boolean;
};

type ExportParams = Record<string, ParamInfo>;

const RUNNER_TEMPLATE = `import asyncio
import json as _json
import sys
from playwright.async_api import async_playwright

{execute_skill_func}


async def main():
    kwargs = {}
    for arg in sys.argv[1:]:
        if arg.startswith("--") and "=" in arg:
            k, v = arg[2:].split("=", 1)
            kwargs[k] = v

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=False)
    context = await browser.new_context(no_viewport=True, accept_downloads=True)
    page = await context.new_page()
    try:
        _result = await execute_skill(page, **kwargs)
        if _result:
            print("SKILL_DATA:" + _json.dumps(_result, ensure_ascii=False, default=str))
        print("SKILL_SUCCESS")
    except Exception as e:
        print(f"SKILL_ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await context.close()
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
`;

export function generatePythonCode(
  actions: RecordedAction[],
  params: ExportParams = {},
): string {
  const lines = [
    'async def execute_skill(page, **kwargs):',
    '    _results = {}',
    '    pages = {"page": page}',
    '    current_page = page',
  ];

  const firstAlias = actions[0]?.pageAlias;
  if (firstAlias && firstAlias !== 'page') {
    lines.push(`    pages["${escapePythonString(firstAlias)}"] = page`);
    lines.push(`    current_page = pages["${escapePythonString(firstAlias)}"]`);
  }

  let currentAlias = firstAlias ?? 'page';
  const knownPages = new Set<string>(['page']);
  if (firstAlias) {
    knownPages.add(firstAlias);
  }

  for (const action of actions) {
    const alias = action.pageAlias || currentAlias;
    if (!knownPages.has(alias)) {
      lines.push(`    pages["${escapePythonString(alias)}"] = current_page`);
      knownPages.add(alias);
    }
    if (alias !== currentAlias) {
      lines.push(`    current_page = pages["${escapePythonString(alias)}"]`);
      currentAlias = alias;
    }

    const scopeVar = buildScope(lines, action.framePath);
    const locator = buildLocator(scopeVar, action);
    lines.push(...buildActionLines(action, locator, params, knownPages));
  }

  lines.push('    return _results');

  return RUNNER_TEMPLATE.replace('{execute_skill_func}', lines.join('\n'));
}

function buildActionLines(
  action: RecordedAction,
  locator: string,
  params: ExportParams,
  knownPages: Set<string>,
): string[] {
  const lines: string[] = [];
  const popupSignal = getSignalRecord(action.signals.popup);
  const navigationSignal = getSignalRecord(action.signals.navigation);
  const downloadSignal = getSignalRecord(action.signals.download);

  if (action.kind === 'navigate') {
    const url = String(action.input.url ?? navigationSignal.url ?? action.snapshot.url ?? '').trim();
    if (url) {
      lines.push(`    await current_page.goto("${escapePythonString(url)}")`);
      lines.push('    await current_page.wait_for_load_state("domcontentloaded")');
    }
    return lines;
  }

  if (popupSignal && action.kind === 'click') {
    const targetAlias = String(popupSignal.targetPageAlias ?? `${action.pageAlias}_popup`);
    knownPages.add(targetAlias);
    lines.push('    async with current_page.expect_popup() as popup_info:');
    lines.push(`        await ${locator}.click()`);
    lines.push('    new_page = await popup_info.value');
    lines.push('    await new_page.wait_for_load_state("domcontentloaded")');
    lines.push(`    pages["${escapePythonString(targetAlias)}"] = new_page`);
    lines.push('    current_page = new_page');
    return lines;
  }

  if (downloadSignal && action.kind === 'click') {
    lines.push('    async with current_page.expect_download() as download_info:');
    lines.push(`        await ${locator}.click()`);
    lines.push('    _download = await download_info.value');
    lines.push('    _results["download"] = {"filename": _download.suggested_filename}');
    return lines;
  }

  if (navigationSignal && action.kind === 'click') {
    lines.push('    async with current_page.expect_navigation(wait_until="domcontentloaded"):');
    lines.push(`        await ${locator}.click()`);
    return lines;
  }

  switch (action.kind) {
    case 'click':
      lines.push(`    await ${locator}.click()`);
      lines.push('    await current_page.wait_for_timeout(500)');
      return lines;
    case 'fill':
      lines.push(
        `    await ${locator}.fill(${parameterizeValue(
          action.input.value ?? action.input.text ?? '',
          params,
        )})`,
      );
      return lines;
    case 'press':
      lines.push(
        `    await ${locator}.press("${escapePythonString(
          String(action.input.key ?? action.input.value ?? ''),
        )}")`,
      );
      return lines;
    case 'selectOption':
      lines.push(
        `    await ${locator}.select_option("${escapePythonString(
          String(action.input.value ?? ''),
        )}")`,
      );
      return lines;
    case 'check':
      lines.push(`    await ${locator}.check()`);
      return lines;
    case 'uncheck':
      lines.push(`    await ${locator}.uncheck()`);
      return lines;
    case 'closePage':
      lines.push('    await current_page.close()');
      return lines;
    case 'openPage':
      lines.push(`    current_page = pages["${escapePythonString(action.pageAlias)}"]`);
      return lines;
    default:
      lines.push(`    # Unsupported action ${action.kind}`);
      return lines;
  }
}

function buildScope(lines: string[], framePath: string[]): string {
  if (framePath.length === 0) {
    return 'current_page';
  }

  let parent = 'current_page';
  for (const selector of framePath) {
    lines.push(`    frame_scope = ${parent}.frame_locator("${escapePythonString(selector)}")`);
    parent = 'frame_scope';
  }
  return 'frame_scope';
}

function buildLocator(scopeVar: string, action: RecordedAction): string {
  return buildLocatorFromAst(scopeVar, action.locator.locatorAst, action.locator.selector);
}

function buildLocatorFromAst(
  scopeVar: string,
  locatorAst: Record<string, unknown>,
  selector: string,
): string {
  const kind = String(locatorAst.kind ?? locatorAst.method ?? inferKindFromSelector(selector));

  if (kind === 'nested') {
    const parent = asRecord(locatorAst.parent);
    const child = asRecord(locatorAst.child);
    const parentLocator = buildLocatorFromAst(scopeVar, parent, selector);
    return buildLocatorFromAst(parentLocator, child, selector);
  }

  if (kind === 'role') {
    const role = String(locatorAst.role ?? inferRoleFromSelector(selector) ?? 'button');
    const name = String(locatorAst.name ?? locatorAst.value ?? inferNameFromSelector(selector) ?? '').trim();
    if (name) {
      return `${scopeVar}.get_by_role("${escapePythonString(role)}", name="${escapePythonString(name)}", exact=True)`;
    }
    return `${scopeVar}.get_by_role("${escapePythonString(role)}")`;
  }

  if (kind === 'testId' || kind === 'testid') {
    const value = String(locatorAst.value ?? inferTestIdFromSelector(selector) ?? '').trim();
    return `${scopeVar}.get_by_test_id("${escapePythonString(value)}")`;
  }

  if (kind === 'label') {
    return `${scopeVar}.get_by_label("${escapePythonString(String(locatorAst.value ?? ''))}", exact=True)`;
  }

  if (kind === 'placeholder') {
    return `${scopeVar}.get_by_placeholder("${escapePythonString(String(locatorAst.value ?? ''))}", exact=True)`;
  }

  if (kind === 'alt') {
    return `${scopeVar}.get_by_alt_text("${escapePythonString(String(locatorAst.value ?? ''))}", exact=True)`;
  }

  if (kind === 'title') {
    return `${scopeVar}.get_by_title("${escapePythonString(String(locatorAst.value ?? ''))}", exact=True)`;
  }

  if (kind === 'text') {
    return `${scopeVar}.get_by_text("${escapePythonString(String(locatorAst.value ?? ''))}", exact=True)`;
  }

  const cssValue = String(locatorAst.value ?? selector ?? 'body');
  return `${scopeVar}.locator("${escapePythonString(cssValue)}")`;
}

function parameterizeValue(value: unknown, params: ExportParams): string {
  const stringValue = String(value ?? '');
  for (const [name, info] of Object.entries(params)) {
    if (String(info.original_value ?? '') !== stringValue) {
      continue;
    }
    if (info.sensitive) {
      return `kwargs["${escapePythonString(name)}"]`;
    }
    return `kwargs.get('${escapePythonString(name)}', '${escapePythonString(stringValue)}')`;
  }
  return `'${escapePythonString(stringValue)}'`;
}

function asRecord(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

function getSignalRecord(value: unknown): Record<string, unknown> | null {
  const record = asRecord(value);
  return Object.keys(record).length > 0 ? record : null;
}

function inferKindFromSelector(selector: string): string {
  if (selector.startsWith('internal:role=')) {
    return 'role';
  }
  if (selector.startsWith('internal:testid=')) {
    return 'testId';
  }
  return 'css';
}

function inferRoleFromSelector(selector: string): string | null {
  const match = selector.match(/^internal:role=([a-zA-Z0-9_-]+)/);
  return match?.[1] ?? null;
}

function inferNameFromSelector(selector: string): string | null {
  const match = selector.match(/\[name="([^"]+)"\]/);
  return match?.[1] ?? null;
}

function inferTestIdFromSelector(selector: string): string | null {
  const match = selector.match(/\[data-testid="([^"]+)"\]/);
  return match?.[1] ?? null;
}

function escapePythonString(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/'/g, "\\'");
}
