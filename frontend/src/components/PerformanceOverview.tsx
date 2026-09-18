import type { PerformanceDto } from '../api/performance';
import {
  BASIS_LABEL,
  MISSING,
  STATUS_LABEL,
  coverageSentence,
  metricReason,
  metricText,
  percentText,
  sparklinePoints,
  streakSentence,
  timelineRows,
} from '../domain/performance';
import type { ExperienceMode } from './AnalysisDashboard';

/**
 * What the simulated history actually says (Phase 10).
 *
 * Each figure is printed exactly as the server sent it, next to the population
 * it describes. A figure the server could not compute shows its reason in its
 * place - a zero would be a different claim entirely.
 */

interface MetricCardProps {
  readonly label: string;
  readonly text: string;
  readonly metric: PerformanceDto['win_rate'];
  readonly hint?: string | undefined;
}

function MetricCard({ label, text, metric, hint }: MetricCardProps) {
  const reason = metricReason(metric);
  return (
    <div className={`perf-metric perf-metric--${metric.status.toLowerCase()}`}>
      <dt>{label}</dt>
      <dd>
        <span className="perf-metric__value">{text}</span>
        {metric.status !== 'AVAILABLE' && (
          <span className="perf-metric__status">{STATUS_LABEL[metric.status]}</span>
        )}
        {reason && <span className="perf-metric__reason">{reason}</span>}
        {hint && metric.status === 'AVAILABLE' && <span className="perf-metric__hint">{hint}</span>}
      </dd>
    </div>
  );
}

export interface PerformanceOverviewProps {
  readonly dto: PerformanceDto;
  readonly mode: ExperienceMode;
}

