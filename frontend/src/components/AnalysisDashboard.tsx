import type { AnalysisView } from '../domain/models';
import { formatTimestamp } from '../format/display';
import { AnalysisUnavailable } from './AnalysisUnavailable';
import { EvidencePanel } from './EvidencePanel';
import { FinalActionCard } from './FinalActionCard';
import { NumericFactValue } from './NumericFactValue';
import { RiskSummaryCard } from './RiskSummaryCard';
import { SetupQualityCard } from './SetupQualityCard';
import { TimeframeLadder } from './TimeframeLadder';
import './AnalysisDashboard.css';

/**
 * The first real UI slice (§39).
 *
 * ## Why this order
 *
 * Reading order is safety order (§14). The final state and the risk sit at the
 * top, in the first column, expanded - a user who reads nothing else has read
 * the two things that could cost them money. Quality, timeframes and facts
 * follow. Nothing safety-critical is collapsed, hover-only, or below a chart.
 *
 * ## Beginner and Pro
 *
 * The same read model drives both; the mode changes *how much* is shown, never
 * *what is true* (§11). Beginner hides raw float values and reference ids
 * because they are noise to someone learning - but a blocker, a missing input
 * and a pending confirmation appear in both, because hiding those for
 * simplicity would make the simple view a misleading one.
 */

export type ExperienceMode = 'BEGINNER' | 'PRO';

export interface AnalysisDashboardProps {
  readonly view: AnalysisView;
  readonly mode: ExperienceMode;
}

export function AnalysisDashboard({ view, mode }: AnalysisDashboardProps) {
  if (view.kind === 'unavailable') {
    return <AnalysisUnavailable status={view.status} detail={view.detail} />;
  }

  const { analysis } = view;
  const isPro = mode === 'PRO';

  return (
    <div className="dashboard">
      <header className="dashboard__context">
        <div>
          <h2 className="dashboard__symbol">{analysis.context.symbol}</h2>
          <p className="dashboard__as-of">
            Analiz verisi: {formatTimestamp(analysis.context.analysisAsOf)}
          </p>
        </div>
        {isPro && analysis.context.contextDigest && (
          /* The label is visible text, not a `title`. This is Pro mode, where
             an audit identifier is the point - and what a bare hex string
             means should not be reachable only by hovering it. */
          <p className="dashboard__digest">
            <span className="dashboard__digest-label">Bağlam özeti (denetim):</span>{' '}
            <code>{analysis.context.contextDigest.slice(0, 12)}…</code>
          </p>
        )}
      </header>

      {/* Safety first: final state and risk lead, always expanded. */}
      <div className="dashboard__primary">
        <FinalActionCard
          action={analysis.finalAction}
          systemStatus={analysis.systemStatus}
          allowedActions={analysis.allowedActions}
        />
        <RiskSummaryCard risk={analysis.risk} showRaw={isPro} />
      </div>

      <div className="dashboard__secondary">
        <SetupQualityCard quality={analysis.setupQuality} />
        <TimeframeLadder readings={analysis.timeframes} />
      </div>

      {/* Shown in both modes. The reasons for a verdict are not an advanced
          detail - a beginner told only "BEKLE" has been given a conclusion
          with no way to disagree with it. Pro adds the audit reference ids. */}
      <EvidencePanel evidence={analysis.evidence} showRaw={isPro} />

      {analysis.missing.length > 0 && (
        <section className="dashboard__missing" aria-labelledby="missing-heading">
          <h3 className="dashboard__heading" id="missing-heading">
            Eksik bilgi
          </h3>
          {/* Shown in both modes. Missing information is not a detail a
              beginner is better off without - it is why an answer is
              incomplete (§15). */}
          <ul className="dashboard__missing-list">
            {analysis.missing.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </section>
      )}

      {analysis.facts.length > 0 && (
        <section className="dashboard__facts" aria-labelledby="facts-heading">
          <h3 className="dashboard__heading" id="facts-heading">
            Ölçülen değerler
          </h3>
          <dl className="dashboard__facts-grid">
            {analysis.facts.map((fact) => (
              <NumericFactValue key={fact.id} fact={fact} showRaw={isPro} />
            ))}
          </dl>
        </section>
      )}
    </div>
  );
}
