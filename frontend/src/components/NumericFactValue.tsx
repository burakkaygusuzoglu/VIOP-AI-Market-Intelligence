import type { NumericFact } from '../domain/models';
import { formatFact } from '../format/display';
import { ProvenanceBadge } from './ProvenanceBadge';
import './NumericFactValue.css';

/**
 * One number, displayed with its origin and its exact value both reachable.
 *
 * This is where §5, §6, §7 and §20 meet a pixel:
 *
 * * the **display** value is rounded by semantic unit, so a beginner sees
 *   `24.66` rather than `24.658334322196957`;
 * * the **raw** value is rendered on screen in Pro mode;
 * * the **provenance** badge says whether the number was calculated or read
 *   off a picture, in words.
 *
 * The pairing matters more than either half. A rounded number with no origin
 * is a number a user will trust for the wrong reason.
 *
 * ## How the exact value is guaranteed to be reachable
 *
 * **Pro mode is the accessible route, not the `title` attribute.** The `title`
 * is a convenience for a sighted mouse user and nothing more: it is not
 * reachable by keyboard, it is unreliable on touch, and screen-reader support
 * for it varies. An earlier version of this comment said the exact value was
 * "always one hover away, even in Beginner mode", which quietly treated a
 * hover tooltip as an accessibility guarantee. It is not one.
 *
 * So Beginner mode stays uncluttered by design and does not pretend to expose
 * audit precision; anyone who needs the exact number switches to Pro, where it
 * is real on-screen text that keyboard, touch and assistive technology all
 * reach. The mode control is a labelled, keyboard-operable radio group.
 */

export interface NumericFactValueProps {
  readonly fact: NumericFact;
  /** Pro mode shows the exact backend value beneath the display value. */
  readonly showRaw?: boolean;
}

export function NumericFactValue({ fact, showRaw = false }: NumericFactValueProps) {
  const formatted = formatFact(fact);

  return (
    <div className="numeric-fact">
      <dt className="numeric-fact__label">{formatted.label}</dt>
      <dd className="numeric-fact__body">
        <span
          className="numeric-fact__value"
          /* A sighted-mouse convenience only. The guaranteed route to the
             exact value is Pro mode, below - see the note above. */
          title={formatted.wasRounded ? `Tam değer: ${formatted.raw}` : undefined}
        >
          {formatted.display}
        </span>
        <ProvenanceBadge source={fact.source} />
        {showRaw && formatted.wasRounded && (
          <span className="numeric-fact__raw">
            ham: <code>{formatted.raw}</code>
          </span>
        )}
      </dd>
    </div>
  );
}
