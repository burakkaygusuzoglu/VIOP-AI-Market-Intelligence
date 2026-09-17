import type { SetupQuality } from '../domain/models';
import { formatScore, MISSING_DISPLAY } from '../format/display';
import './SetupQualityCard.css';

/**
 * Setup quality, rendered so it cannot be read as a probability (§9).
 *
 * `72` next to a progress bar is, to almost any reader, "72% likely to work".
 * It is not. It is a weighted heuristic score whose weights are project
 * policy, and this project has no calibrated probability model at all.
 *
 * Four deliberate choices keep that clear:
 *
 * 1. the value always renders as **`72 / 100`**, never bare and never with a
 *    `%` - a fraction of a total reads as a score, a percent reads as odds;
 * 2. the word *heuristic* appears next to it, from the backend's own label;
 * 3. a sentence states outright that it is not a probability, rather than
 *    hoping the label carries the point;
 * 4. the bar is a segmented gauge, not a filling progress bar - progress bars
 *    imply completion towards a certain outcome.
 *
 * `null` renders as unavailable, never as zero: an unscored setup is not a bad
 * setup (§15).
 */

export interface SetupQualityCardProps {
  readonly quality: SetupQuality;
}

const SEGMENTS = 10;

/**
 * How many gauge segments to light.
 *
 * This is the only arithmetic in the component, and it is **presentation
 * scaling, not a financial calculation**: it maps a score the backend already
 * finalised onto ten decorative segments. It produces no number the user
 * reads — the displayed value is `formatScore(score, outOf)` — and the gauge
 * itself is `aria-hidden`.
 *
 * The clamps are not decoration either. `outOf` arrives from the backend, and
 * a zero would make `score / outOf` infinite and light every segment, drawing
 * a perfect score out of a malformed payload. Out-of-range values clamp rather
 * than overflow the gauge.
 */
function litSegments(score: number | null, outOf: number): number {
  if (score === null || outOf <= 0) return 0;
  const scaled = Math.round((score / outOf) * SEGMENTS);
  return Math.max(0, Math.min(SEGMENTS, scaled));
}

export function SetupQualityCard({ quality }: SetupQualityCardProps) {
  const { score, outOf, label } = quality;
  const filled = litSegments(score, outOf);

  return (
    <section className="quality" aria-labelledby="quality-heading">
      <h3 className="quality__heading" id="quality-heading">
        Kurulum Kalitesi
      </h3>

      <p className="quality__value">
        <span className="quality__score">{formatScore(score, outOf)}</span>
        <span className="quality__kind">sezgisel puan</span>
      </p>

      {/* Segmented, not a progress bar. `aria-hidden` because the value is
          already stated in text immediately above - a screen reader should
          hear "72 / 100", not ten anonymous segments. */}
      <div className="quality__gauge" aria-hidden="true">
        {Array.from({ length: SEGMENTS }, (_, index) => (
          <span
            key={index}
            className={`quality__segment ${index < filled ? 'quality__segment--on' : ''}`}
          />
        ))}
      </div>

      <p className="quality__caveat">
        Bu bir <strong>olasılık değildir</strong>. Kazanma ihtimalini ifade etmez; kurulumun
        ölçülebilen özelliklerinin ağırlıklı bir değerlendirmesidir.
      </p>

      {/* The engine's own classification constant, shown as such.
          `QUALITY_LABEL` is English ("HEURISTIC SETUP QUALITY") and exists in
          the domain precisely so this number can never be read as a
          probability - so it is worth surfacing. Dropping it raw into a
          Turkish paragraph, styled like the sentences around it, made it look
          like untranslated copy rather than a machine tag. It is glossed and
          set apart instead. */}
      {label && (
        <p className="quality__tag">
          <span className="quality__tag-note">Motor etiketi:</span>{' '}
          <code className="quality__tag-value">{label}</code>
        </p>
      )}
      {score === null && (
        <p className="quality__missing">
          Puan üretilemedi ({MISSING_DISPLAY}). Bu, kötü bir kurulum anlamına gelmez.
        </p>
      )}
    </section>
  );
}
