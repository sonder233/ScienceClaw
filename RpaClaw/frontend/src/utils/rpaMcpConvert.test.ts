import { describe, expect, it, vi } from 'vitest';

import { focusPreviewTestSection, getPreviewTestStatus } from './rpaMcpConvert';

describe('getPreviewTestStatus', () => {
  it('returns untested before any preview test runs', () => {
    expect(getPreviewTestStatus(false, null)).toBe('untested');
  });

  it('returns failed after an unsuccessful preview test', () => {
    expect(getPreviewTestStatus(false, { success: false })).toBe('failed');
  });

  it('returns success after a successful preview test', () => {
    expect(getPreviewTestStatus(true, { success: true })).toBe('success');
  });
});

describe('focusPreviewTestSection', () => {
  it('scrolls the preview test section into view and focuses its primary action', () => {
    const focus = vi.fn();
    const querySelector = vi.fn(() => ({ focus }));
    const scrollIntoView = vi.fn();

    focusPreviewTestSection({
      scrollIntoView,
      querySelector,
    } as unknown as HTMLElement);

    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' });
    expect(querySelector).toHaveBeenCalledWith('[data-preview-test-action]');
    expect(focus).toHaveBeenCalledWith({ preventScroll: true });
  });

  it('does nothing when the section is missing', () => {
    expect(() => focusPreviewTestSection(null)).not.toThrow();
  });
});
