/**
 * TEST FIXTURES ONLY.
 *
 * Nothing in `src/` outside `src/test/` and `*.test.tsx` may import this file,
 * and an architecture test enforces that. These values exist so populated
 * dashboard states can be exercised; they must never reach a production render
 * path, because a UI showing invented analysis is a fake product runtime (§3).
 *
 * Every value is fixture data. No instrument here is real.
 */

import type {
  AnalysisReadModel,
  EvidenceItem,
  Finding,
  NumericFact,
  TimeframeReading,
} from '../domain/models';

export const FIXTURE_SYMBOL = 'TEST_FIXTURE_FUT';

export const calculatedRsi: NumericFact = {
  id: 'FACT-RSI-BIAS',
  label: 'RSI (eğilim)',
  // Deliberately the messy float-derived value Phase 7 documents, so display
  // formatting is tested against the real shape of backend data.
  raw: '24.658334322196957',
  unit: 'indicator',
  source: 'CALCULATED',
};

export const visionRsi: NumericFact = {
  id: 'VIS-RSI-1',
  label: 'RSI (görüntüden)',
  raw: '99',
  unit: 'indicator',
  source: 'VISION_READ',
};

export const allowedContracts: NumericFact = {
  id: 'FACT-ALLOWED_CONTRACTS',
  label: 'İzin verilen kontrat',
  raw: '4',
  unit: 'contracts',
  source: 'CALCULATED',
};

/**
 * What Phase 3 sizing returns when the account cannot fund the position.
 *
 * A blocked risk state must not display a positive permitted-contract count.
 * The blocked fixture reused `allowedContracts` and rendered "RİSK İZİN
 * VERMİYOR" directly above "İzin verilen kontrat: 4" - a flat contradiction
 * on screen, and a fixture that no real backend response could produce.
 */
export const noContracts: NumericFact = {
  id: 'FACT-ALLOWED_CONTRACTS',
  label: 'İzin verilen kontrat',
  raw: '0',
  unit: 'contracts',
  source: 'CALCULATED',
};

export const riskAmount: NumericFact = {
  id: 'FACT-RISK_AMOUNT',
  label: 'Risk tutarı',
  raw: '1000.005',
  unit: 'currency',
  source: 'CALCULATED',
};

export const unverifiedTick: NumericFact = {
  id: 'FACT-TICK',
  label: 'Tik büyüklüğü',
  raw: '0.25',
  unit: 'price',
  source: 'UNVERIFIED',
};

export const blockingFinding: Finding = {
  id: 'SUIT-1',
  code: 'RISK_NOT_PERMITTED',
  severity: 'BLOCKING',
  detail: 'Sizing sıfır kontrata izin veriyor.',
};

export const pendingFinding: Finding = {
  id: 'SUIT-2',
  code: 'NO_CONFIRMATION',
  severity: 'PENDING',
  detail: '5M teyidi henüz gelmedi.',
};

export const ladder: readonly TimeframeReading[] = [
  {
    role: 'REGIME',
    timeframe: '1D',
    direction: 'BULLISH',
    confirmation: 'POINT_IN_TIME',
    summary: 'Yükseliş rejimi',
  },
  {
    role: 'BIAS',
    timeframe: '1H',
    direction: 'BULLISH',
    confirmation: 'CONFIRMED',
    summary: 'Yükseliş eğilimi',
  },
  {
    role: 'SETUP',
    timeframe: '15M',
    direction: 'BULLISH',
    confirmation: 'CONFIRMED',
    summary: 'Kurulum geçerli',
  },
  {
    role: 'ENTRY',
    timeframe: '5M',
    direction: 'NEUTRAL',
    confirmation: 'FORMING',
    summary: 'Teyit bekleniyor',
  },
];

export const evidence: readonly EvidenceItem[] = [
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
    category: 'VOLUME',
    reason: 'Hacim zayıflıyor',
    timeframe: '15M',
    confirmation: 'FORMING',
    source: 'CALCULATED',
  },
];

/** A clean setup awaiting its entry confirmation: the archetypal WAIT. */
export function waitingAnalysis(): AnalysisReadModel {
  return {
    context: {
      symbol: FIXTURE_SYMBOL,
      analysisAsOf: '2026-03-02T12:00:00+00:00',
      contextDigest: '7acfbbdd12e6ae1355aa',
    },
    finalAction: 'WAIT',
    systemStatus: null,
    allowedActions: ['WAIT', 'NO_TRADE'],
    setupQuality: { score: 72, outOf: 100, label: 'HEURISTIC SETUP QUALITY', isHeuristic: true },
    risk: {
      outcome: 'ALLOWED',
      detail: 'Dört kontrata kadar izin var.',
      facts: [allowedContracts, riskAmount],
      findings: [pendingFinding],
    },
    timeframes: ladder,
    evidence,
    findings: [pendingFinding],
    missing: ['15M hacim verisi sağlanmadı'],
    facts: [calculatedRsi, visionRsi],
    dataQuality: 'PARTIAL',
  };
}

/** Risk refuses the position: the archetypal NO_TRADE. */
export function blockedAnalysis(): AnalysisReadModel {
  return {
    ...waitingAnalysis(),
    finalAction: 'NO_TRADE',
    allowedActions: ['NO_TRADE'],
    risk: {
      outcome: 'NOT_PERMITTED',
      detail: 'Hesap bu pozisyonu fonlayamıyor.',
      facts: [noContracts],
      findings: [blockingFinding],
    },
    findings: [blockingFinding],
  };
}

/** Synthesis failed. There is no action at all. */
export function providerFailureAnalysis(): AnalysisReadModel {
  return {
    ...waitingAnalysis(),
    finalAction: null,
    systemStatus: 'PROVIDER_FAILURE',
    allowedActions: ['WAIT', 'NO_TRADE'],
  };
}
