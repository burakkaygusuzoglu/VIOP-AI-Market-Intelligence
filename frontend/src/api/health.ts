import { z } from 'zod';
import { getJson } from './client';

export const componentHealthSchema = z.object({
  name: z.string(),
  healthy: z.boolean(),
  detail: z.string(),
  latency_ms: z.number().nullable(),
});

export const healthResponseSchema = z.object({
  status: z.enum(['ok', 'degraded']),
  app_env: z.string(),
  version: z.string(),
  checked_at: z.string(),
  components: z.array(componentHealthSchema),
});

export type ComponentHealth = z.infer<typeof componentHealthSchema>;
export type HealthResponse = z.infer<typeof healthResponseSchema>;

/**
 * The endpoint answers 503 when a dependency is down; that is a meaningful
 * degraded state, not a transport failure, so the body is still parsed.
 */
export function fetchHealth(): Promise<HealthResponse> {
  return getJson('/health', healthResponseSchema);
}

export const healthQueryKey = ['system', 'health'] as const;
