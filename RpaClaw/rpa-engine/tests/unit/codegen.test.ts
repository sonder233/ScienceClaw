import { describe, expect, it } from 'vitest';
import { generatePythonCode } from '../../src/replay/codegen.js';

describe('generatePythonCode', () => {
  it('renders runnable playwright python from structured selectors', () => {
    const code = generatePythonCode([
      {
        id: '1',
        sessionId: 's1',
        seq: 1,
        kind: 'click',
        pageAlias: 'page',
        framePath: [],
        locator: {
          selector: 'internal:role=button[name="Save"]',
          locatorAst: { kind: 'role', role: 'button', name: 'Save' },
        },
        locatorAlternatives: [],
        signals: {},
        input: {},
        timing: {},
        snapshot: {},
        status: 'recorded',
      },
    ]);

    expect(code).toContain('from playwright.async_api import async_playwright');
    expect(code).toContain('async def execute_skill(page, **kwargs):');
    expect(code).toContain(
      'await current_page.get_by_role("button", name="Save", exact=True).click()',
    );
    expect(code).toContain('asyncio.run(main())');
  });

  it('parameterizes fill values from export params', () => {
    const code = generatePythonCode(
      [
        {
          id: '1',
          sessionId: 's1',
          seq: 1,
          kind: 'fill',
          pageAlias: 'page',
          framePath: [],
          locator: {
            selector: 'internal:testid=[data-testid="email"]',
            locatorAst: { kind: 'testId', value: 'email' },
          },
          locatorAlternatives: [],
          signals: {},
          input: { value: 'person@example.com' },
          timing: {},
          snapshot: {},
          status: 'recorded',
        },
      ],
      {
        email: {
          original_value: 'person@example.com',
          sensitive: false,
        },
      },
    );

    expect(code).toContain(
      'await current_page.get_by_test_id("email").fill(kwargs.get(\'email\', \'person@example.com\'))',
    );
  });

  it('wraps popup-producing press actions with expect_popup', () => {
    const code = generatePythonCode([
      {
        id: '1',
        sessionId: 's1',
        seq: 1,
        kind: 'press',
        pageAlias: 'page',
        framePath: [],
        locator: {
          selector: '#s',
          locatorAst: { kind: 'css', value: '#s' },
        },
        locatorAlternatives: [],
        signals: {
          popup: {
            targetPageAlias: 'page-2',
          },
        },
        input: { key: 'Enter', value: 'Enter' },
        timing: {},
        snapshot: {},
        status: 'recorded',
      },
    ]);

    expect(code).toContain('async with current_page.expect_popup() as popup_info:');
    expect(code).toContain('await current_page.locator("#s").press("Enter")');
    expect(code).toContain('pages["page-2"] = new_page');
  });
});
