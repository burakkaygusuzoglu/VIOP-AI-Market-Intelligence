import type { WhyExplanation } from '../domain/models';
import './WhyPanel.css';

/**
 * Explanations for the conclusions this analysis reached (§6, §7).
 *
 * ## It renders; it does not explain
 *
 * Every sentence here was written by the backend Why Engine, which walks the
 * breakdown the scoring engine produced. There is no explanation logic in this
 * component and none anywhere in the frontend: a second explanation engine
 * would eventually describe a conclusion the first one no longer reaches.
 *
 * Beginner and Pro are two renderings of the *same* reason, produced together
 * by that engine. This component chooses which string to show. It never
 * composes one, never derives one from the other, and never supplies a
 * fallback - a missing explanation is an engine problem to surface, not a gap
 * to paper over.
 *
 * ## Unavailable is an answer
 *
 * A topic with `available: false` carries the reason it cannot be explained,
 * and that is displayed as the answer it is. A position-size explanation for
 * an analysis that never sized a position would explain nothing; showing
 * "Pozisyon büyüklüğü: doğrulanmış kontrat bilgisi yok" is the honest form.
 *
 * ## Why never becomes the conclusion
 *
 * Nothing here is wired back into an action, a number, a provenance or a
 * confirmation state. The panel has no callbacks and emits nothing.
 */

const TOPIC_LABEL: Record<string, string> = {
  BULLISH_EVIDENCE: 'Neden yükseliş?',
  BEARISH_EVIDENCE: 'Neden düşüş?',
  SETUP_QUALITY: 'Kurulum kalitesi neden bu?',
  ENTRY_QUALITY: 'Giriş kalitesi neden bu?',
  SCENARIO_STATE: 'Senaryo neden bu durumda?',
  PENDING_CONFIRMATION: 'Neden bekleniyor?',
  CONTRADICTION: 'Neden çelişki var?',
  SUPPORT_ZONE: 'Neden destek?',
  RESISTANCE_ZONE: 'Neden direnç?',
  POSITION_SIZE: 'Pozisyon büyüklüğü neden bu?',
  RISK_FINDING: 'Risk neden uyarıyor?',
  NO_TRADE_BLOCK: 'Neden işlem yok?',
  SCORE_CHANGE: 'Puan neden değişti?',
};

export interface WhyPanelProps {
  readonly explanations: readonly WhyExplanation[];
  readonly showRaw?: boolean;
}

export function WhyPanel({ explanations, showRaw = false }: WhyPanelProps) {
  if (explanations.length === 0) return null;

  return (
    <section className="why" aria-labelledby="why-heading">
      <h3 className="why__heading" id="why-heading">
        Gerekçeler
      </h3>
      <p className="why__note">
        Her açıklama, sonucun kendi hesap dökümünden okunur; sonucu değiştirmez, yeni bir değer
        üretmez.
      </p>

      <ul className="why__list">
        {explanations.map((item) => (
          <li
            key={`${item.topic}-${item.subject}`}
            className={`why__item ${item.available ? '' : 'why__item--unavailable'}`}
          >
            <h4 className="why__topic">
              {TOPIC_LABEL[item.topic] ?? item.topic}
              <span className="why__subject">{item.subject}</span>
            </h4>

            {item.available ? (
              <ul className="why__reasons">
                {item.reasons.map((reason) => (
                  <li
                    key={`${reason.code}-${reason.beginner}`}
                    className={`why__reason why__reason--${reason.severity.toLowerCase()}`}
                  >
                    {/* One fact, two registers. Pro adds the machine-readable
                        code and the engine that produced it, so a reader can
                        go and check. */}
                    <span className="why__text">{showRaw ? reason.pro : reason.beginner}</span>
                    {showRaw && (
                      <span className="why__origin">
                        <code>{reason.code}</code> · {reason.source}
                        {reason.timeframe ? ` · ${reason.timeframe}` : ''}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="why__unavailable">{item.unavailableReason}</p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
