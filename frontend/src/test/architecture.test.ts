import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Frontend architecture boundaries (§40).
 *
 * These are the rules that would otherwise erode one convenient shortcut at a
 * time: a `.toFixed(2)` here, a `fetch` there, a demo fixture imported "just
 * to see the layout" that ships.
 */

const SRC = join(process.cwd(), 'src');

function sourceFiles(dir: string = SRC): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    return /\.(ts|tsx)$/.test(entry) ? [full] : [];
  });
}

const SELF = join(SRC, 'test', 'architecture.test.ts');

/**
 * Every source file except this one.
 *
 * This file necessarily contains every banned string as a literal. Scanning
 * itself would fail every rule it defines - which is how the first version of
 * these tests behaved.
 */
const ALL = sourceFiles().filter((file) => file !== SELF);

const PRODUCTION = ALL.filter(
  (file) => !/\.test\.tsx?$/.test(file) && !file.includes(join('src', 'test')),
);

/**
 * A file's code, with comments removed.
 *
 * The rules here are about what the code *does*, and a module that explains a
 * hazard in a comment is documenting it, not committing it: `display.ts` warns
 * that `parseFloat` on a decimal string reintroduces float error, and an
 * earlier version of this test flagged that warning as the violation.
 */
function read(file: string): string {
  return readFileSync(file, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:])\/\/.*$/gm, '$1');
}

function shortName(file: string): string {
  return relative(SRC, file).replace(/\\/g, '/');
}

describe('no financial calculation in the frontend', () => {
  it('declares no indicator or sizing routine', () => {
    for (const file of PRODUCTION) {
      const text = read(file);
      for (const banned of [
        'function calculateRsi',
        'function calculateEma',
        'function computeAtr',
        'function positionSize',
        'function calculateRisk',
        'function computePnl',
        // Phase 10. Performance metrics are server-derived; a second
        // implementation here is how a dashboard starts disagreeing with the
        // ledger it claims to describe.
        'function computeWinRate',
        'function calculateWinRate',
        'function computeProfitFactor',
        'function computeExpectancy',
        'function computeDrawdown',
        'function calculateDrawdown',
        'function computeStreak',
        'function computeRMultiple',
      ]) {
        expect(text, shortName(file)).not.toContain(banned);
      }
    }
  });

  it('accumulates no cumulative curve of its own (Phase 10)', () => {
    // The timeline arrives with each point's cumulative value already computed.
    // Re-accumulating it in the browser is how a chart and a table drift apart.
    for (const file of PRODUCTION) {
      const text = read(file);
      expect(text, shortName(file)).not.toMatch(/cumulative\s*\+=/);
      expect(text, shortName(file)).not.toMatch(/running\s*\+=\s*Number/);
      expect(text, shortName(file)).not.toMatch(/reduce\([^)]*cumulative/);
    }
  });

  it('never parses a backend decimal string into a float for arithmetic', () => {
    // The backend sends exact decimal strings. `parseFloat` on one, followed
    // by arithmetic, reintroduces the float error the whole project avoids.
    for (const file of PRODUCTION) {
      const text = read(file);
      expect(text, shortName(file)).not.toMatch(/parseFloat\([^)]*\)\s*[+\-*/]/);
      expect(text, shortName(file)).not.toMatch(/Number\([^)]*raw[^)]*\)\s*[+\-*/]/);
    }
  });
});

describe('formatting is centralised and versioned (§5)', () => {
  it('has no toFixed outside the formatting module', () => {
    for (const file of PRODUCTION) {
      if (shortName(file) === 'format/display.ts') continue;
      expect(read(file), shortName(file)).not.toContain('.toFixed(');
    }
  });

  it('has no ad-hoc Intl.NumberFormat outside the formatting module', () => {
    for (const file of PRODUCTION) {
      if (shortName(file) === 'format/display.ts') continue;
      expect(read(file), shortName(file)).not.toContain('Intl.NumberFormat');
    }
  });
});

