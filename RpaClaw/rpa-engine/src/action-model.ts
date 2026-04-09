export type ActionKind =
  | 'openPage'
  | 'navigate'
  | 'click'
  | 'fill'
  | 'press'
  | 'selectOption'
  | 'check'
  | 'uncheck'
  | 'closePage';

export type LocatorDescriptor = {
  selector: string;
  locatorAst: Record<string, unknown>;
};

export type LocatorCandidate = LocatorDescriptor & {
  score: number;
  matchCount: number;
  visibleMatchCount: number;
  isSelected: boolean;
  engine: 'playwright';
  reason: string;
};

export type RecordedAction = {
  id: string;
  sessionId: string;
  seq: number;
  kind: ActionKind;
  pageAlias: string;
  framePath: string[];
  locator: LocatorDescriptor;
  locatorAlternatives: LocatorCandidate[];
  signals: Record<string, unknown>;
  input: Record<string, unknown>;
  timing: Record<string, unknown>;
  snapshot: Record<string, unknown>;
  status: 'recorded' | 'updated' | 'replayed' | 'failed';
};

function cloneValue<T>(value: T): T {
  if (Array.isArray(value)) {
    return value.map(item => cloneValue(item)) as T;
  }

  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, nestedValue]) => [key, cloneValue(nestedValue)]),
    ) as T;
  }

  return value;
}

export function cloneLocatorDescriptor(locator: LocatorDescriptor): LocatorDescriptor {
  return {
    selector: locator.selector,
    locatorAst: cloneValue(locator.locatorAst),
  };
}

export function cloneLocatorCandidate(candidate: LocatorCandidate): LocatorCandidate {
  return {
    ...candidate,
    locatorAst: cloneValue(candidate.locatorAst),
  };
}

export function normalizeAction(
  input: Partial<RecordedAction> &
    Pick<RecordedAction, 'kind' | 'pageAlias' | 'framePath' | 'locator'>,
): RecordedAction {
  return {
    id: input.id ?? 'pending',
    sessionId: input.sessionId ?? 'pending',
    seq: input.seq ?? 0,
    kind: input.kind,
    pageAlias: input.pageAlias,
    framePath: [...input.framePath],
    locator: cloneLocatorDescriptor(input.locator),
    locatorAlternatives: (input.locatorAlternatives ?? []).map(candidate =>
      cloneLocatorCandidate(candidate),
    ),
    signals: cloneValue(input.signals ?? {}),
    input: cloneValue(input.input ?? {}),
    timing: cloneValue(input.timing ?? {}),
    snapshot: cloneValue(input.snapshot ?? {}),
    status: input.status ?? 'recorded',
  };
}
