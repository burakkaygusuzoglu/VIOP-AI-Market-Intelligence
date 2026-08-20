import type { z } from 'zod';

/**
 * Thin fetch wrapper that validates every response against a schema.
 *
 * Unvalidated data must never reach application state, so the parse step is
 * part of the transport rather than left to each call site.
 */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export const API_BASE = '/api';

export async function getJson<TSchema extends z.ZodTypeAny>(
  path: string,
  schema: TSchema,
  init?: RequestInit,
): Promise<z.infer<TSchema>> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: { Accept: 'application/json' },
      ...init,
    });
  } catch (cause) {
    throw new ApiError(cause instanceof Error ? cause.message : 'network error', null);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError('response was not valid JSON', response.status);
  }

  const parsed = schema.safeParse(payload);
  if (!parsed.success) {
    throw new ApiError(
      `response did not match the expected schema: ${parsed.error.message}`,
      response.status,
    );
  }
  return parsed.data;
}