describe('the API boundary is centralised (§33)', () => {
  it('has no bare fetch outside the api directory', () => {
    for (const file of PRODUCTION) {
      if (shortName(file).startsWith('api/')) continue;
      expect(read(file), shortName(file)).not.toMatch(/\bfetch\(/);
    }
  });

  it('embeds no credential', () => {
    for (const file of ALL) {
      const text = read(file);
      expect(text, shortName(file)).not.toMatch(/sk-ant-[A-Za-z0-9]/);
      expect(text, shortName(file)).not.toMatch(/api[_-]?key\s*[:=]\s*['"][^'"]{8,}/i);
    }
  });
});

describe('model output is never rendered as HTML (§30)', () => {
  it('uses no dangerouslySetInnerHTML anywhere', () => {
    for (const file of ALL) {
      expect(read(file), shortName(file)).not.toContain('dangerouslySetInnerHTML');
    }
  });

  it('uses no innerHTML assignment', () => {
    for (const file of PRODUCTION) {
      expect(read(file), shortName(file)).not.toMatch(/\.innerHTML\s*=/);
    }
  });
});

describe('no fake production runtime (§3)', () => {
  it('keeps test fixtures out of production modules', () => {
    /*
     * Any import from `src/test/`, not two named files.
     *
     * The first version listed `../test/fixtures` and `./test/fixtures`
     * explicitly, and a mutation probe walked straight past it by importing
     * `../test/dto` - a fixture module added later, in the same directory,
     * that the rule had never heard of. A rule that enumerates its targets
     * only catches the targets somebody remembered.
     */
    for (const file of PRODUCTION) {
      const text = read(file);
      expect(text, shortName(file)).not.toMatch(/from\s+'[^']*\/test\/[^']*'/);
      expect(text, shortName(file)).not.toMatch(/from\s+'\.\/test\/[^']*'/);
    }
  });

  it('hard-codes no VIOP instrument code', () => {
    // Master spec §118: a contract code in the source is a remembered
    // exchange fact. The backend has the same rule and enforces it.
    for (const file of PRODUCTION) {
      const text = read(file).toUpperCase();
      for (const banned of ['XU030', 'XU100', 'BIST30', 'BIST100', 'F_XU030']) {
        expect(text, shortName(file)).not.toContain(banned);
      }
    }
  });

  it('hard-codes no exchange multiplier, tick size or margin', () => {
    for (const file of PRODUCTION) {
      const text = read(file);
      for (const banned of ['DEFAULT_MULTIPLIER', 'TICK_SIZE =', 'INITIAL_MARGIN']) {
        expect(text, shortName(file)).not.toContain(banned);
      }
    }
  });
});

describe('no later-phase leakage (§38)', () => {
  /*
   * Phase 8 also banned any paper-trading concept here. Phase 9 is that concept,
   * approved, so the word is no longer banned - but everything that would turn a
   * simulation into an order still is, and the list grew rather than shrank.
   */
  it('contains no execution or broker concept', () => {
    for (const file of ALL) {
      const text = read(file);
      for (const banned of [
        'placeOrder',
        'submitOrder',
        'sendOrder',
        'executeOrder',
        'brokerClient',
        'brokerSession',
        'Midas',
        'WebSocket(',
        'EventSource(',
      ]) {
        expect(text, shortName(file)).not.toContain(banned);
      }
    }
  });

  it('never tells a person an order was sent', () => {
    for (const file of PRODUCTION) {
      const text = read(file).toLocaleLowerCase('tr-TR');
      for (const banned of ['emir gönderildi', 'emriniz iletildi', 'order placed', 'order sent']) {
        expect(text, shortName(file)).not.toContain(banned);
      }
    }
  });
});

describe('replay computes nothing and derives no cursor (Phase 11)', () => {
  /*
   * Phase 11 drives the existing engines. The failure mode it invites is a
   * browser that works out the replay clock for itself - "the next candle is
   * five minutes later" - and then disagrees with the server about what has
   * happened. So the client may read a cursor and may not build one.
   */
  it('declares no replay-only market calculation', () => {
    for (const file of PRODUCTION) {
      const text = read(file);
      for (const banned of [
        'function replayPnl',
        'function computeReplayPnl',
        'function replayFill',
        'function replayIndicator',
        'function replayWinRate',
        'function nextReplayTime',
        'function computeAsOf',
        'function deriveAsOf',
        'function advanceClock',
      ]) {
        expect(text, shortName(file)).not.toContain(banned);
      }
    }
  });

  it('never adds a timeframe duration to a replay timestamp', () => {
    // Coverage end is the server's rule. A browser that computed it would be
    // deciding for itself which candles have finished.
    for (const file of PRODUCTION) {
      const text = read(file);
      expect(text, shortName(file)).not.toMatch(/coverageEnd\s*=/);
      expect(text, shortName(file)).not.toMatch(/replayAsOf\s*=\s*new Date\([^)]*\+/);
      expect(text, shortName(file)).not.toMatch(/as_of[^\n]*setMinutes/);
    }
  });

  it('declares no request type carrying server-owned replay state', () => {
    /*
     * Scoped to the request types rather than the whole module: a response
     * schema legitimately names every field the server sends, including
     * `replay_as_of`. What must not exist is somewhere to *put* one on the way
     * out, so the check reads the declared request shapes.
     *
     * The matching runtime proof - that an advance body is exactly
     * {steps, expected_version} - lives in components/replay.test.tsx.
     */
    const text = read(join(SRC, 'api', 'replay.ts'));
    const requestTypes = [
      /export interface CreateReplayPayload {[^}]*}/s,
      /options: \{[^}]*\},\s*\): Promise<ReplayStepDto>/s,
    ];
    for (const pattern of requestTypes) {
      const declared = text.match(pattern)?.[0] ?? '';
      expect(declared, pattern.source).not.toBe('');
      for (const banned of ['replay_as_of', 'as_of', 'cursor', 'revealed', 'candles', 'speed']) {
        expect(declared, banned).not.toContain(banned);
      }
    }
  });

  it('keeps playback speed out of every request', () => {
    for (const file of PRODUCTION) {
      if (!shortName(file).startsWith('api/')) continue;
      const text = read(file);
      expect(text, shortName(file)).not.toMatch(/speed\s*[:=][^=]/);
    }
  });

  it('contains no optimisation concept (Phase 12 boundary)', () => {
    // Phase 12 Part 2A added the backtesting workspace, so the boundary moved:
    // what must still be absent is *optimisation*, which this phase explicitly
    // does not build. The previous version of this test banned the word
    // "backtest" and only passed afterwards by an accident of capitalisation,
    // which is worth recording - a guard that passes for the wrong reason is
    // indistinguishable from one that works.
    for (const file of ALL) {
      const text = read(file);
      for (const banned of [
        'walkForward',
        'walk_forward',
        'monteCarlo',
        'monte_carlo',
        'optimizeParameters',
        'optimiseParameters',
        'parameterSweep',
        'gridSearch',
        'strategyRanking',
        'leaderboard',
        'shadowMode',
      ]) {
        expect(text, shortName(file)).not.toContain(banned);
      }
    }
  });

  it('never computes a financial quantity in the browser', () => {
    // The backtest screen renders money that arrived as exact decimal strings.
    // Parsing one into a JavaScript number would round a value the backend
    // spent twelve phases keeping exact.
    const backtest = ALL.filter((file) => shortName(file).includes('Backtest'));
    expect(backtest.length).toBeGreaterThan(0);
    for (const file of backtest) {
      const text = read(file);
      for (const banned of ['parseFloat(', 'Number(', '.reduce((']) {
        expect(text, shortName(file)).not.toContain(banned);
      }
    }
  });
});
