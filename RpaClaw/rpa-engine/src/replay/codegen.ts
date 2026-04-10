import type { RecordedAction } from '../action-model.js';

export function generatePythonCode(actions: RecordedAction[]): string {
  const body = actions
    .map(action => `    # ${action.kind} ${action.locator.selector}`)
    .join('\n');

  return `async def execute_skill(page, **kwargs):\n${body ? `${body}\n    return {}` : '    return {}'}\n`;
}
