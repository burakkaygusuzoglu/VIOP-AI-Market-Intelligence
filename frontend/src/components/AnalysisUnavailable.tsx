import { CAPABILITIES, type CapabilityState } from '../domain/capabilities';
import type { SystemStatus } from '../domain/models';
import { FinalActionCard } from './FinalActionCard';
import './AnalysisUnavailable.css';

/**
 * What the product honestly shows today (§3, §39).
 *
 * There is no runtime path that produces an analysis: no market-data provider
 * is composed at the backend root, and the synthesis endpoint is deliberately
 * deferred. So this is the **live** state of the dashboard, and it says so.
 *
 * What it deliberately is not:
 *
 * * not a spinner that never resolves, which would read as a broken fetch;
 * * not an empty shell with zeroed cards, which would read as "no risk, no
 *   evidence, quality zero" - a set of measurements nobody made;
 * * not seeded with demo data, which would be a fake product runtime.
 *
 * It also carries the capability matrix, so the honesty is specific: a reader
 * can see which parts of the system are live, which exist only inside the
 * backend, and which are not built at all.
 */

/**
 * Turkish label per state.
 *
 * Keyed by `CapabilityState`, not by `string`, so adding a state without a
 * label is a type error rather than a blank badge. The first version of this
 * map was `Record<string, string>` and would have rendered
 * `ENDPOINT_NOT_WIRED` as empty text - a capability silently losing its
 * caveat, which is the exact failure this screen exists to prevent.
 */
const STATE_LABEL: Record<CapabilityState, string> = {
  AVAILABLE_NOW: 'Kullanılabilir',
  ENDPOINT_NOT_WIRED: 'Uç nokta var, sağlayıcı bağlı değil',
  REQUIRES_CONFIGURATION: 'Yapılandırma gerekir',
  APPLICATION_ONLY: 'Yalnızca uygulama içi',
  DEFERRED: 'Ertelendi',
  NOT_IMPLEMENTED: 'Yok',
};

export interface AnalysisUnavailableProps {
  readonly status?: SystemStatus;
  readonly detail?: string;
}

export function AnalysisUnavailable({
  status = 'ANALYSIS_UNAVAILABLE',
  detail,
}: AnalysisUnavailableProps) {
  return (
    <div className="unavailable">
      <FinalActionCard
        action={null}
        systemStatus={status}
        allowedActions={[]}
        detail={
          detail ??
          'Bu kurulumda deterministik analiz üreten bir çalışma zamanı yolu henüz bağlı değil. ' +
            'Gösterilecek bir sonuç yok - bu, piyasa hakkında bir görüş değildir.'
        }
      />

      <section className="unavailable__matrix" aria-labelledby="capability-heading">
        <h3 className="unavailable__heading" id="capability-heading">
          Şu anda gerçekten çalışan yetenekler
        </h3>
        <p className="unavailable__intro">
          Arayüz, arka ucun yapamadığı bir şeyi yapabiliyormuş gibi göstermez. Aşağıdaki liste kodun
          içeriğini değil, çalışma zamanında erişilebilen yüzeyi anlatır.
        </p>
        <ul className="unavailable__list">
          {CAPABILITIES.map((item) => (
            <li key={item.id} className={`unavailable__item unavailable__item--${item.state}`}>
              <span className="unavailable__name">{item.label}</span>
              <span className="unavailable__state">{STATE_LABEL[item.state]}</span>
              <span className="unavailable__detail">{item.detail}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
