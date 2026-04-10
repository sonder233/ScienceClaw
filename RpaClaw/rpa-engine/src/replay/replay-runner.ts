import type { RecordedAction } from '../action-model.js';
import { generatePythonCode } from './codegen.js';

export type ReplayPlanStep = {
  id: string;
  pageAlias: string;
  framePath: string[];
  selector: string;
  waitForPopup: boolean;
  waitForNavigation: boolean;
  waitForDownload: boolean;
};

type ReplayParams = Record<string, unknown>;

export type ReplayExecutionResult = {
  success: boolean;
  output: string;
  error?: string;
  data?: Record<string, unknown>;
};

export type ReplayResponse = {
  result: ReplayExecutionResult;
  logs: string[];
  plan: ReplayPlanStep[];
  script: string;
};

export type ReplayExecutor = (input: {
  actions: RecordedAction[];
  plan: ReplayPlanStep[];
  script: string;
  params: ReplayParams;
}) => Promise<ReplayExecutionResult> | ReplayExecutionResult;

export function buildReplayPlan(actions: RecordedAction[]): ReplayPlanStep[] {
  return actions.map(action => ({
    id: action.id,
    pageAlias: action.pageAlias,
    framePath: action.framePath,
    selector: action.locator.selector,
    waitForPopup: Boolean(action.signals.popup),
    waitForNavigation: Boolean(action.signals.navigation),
    waitForDownload: Boolean(action.signals.download),
  }));
}

export async function runReplay(
  actions: RecordedAction[],
  params: ReplayParams = {},
  executor?: ReplayExecutor,
): Promise<ReplayResponse> {
  const plan = buildReplayPlan(actions);
  const script = generatePythonCode(actions, params as Record<string, { original_value?: unknown; sensitive?: boolean }>);

  if (!executor) {
    const error =
      'replay execution unavailable until runtime sessions attach live Playwright pages';
    return {
      result: {
        success: false,
        output: `SKILL_ERROR: ${error}`,
        error,
        data: { replayPlan: plan },
      },
      logs: ['Engine replay execution unavailable: no browser runtime adapter is connected'],
      plan,
      script,
    };
  }

  try {
    const result = await executor({ actions, plan, script, params });
    return {
      result,
      logs: [`Engine replay executed ${plan.length} step(s)`],
      plan,
      script,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      result: {
        success: false,
        output: `SKILL_ERROR: ${message}`,
        error: message,
        data: { replayPlan: plan },
      },
      logs: [`Engine replay failed: ${message}`],
      plan,
      script,
    };
  }
}
