import type { ConfirmationState, EvidenceDirection, TimeframeReading } from '../domain/models';
import { ROLE_LABEL } from '../domain/models';
import './TimeframeLadder.css';

/**
 * The four canonical roles, in order, never averaged (§17).
 *
 * 1D regime → 1H bias → 15M setup → 5M entry. Presented as a ladder because
 * that is what it is: each rung qualifies the one below, and the reason a
 * setup is WAIT is usually visible as soon as the rungs are read together -
 * three agreeing and the entry still pending.
 *
 * Disagreement is shown, never resolved. Averaging four timeframes into one
 * "overall bias" would destroy exactly the information that makes the ladder
 * worth reading.
 *
 * Confirmation state gets its own column (§16): a forming 5M reading must not
 * look like a confirmed entry, so it carries a different word and a different
 * mark, not merely a different shade.
 */

const DIRECTION_LABEL: Record<EvidenceDirection, string> = {
  BULLISH: 'Yükseliş',
  BEARISH: 'Düşüş',
  NEUTRAL: 'Nötr',
  UNAVAILABLE: 'Veri yok',
};

const CONFIRMATION_LABEL: Record<ConfirmationState, string> = {
  CONFIRMED: 'Teyitli',
  FORMING: 'Oluşuyor',
  PENDING: 'Bekleniyor',
  POINT_IN_TIME: 'Anlık',
  UNKNOWN: 'Bilinmiyor',
};

/**
 * A glyph per state, so the distinction survives greyscale printing.
 *
 * `POINT_IN_TIME` was `◑` and sat one row above `FORMING`'s `◐` - two half
 * circles that are the same shape mirrored, and indistinguishable at 12px
 * without reading the word beside them. A snapshot reading and a forming one
 * are different claims about whether a bar has closed, so they get different
 * shapes, not different orientations of one shape.
 */
const CONFIRMATION_MARK: Record<ConfirmationState, string> = {
  CONFIRMED: '●',
  FORMING: '◐',
  PENDING: '○',
  POINT_IN_TIME: '◉',
  UNKNOWN: '?',
};

export interface TimeframeLadderProps {
  readonly readings: readonly TimeframeReading[];
}

export function TimeframeLadder({ readings }: TimeframeLadderProps) {
  if (readings.length === 0) {
    return (
      <section className="ladder" aria-labelledby="ladder-heading">
        <h3 className="ladder__heading" id="ladder-heading">
          Zaman Dilimleri
        </h3>
        <p className="ladder__empty">Zaman dilimi okuması yok.</p>
      </section>
    );
  }

  return (
    <section className="ladder" aria-labelledby="ladder-heading">
      <h3 className="ladder__heading" id="ladder-heading">
        Zaman Dilimleri
      </h3>
      {/* Every role is stated explicitly, even though a `<table>` implies all
          of them. Below 560px the cells become `display: block` so four
          columns do not overflow a 390px screen - and changing a table
          element's `display` drops its implicit ARIA role in every major
          browser. Without these attributes the ladder silently stops being a
          table for assistive technology at exactly the width where the visual
          column headers are hidden too, which is the worst of both. */}
      <table className="ladder__table" role="table">
        <caption className="ladder__caption">
          Roller ayrı okunur; hiçbir zaman ortalanmaz. Anlaşmazlık gizlenmez.
        </caption>
        <thead role="rowgroup">
          <tr role="row">
            <th role="columnheader" scope="col">
              Rol
            </th>
            <th role="columnheader" scope="col">
              Dilim
            </th>
            <th role="columnheader" scope="col">
              Yön
            </th>
            <th role="columnheader" scope="col">
              Durum
            </th>
          </tr>
        </thead>
        <tbody role="rowgroup">
          {readings.map((reading) => (
            <tr key={reading.role} role="row" data-role={reading.role}>
              <th role="rowheader" scope="row" className="ladder__role">
                {ROLE_LABEL[reading.role]}
              </th>
              {/* `data-label` is what the stacked layout prints in front of
                  each value. With `thead` hidden, "Yükseliş" and "Anlık" sit
                  next to each other with nothing saying which is direction and
                  which is confirmation. */}
              <td role="cell" className="ladder__timeframe" data-label="Dilim">
                {reading.timeframe}
              </td>
              <td
                role="cell"
                className={`ladder__direction ladder__direction--${reading.direction.toLowerCase()}`}
                data-label="Yön"
              >
                {DIRECTION_LABEL[reading.direction]}
              </td>
              <td role="cell" className="ladder__confirmation" data-label="Durum">
                <span aria-hidden="true" className="ladder__mark">
                  {CONFIRMATION_MARK[reading.confirmation]}
                </span>
                {CONFIRMATION_LABEL[reading.confirmation]}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export const DIRECTION_LABEL_TEXT = DIRECTION_LABEL;
export const CONFIRMATION_LABEL_TEXT = CONFIRMATION_LABEL;
