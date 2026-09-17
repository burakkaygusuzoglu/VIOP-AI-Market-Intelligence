import { useId, useState } from 'react';
import type { TechnicalPanel as Panel } from '../domain/models';
import { formatValue, MISSING_DISPLAY } from '../format/display';
import './TechnicalPanel.css';

/**
 * The Phase 1 indicator readings, per timeframe (§10).
 *
 * ## Nothing is calculated here
 *
 * Every number arrives finished from the backend, which read it out of a
 * `TechnicalSnapshot` the Phase 1 engine produced. This component formats and
 * lays out; there is no arithmetic in it, and an architecture test forbids any.
 * Recomputing an EMA in JavaScript to "fill in" a gap would create a second
 * numerical authority that nobody tests.
 *
 * ## Unavailable is shown as unavailable
 *
 * A reading whose warm-up has not elapsed renders as `—` with the reason, never
 * as `0`. A zero ATR would read as "no volatility measured" rather than "not
 * enough candles yet", and those are different facts.
 *
 * ## Beginner sees fewer of the same readings
 *
 * Not a different list: the same readings, filtered. Pro adds the rest and the
 * raw value. A beginner shown four numbers and a pro shown twenty-three are
 * looking at one dataset at two densities, so they cannot disagree.
 */

export interface TechnicalPanelProps {
  readonly panels: readonly Panel[];
  readonly showRaw?: boolean;
}

export function TechnicalPanel({ panels, showRaw = false }: TechnicalPanelProps) {
  const groupId = useId();
  const [active, setActive] = useState(() => panels[0]?.timeframe ?? '');

  if (panels.length === 0) return null;

  const panel = panels.find((item) => item.timeframe === active) ?? panels[0];
  if (!panel) return null;

  const readings = showRaw ? panel.readings : panel.readings.filter((item) => item.beginner);

  return (
    <section className="technical" aria-labelledby="technical-heading">
      <div className="technical__header">
        <h3 className="technical__heading" id="technical-heading">
          Teknik göstergeler
        </h3>
        {panels.length > 1 && (
          <div className="technical__tabs" role="group" aria-labelledby={groupId}>
            <span className="visually-hidden" id={groupId}>
              Gösterge zaman dilimi
            </span>
            {panels.map((item) => (
              <button
                key={item.timeframe}
                type="button"
                className="technical__tab"
                aria-pressed={item.timeframe === panel.timeframe}
                onClick={() => setActive(item.timeframe)}
              >
                {item.timeframe}
              </button>
            ))}
          </div>
        )}
      </div>

      <p className="technical__note">
        Tüm değerler arka uçtaki Faz 1 motorundan gelir; arayüz hiçbirini yeniden hesaplamaz.
        {!showRaw && ' Profesyonel görünüm tüm göstergeleri ve ham değerleri açar.'}
      </p>

      <dl className="technical__grid">
        {readings.map((reading) => (
          <div key={reading.key} className="technical__reading">
            <dt className="technical__label">{reading.label}</dt>
            <dd className="technical__value">
              {reading.available ? (
                <>
                  <span className="technical__number">
                    {formatValue(reading.raw, reading.unit)}
                  </span>
                  {showRaw && <code className="technical__raw">{reading.raw}</code>}
                </>
              ) : (
                <>
                  <span className="technical__number technical__number--absent">
                    {MISSING_DISPLAY}
                  </span>
                  <span className="technical__absent">{reading.unavailableReason}</span>
                </>
              )}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
