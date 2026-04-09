export type RuntimeSessionMode = 'idle' | 'recording' | 'replaying' | 'stopped';

export type RuntimePage = {
  alias: string;
  title: string;
  url: string;
  openerPageAlias: string | null;
  status: 'open' | 'closed';
};

export type RuntimeAction = Record<string, unknown>;

export type RuntimeSession = {
  id: string;
  userId: string;
  mode: RuntimeSessionMode;
  status: RuntimeSessionMode;
  sandboxSessionId: string;
  activePageAlias: string | null;
  pages: RuntimePage[];
  actions: RuntimeAction[];
};
