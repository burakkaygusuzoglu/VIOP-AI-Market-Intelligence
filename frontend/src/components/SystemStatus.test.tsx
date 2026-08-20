import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SystemStatus } from './SystemStatus';
import { tr } from '../i18n/tr';

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function mockHealth(status: 'ok' | 'degraded', healthy: boolean) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      status: healthy ? 200 : 503,
      json: () =>
        Promise.resolve({
          status,
          app_env: 'test',
          version: '0.1.0-test',
          checked_at: '2026-01-02T10:30:00Z',
          components: [
            {
              name: 'database',
              healthy,
              detail: healthy ? 'reachable' : 'unreachable',
              latency_ms: healthy ? 1.5 : null,
            },
          ],
        }),
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('SystemStatus', () => {
  it('renders Turkish labels by default', async () => {
    mockHealth('ok', true);
    render(<SystemStatus />, { wrapper });
    expect(await screen.findByText(tr.systemStatus.ok)).toBeInTheDocument();
    expect(screen.getByText(tr.systemStatus.title)).toBeInTheDocument();
  });

  it('shows the degraded state when a dependency is down', async () => {
    mockHealth('degraded', false);
    render(<SystemStatus />, { wrapper });
    expect(await screen.findAllByText(tr.systemStatus.degraded)).not.toHaveLength(0);
  });

  it('shows an unreachable message when the request fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    render(<SystemStatus />, { wrapper });
    expect(await screen.findByText(tr.systemStatus.unreachable)).toBeInTheDocument();
  });

  it('never renders colour as the only signal', async () => {
    mockHealth('ok', true);
    render(<SystemStatus />, { wrapper });
    const badge = await screen.findByText(tr.systemStatus.ok);
    expect(badge.textContent?.trim()).not.toBe('');
  });
});