export function PerformanceOverview({ dto, mode }: PerformanceOverviewProps) {
  const streak = streakSentence(dto);
  const rows = timelineRows(dto);
  const basis = BASIS_LABEL[dto.basis] ?? dto.basis;

  return (
    <section className="perf-overview" aria-labelledby="perf-overview-heading">
      <div className="perf-overview__header">
        <h3 id="perf-overview-heading">Simülasyon performansı</h3>
        <p className="perf-basis">
          Ölçüm temeli: <strong>{basis}</strong>
        </p>
      </div>
      <p className="perf-coverage">{coverageSentence(dto)}</p>

      {dto.sample_size === 0 ? (
        <p className="perf-empty" role="status">
          Henüz tamamlanmış işlem yok. Açık, iptal veya reddedilmiş pozisyonlar kazanç oranına
          girmez; bu yüzden burada oran yerine sebep gösterilir.
        </p>
      ) : (
        <dl className="perf-metrics">
          <MetricCard
            label="Kazanç oranı"
            text={percentText(dto.win_rate)}
            metric={dto.win_rate}
            hint={
              dto.win_rate.numerator !== null && dto.win_rate.denominator !== null
                ? `${dto.win_rate.numerator} / ${dto.win_rate.denominator} tamamlanan işlem`
                : undefined
            }
          />
          <MetricCard
            label="Tamamlanan işlemlerin brüt K/Z"
            text={metricText(dto.realized_gross)}
            metric={dto.realized_gross}
          />
          <MetricCard
            label="Tamamlanan işlemlerin ücretleri"
            text={metricText(dto.fees_known)}
            metric={dto.fees_known}
          />
          <MetricCard
            label="Tamamlanan işlemlerin net K/Z"
            text={metricText(dto.realized_net)}
            metric={dto.realized_net}
          />
          <MetricCard
            label="İşlem başına ortalama (beklenti)"
            text={metricText(dto.expectancy)}
            metric={dto.expectancy}
            hint={`${dto.expectancy.sample_size} işlemlik örnek`}
          />
          <MetricCard
            label="Kâr faktörü"
            text={metricText(dto.profit_factor)}
            metric={dto.profit_factor}
          />
          <MetricCard
            label="En büyük gerçekleşen düşüş"
            text={metricText(dto.max_drawdown_absolute)}
            metric={dto.max_drawdown_absolute}
          />
          <MetricCard
            label="Açık pozisyonların gerçekleşmemiş K/Z"
            text={metricText(dto.unrealized_gross_open)}
            metric={dto.unrealized_gross_open}
          />
        </dl>
      )}

      <section className="perf-accounting" aria-labelledby="perf-accounting-heading">
        <h4 id="perf-accounting-heading">Gerçekleşen para (kapanmamış pozisyonlar dahil)</h4>
        <p className="perf-accounting__note">
          Bu bölüm tamamlanan işlem istatistiği değildir. Hedefe ulaşıp bir kısmı kapanan ama hâlâ
          açık olan bir pozisyon, gerçekleşen parasını burada gösterir; kazanç oranına, beklentiye
          veya seriye girmez. Her dolum kendi piyasa zamanına aittir.
        </p>
        <dl className="perf-metrics">
          <MetricCard
            label="Gerçekleşen brüt (tüm dolumlar)"
            text={metricText(dto.realized_accounting.gross)}
            metric={dto.realized_accounting.gross}
            hint={`${dto.realized_accounting.fill_count} dolum · ${dto.realized_accounting.from_completed_positions} tamamlanan + ${dto.realized_accounting.from_open_positions} açık pozisyon`}
          />
          <MetricCard
            label="Bilinen ücretler (tüm dolumlar)"
            text={metricText(dto.realized_accounting.fees_known)}
            metric={dto.realized_accounting.fees_known}
          />
          <MetricCard
            label="Gerçekleşen net (tüm dolumlar)"
            text={metricText(dto.realized_accounting.net)}
            metric={dto.realized_accounting.net}
          />
        </dl>
      </section>

      <div className="perf-counts">
        <h4>Pozisyonlar</h4>
        <ul>
          <li>
            Tamamlanan (işlem örneği): <strong>{dto.sample_size}</strong>
          </li>
          <li>
            Açık: <strong>{dto.counts.open_exposure}</strong>
          </li>
          <li>
            Kazanç / kayıp / başabaş:{' '}
            <strong>
              {dto.wins} / {dto.losses} / {dto.breakevens}
            </strong>
          </li>
          <li>
            Girilmeyen (bekleyen, iptal, reddedilen): <strong>{dto.counts.never_entered}</strong>
          </li>
        </ul>
        <p className="perf-streak">{streak.text}</p>
      </div>

      {mode === 'PRO' && (
        <div className="perf-pro">
          <h4>Ayrıntı ve kaynak</h4>
          <dl className="perf-detail-grid">
            <div>
              <dt>Temel seçimi</dt>
              <dd>{dto.basis_reason}</dd>
            </div>
            <div>
              <dt>Ücret kapsamı</dt>
              <dd>
                {dto.fee_coverage.covered} / {dto.fee_coverage.total}
              </dd>
            </div>
            <div>
              <dt>Ortalama kazanç</dt>
              <dd>{metricText(dto.average_win)}</dd>
            </div>
            <div>
              <dt>Ortalama kayıp (mutlak)</dt>
              <dd>{metricText(dto.average_loss)}</dd>
            </div>
            <div>
              <dt>En uzun kazanç / kayıp serisi</dt>
              <dd>
                {dto.streaks.max_win_streak} / {dto.streaks.max_loss_streak}
              </dd>
            </div>
            <div>
              <dt>Başabaş serisi kuralı</dt>
              <dd>{dto.streaks.policy}</dd>
            </div>
            <div>
              <dt>Dolum sayısı (kısmi çıkışlar dahil)</dt>
              <dd>{dto.fill_count}</dd>
            </div>
            <div>
              <dt>Kaynak</dt>
              <dd>{dto.source}</dd>
            </div>
            <div>
              <dt>Tarih kuralı</dt>
              <dd>{dto.filters.range_rule}</dd>
            </div>
            <div>
              <dt>Dolum zaman kuralı</dt>
              <dd>{dto.realized_accounting.time_rule}</dd>
            </div>
            <div>
              <dt>Dolum ücret kapsamı</dt>
              <dd>
                {dto.realized_accounting.coverage.covered} /{' '}
                {dto.realized_accounting.coverage.total}
              </dd>
            </div>
          </dl>

          <h4>Hesaplanamayan ölçümler</h4>
          <ul className="perf-unavailable">
            {(
              [
                ['Yüzde düşüş', dto.drawdown_percentage],
                ['R katsayısı beklentisi', dto.realized_r_expectancy],
                ['MAE', dto.mae],
                ['MFE', dto.mfe],
                ['Sharpe', dto.sharpe_ratio],
                ['Sortino', dto.sortino_ratio],
                ['Yıllıklandırılmış getiri', dto.annualised_return],
              ] as const
            ).map(([label, metric]) => (
              <li key={label}>
                <strong>{label}:</strong> {STATUS_LABEL[metric.status]} — {metric.reason}
              </li>
            ))}
          </ul>
          <p className="perf-linkage">{dto.analysis_linkage}</p>
        </div>
      )}

      <section className="perf-timeline" aria-labelledby="perf-timeline-heading">
        <h4 id="perf-timeline-heading">Kümülatif gerçekleşen K/Z</h4>
        <p className="perf-timeline__note">
          Bu bir hesap bakiyesi eğrisi değil; yalnızca tamamlanan simülasyon işlemlerinin kümülatif
          gerçekleşen sonucudur.
        </p>
        {rows.length === 0 ? (
          <p className="perf-empty">Gösterilecek tamamlanmış işlem yok.</p>
        ) : (
          <>
            <svg
              className="perf-sparkline"
              viewBox="0 0 320 80"
              role="img"
              aria-label={`Kümülatif gerçekleşen K/Z eğrisi, ${rows.length} nokta. Son değer ${
                rows[rows.length - 1]?.cumulative ?? MISSING
              }.`}
              preserveAspectRatio="none"
            >
              <polyline points={sparklinePoints(dto, 320, 80)} fill="none" strokeWidth="2" />
            </svg>
            <div className="perf-table-scroll">
              <table className="perf-table">
                <caption>Her satır bir tamamlanan pozisyondur.</caption>
                <thead>
                  <tr>
                    <th scope="col">Kapanış (piyasa zamanı)</th>
                    <th scope="col">İşlem sonucu</th>
                    <th scope="col">Kümülatif</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.positionId}>
                      <td>{row.when}</td>
                      <td>{row.amount}</td>
                      <td>{row.cumulative}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </section>
  );
}
