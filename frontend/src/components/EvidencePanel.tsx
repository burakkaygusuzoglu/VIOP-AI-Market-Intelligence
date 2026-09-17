import type { EvidenceDirection, EvidenceItem, EvidenceStrength } from '../domain/models';
import { ProvenanceBadge } from './ProvenanceBadge';
import './EvidencePanel.css';

/**
 * The reasons behind the verdict, for and against, side by side.
 *
 * ## Two columns, never one score
 *
 * Bullish and bearish evidence are listed separately and never netted into a
 * single number. "Three bullish minus two bearish equals mildly bullish" is
 * arithmetic on things that are not commensurable, and it destroys exactly the
 * information a user needs: *what* disagrees. A setup with strong evidence on
 * both sides is not the same as one with weak evidence on neither, and a net
 * score renders them identically.
 *
 * So the disagreement stays visible, the same way the timeframe ladder keeps
 * its four roles apart instead of averaging them.
 *
 * ## Strength is a label, not a weight
 *
 * `WEAK` / `MODERATE` / `STRONG` arrive from the backend already decided. They
 * are rendered as words. Nothing here converts them to numbers, sums them, or
 * sorts by them - the order the backend sent is the order shown.
 *
 * Every item carries its own provenance, because an inference a model drew
 * from a picture and a fact computed from candles are not the same kind of
 * reason, however similarly they read.
 */

const DIRECTION_LABEL: Record<EvidenceDirection, string> = {
  BULLISH: 'Yükseliş yönünde',
  BEARISH: 'Düşüş yönünde',
  NEUTRAL: 'Nötr',
  UNAVAILABLE: 'Veri yok',
};

const STRENGTH_LABEL: Record<EvidenceStrength, string> = {
  WEAK: 'zayıf',
  MODERATE: 'orta',
  STRONG: 'güçlü',
};

export interface EvidencePanelProps {
  readonly evidence: readonly EvidenceItem[];
  /** Pro mode shows the content-derived reference id used in audit trails. */
  readonly showRaw?: boolean;
}

function Column({
  direction,
  items,
  showRaw,
}: {
  direction: EvidenceDirection;
  items: readonly EvidenceItem[];
  showRaw: boolean;
}) {
  const key = direction.toLowerCase();
  return (
    <div className={`evidence__column evidence__column--${key}`}>
      <h4 className="evidence__column-heading">
        {DIRECTION_LABEL[direction]}
        <span className="evidence__count">({items.length})</span>
      </h4>
      {items.length === 0 ? (
        // Not "no evidence against", which would read as a clean bill of
        // health. Nothing was recorded on this side; that is all it means.
        <p className="evidence__empty">Bu yönde kayıtlı kanıt yok.</p>
      ) : (
        <ul className="evidence__list">
          {items.map((item) => (
            <li key={item.id} className="evidence__item">
              <p className="evidence__reason">{item.reason}</p>
              <p className="evidence__meta">
                <span className="evidence__category">{item.category}</span>
                {item.timeframe && <span className="evidence__timeframe">{item.timeframe}</span>}
                <span className="evidence__strength">{STRENGTH_LABEL[item.strength]}</span>
                <ProvenanceBadge source={item.source} />
              </p>
              {showRaw && <code className="evidence__id">{item.id}</code>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function EvidencePanel({ evidence, showRaw = false }: EvidencePanelProps) {
  if (evidence.length === 0) return null;

  const bullish = evidence.filter((item) => item.direction === 'BULLISH');
  const bearish = evidence.filter((item) => item.direction === 'BEARISH');
  const other = evidence.filter(
    (item) => item.direction !== 'BULLISH' && item.direction !== 'BEARISH',
  );

  return (
    <section className="evidence" aria-labelledby="evidence-heading">
      <h3 className="evidence__heading" id="evidence-heading">
        Kanıtlar
      </h3>
      <p className="evidence__note">
        Lehte ve aleyhte kanıtlar ayrı listelenir; birbirini götürmez. Tek bir puana indirgenmez.
      </p>
      <div className="evidence__columns">
        <Column direction="BULLISH" items={bullish} showRaw={showRaw} />
        <Column direction="BEARISH" items={bearish} showRaw={showRaw} />
      </div>
      {other.length > 0 && (
        <div className="evidence__other">
          <Column direction="NEUTRAL" items={other} showRaw={showRaw} />
        </div>
      )}
    </section>
  );
}
