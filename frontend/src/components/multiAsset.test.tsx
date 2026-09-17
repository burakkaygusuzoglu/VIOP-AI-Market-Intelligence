import { render, screen } from '@testing-library/react';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { mapAnalysis } from '../api/mapAnalysis';
import { analysisDto } from '../test/dto';
import { RiskSummaryCard } from './RiskSummaryCard';

/**
 * Phase 8.5 in the UI: truthful about the one asset class that works, and
 * silent about the ones that do not.
 *
 * The backend now names the asset class of a contract record, with its
 * provenance. The UI shows it only when that record exists, labels an
 * unverified classification, and offers no equity, crypto or FX control
 * anywhere - an enum value on the server is not a product in the browser.
 */

type Contract = NonNullable<ReturnType<typeof analysisDto>['risk']['contract']>;

function withContract(contract: Partial<Contract> | null) {
  const dto = analysisDto();
  dto.risk.contract =
    contract === null
      ? null
      : {
          symbol: 'TEST_FIXTURE_FUT',
          verified: false,
          multiplier: '10',
          multiplier_status: 'TEST_FIXTURE',
          tick_size: '0.25',
          tick_size_status: 'TEST_FIXTURE',
          asset_class: 'FUTURES',
          asset_class_status: 'TEST_FIXTURE',
          ...contract,
        };
  return mapAnalysis(dto).risk;
}

describe('the asset class is shown only from a contract record', () => {
  it('says nothing about asset class when no contract record exists', () => {
    render(<RiskSummaryCard risk={withContract(null)} />);

    expect(screen.queryByText(/Varlık sınıfı/)).toBeNull();
  });

  it('names a futures contract record', () => {
    render(<RiskSummaryCard risk={withContract({})} />);

    expect(screen.getByText(/Varlık sınıfı: Vadeli işlem sözleşmesi/)).toBeInTheDocument();
  });

  it('labels an unverified classification in Beginner mode', () => {
    render(<RiskSummaryCard risk={withContract({ asset_class_status: 'TEST_FIXTURE' })} />);

    expect(screen.getByText(/Varlık sınıfı/)).toHaveTextContent('(doğrulanmadı)');
  });

  it('shows the raw provenance in Pro mode', () => {
    render(<RiskSummaryCard risk={withContract({})} showRaw />);

    expect(screen.getByText(/Varlık sınıfı/)).toHaveTextContent('(TEST_FIXTURE)');
  });

  it('does not caveat a verified classification', () => {
    render(
      <RiskSummaryCard
        risk={withContract({ verified: true, asset_class_status: 'VERIFIED_CURRENT_FACT' })}
      />,
    );

    expect(screen.getByText(/Varlık sınıfı/)).not.toHaveTextContent('doğrulanmadı');
  });

  it('never translates an unimplemented class into something that sounds supported', () => {
    render(<RiskSummaryCard risk={withContract({ asset_class: 'CRYPTO_SPOT' })} />);

    const line = screen.getByText(/Varlık sınıfı/);
    expect(line).toHaveTextContent('CRYPTO_SPOT');
    expect(line).not.toHaveTextContent(/kripto/i);
  });

  it('still reads a Phase 8 response that has no asset class fields', () => {
    const dto = analysisDto();
    dto.risk.contract = {
      symbol: 'X',
      verified: false,
      multiplier: '1',
      multiplier_status: 'UNVERIFIED',
      tick_size: '1',
      tick_size_status: 'UNVERIFIED',
    } as Contract;

    const risk = mapAnalysis(dto).risk;
    render(<RiskSummaryCard risk={risk} />);

    expect(risk.contract?.assetClass ?? null).toBeNull();
    expect(screen.queryByText(/Varlık sınıfı/)).toBeNull();
  });
});

describe('no unimplemented market is offered', () => {
  function sources(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) return sources(path);
      return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) && !path.includes('test')
        ? [path]
        : [];
    });
  }

  it('has no selectable equity, crypto or FX control in any component', () => {
    const offenders = sources(join(__dirname, '..')).filter((path) => {
      const text = readFileSync(path, 'utf8');
      return /(<option|<button|role="radio"|type="radio")[^>]*>?[^<]*(Hisse|Kripto|Forex|Crypto|Equity|Stocks)/i.test(
        text,
      );
    });

    expect(offenders).toEqual([]);
  });
});
