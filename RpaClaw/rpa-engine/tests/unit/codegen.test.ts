import { describe, expect, it } from 'vitest';
import { generatePythonCode } from '../../src/replay/codegen.js';

describe('generatePythonCode', () => {
  it('renders replay actions from structured selectors', () => {
    const code = generatePythonCode([
      {
        id: '1',
        sessionId: 's1',
        seq: 1,
        kind: 'click',
        pageAlias: 'page',
        framePath: [],
        locator: { selector: 'internal:role=button[name="Save"]', locatorAst: {} },
        locatorAlternatives: [],
        signals: {},
        input: {},
        timing: {},
        snapshot: {},
        status: 'recorded',
      },
    ]);

    expect(code).toContain('async def execute_skill(page, **kwargs):');
    expect(code).toContain('# click internal:role=button[name="Save"]');
  });
});
