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
      ]) {
        expect(text, shortName(file)).not.toContain(banned);
      }
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
  it('contains no execution, broker or paper-trading concept', () => {
    for (const file of ALL) {
      const text = read(file);
      for (const banned of ['placeOrder', 'submitOrder', 'brokerClient', 'paperTrade', 'Midas']) {
        expect(text, shortName(file)).not.toContain(banned);
      }
    }
  });
});
