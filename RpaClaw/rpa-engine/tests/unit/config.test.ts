import { describe, expect, it } from 'vitest';
import { loadConfig } from '../../src/config.js';

describe('loadConfig', () => {
  it('rejects invalid RPA_ENGINE_PORT values', () => {
    const originalPort = process.env.RPA_ENGINE_PORT;
    process.env.RPA_ENGINE_PORT = 'not-a-number';

    try {
      expect(() => loadConfig()).toThrow('RPA_ENGINE_PORT must be a valid port number');
    } finally {
      if (originalPort === undefined) {
        delete process.env.RPA_ENGINE_PORT;
      } else {
        process.env.RPA_ENGINE_PORT = originalPort;
      }
    }
  });
});
