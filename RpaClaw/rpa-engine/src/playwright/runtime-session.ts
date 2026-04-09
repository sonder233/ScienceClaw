import { randomUUID } from 'node:crypto';
import type { RuntimePage, RuntimeSession } from '../contracts.js';

export function createRuntimeSession(input: {
  userId: string;
  sandboxSessionId?: string;
  id?: string;
  mode?: RuntimeSession['mode'];
}): RuntimeSession {
  const mode = input.mode ?? 'idle';
  return {
    id: input.id ?? randomUUID(),
    userId: input.userId,
    mode,
    status: mode,
    sandboxSessionId: input.sandboxSessionId ?? '',
    activePageAlias: null,
    pages: [],
    actions: [],
  };
}

export function ensureRuntimePage(
  session: RuntimeSession,
  pageAlias: string,
): RuntimePage {
  let page = session.pages.find(candidate => candidate.alias === pageAlias);
  if (!page) {
    page = {
      alias: pageAlias,
      title: '',
      url: '',
      openerPageAlias: null,
      status: 'open',
    };
    session.pages.push(page);
  }
  return page;
}
