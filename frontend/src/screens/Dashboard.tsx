import { CAPABILITIES, type CapabilityState } from '../domain/capabilities';
import { SystemStatus } from '../components/SystemStatus';
import './Dashboard.css';

/**
 * The entry point (§26).
 *
 * Built only from what exists **now**. The master spec's dashboard also lists
 * watchlists, live setup cards, paper positions and a journal. Paper positions
 * have their own screen since Phase 9, reached from a secondary control rather
 * than a dashboard card; the rest belong to phases that own their data sources,
 * and a card showing zero items would be indistinguishable from a working
 * feature with nothing in it.
 *
 * So the unavailable ones are listed as unavailable, by name, with the reason —
 * which is more useful than omitting them (a reader wonders whether they were
 * forgotten) and far more honest than rendering an empty widget.
 */

const STATE_LABEL: Record<CapabilityState, string> = {
  AVAILABLE_NOW: 'Kullanılabilir',
  ENDPOINT_NOT_WIRED: 'Uç nokta var, sağlayıcı bağlı değil',
  REQUIRES_CONFIGURATION: 'Yapılandırma gerekir',
  APPLICATION_ONLY: 'Yalnızca uygulama içi',
  DEFERRED: 'Ertelendi',
  NOT_IMPLEMENTED: 'Yok',
};

/** Modules the master spec describes for later phases. Named, never faked. */
const FUTURE_MODULES = [
  { name: 'İzleme listesi', phase: 'Sonraki faz', reason: 'Canlı piyasa verisi gerektirir.' },
  {
    name: 'Canlı kurulum kartları',
    phase: 'Faz 13',
    reason: 'Canlı veri mimarisi bu fazda değil.',
  },
  {
    name: 'Strateji ve kurulum performansı',
    phase: 'Sonraki faz',
    reason:
      'Pozisyonlar bir analize bağlı olarak kaydedilmediği için kurulum/rejim performansı türetilemez.',
  },
] as const;

export interface DashboardProps {
  readonly onAnalyse: () => void;
  readonly onPaper: () => void;
  readonly onPerformance: () => void;
}

export function Dashboard({ onAnalyse, onPaper, onPerformance }: DashboardProps) {
  return (
    <div className="dashboard-screen">
      <section className="dashboard-screen__hero" aria-labelledby="dashboard-heading">
        <h2 className="dashboard-screen__heading" id="dashboard-heading">
          Analiz başlat
        </h2>
        <p className="dashboard-screen__intro">
          Kendi geçmiş OHLCV verinizle deterministik bir analiz üretin. Analiz geçicidir:
          kaydedilmez ve sayfa yenilendiğinde kaybolur.
        </p>
        <button type="button" className="dashboard-screen__cta" onClick={onAnalyse}>
          PİYASA ANALİZİ
        </button>
        <button type="button" className="dashboard-screen__secondary" onClick={onPaper}>
          KAĞIT İŞLEM (SİMÜLASYON)
        </button>
        <button type="button" className="dashboard-screen__secondary" onClick={onPerformance}>
          PERFORMANS VE GÜNLÜK (SİMÜLASYON)
        </button>
        <p className="dashboard-screen__intro">
          Kağıt işlemler yalnızca simülasyondur: gerçek emir oluşturulmaz veya gönderilmez.
        </p>
      </section>

      <SystemStatus />

      <section className="dashboard-screen__capabilities" aria-labelledby="capabilities-heading">
        <h3 className="dashboard-screen__subheading" id="capabilities-heading">
          Şu anda gerçekten çalışan yetenekler
        </h3>
        <ul className="dashboard-screen__list">
          {CAPABILITIES.map((item) => (
            <li key={item.id} className={`dashboard-screen__item--${item.state}`}>
              <span className="dashboard-screen__name">{item.label}</span>
              <span className="dashboard-screen__state">{STATE_LABEL[item.state]}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="dashboard-screen__future" aria-labelledby="future-heading">
        <h3 className="dashboard-screen__subheading" id="future-heading">
          Henüz olmayan modüller
        </h3>
        <p className="dashboard-screen__note">
          Bu modüller sonraki fazlara aittir. Boş bir kart göstermek yerine burada adlarıyla
          listelenirler; hiçbiri çalışıyormuş gibi sunulmaz.
        </p>
        <ul className="dashboard-screen__list">
          {FUTURE_MODULES.map((item) => (
            <li key={item.name} className="dashboard-screen__item--NOT_IMPLEMENTED">
              <span className="dashboard-screen__name">{item.name}</span>
              <span className="dashboard-screen__state">{item.phase}</span>
              <span className="dashboard-screen__reason">{item.reason}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
