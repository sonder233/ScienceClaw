import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import { createRuntimeSession } from '../playwright/runtime-session.js';

const createSessionBodySchema = z.object({
  userId: z.string().trim().min(1),
});

export async function registerSessionRoutes(app: FastifyInstance) {
  app.post('/sessions', async (request, reply) => {
    const parsedBody = createSessionBodySchema.safeParse(request.body);
    if (!parsedBody.success) {
      return reply.code(400).send({
        message: 'body must be an object with a non-empty userId',
      });
    }

    const session = createRuntimeSession({ userId: parsedBody.data.userId });

    app.sessionRegistry.set(session);
    app.eventBus.publish('session.created', session);

    return { session };
  });

  app.get('/sessions/:id', async (request, reply) => {
    const { id } = request.params as { id: string };
    const session = app.sessionRegistry.get(id);

    if (!session) {
      return reply.code(404).send({ message: `unknown session ${id}` });
    }

    return { session };
  });
}
