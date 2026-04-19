import type { RpaMcpExecutionResult } from '@/api/rpaMcp';

export type PreviewTestStatus = 'untested' | 'failed' | 'success';

export function getPreviewTestStatus(
  hasSuccessfulTest: boolean,
  testResult: RpaMcpExecutionResult | null,
): PreviewTestStatus {
  if (hasSuccessfulTest) return 'success';
  if (testResult) return testResult.success ? 'success' : 'failed';
  return 'untested';
}

export function focusPreviewTestSection(section: HTMLElement | null): void {
  if (!section) return;
  section.scrollIntoView({ behavior: 'smooth', block: 'start' });
  const action = section.querySelector<HTMLElement>('[data-preview-test-action]');
  action?.focus({ preventScroll: true });
}
