import { describe, expect, it } from 'vitest';
import {
  CAPABILITIES,
  analysisIsAvailable,
  capability,
  isLive,
  requiresConfiguration,
} from './capabilities';

/**
 * The capability matrix is a claim about the running backend (§1, §2, §3).
 *
 * These tests cannot verify that claim - only a request to a live server could
 * - so they lock the properties that keep the claim *checkable*: no duplicate
 * ids, no state without evidence, and no capability that says "available" while
 * admitting in its own detail that it is not.
 */

describe('shape', () => {
  it('has unique ids', () => {
    const ids = CAPABILITIES.map((item) => item.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('gives every capability a Turkish label and a reason', () => {
    for (const item of CAPABILITIES) {
      expect(item.label.trim(), item.id).not.toBe('');
      expect(item.detail.trim().length, item.id).toBeGreaterThan(20);
    }
  });

  it('resolves a known id and returns undefined for an unknown one', () => {
    expect(capability('health')?.state).toBe('AVAILABLE_NOW');
    expect(capability('teleportation')).toBeUndefined();
  });
});

describe('states are backed by an endpoint or the absence of one', () => {
  it('gives every HTTP-surfaced state an endpoint', () => {
    for (const item of CAPABILITIES) {
      if (
        item.state === 'AVAILABLE_NOW' ||
        item.state === 'ENDPOINT_NOT_WIRED' ||
        item.state === 'REQUIRES_CONFIGURATION'
      ) {
        expect(item.endpoint, item.id).toMatch(/^(GET|POST|PATCH|DELETE) \//);
      }
    }
  });

  it('gives no endpoint to a capability that is not on the HTTP surface', () => {
    for (const item of CAPABILITIES) {
      if (item.state === 'APPLICATION_ONLY' || item.state === 'NOT_IMPLEMENTED') {
        expect(item.endpoint, item.id).toBeNull();
      }
    }
  });
});

describe('isLive is the only gate the UI may use', () => {
  it('is true only for AVAILABLE_NOW', () => {
    for (const item of CAPABILITIES) {
      expect(isLive(item.id), item.id).toBe(item.state === 'AVAILABLE_NOW');
    }
  });

  it('is false for an unknown capability rather than throwing', () => {
    expect(isLive('teleportation')).toBe(false);
  });

  it('does not treat a configuration-gated route as live', () => {
    // Phase 8 wired the analyzer at the composition root, so a credential
    // would now make this work - which is why it is no longer
    // ENDPOINT_NOT_WIRED. It is still not `AVAILABLE_NOW`: this deployment has
    // no credential, and the UI must not offer an action that would 503.
    expect(capability('screenshot-analysis')?.state).toBe('REQUIRES_CONFIGURATION');
    expect(isLive('screenshot-analysis')).toBe(false);
    expect(requiresConfiguration('screenshot-analysis')).toBe(true);
  });
});

describe('analysis availability', () => {
  it('is true now that the analysis endpoint exists', () => {
    expect(analysisIsAvailable()).toBe(true);
    expect(capability('deterministic-analysis')?.state).toBe('AVAILABLE_NOW');
    expect(capability('deterministic-analysis')?.endpoint).toBe('POST /api/analysis');
  });

  it('treats synthesis as an optional step of that flow, not its own endpoint', () => {
    // Phase 7 removed a dead synthesis endpoint. Phase 8 did not resurrect it:
    // synthesis runs inside the analysis request, from a context the server
    // built, and never from anything a client supplied.
    expect(capability('synthesis')?.state).toBe('REQUIRES_CONFIGURATION');
    expect(capability('synthesis')?.endpoint).toBe('POST /api/analysis');
    expect(isLive('synthesis')).toBe(false);
  });

  it('still promises no persistence', () => {
    // An analysis is ephemeral. Nothing may claim otherwise.
    expect(capability('persisted-analysis')?.state).toBe('NOT_IMPLEMENTED');
    expect(capability('historical-analysis')?.state).toBe('NOT_IMPLEMENTED');
  });
});

describe('nothing later-phase is advertised as working', () => {
  it('keeps live analysis and analysis persistence unimplemented', () => {
    for (const id of ['persisted-analysis', 'historical-analysis', 'live-analysis']) {
      expect(capability(id)?.state, id).toBe('NOT_IMPLEMENTED');
    }
  });

  it('describes paper trading as routed but unable to open positions here', () => {
    // Phase 9: the routes exist, but no verified contract metadata provider is
    // composed, so every new position is refused. Never AVAILABLE_NOW.
    const paper = capability('paper-trading');
    expect(paper?.state).toBe('ENDPOINT_NOT_WIRED');
    expect(paper?.endpoint).toBe('POST /api/paper/positions');
    expect(paper?.detail).toMatch(/reddeder/);
    expect(paper?.detail).toMatch(/emir yürütme kalıcı olarak devre dışı/);
  });
});
