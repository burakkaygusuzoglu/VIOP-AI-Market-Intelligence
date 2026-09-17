import type { AnalysisResponseDto } from '../api/analysis';

/**
 * A backend-shaped analysis payload, for tests only.
 *
 * TEST FIXTURE. An architecture test forbids any production module importing
 * this file, and the shipped bundle is checked for these markers.
 *
 * It mirrors the DTO the real endpoint produces so the mapper and the screens
 * are exercised against the shape they will actually receive. It is *input to
 * the mapper*, never a stand-in for an analysis: the backend tests prove the
 * engines compute these values from real candles.
 */

export function analysisDto(overrides: { symbol?: string } = {}): AnalysisResponseDto {
  const symbol = overrides.symbol ?? 'TEST_FIXTURE_FUT';
  return {
    identity: {
      analysis_id: '7acfbbdd12e6aa9f0000000000000000',
      symbol,
      analysis_as_of: '2026-03-02T12:00:00+00:00',
      generated_at: '2026-03-02T12:05:00+00:00',
      contract_metadata_verified: false,
      datasets: [
        {
          timeframe: '1H',
          digest: 'abc123',
          source_name: '1H.csv',
          row_count: 220,
          usable: true,
        },
      ],
      ephemeral: true,
    },
    technical_available: true,
    timeframes: [
      {
        timeframe: '1H',
        role: 'BIAS',
        usable: true,
        verdict: 'ACCEPTED',
        candle_count: 220,
        direction: 'BULLISH',
        confirmation: 'CONFIRMED',
        issues: [],
        omitted_issue_count: 0,
      },
    ],
    missing_timeframes: ['1D', '15M', '5M'],
    input_errors: [],
    chart: [
      {
        timeframe: '1H',
        candles: [
          {
            open_time: '2026-03-02T10:00:00+00:00',
            open: '100.00',
            high: '101.50',
            low: '99.50',
            close: '101.00',
            volume: '1500',
            is_closed: true,
          },
          {
            open_time: '2026-03-02T11:00:00+00:00',
            open: '101.00',
            high: '102.25',
            low: '100.75',
            close: '102.00',
            volume: '1600',
            is_closed: true,
          },
        ],
        zones: [{ id: 'ZN-AAAA', kind: 'SUPPORT', lower: '99.00', upper: '99.80', score: 70 }],
        // Aligned one-to-one with the two drawn candles; `null` is a
        // warm-up gap the chart breaks the line at rather than filling.
        overlays: [{ key: 'ema_9', label: 'EMA 9', values: [null, '100.5'] }],
        // The analysed dataset is larger than the drawn window on purpose.
        analysed_count: 220,
        omitted_count: 218,
        omitted_zone_count: 0,
        window_policy: 'latest-400/v1',
      },
    ],
    omitted_evidence_count: 0,
    evidence: [
      {
        id: 'EV-BULL-8F2A1C9D0B',
        direction: 'BULLISH',
        strength: 'STRONG',
        category: 'TREND',
        reason: 'EMA dizilimi yukarı',
        timeframe: '1H',
        confirmation: 'CONFIRMED',
        source: 'CALCULATED',
      },
      {
        id: 'EV-BEAR-4E7B22A105',
        direction: 'BEARISH',
        strength: 'WEAK',
        category: 'MOMENTUM',
        reason: 'RSI zayıflıyor',
        timeframe: '1H',
        confirmation: 'CONFIRMED',
        source: 'CALCULATED',
      },
    ],
    contradictions: [
      {
        id: 'CX-1111111111',
        type: 'HIGHER_TIMEFRAME_CONFLICT',
        severity: 'MAJOR',
        detail: '1D ve 1H yönleri uyuşmuyor.',
      },
    ],
    scenarios: [
      {
        case: 'BULL',
        state: 'WAITING_FOR_CONFIRMATION',
        reason: 'Eğilim yukarı, teyit bekleniyor.',
        quality_score: 72,
        quality_label: 'HEURISTIC SETUP QUALITY',
        entry_score: 40,
        supporting: [],
        counter: [],
        requirements: ['RETEST: geri test bekleniyor'],
        invalidations: [],
        components: [{ component: 'TREND', awarded: 20, weight: 25, availability: 'AVAILABLE' }],
      },
      {
        case: 'BEAR',
        state: 'INACTIVE',
        reason: 'Düşüş tarafını destekleyen kanıt yok.',
        quality_score: 21,
        quality_label: 'HEURISTIC SETUP QUALITY',
        entry_score: null,
        supporting: [],
        counter: [],
        requirements: [],
        invalidations: [],
        components: [],
      },
      {
        case: 'NEUTRAL',
        state: 'INACTIVE',
        reason: 'Range koşulu yok.',
        quality_score: null,
        quality_label: '',
        entry_score: null,
        supporting: [],
        counter: [],
        requirements: [],
        invalidations: [],
        components: [],
      },
    ],
    suitability: [
      {
        direction: 'BULLISH',
        no_trade: true,
        findings: [
          {
            id: 'FN-9999999999',
            code: 'INSUFFICIENT_DATA',
            severity: 'BLOCKING',
            detail: 'Giriş zaman dilimi verisi yok.',
          },
        ],
        missing_requirements: ['5M verisi'],
      },
    ],
    risk: {
      available: false,
      outcome: 'UNAVAILABLE',
      direction: null,
      detail: 'Pozisyon büyüklüğü hesaplanamadı.',
      unavailable_reasons: ['Doğrulanmış kontrat bilgisi yok (çarpan, tik büyüklüğü).'],
      facts: [],
      warnings: [],
      contract: null,
    },
    synthesis: {
      status: 'NOT_CONFIGURED',
      detail: 'synthesis is not configured',
      final_action: null,
      allowed_actions: ['NO_TRADE'],
      summary: [],
      bull_case: [],
      bear_case: [],
      neutral_case: [],
      devils_advocate: [],
      context_digest: '',
    },
    facts: [
      {
        id: 'FACT-RSI-1H',
        label: 'RSI (1H)',
        raw: '24.658334322196957',
        unit: 'indicator',
        source: 'CALCULATED',
        currency: null,
      },
    ],
    missing: ['5M verisi sağlanmadı.'],
    technical: [
      {
        timeframe: '1H',
        role: 'BIAS',
        readings: [
          {
            key: 'rsi',
            label: 'RSI (14)',
            raw: '24.658334322196957',
            unit: 'indicator',
            available: true,
            unavailable_reason: '',
            beginner: true,
          },
          {
            key: 'ema_200',
            label: 'EMA 200',
            raw: '',
            unit: 'price',
            available: false,
            unavailable_reason: 'Yeterli mum yok; gösterge henüz hesaplanamıyor.',
            beginner: false,
          },
        ],
      },
    ],
    why: [
      {
        topic: 'BULLISH_EVIDENCE',
        subject: 'Yükseliş kanıtları',
        available: true,
        unavailable_reason: '',
        reasons: [
          {
            code: 'TREND',
            source: 'EVIDENCE',
            severity: 'NOTABLE',
            beginner: '1H trend yükseliş yönünü destekliyor (güçlü).',
            pro: 'TREND/BIAS 1H: BULLISH, STRONG.',
            timeframe: '1H',
            role: 'BIAS',
          },
        ],
        omitted_reason_count: 0,
      },
      {
        topic: 'POSITION_SIZE',
        subject: 'Pozisyon büyüklüğü',
        available: false,
        unavailable_reason: 'Doğrulanmış kontrat bilgisi yok (çarpan, tik büyüklüğü).',
        reasons: [],
        omitted_reason_count: 0,
      },
    ],
  };
}
