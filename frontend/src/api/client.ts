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
  if (parsed.success) return parsed.data;

  // The body is not what this endpoint returns. If the status also says the
  // request failed, it is a typed refusal - a range too large, a store
  // unavailable - and its own sentence is what a person needs to read. Without
  // this it surfaced as "response did not match the expected schema", which
  // describes the transport rather than the answer.
  //
  // Order matters: readiness answers 503 with a *valid* health body, and that
  // must still parse. So the schema is tried first, and the status only decides
  // how to report a body that did not match.
  if (!response.ok) {
    throw new ApiError(detailOf(payload) ?? `request failed (${response.status})`, response.status);
  }

  throw new ApiError(
    `response did not match the expected schema: ${parsed.error.message}`,
    response.status,
  );
}

/**
 * POST a JSON body and validate the response.
 *
 * A 4xx carrying the backend's typed `{code, detail}` becomes an `ApiError`
 * with that detail, because those messages are written to be shown: they are
 * the reason an analysis was refused, not an internal string. A body that does
 * not match the schema is an error even on 200 - silently casting is how a
 * backend change becomes a wrong number on screen.
 */
export async function postJson<TSchema extends z.ZodTypeAny>(
  path: string,
  body: unknown,
  schema: TSchema,
  init?: RequestInit,
): Promise<z.infer<TSchema>> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      ...init,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
    throw new ApiError(cause instanceof Error ? cause.message : 'network error', null);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError('response was not valid JSON', response.status);
  }

  if (!response.ok) {
    throw new ApiError(detailOf(payload) ?? `request failed (${response.status})`, response.status);
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

/** Pull the backend's own user-facing message out of an error body. */
function detailOf(payload: unknown): string | null {
  if (typeof payload !== 'object' || payload === null) return null;
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === 'string') return detail;
  if (typeof detail === 'object' && detail !== null) {
    const inner = (detail as { detail?: unknown }).detail;
    if (typeof inner === 'string') return inner;
  }
  if (Array.isArray(detail)) {
    // Validation errors. Reported as a count rather than pasted in: the raw
    // list names internal field paths and is not user-facing copy.
    //
    // The backend caps the list it sends, so the count shown must include what
    // it dropped - otherwise a request wrong in two hundred ways would report
    // twenty and read as complete.
    const omitted = (payload as { omitted_error_count?: unknown }).omitted_error_count;
    const total = detail.length + (typeof omitted === 'number' ? omitted : 0);
    return `Girdi doğrulanamadı (${total} sorun).`;
  }
  return null;
}
