import { z } from 'zod';
import { getJson } from './client';

/**
 * Source-status transport (Phase 15 Part 2B). Read-only: every function here
 * is a GET, and there is deliberately no function that submits, approves or
 * verifies anything - the backend has no such route, because this application
 * has no authentication. Reviews are written only by the local operator
 * command on the host.
 *
 * Three truths are literal types, mirroring the backend: no real provider is
 * connected, financial use of verified facts is not enabled, and a reviewer
 * name is an operator assertion rather than an authenticated identity. A
 * response claiming otherwise fails to parse and is never rendered.
 */

const category = z.enum([
  'MARKET_DATA',
  'CONTRACT_METADATA',
  'OPEN_INTEREST',
  'NEWS',
  'MARKET_BREADTH',
  'SESSION_CALENDAR',
]);
export type SourceCategory = z.infer<typeof category>;

const capability = z.enum(['NOT_CONFIGURED', 'NOT_LICENSED', 'UNAVAILABLE', 'AVAILABLE', 'STALE']);
export type CapabilityStatus = z.infer<typeof capability>;

const assertion = z.literal('OPERATOR_ASSERTION_NOT_AUTHENTICATED');

export const capabilitiesSchema = z.object({
  deployment: z.object({
    market_data_provider: z.string(),
    real_provider_connected: z.literal(false),
    simulated_market_data: z.boolean(),
    calendar_source_composed: z.boolean(),
    verification_writes: z.literal('LOCAL_OPERATOR_COMMAND_ONLY'),
    reviewer_identity: assertion,
    financial_use_enabled: z.literal(false),
  }),
  categories: z.array(
    z.object({
      category,
      status: capability,
      reason: z.string(),
      configured: z.boolean(),
      licensed: z.boolean(),
      connected: z.boolean(),
      available: z.boolean(),
      fresh: z.boolean(),
      verified: z.boolean(),
      adapter_in_build: z.boolean(),
    }),
  ),
  journal: z.object({
    submissions: z.number().int(),
    approved: z.number().int(),
    rejected: z.number().int(),
    refused: z.number().int(),
    records: z.number().int(),
  }),
  server_time: z.string(),
});
export type CapabilitiesDto = z.infer<typeof capabilitiesSchema>;

const authority = z.enum(['EXCHANGE_OFFICIAL', 'LICENSED_PROVIDER', 'SECONDARY', 'UNKNOWN']);
export type SourceAuthority = z.infer<typeof authority>;

export const metadataSchema = z.object({
  symbol: z.string(),
  applies_at: z.string(),
  known_by: z.string().nullable(),
  retrospective: z.boolean(),
  verdict: z.string(),
  reason: z.string(),
  governing_record: z.string().nullable(),
  fields: z.array(
    z.object({
      name: z.enum([
        'multiplier',
        'tick_size',
        'tick_value',
        'expiry_date',
        'initial_margin',
        'maintenance_margin',
      ]),
      state: z.enum(['VERIFIED', 'MISSING', 'NOT_REVIEWABLE', 'UNAVAILABLE']),
      value: z.string().nullable(),
      source: z.string().nullable(),
      verified_at: z.string().nullable(),
    }),
  ),
  checks: z.object({
    source_claims_value: z.boolean(),
    operator_examined_evidence: z.boolean(),
    source_authority_assessed: z.boolean(),
    applicable_to_contract: z.boolean(),
    applicable_at_market_time: z.boolean(),
    known_by_requested_time: z.boolean(),
    current: z.boolean(),
    financial_use_enabled: z.literal(false),
  }),
  conflicts: z.array(
    z.object({
      fact: z.string(),
      chosen_record: z.string(),
      chosen_value: z.string(),
      other_record: z.string(),
      other_value: z.string(),
      resolution: z.string(),
    }),
  ),
  superseded: z.array(z.string()),
  records: z.array(
    z.object({
      record_id: z.string(),
      authority: z.enum(['EXCHANGE_OFFICIAL', 'LICENSED_PROVIDER']),
      reference: z.string(),
      effective_from: z.string(),
      effective_until: z.string().nullable(),
      verified_at: z.string(),
      known_at: z.string().nullable(),
      corrects: z.string().nullable(),
      reviewed_by: z.string().nullable(),
      multiplier: z.string(),
      tick_size: z.string(),
      expiry_date: z.string().nullable(),
    }),
  ),
  financial_use_enabled: z.literal(false),
  server_time: z.string(),
});
export type MetadataDto = z.infer<typeof metadataSchema>;

