import { z } from 'zod';
import { ApiError, API_BASE, postJson } from './client';

/**
 * The screenshot API boundary (§15, §16, §17).
 *
 * Two endpoints, and their failure modes are as much a part of the contract as
 * their successes: an unconfigured Vision provider answers `503
 * VISION_NOT_CONFIGURED`, which is a **system state** and must never be shown
 * as anything the market did.
 */

const observationSchema = z.object({
  field: z.string(),
  value: z.string(),
  kind: z.string(),
  /**
   * Model-reported extraction confidence, as a string.
   *
   * A string on the wire on purpose: a JSON number invites a client to round
   * it, scale it, or compare it against a threshold as though it were
   * calibrated. `null` when the model reported none - never 0, which would be
   * a claim of *low* confidence rather than an absence of one.
   */
  confidence: z.string().nullable().default(null),
});

const qualityDimensionSchema = z.object({
  dimension: z.string(),
  state: z.string(),
  awarded: z.number().nullable(),
  weight: z.number(),
});

const qualitySchema = z.object({
  score: z.number().nullable(),
  coverage: z.number(),
  evaluated_weight: z.number(),
  total_weight: z.number(),
  method_version: z.string(),
  dimensions: z.array(qualityDimensionSchema).default([]),
});

export const screenshotAnalysisSchema = z.object({
  screenshot_id: z.string(),
  slot: z.string(),
  image_format: z.string(),
  width: z.number(),
  height: z.number(),
  detected_timeframe: z.string().nullable(),
  timeframe_agreement: z.string(),
  mismatches: z.array(z.string()).default([]),
  observations: z.array(observationSchema).default([]),
  unreadable: z.array(z.string()).default([]),
  quality: qualitySchema,
  warnings: z.array(z.string()).default([]),
  model: z.string().default(''),
  prompt_version: z.string().default(''),
});

export const correctionSchema = z.object({
  field: z.string(),
  action: z.string(),
  observed_screen_value: z.string().nullable(),
  observed_value_origin: z.string(),
  user_value: z.string().nullable(),
  authoritative_value: z.string().nullable(),
  authoritative_source: z.string().nullable(),
  user_input_was_overridden: z.boolean(),
  conflicts: z.array(z.string()).default([]),
  agreeing: z.array(z.string()).default([]),
  corrected_at: z.string(),
});

export type ScreenshotAnalysisDto = z.infer<typeof screenshotAnalysisSchema>;
export type ObservationDto = z.infer<typeof observationSchema>;
export type CorrectionDto = z.infer<typeof correctionSchema>;

/**
 * Upload one screenshot for analysis.
 *
 * `multipart/form-data`, so this uses `fetch` directly rather than `postJson`.
 * The backend remains the security authority: the file's extension and the
 * browser-reported MIME type are hints, and every real check - byte size,
 * header preflight, dimension policy, a bounded decode - happens server-side
 * (§37). Nothing here decides an upload is safe.
 */
export async function analyseScreenshot(
  slot: string,
  file: File,
  expectedSymbol: string,
  signal?: AbortSignal,
): Promise<ScreenshotAnalysisDto> {
  const form = new FormData();
  form.append('slot', slot);
  form.append('file', file);
  form.append('expected_symbol', expectedSymbol);

  let response: Response;
  try {
    response = await fetch(`${API_BASE}/screenshots/analyse`, {
      method: 'POST',
      body: form,
      ...(signal ? { signal } : {}),
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
    const detail = (payload as { detail?: { code?: string; detail?: string } })?.detail;
    throw new ApiError(detail?.detail ?? `upload failed (${response.status})`, response.status);
  }

  const parsed = screenshotAnalysisSchema.safeParse(payload);
  if (!parsed.success) {
    throw new ApiError('response did not match the expected schema', response.status);
  }
  return parsed.data;
}

export interface CorrectionPayload {
  readonly screenshot_id: string;
  readonly slot: string;
  readonly field: string;
  readonly replayed_observation: string;
  readonly action: 'CONFIRMED' | 'CORRECTED' | 'REJECTED';
  readonly corrected_value?: string;
  readonly note?: string;
  readonly numeric?: boolean;
}

/**
 * Confirm, correct or reject one observation.
 *
 * There is deliberately no field here for a source, a priority or a
 * verification status. A user's correction earns `USER_CONFIRMED`, which the
 * server assigns; it can never earn `STRUCTURED_MARKET_DATA`, and the request
 * type has no way to ask.
 */
export function submitCorrection(payload: CorrectionPayload): Promise<CorrectionDto> {
  return postJson('/screenshots/corrections', payload, correctionSchema);
}
