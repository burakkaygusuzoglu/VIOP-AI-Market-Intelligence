import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchHealth, healthResponseSchema } from './health';
import { ApiError } from './client';

const validPayload = {
  status: 'ok',
  app_env: 'test',
  version: '0.1.0-test',
  checked_at: '2026-01-02T10:30:00Z',
  components: [{ name: 'database', healthy: true, detail: 'reachable', latency_ms: 1.5 }],
};

function mockFetch(payload: unknown, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      status,
      json: () => Promise.resolve(payload),
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('health schema', () => {
  it('accepts a valid response', () => {
    expect(healthResponseSchema.safeParse(validPayload).success).toBe(true);
  });

  it('accepts a degraded response with a null latency', () => {
    const degraded = {
      ...validPayload,
      status: 'degraded',
      components: [{ name: 'database', healthy: false, detail: 'unreachable', latency_ms: null }],
    };
    expect(healthResponseSchema.safeParse(degraded).success).toBe(true);
  });

  it('rejects an unknown status value', () => {
    expect(healthResponseSchema.safeParse({ ...validPayload, status: 'fine' }).success).toBe(false);
  });

  it('rejects a missing field rather than defaulting it', () => {
    const withoutVersion: Record<string, unknown> = { ...validPayload };
    delete withoutVersion.version;
    expect(healthResponseSchema.safeParse(withoutVersion).success).toBe(false);
  });
});

describe('fetchHealth', () => {
  it('returns parsed data', async () => {
    mockFetch(validPayload);
    await expect(fetchHealth()).resolves.toMatchObject({ status: 'ok' });
  });

  it('parses a 503 degraded body instead of treating it as a transport failure', async () => {
    mockFetch({ ...validPayload, status: 'degraded' }, 503);
    await expect(fetchHealth()).resolves.toMatchObject({ status: 'degraded' });
  });

  it('rejects a response that does not match the schema', async () => {
    mockFetch({ unexpected: true });
    await expect(fetchHealth()).rejects.toBeInstanceOf(ApiError);
  });

  it('reports a network failure as an ApiError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    await expect(fetchHealth()).rejects.toBeInstanceOf(ApiError);
  });
});
