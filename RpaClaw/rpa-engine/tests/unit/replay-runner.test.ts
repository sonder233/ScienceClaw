import { describe, expect, it, vi } from 'vitest';
import { buildReplayPlan, runReplay } from '../../src/replay/replay-runner.js';

describe('buildReplayPlan', () => {
  it('wraps popup clicks in an expectPopup step', () => {
    const plan = buildReplayPlan([
      {
        id: '1',
        sessionId: 's1',
        seq: 1,
        kind: 'click',
        pageAlias: 'page',
        framePath: [],
        locator: { selector: 'internal:role=link[name="Open"]', locatorAst: {} },
        locatorAlternatives: [],
        signals: { popup: { targetPageAlias: 'popup1' } },
        input: {},
        timing: {},
        snapshot: {},
        status: 'recorded',
      },
    ]);

    expect(plan[0].waitForPopup).toBe(true);
  });

  it('returns failure when no replay executor is attached', async () => {
    const response = await runReplay([
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

    expect(response.result.success).toBe(false);
    expect(response.result.error).toContain('replay execution unavailable');
  });

  it('reports success only after the replay executor runs', async () => {
    const executor = vi.fn(async () => ({
      success: true,
      output: 'SKILL_SUCCESS',
      data: { ok: true },
    }));

    const response = await runReplay(
      [
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
      ],
      {},
      executor,
    );

    expect(executor).toHaveBeenCalledTimes(1);
    expect(response.result.success).toBe(true);
    expect(response.result.output).toBe('SKILL_SUCCESS');
  });
});
