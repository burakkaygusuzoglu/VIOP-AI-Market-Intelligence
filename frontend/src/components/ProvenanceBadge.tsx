import type { Provenance } from '../domain/models';
import { isAuthoritative } from '../domain/models';
import './ProvenanceBadge.css';

/**
 * Where a value came from, said in words (§7, §20).
 *
 * The badge carries a **text label**, not just a colour. Colour alone fails
 * for a colour-blind reader, fails in a screenshot printed in greyscale, and
 * fails entirely for a screen reader - and this is the one distinction the
 * whole product rests on: a number a model read off a picture must never be
 * mistaken for a number an engine calculated.
 *
 * The labels mirror backend semantics exactly. The frontend does not invent a
 * provenance and does not soften one.
 */

const LABEL: Record<Provenance, string> = {
  CALCULATED: 'Hesaplanan',
  STRUCTURED_DATA: 'Piyasa verisi',
  USER_CONFIRMED: 'Kullanıcı onaylı',
  VISION_READ: 'Görüntüden okundu',
  AI_INFERENCE: 'AI çıkarımı',
  UNVERIFIED: 'Doğrulanmamış',
  MISSING: 'Eksik',
};

/**
 * The longer form, read by screen readers and shown on hover.
 *
 * A badge saying "Görüntüden okundu" is honest but terse; this says why it
 * matters, which is the part a beginner needs.
 */
const DESCRIPTION: Record<Provenance, string> = {
  CALCULATED: 'Deterministik Python tarafından hesaplandı. Yetkili değer.',
  STRUCTURED_DATA: 'Doğrulanmış piyasa verisinden alındı. Yetkili değer.',
  USER_CONFIRMED: 'Kullanıcı onayladı. Ham okumadan güçlü, piyasa verisinden zayıf.',
  VISION_READ: 'Bir model ekran görüntüsünden okudu. Yetkili değildir.',
  AI_INFERENCE: 'Bir modelin görüntüden çıkarımı. En zayıf kaynak, yetkili değildir.',
  UNVERIFIED: 'Doğrulanmamış. Hesaplamada yetkili olarak kullanılamaz.',
  MISSING: 'Bu bilgi mevcut değil. Nötr veya sıfır anlamına gelmez.',
};

const VARIANT: Record<Provenance, string> = {
  CALCULATED: 'calculated',
  STRUCTURED_DATA: 'calculated',
  USER_CONFIRMED: 'observed',
  VISION_READ: 'observed',
  AI_INFERENCE: 'inferred',
  UNVERIFIED: 'unverified',
  MISSING: 'unverified',
};

export interface ProvenanceBadgeProps {
  readonly source: Provenance;
}

export function ProvenanceBadge({ source }: ProvenanceBadgeProps) {
  const authoritative = isAuthoritative(source);
  return (
    <span
      className={`provenance provenance--${VARIANT[source]}`}
      title={DESCRIPTION[source]}
      data-authoritative={authoritative}
    >
      {/* Aria-hidden: the glyph repeats what the label already says, so a
          screen reader should not read it twice. */}
      <span className="provenance__mark" aria-hidden="true">
        {authoritative ? '◆' : '◇'}
      </span>
      <span className="provenance__label">{LABEL[source]}</span>
      {/* Not authoritative is the safety-relevant half, so it is stated rather
          than left implied by a hollow diamond. */}
      {!authoritative && <span className="provenance__caveat"> · yetkili değil</span>}
    </span>
  );
}

export const PROVENANCE_LABEL = LABEL;
export const PROVENANCE_DESCRIPTION = DESCRIPTION;
