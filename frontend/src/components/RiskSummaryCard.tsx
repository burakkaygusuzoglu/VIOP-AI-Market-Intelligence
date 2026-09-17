import type { RiskSummary, SizingOutcome } from '../domain/models';
import { NumericFactValue } from './NumericFactValue';
import './RiskSummaryCard.css';

/**
 * Risk, and never buried (§14).
 *
 * A blocker is the single most consequential thing on the screen, so it is
 * rendered at the top of the card, expanded, in words, with a role that makes
 * a screen reader announce it. It is never behind a collapsed section, never
 * hover-only, and never reduced to a coloured dot.
 *
 * `UNDETERMINED` gets its own treatment rather than being folded into either
 * side: sizing that could not reach an answer is not permission and is not
 * refusal, and presenting it as either would be a lie in one direction or the
 * other (§15).
 */

const OUTCOME_LABEL: Record<SizingOutcome, string> = {
  ALLOWED: 'Pozisyon büyüklüğü hesaplandı',
  NOT_PERMITTED: 'RİSK İZİN VERMİYOR',
  UNDETERMINED: 'Risk belirlenemedi',
  INVALID: 'Risk girdileri geçersiz',
};

const OUTCOME_MEANING: Record<SizingOutcome, string> = {
  ALLOWED: 'Hesap ve risk politikası bir pozisyona izin veriyor.',
  NOT_PERMITTED:
    'Bu koşullarda pozisyon açılamaz. Beklemek bunu değiştirmez; bu kalıcı bir engeldir.',
  UNDETERMINED:
    'Sizing bir sonuca ulaşamadı. Bu, izin verildiği anlamına gelmez - yalnızca bilinmediği.',
  INVALID: 'Risk hesabı için verilen girdiler tutarsız.',
};

/** Only the asset class that is implemented has a label; any other value is
 * shown raw rather than translated into something that sounds supported. */
const ASSET_CLASS_LABEL: Record<string, string> = {
  FUTURES: 'Vadeli işlem sözleşmesi',
};

const OUTCOME_TONE: Record<SizingOutcome, string> = {
  ALLOWED: 'ok',
  NOT_PERMITTED: 'blocked',
  UNDETERMINED: 'unknown',
  INVALID: 'unknown',
};

export interface RiskSummaryCardProps {
  readonly risk: RiskSummary;
  readonly showRaw?: boolean;
}

export function RiskSummaryCard({ risk, showRaw = false }: RiskSummaryCardProps) {
  const blocking = risk.findings.filter((item) => item.severity === 'BLOCKING');
  const isBlocked = risk.outcome === 'NOT_PERMITTED' || blocking.length > 0;

  return (
    <section className={`risk risk--${OUTCOME_TONE[risk.outcome]}`} aria-labelledby="risk-heading">
      <h3 className="risk__heading" id="risk-heading">
        Risk
      </h3>

      {/* `role="alert"` only when something actually blocks: an alert role on
          every render would train users to ignore it. */}
      <p className="risk__outcome" {...(isBlocked ? { role: 'alert' } : {})}>
        {OUTCOME_LABEL[risk.outcome]}
      </p>
      <p className="risk__meaning">{OUTCOME_MEANING[risk.outcome]}</p>
      {risk.detail && <p className="risk__detail">{risk.detail}</p>}

      {blocking.length > 0 && (
        <ul className="risk__findings">
          {blocking.map((finding) => (
            <li key={finding.id} className="risk__finding">
              <span className="risk__finding-code">{finding.code}</span> {finding.detail}
            </li>
          ))}
        </ul>
      )}

      {/* Why there is no number (§32).
          Rendered in place of the facts, never beside them: a reason list and
          a contract count are answers to different questions, and showing both
          would suggest the count survived whatever the reason describes.
          Without this the card read "Risk belirlenemedi" and stopped, leaving
          a user to guess whether they had done something wrong. */}
      {risk.facts.length === 0 && (risk.unavailableReasons?.length ?? 0) > 0 && (
        <div className="risk__unavailable">
          <h4 className="risk__unavailable-heading">Neden hesaplanamadı</h4>
          <ul className="risk__unavailable-list">
            {risk.unavailableReasons?.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Unverified contract metadata must visibly limit what depends on it. */}
      {risk.contract && !risk.contract.verified && (
        <p className="risk__unverified">
          Sözleşme bilgisi doğrulanmadı ({risk.contract.multiplierStatus ?? 'bilinmiyor'}); çarpana
          bağlı hesaplamalar yapılmaz.
        </p>
      )}

      {/* Phase 8.5. Named only when the trusted contract record says so, and
          always with its provenance: a classification is a fact like any
          other, and an unverified one is labelled as such. Only implemented
          asset classes can reach this line - nothing else has a record. */}
      {risk.contract?.assetClass && (
        <p className="risk__asset-class">
          Varlık sınıfı: {ASSET_CLASS_LABEL[risk.contract.assetClass] ?? risk.contract.assetClass}
          {showRaw && ` (${risk.contract.assetClassStatus ?? 'bilinmiyor'})`}
          {!showRaw &&
            risk.contract.assetClassStatus !== 'VERIFIED_CURRENT_FACT' &&
            ' (doğrulanmadı)'}
        </p>
      )}

      {risk.facts.length > 0 && (
        <dl className="risk__facts">
          {risk.facts.map((fact) => (
            <NumericFactValue key={fact.id} fact={fact} showRaw={showRaw} />
          ))}
        </dl>
      )}

      {showRaw && (risk.warnings?.length ?? 0) > 0 && (
        <p className="risk__warnings">Uygunluk: {risk.warnings?.join(' · ')}</p>
      )}
    </section>
  );
}