export const calendarSchema = z.object({
  symbol: z.string(),
  at: z.string(),
  status: z.enum(['IN_SESSION', 'OUT_OF_SESSION', 'UNAVAILABLE']),
  reason: z.string(),
  source: z.string().nullable(),
  source_verified_at: z.string().nullable(),
  calendar_source_composed: z.boolean(),
  server_time: z.string(),
});
export type CalendarDto = z.infer<typeof calendarSchema>;

export const reviewPageSchema = z.object({
  items: z.array(
    z.object({
      sequence: z.number().int(),
      submission_id: z.string(),
      symbol: z.string(),
      fact: z.enum(['MULTIPLIER', 'TICK_SIZE', 'EXPIRY_DATE']),
      claimed_value: z.string().nullable(),
      reference: z.string(),
      authority,
      effective_from: z.string().nullable(),
      effective_until: z.string().nullable(),
      submitted_by: z.string(),
      submitted_at: z.string(),
      origin: z.enum(['MANUAL_ENTRY', 'FILE_IMPORT']),
      corrects: z.string().nullable(),
      recorded_at: z.string(),
      decision: z
        .object({
          reviewer: z.string(),
          reviewer_identity: assertion,
          decided_at: z.string(),
          outcome: z.enum(['APPROVED', 'REJECTED']),
          document_checked: z.boolean(),
          note: z.string(),
          result: z.enum(['APPROVED', 'REJECTED', 'REFUSED']),
          refusal_code: z.string().nullable(),
          recorded_at: z.string(),
        })
        .nullable(),
    }),
  ),
  total: z.number().int(),
  next_after: z.number().int().nullable(),
  server_time: z.string(),
});
export type ReviewPageDto = z.infer<typeof reviewPageSchema>;

function withSignal(signal?: AbortSignal): RequestInit | undefined {
  return signal ? { signal } : undefined;
}

/** Matches the backend path pattern, so a hostile value never becomes a URL. */
export const SYMBOL_PATTERN = /^[A-Za-z0-9._-]{1,32}$/;

export function getSourceCapabilities(signal?: AbortSignal): Promise<CapabilitiesDto> {
  return getJson('/sources/capabilities', capabilitiesSchema, withSignal(signal));
}

export interface MetadataQuery {
  readonly symbol: string;
  /** Market time the facts must govern; ISO-8601 with an offset. */
  readonly appliesAt?: string;
  /** "What do we know today about that period" - labelled as such. */
  readonly retrospective?: boolean;
}

export function getMetadataStatus(
  query: MetadataQuery,
  signal?: AbortSignal,
): Promise<MetadataDto> {
  const params = new URLSearchParams();
  if (query.appliesAt) params.set('applies_at', query.appliesAt);
  if (query.retrospective) params.set('retrospective', 'true');
  const suffix = params.size ? `?${params.toString()}` : '';
  return getJson(
    `/sources/metadata/${encodeURIComponent(query.symbol)}${suffix}`,
    metadataSchema,
    withSignal(signal),
  );
}

export function getCalendarStatus(symbol: string, signal?: AbortSignal): Promise<CalendarDto> {
  return getJson(
    `/sources/calendar/${encodeURIComponent(symbol)}`,
    calendarSchema,
    withSignal(signal),
  );
}

export const REVIEW_PAGE_SIZE = 25;

export function getReviewPage(after: number, signal?: AbortSignal): Promise<ReviewPageDto> {
  return getJson(
    `/sources/reviews?after=${after}&limit=${REVIEW_PAGE_SIZE}`,
    reviewPageSchema,
    withSignal(signal),
  );
}
