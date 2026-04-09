import { describe, expect, it } from 'vitest';
import { normalizeAction } from '../../src/action-model.js';

describe('normalizeAction', () => {
  it('keeps click as the action kind and moves popup into signals', () => {
    const action = normalizeAction({
      kind: 'click',
      pageAlias: 'page',
      framePath: [],
      locator: {
        selector: 'internal:role=button[name="Open"]',
        locatorAst: {},
      },
      signals: { popup: { targetPageAlias: 'popup1' } },
    });

    expect(action.kind).toBe('click');
    expect(action.signals.popup).toEqual({ targetPageAlias: 'popup1' });
    expect(action.status).toBe('recorded');
  });

  it('detaches normalized action data from caller-owned objects', () => {
    const locatorAst = { role: 'button' };
    const alternativeAst = { role: 'button', name: 'Save' };
    const signals = { popup: { targetPageAlias: 'popup1' } };
    const input = { text: 'alpha' };
    const timing = { timeoutMs: 500 };
    const snapshot = { url: 'https://example.test' };
    const action = normalizeAction({
      kind: 'click',
      pageAlias: 'page',
      framePath: ['iframe[name="shell"]'],
      locator: {
        selector: 'internal:role=button[name="Open"]',
        locatorAst,
      },
      locatorAlternatives: [
        {
          selector: 'internal:testid=[data-testid="open"]',
          locatorAst: alternativeAst,
          score: 10,
          matchCount: 1,
          visibleMatchCount: 1,
          isSelected: false,
          engine: 'playwright',
          reason: 'fallback',
        },
      ],
      signals,
      input,
      timing,
      snapshot,
    });

    locatorAst.role = 'link';
    alternativeAst.name = 'Changed';
    signals.popup.targetPageAlias = 'popup2';
    input.text = 'beta';
    timing.timeoutMs = 1000;
    snapshot.url = 'https://mutated.test';

    expect(action.locator.locatorAst).toEqual({ role: 'button' });
    expect(action.locatorAlternatives[0]?.locatorAst).toEqual({
      role: 'button',
      name: 'Save',
    });
    expect(action.signals).toEqual({ popup: { targetPageAlias: 'popup1' } });
    expect(action.input).toEqual({ text: 'alpha' });
    expect(action.timing).toEqual({ timeoutMs: 500 });
    expect(action.snapshot).toEqual({ url: 'https://example.test' });
  });
});
