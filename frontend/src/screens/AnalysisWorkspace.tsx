import { useEffect, useRef, useState } from 'react';
import { CandlestickChart } from '../components/CandlestickChart';
import { EvidencePanel } from '../components/EvidencePanel';
import { FinalActionCard } from '../components/FinalActionCard';
import { Narrative, ScenarioPanel } from '../components/ScenarioPanel';
import { RiskSummaryCard } from '../components/RiskSummaryCard';
import { TechnicalPanel } from '../components/TechnicalPanel';
import { TimeframeLadder } from '../components/TimeframeLadder';
import { WhyPanel } from '../components/WhyPanel';
import type { AnalysisReadModel } from '../domain/models';
import { formatTimestamp } from '../format/display';
import './AnalysisWorkspace.css';

/**
 * The professional workspace (§23).
 *
 * Layout is centre-chart / right-intelligence / bottom-risk on a workstation,
 * and a single ordered column on anything narrower. The order when it stacks
 * is not the desktop order read top-to-bottom: **the final state and the risk
 * blocker come first**, because on a phone the thing a user must not scroll
 * past is the thing that could cost them money.
 *
 * Everything here is projection. No component in this tree computes a
 * financial value; the chart converts prices to pixel coordinates and nothing
 * else does arithmetic at all.
 */

export type ExperienceMode = 'BEGINNER' | 'PRO';

export interface AnalysisWorkspaceProps {
  readonly analysis: AnalysisReadModel;
  readonly mode: ExperienceMode;
  readonly onBack: () => void;
}

export function AnalysisWorkspace({ analysis, mode, onBack }: AnalysisWorkspaceProps) {
  const isPro = mode === 'PRO';
  const chart = analysis.chart ?? [];
  const [timeframe, setTimeframe] = useState(() => chart[0]?.timeframe ?? '');
  const series = chart.find((item) => item.timeframe === timeframe) ?? chart[0] ?? null;

  /* Focus lands on the result, not on nothing (§12).
   *
   * Measured in a real browser: after submitting the form, `document.activeElement`
   * was `<body>`. The submit button had been unmounted with the form it lived in,
   * so the browser reset focus to the document - and the next Tab restarted from
   * the top of the page, with no announcement that an analysis had arrived.
   *
   * Focusing the heading moves the reading position to the new content and names
   * it. `tabIndex={-1}` makes it programmatically focusable without adding a stop
   * to the tab order, and the effect re-runs per analysis so a second run
   * announces itself too. */
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, [analysis]);

  return (
    <div className="workspace">
      <header className="workspace__header">
        <div>
          <h2 className="workspace__symbol" ref={heading} tabIndex={-1}>
            {analysis.context.symbol}
          </h2>
          <p className="workspace__as-of">
            Analiz verisi: {formatTimestamp(analysis.context.analysisAsOf)}
          </p>
        </div>
        <div className="workspace__header-actions">
          {/* An ephemeral analysis cannot survive a refresh, so the page never
              implies it can: there is no shareable URL for this result and the
              caveat is stated rather than left to be discovered (§35). */}
          <p className="workspace__ephemeral">
            Bu analiz geçicidir; kaydedilmez ve sayfa yenilenirse kaybolur.
          </p>
          <button type="button" onClick={onBack}>
            Yeni analiz
          </button>
        </div>
      </header>

      {/* Safety first, in both layouts. */}
      <div className="workspace__primary">
        <FinalActionCard
          action={analysis.finalAction}
          systemStatus={analysis.systemStatus}
          allowedActions={analysis.allowedActions}
          {...(analysis.synthesis && analysis.synthesis.status !== 'SUCCESS'
            ? { detail: analysis.synthesis.detail }
            : {})}
        />
        <RiskSummaryCard risk={analysis.risk} showRaw={isPro} />
      </div>

      {analysis.synthesis?.status === 'SUCCESS' && analysis.synthesis.summary.length > 0 && (
        <section className="workspace__summary" aria-labelledby="summary-heading">
          <h3 className="workspace__heading" id="summary-heading">
            Özet
          </h3>
          <Narrative segments={analysis.synthesis.summary} />
        </section>
      )}

      <div className="workspace__main">
        <section className="workspace__chart" aria-labelledby="chart-heading">
          <div className="workspace__chart-header">
            <h3 className="workspace__heading" id="chart-heading">
              Fiyat grafiği
            </h3>
            {chart.length > 1 && (
              <div className="workspace__timeframes" role="group" aria-label="Grafik zaman dilimi">
                {chart.map((item) => (
                  <button
                    key={item.timeframe}
                    type="button"
                    className="workspace__timeframe"
                    aria-pressed={item.timeframe === series?.timeframe}
                    onClick={() => setTimeframe(item.timeframe)}
                  >
                    {item.timeframe}
                  </button>
                ))}
              </div>
            )}
          </div>
          <CandlestickChart series={series} symbol={analysis.context.symbol} />
        </section>

        <aside className="workspace__intelligence" aria-label="Analiz paneli">
          <TimeframeLadder readings={analysis.timeframes} />

          {/* The headline-facts block that used to sit here showed one RSI
              per timeframe. The technical panel below now shows that and every
              other Phase 1 reading, per timeframe, so keeping both printed the
              same number twice in Pro mode. */}
          {analysis.missing.length > 0 && (
            <section className="workspace__missing" aria-labelledby="missing-heading">
              <h3 className="workspace__heading" id="missing-heading">
                Eksik bilgi
              </h3>
              {/* Shown in both modes. Missing information is why an answer is
                  incomplete, not an advanced detail (§15). */}
              <ul className="workspace__missing-list">
                {analysis.missing.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>
          )}
        </aside>
      </div>

      {/* §10: the readings Phase 1 already produced, per timeframe. */}
      <TechnicalPanel panels={analysis.technical ?? []} showRaw={isPro} />

      <ScenarioPanel
        scenarios={analysis.scenarios ?? []}
        contradictions={analysis.contradictions ?? []}
        synthesis={analysis.synthesis}
        showRaw={isPro}
      />

      <EvidencePanel evidence={analysis.evidence} showRaw={isPro} />

      {/* Shown in both modes: a conclusion without its reason is a
          conclusion a user cannot disagree with (§6). */}
      <WhyPanel explanations={analysis.why ?? []} showRaw={isPro} />

      {isPro && (
        <section className="workspace__audit" aria-labelledby="audit-heading">
          <h3 className="workspace__heading" id="audit-heading">
            Denetim
          </h3>
          <dl className="detail-grid">
            <dt>Analiz kimliği</dt>
            <dd>{analysis.context.analysisId}</dd>
            <dt>Bağlam özeti</dt>
            <dd>{analysis.context.contextDigest}</dd>
            <dt>Üretim zamanı</dt>
            <dd>{formatTimestamp(analysis.context.generatedAt ?? null)}</dd>
            <dt>Sözleşme doğrulandı mı</dt>
            <dd>{analysis.context.contractVerified ? 'evet' : 'hayır'}</dd>
          </dl>
        </section>
      )}
    </div>
  );
}
