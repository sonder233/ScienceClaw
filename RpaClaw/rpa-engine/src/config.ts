export type EngineConfig = {
  NODE_ENV: string;
  RPA_ENGINE_HOST: string;
  RPA_ENGINE_PORT: number;
  RPA_ENGINE_AUTH_TOKEN: string;
};

function parsePort(value: number | string | undefined): number {
  const port = typeof value === 'number' ? value : Number(value);

  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error('RPA_ENGINE_PORT must be a valid port number');
  }

  return port;
}

export function loadConfig(overrides: Partial<EngineConfig> = {}): EngineConfig {
  return {
    NODE_ENV: overrides.NODE_ENV ?? process.env.NODE_ENV ?? 'development',
    RPA_ENGINE_HOST: overrides.RPA_ENGINE_HOST ?? process.env.RPA_ENGINE_HOST ?? '127.0.0.1',
    RPA_ENGINE_PORT: parsePort(overrides.RPA_ENGINE_PORT ?? process.env.RPA_ENGINE_PORT ?? '3310'),
    RPA_ENGINE_AUTH_TOKEN:
      overrides.RPA_ENGINE_AUTH_TOKEN ?? process.env.RPA_ENGINE_AUTH_TOKEN ?? '',
  };
}
