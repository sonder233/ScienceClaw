import type { RecordedAction } from '../action-model.js';

export type ReplayPlanStep = {
  id: string;
  pageAlias: string;
  framePath: string[];
  selector: string;
  waitForPopup: boolean;
  waitForNavigation: boolean;
  waitForDownload: boolean;
};

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
