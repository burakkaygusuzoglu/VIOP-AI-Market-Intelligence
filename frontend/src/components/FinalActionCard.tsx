import type { FinalAction, SystemStatus } from '../domain/models';
import { ACTION_TONE } from '../domain/models';
import './FinalActionCard.css';

/**
 * The final state, or an honest reason there is none (§8, §23).
 *
 * Two things this component exists to prevent:
 *
 * **WAIT rendered as NO_TRADE.** They are not degrees of the same caution.
 * WAIT means a setup may exist and something has not happened yet; NO_TRADE
 * means a deterministic blocker prevents the trade and waiting will not fix
 * it. They get different tones, different headlines and different explanatory
 * copy, because a user who reads "bekle" as "reddedildi" will stop watching a
 * setup that was about to trigger.
 *
 * **A system failure rendered as a market view.** A provider timeout is not
 * bearish. When `systemStatus` is set, the card renders a system panel with a
 * neutral tone and no directional language at all - and it still shows which
 * actions deterministic policy permits, because that answer never needed a
 * model.
 */

const ACTION_HEADLINE: Record<FinalAction, string> = {
  LONG: 'LONG',
  SHORT: 'SHORT',
  WAIT: 'BEKLE',
  NO_TRADE: 'İŞLEM YOK',
};

const ACTION_MEANING: Record<FinalAction, string> = {
  LONG: 'Koşullar alış yönünde bir işlemi destekliyor.',
  SHORT: 'Koşullar satış yönünde bir işlemi destekliyor.',
  WAIT: 'Kurulum oluşabilir ancak bir teyit hâlâ bekleniyor. Reddedilmiş değil.',
  NO_TRADE: 'Deterministik bir engel işlemi uygun kılmıyor. Beklemek bunu çözmez.',
};

const STATUS_HEADLINE: Record<SystemStatus, string> = {
  NOT_CONFIGURED: 'SENTEZ YAPILANDIRILMAMIŞ',
  PROVIDER_FAILURE: 'SENTEZ SERVİSİ YANIT VERMEDİ',
  INVALID_OUTPUT: 'SENTEZ SONUCU KABUL EDİLMEDİ',
  CONTEXT_TOO_LARGE: 'ANALİZ BAĞLAMI ÇOK BÜYÜK',
  CONTEXT_UNAVAILABLE: 'SENTEZ BAĞLAMI OLUŞTURULAMADI',
  ANALYSIS_UNAVAILABLE: 'ANALİZ HENÜZ ÜRETİLMEDİ',
};

const STATUS_MEANING: Record<SystemStatus, string> = {
  NOT_CONFIGURED: 'Bu bir sistem durumudur, piyasa görüşü değildir.',
  PROVIDER_FAILURE: 'Bu bir sistem durumudur. Piyasa hakkında hiçbir şey söylemez.',
  INVALID_OUTPUT: 'Model geçerli bir sonuç döndürmedi; hiçbir işlem kararı kabul edilmedi.',
  CONTEXT_TOO_LARGE: 'İstek güvenli sınırın üstünde kaldı. Sisteme dair bir durumdur.',
  CONTEXT_UNAVAILABLE:
    'Sentez için gereken bağlam derlenemedi. Deterministik analiz etkilenmedi ve yukarıdadır; bu bir sistem durumudur, piyasa görüşü değildir.',
  ANALYSIS_UNAVAILABLE: 'Gösterilecek deterministik analiz yok. Piyasa görüşü değildir.',
};

export interface FinalActionCardProps {
  readonly action: FinalAction | null;
  readonly systemStatus: SystemStatus | null;
  readonly allowedActions: readonly FinalAction[];
  readonly detail?: string;
}

export function FinalActionCard({
  action,
  systemStatus,
  allowedActions,
  detail,
}: FinalActionCardProps) {
  // A system status always wins the headline. If synthesis did not happen,
  // showing an action here - any action - would attribute a market view to a
  // system that produced none.
  if (systemStatus !== null || action === null) {
    const status: SystemStatus = systemStatus ?? 'ANALYSIS_UNAVAILABLE';
    return (
      <section className="action-card action-card--system" aria-labelledby="final-state-heading">
        <p className="action-card__kicker">Sistem durumu</p>
        <h2 className="action-card__headline" id="final-state-heading">
          {STATUS_HEADLINE[status]}
        </h2>
        <p className="action-card__meaning">{STATUS_MEANING[status]}</p>
        {detail && <p className="action-card__detail">{detail}</p>}
        <AllowedActions actions={allowedActions} />
      </section>
    );
  }

  const tone = ACTION_TONE[action];
  return (
    <section
      className={`action-card action-card--${tone}`}
      aria-labelledby="final-state-heading"
      data-action={action}
    >
      <p className="action-card__kicker">Nihai durum</p>
      <h2 className="action-card__headline" id="final-state-heading">
        {ACTION_HEADLINE[action]}
      </h2>
      <p className="action-card__meaning">{ACTION_MEANING[action]}</p>
      {detail && <p className="action-card__detail">{detail}</p>}
      <AllowedActions actions={allowedActions} />
    </section>
  );
}

/**
 * What deterministic policy permitted, shown alongside every state.
 *
 * Present even on failure: the envelope is computed without a model, so it is
 * the honest fallback when synthesis is unavailable.
 */
function AllowedActions({ actions }: { readonly actions: readonly FinalAction[] }) {
  if (actions.length === 0) return null;
  return (
    <p className="action-card__allowed">
      <span className="action-card__allowed-label">İzin verilen durumlar:</span>{' '}
      {actions.map((item) => ACTION_HEADLINE[item]).join(' · ')}
    </p>
  );
}

export const ACTION_HEADLINE_TEXT = ACTION_HEADLINE;
export const SYSTEM_HEADLINE_TEXT = STATUS_HEADLINE;
