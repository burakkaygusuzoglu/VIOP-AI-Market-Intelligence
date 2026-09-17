import { describe, expect, it } from 'vitest';
import { analysisResponseSchema } from './analysis';
import { mapAnalysis } from './mapAnalysis';
import { analysisDto } from '../test/dto';

/**
 * The API boundary's safety properties (§19).
 *
 * The mapper is where a backend the frontend does not fully understand becomes
 * something components can render without lying. Every test here is about what
 * happens when the payload is *not* what this build expects.
 */

function map(mutate: (dto: ReturnType<typeof analysisDto>) => void = () => {}) {
  const dto = analysisDto();
  mutate(dto);
  return mapAnalysis(dto);
}

describe('unknown values never become good news', () => {
  it('maps an unknown direction to UNAVAILABLE, not NEUTRAL', () => {
    // A gap in understanding is not a balanced market.
    const model = map((dto) => {
      (dto.evidence[0] as { direction: string }).direction = 'SIDEWAYS_ISH';
    });
    expect(model.evidence[0]?.direction).toBe('UNAVAILABLE');
  });

  it('maps an unknown sizing outcome to UNDETERMINED, not ALLOWED', () => {
    const model = map((dto) => {
      (dto.risk as { outcome: string }).outcome = 'PROBABLY_FINE';
    });
    expect(model.risk.outcome).toBe('UNDETERMINED');
  });

  it('maps an unknown confirmation to UNKNOWN, not CONFIRMED', () => {
    const model = map((dto) => {
      (dto.timeframes[0] as { confirmation: string }).confirmation = 'SORT_OF';
    });
    expect(model.timeframes[0]?.confirmation).toBe('UNKNOWN');
  });

  it('maps an unknown provenance to UNVERIFIED, not CALCULATED', () => {
    const model = map((dto) => {
      (dto.facts[0] as { source: string }).source = 'VIBES';
    });
    expect(model.facts[0]?.source).toBe('UNVERIFIED');
  });

  it('maps an unknown synthesis status to UNKNOWN and shows no action', () => {
    const model = map((dto) => {
      (dto.synthesis as { status: string }).status = 'SOMETHING_NEW';
    });
    expect(model.synthesis?.status).toBe('UNKNOWN');
    expect(model.finalAction).toBeNull();
  });
});

describe('a final action comes only from an accepted synthesis (§22)', () => {
  it('is null when synthesis is not configured', () => {
    expect(map().finalAction).toBeNull();
  });

  it('is null when the provider failed, even if an action is present', () => {
    const model = map((dto) => {
      (dto.synthesis as { status: string }).status = 'PROVIDER_FAILURE';
      (dto.synthesis as { final_action: string | null }).final_action = 'LONG';
    });
    expect(model.finalAction).toBeNull();
    expect(model.systemStatus).toBe('PROVIDER_FAILURE');
  });

  it('is present when synthesis succeeded', () => {
    const model = map((dto) => {
      (dto.synthesis as { status: string }).status = 'SUCCESS';
      (dto.synthesis as { final_action: string | null }).final_action = 'WAIT';
    });
    expect(model.finalAction).toBe('WAIT');
    expect(model.systemStatus).toBeNull();
  });

  it('never turns a system status into a market action', () => {
    for (const status of ['NOT_CONFIGURED', 'PROVIDER_FAILURE', 'INVALID_OUTPUT']) {
      const model = map((dto) => {
        (dto.synthesis as { status: string }).status = status;
      });
      expect(model.finalAction, status).toBeNull();
      expect(['LONG', 'SHORT', 'WAIT', 'NO_TRADE']).not.toContain(model.systemStatus);
    }
  });
});

describe('risk is unavailable rather than zero (§32)', () => {
  it('carries the reasons and no facts', () => {
    const model = map();
    expect(model.risk.outcome).toBe('UNDETERMINED');
    expect(model.risk.facts).toHaveLength(0);
    expect(model.risk.unavailableReasons?.length).toBeGreaterThan(0);
  });

  it('reports unverified contract metadata as unverified', () => {
    expect(map().context.contractVerified).toBe(false);
  });
});

describe('scenarios stay separate', () => {
  it('keeps all three and never sums their scores', () => {
    const model = map();
    const cases = model.scenarios?.map((item) => item.case);
    expect(cases).toEqual(['BULL', 'BEAR', 'NEUTRAL']);
  });

  it('leaves the neutral case without a directional quality score', () => {
    const neutral = map().scenarios?.find((item) => item.case === 'NEUTRAL');
    expect(neutral?.quality).toBeNull();
  });

  it('leads with the higher-scoring case without changing any of them', () => {
    const model = map();
    expect(model.setupQuality.score).toBe(72);
    expect(model.scenarios?.find((item) => item.case === 'BEAR')?.quality?.score).toBe(21);
  });
});

describe('the chart receives only supplied candles (§24)', () => {
  it('carries every price straight through as a string', () => {
    const model = map();
    const candles = model.chart?.[0]?.candles ?? [];

    expect(candles).toHaveLength(2);
    expect(candles[0]?.close).toBe('101.00');
    expect(typeof candles[0]?.close).toBe('string');
  });

  it('carries zones with their kind', () => {
    expect(map().chart?.[0]?.zones?.[0]?.kind).toBe('SUPPORT');
  });
});

describe('data quality reflects what actually happened', () => {
  it('is PARTIAL when timeframes are missing', () => {
    expect(map().dataQuality).toBe('PARTIAL');
  });

  it('is BLOCKED when nothing was analysable', () => {
    const model = map((dto) => {
      (dto as { technical_available: boolean }).technical_available = false;
    });
    expect(model.dataQuality).toBe('BLOCKED');
  });
});

describe('the schema rejects a malformed payload rather than casting it', () => {
  it('fails when a required section is missing', () => {
    const dto = analysisDto() as Record<string, unknown>;
    delete dto.risk;
    expect(analysisResponseSchema.safeParse(dto).success).toBe(false);
  });

  it('fails when a price arrives as a number instead of a string', () => {
    // A JSON number has already been through a double by the time it lands
    // here; the schema refuses it rather than accepting the lossy value.
    const dto = analysisDto();
    const candle = dto.chart[0]?.candles[0] as unknown as { close: number } | undefined;
    if (candle) candle.close = 101;
    expect(analysisResponseSchema.safeParse(dto).success).toBe(false);
  });

  it('accepts the shape the backend actually sends', () => {
    expect(analysisResponseSchema.safeParse(analysisDto()).success).toBe(true);
  });
});
