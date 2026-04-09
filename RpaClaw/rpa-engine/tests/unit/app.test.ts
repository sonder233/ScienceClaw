import { describe, expect, it } from 'vitest';
import { buildApp } from '../../src/app.js';
import { EventBus } from '../../src/event-bus.js';

describe('engine health endpoint', () => {
  it('returns the service name and mode', async () => {
    const app = buildApp({ NODE_ENV: 'test', RPA_ENGINE_PORT: 3310 });
    const response = await app.inject({ method: 'GET', url: '/health' });
    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      status: 'ok',
      service: 'rpa-engine',
    });
  });

  it('creates and retrieves runtime sessions', async () => {
    const app = buildApp({ NODE_ENV: 'test', RPA_ENGINE_PORT: 3310 });

    const createResponse = await app.inject({
      method: 'POST',
      url: '/sessions',
      payload: { userId: 'u1', sandboxSessionId: 'sandbox-1' },
    });

    expect(createResponse.statusCode).toBe(200);
    const created = createResponse.json().session as {
      id: string;
      userId: string;
      mode: string;
      status: string;
      sandboxSessionId: string;
      activePageAlias: string | null;
      pages: unknown[];
      actions: unknown[];
    };
    expect(created.userId).toBe('u1');
    expect(created.mode).toBe('idle');
    expect(created.status).toBe('idle');
    expect(created.sandboxSessionId).toBe('sandbox-1');
    expect(created.activePageAlias).toBeNull();
    expect(created.pages).toEqual([]);
    expect(created.actions).toEqual([]);

    const getResponse = await app.inject({
      method: 'GET',
      url: `/sessions/${created.id}`,
    });

    expect(getResponse.statusCode).toBe(200);
    expect(getResponse.json()).toEqual({
      session: created,
    });
  });

  it('activates tabs, navigates, and stops through session control endpoints', async () => {
    const app = buildApp({ NODE_ENV: 'test', RPA_ENGINE_PORT: 3310 });

    const createResponse = await app.inject({
      method: 'POST',
      url: '/sessions',
      payload: { userId: 'u1', sandboxSessionId: 'sandbox-1' },
    });
    const created = createResponse.json().session as {
      id: string;
      activePageAlias: string | null;
      pages: Array<{ alias: string; url: string }>;
      mode: string;
      status: string;
    };

    const activateResponse = await app.inject({
      method: 'POST',
      url: `/sessions/${created.id}/activate`,
      payload: { pageAlias: 'page-1' },
    });

    expect(activateResponse.statusCode).toBe(200);
    expect(activateResponse.json().session).toMatchObject({
      id: created.id,
      activePageAlias: 'page-1',
      pages: [{ alias: 'page-1', url: '' }],
    });

    const navigateResponse = await app.inject({
      method: 'POST',
      url: `/sessions/${created.id}/navigate`,
      payload: { url: 'docs.example.com' },
    });

    expect(navigateResponse.statusCode).toBe(200);
    expect(navigateResponse.json().session).toMatchObject({
      id: created.id,
      activePageAlias: 'page-1',
      pages: [{ alias: 'page-1', url: 'https://docs.example.com' }],
    });

    const stopResponse = await app.inject({
      method: 'POST',
      url: `/sessions/${created.id}/stop`,
    });

    expect(stopResponse.statusCode).toBe(200);
    expect(stopResponse.json().session).toMatchObject({
      id: created.id,
      mode: 'stopped',
      status: 'stopped',
    });
  });

  it('rejects missing session payloads', async () => {
    const app = buildApp({ NODE_ENV: 'test', RPA_ENGINE_PORT: 3310 });

    const response = await app.inject({
      method: 'POST',
      url: '/sessions',
    });

    expect(response.statusCode).toBe(400);
    expect(response.json()).toEqual({
      message: 'body must be an object with a non-empty userId',
    });
  });

  it('rejects invalid user ids when creating sessions', async () => {
    const app = buildApp({ NODE_ENV: 'test', RPA_ENGINE_PORT: 3310 });

    const response = await app.inject({
      method: 'POST',
      url: '/sessions',
      payload: { userId: '' },
    });

    expect(response.statusCode).toBe(400);
    expect(response.json()).toEqual({
      message: 'body must be an object with a non-empty userId',
    });
  });
});

describe('EventBus', () => {
  it('continues publishing when one subscriber throws', () => {
    const bus = new EventBus<{ event: { value: number } }>();
    const received: number[] = [];

    bus.subscribe('event', () => {
      throw new Error('listener failed');
    });
    bus.subscribe('event', payload => {
      received.push(payload.value);
    });

    expect(() => bus.publish('event', { value: 7 })).not.toThrow();
    expect(received).toEqual([7]);
  });
});
