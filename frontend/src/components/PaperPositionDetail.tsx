import { useEffect, useId, useRef, useState } from 'react';
import {
  ApiError,
  listPaperEvents,
  postPaperAction,
  postPaperObservations,
  type PaperAction,
  type PaperEventDto,
  type PaperPositionDto,
} from '../api/paper';
import { DIRECTION_LABEL, STATE_LABEL, mapPaperPosition } from '../domain/paper';
import { formatTimestamp, formatValue, MISSING_DISPLAY } from '../format/display';
import type { ExperienceMode } from '../screens/AnalysisWorkspace';
import './PaperTrading.css';

/**
 * One paper position (Phase 9).
 *
 * ## Beginner and Pro read one object
 *
 * Both render `mapPaperPosition(dto)`. Beginner shows the state, the money, the
 * stop, the targets and what happened; Pro adds the full event data, the
 * simulation rules, provenance and the risk engine's own reason. No number is
 * computed here - gross, fees, net and unrealized are the backend's strings.
 *
 * ## Nothing here trades
 *
 * The buttons ask the server to change a *simulation*: close at the next bar,
 * move the simulated stop, cancel a pending plan, apply uploaded bars. None of
 * them sends an order anywhere, and the copy never says one was sent.
 */

export interface PaperPositionDetailProps {
  readonly position: PaperPositionDto;
  readonly mode: ExperienceMode;
  readonly onUpdated: (position: PaperPositionDto) => void;
}

const ACTION_LABEL: Record<PaperAction, string> = {
  close: 'Sonraki çubukta kapat',
  'stop/breakeven': 'Stopu başabaşa taşı',
  cancel: 'Planı iptal et',
};

