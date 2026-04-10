import type { FastifyInstance } from 'fastify';
import { z } from 'zod';

const executeBodySchema = z.object({
  intent: z.record(z.unknown()),
});

export async function registerAssistantRoutes(app: FastifyInstance) {
  app.get('/sessions/:id/assistant/snapshot', async (request, reply) => {
    const { id } = request.params as { id: string };
    const session = app.sessionRegistry.get(id);
    if (!session) {
      return reply.code(404).send({ message: `unknown session ${id}` });
    }

    const snapshot = await app.runtimeController.captureSnapshot(session);
    return { snapshot };
  });

  app.post('/sessions/:id/assistant/execute', async (request, reply) => {
    const { id } = request.params as { id: string };
    const session = app.sessionRegistry.get(id);
    if (!session) {
      return reply.code(404).send({ message: `unknown session ${id}` });
    }

    const parsedBody = executeBodySchema.safeParse(request.body ?? {});
    if (!parsedBody.success) {
      return reply.code(400).send({ message: 'body must be an object with an intent payload' });
    }

    const result = await app.runtimeController.executeAssistantIntent(session, parsedBody.data.intent);
    return result;
  });
}
