import { describe, expect, it } from 'vitest';
import { buildReplayPlan } from '../../src/replay/replay-runner.js';

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
});