export function PaperPositionDetail({ position: dto, mode, onUpdated }: PaperPositionDetailProps) {
  const position = mapPaperPosition(dto);
  const isPro = mode === 'PRO';
  const headingId = useId();
  const fileId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState('');
  const [bars, setBars] = useState<readonly PaperEventDto[] | null>(null);

  // A different position moves the reading position to its heading (§49).
  useEffect(() => {
    heading.current?.focus();
    setBars(null);
    setError(null);
  }, [dto.id]);

  const run = async (label: string, work: () => Promise<PaperPositionDto>) => {
    if (busy) return;
    setBusy(label);
    setError(null);
    try {
      const updated = await work();
      onUpdated(updated);
      setStatus(`${label}: ${STATE_LABEL[mapPaperPosition(updated).state]}.`);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : `${label} tamamlanamadı.`);
    } finally {
      setBusy(null);
    }
  };

  const upload = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      const content = typeof reader.result === 'string' ? reader.result : '';
      void run('Çubuklar uygulandı', () => postPaperObservations(dto.id, content, file.name));
    };
    reader.onerror = () => setError('Dosya okunamadı.');
    reader.readAsText(file);
  };

  const loadBars = async () => {
    try {
      const page = await listPaperEvents(dto.id, 0);
      setBars(page.items.filter((item) => item.type === 'OBSERVATION_APPLIED'));
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Olaylar yüklenemedi.');
    }
  };

  const price = (raw: string | null) => formatValue(raw, 'price');

  return (
    <section className="paper-detail" aria-labelledby={headingId}>
      <header className="paper-detail__header">
        <h3 className="paper-detail__heading" id={headingId} ref={heading} tabIndex={-1}>
          {dto.symbol} · {DIRECTION_LABEL[dto.direction] ?? dto.direction}
        </h3>
        <p className={`paper-state paper-state--${position.state}`}>
          <span className="paper-state__tag">SİMÜLASYON</span> {STATE_LABEL[position.state]}
        </p>
      </header>

      {position.state === 'AMBIGUOUS_HALTED' && (
        <p className="paper-detail__warning" role="alert">
          Bir çubuk hem stopa hem hedefe dokundu; OHLC verisi hangisinin önce olduğunu söylemez.
          Pozisyon donduruldu ve hiçbir dolum simüle edilmedi. Yalnızca manuel kapanış mümkündür.
        </p>
      )}
      {position.state === 'REJECTED' && (
        <p className="paper-detail__warning" role="alert">
          Giriş çubuğu planı geçersiz kıldı; pozisyon hiç açılmadı.
        </p>
      )}

      <dl className="paper-detail__grid">
        <div>
          <dt>Miktar / kalan</dt>
          <dd>
            {dto.quantity} / {dto.remaining} {dto.provenance.unit || 'birim'}
          </dd>
        </div>
        <div>
          <dt>Planlanan giriş</dt>
          <dd>{price(dto.intended_entry)}</dd>
        </div>
        <div>
          <dt>Simüle giriş dolumu</dt>
          <dd>{price(dto.entry_fill_price)}</dd>
        </div>
        <div>
          <dt>Stop (ilk / şimdiki)</dt>
          <dd>
            {price(dto.initial_stop)} / {price(dto.stop)}
          </dd>
        </div>
        <div>
          <dt>Son işaret fiyatı</dt>
          <dd>{price(dto.last_mark)}</dd>
        </div>
        <div>
          <dt>Gerçekleşen brüt K/Z</dt>
          <dd>{price(dto.realized_gross)}</dd>
        </div>
        <div>
          <dt>Ücretler</dt>
          <dd>{dto.fees_total === null ? 'Modellenmedi' : price(dto.fees_total)}</dd>
        </div>
        <div>
          <dt>Gerçekleşen net K/Z</dt>
          <dd>
            {dto.realized_net === null ? 'Ücret modellenmediği için yok' : price(dto.realized_net)}
          </dd>
        </div>
        <div>
          <dt>Gerçekleşmemiş brüt K/Z</dt>
          <dd>{dto.unrealized_gross === null ? MISSING_DISPLAY : price(dto.unrealized_gross)}</dd>
        </div>
      </dl>

      <div className="paper-table-scroll">
        <table className="paper-table">
          <caption>Hedefler</caption>
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Fiyat</th>
              <th scope="col">Miktar</th>
              <th scope="col">Durum</th>
            </tr>
          </thead>
          <tbody>
            {dto.targets.map((target) => (
              <tr key={target.index}>
                <th scope="row">{target.index}</th>
                <td>{price(target.price)}</td>
                <td>{target.quantity}</td>
                <td>{target.filled ? `Doldu (${price(target.fill_price)})` : 'Bekliyor'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="paper-detail__actions">
        {position.actions.canObserve && (
          <>
            <label htmlFor={fileId} className="paper-detail__upload">
              Kapanmış geçmiş çubukları yükle (CSV)
            </label>
            <input
              id={fileId}
              type="file"
              accept=".csv,text/csv"
              disabled={busy !== null}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) upload(file);
                event.target.value = '';
              }}
            />
          </>
        )}
        {position.actions.canClose && (
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => void run(ACTION_LABEL.close, () => postPaperAction(dto.id, 'close'))}
          >
            {ACTION_LABEL.close}
          </button>
        )}
        {position.actions.canMoveStop && (
          <button
            type="button"
            disabled={busy !== null}
            onClick={() =>
              void run(ACTION_LABEL['stop/breakeven'], () =>
                postPaperAction(dto.id, 'stop/breakeven'),
              )
            }
          >
            {ACTION_LABEL['stop/breakeven']}
          </button>
        )}
        {position.actions.canCancel && (
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => void run(ACTION_LABEL.cancel, () => postPaperAction(dto.id, 'cancel'))}
          >
            {ACTION_LABEL.cancel}
          </button>
        )}
      </div>
      {dto.close_pending && (
        <p className="paper-detail__note">
          Kapanış istendi: kalan birimler sonraki çubuğun açılışında simüle edilecek.
        </p>
      )}

      <p className="visually-hidden" role="status" aria-live="polite">
        {busy ? `${busy}…` : status}
      </p>
      {error && (
        <p className="paper-detail__error" role="alert">
          {error}
        </p>
      )}

      <section className="paper-detail__why" aria-label="Ne oldu ve neden">
        <h4 className="paper-detail__subheading">Ne oldu ve neden</h4>
        <ol className="paper-events">
          {position.events.map((event) => (
            <li
              key={event.sequence}
              className={
                event.important
                  ? 'paper-events__item paper-events__item--important'
                  : 'paper-events__item'
              }
            >
              <p className="paper-events__title">
                {event.important && <span className="paper-events__marker">Önemli: </span>}
                {event.title}
                {event.marketTime && (
                  <span className="paper-events__time"> · {formatTimestamp(event.marketTime)}</span>
                )}
              </p>
              <p className="paper-events__detail">{event.detail}</p>
              {isPro && (
                <dl className="paper-events__data">
                  {Object.entries(event.data).map(([name, value]) => (
                    <div key={name}>
                      <dt>{name}</dt>
                      <dd>{value === '' ? MISSING_DISPLAY : value}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </li>
          ))}
        </ol>
      </section>

      {isPro && (
        <section className="paper-detail__pro" aria-label="Simülasyon ayrıntıları">
          <h4 className="paper-detail__subheading">Simülasyon kuralları ve kaynak</h4>
          <dl className="paper-detail__grid">
            <div>
              <dt>Kural sürümü</dt>
              <dd>{dto.simulation.rules_version}</dd>
            </div>
            <div>
              <dt>Giriş modeli</dt>
              <dd>{dto.simulation.entry_model}</dd>
            </div>
            <div>
              <dt>Stop dolum modeli</dt>
              <dd>{dto.simulation.stop_fill_model}</dd>
            </div>
            <div>
              <dt>Hedef dolum modeli</dt>
              <dd>{dto.simulation.target_fill_model}</dd>
            </div>
            <div>
              <dt>Aynı çubuk politikası</dt>
              <dd>{dto.simulation.same_bar}</dd>
            </div>
            <div>
              <dt>Kayma</dt>
              <dd>
                {dto.simulation.slippage_mode}
                {dto.simulation.slippage_points ? ` (${dto.simulation.slippage_points})` : ''}
              </dd>
            </div>
            <div>
              <dt>Ücret</dt>
              <dd>
                {dto.simulation.fee_mode}
                {dto.simulation.fee_per_unit
                  ? ` (${dto.simulation.fee_per_unit}, kullanıcı tanımlı)`
                  : ''}
              </dd>
            </div>
            <div>
              <dt>Dolumların kaynağı</dt>
              <dd>
                {dto.provenance.fills} · {dto.provenance.market_data}
              </dd>
            </div>
            <div>
              <dt>Nokta değeri</dt>
              <dd>
                {dto.provenance.point_value} ({dto.provenance.point_value_status})
              </dd>
            </div>
            <div>
              <dt>Varlık sınıfı</dt>
              <dd>
                {dto.provenance.asset_class} ({dto.provenance.asset_class_status})
              </dd>
            </div>
            <div>
              <dt>Risk motoru</dt>
              <dd>
                {dto.risk.outcome} — {dto.risk.reason}
              </dd>
            </div>
            <div>
              <dt>Köken</dt>
              <dd>{dto.origin}</dd>
            </div>
          </dl>
          <button type="button" onClick={() => void loadBars()}>
            Uygulanan çubukları göster ({dto.bars_applied})
          </button>
          {bars && (
            <div className="paper-table-scroll">
              <table className="paper-table">
                <caption>Uygulanan çubuklar (ilk sayfa)</caption>
                <thead>
                  <tr>
                    <th scope="col">Sıra</th>
                    <th scope="col">Açılış zamanı</th>
                    <th scope="col">A</th>
                    <th scope="col">Y</th>
                    <th scope="col">D</th>
                    <th scope="col">K</th>
                  </tr>
                </thead>
                <tbody>
                  {bars.map((item) => (
                    <tr key={item.sequence}>
                      <th scope="row">{item.sequence}</th>
                      <td>{formatTimestamp(item.market_time)}</td>
                      <td>{item.data.open}</td>
                      <td>{item.data.high}</td>
                      <td>{item.data.low}</td>
                      <td>{item.data.close}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </section>
  );
}
