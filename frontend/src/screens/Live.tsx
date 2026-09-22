import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { ApiError } from '../api/client';
import {
  analyseLiveSession,
  cancelLiveSession,
  createLiveSession,
  getLiveCapability,
  getLiveSession,
  listLiveSessions,
  listLiveSources,
  openLiveEvents,
  readLiveTimeline,
  removeLiveSession,
  type CreateLivePayload,
  type LiveAnalysisDto,
  type LiveCandleDto,
  type LiveCapabilityDto,
  type LiveEnvelopeDto,
  type LiveSessionDto,
  type LiveSessionListDto,
  type LiveSourceDto,
  type LiveTimeframeDto,
} from '../api/live';
import { mapAnalysis } from '../api/mapAnalysis';
import { FinalActionCard } from '../components/FinalActionCard';
import { TechnicalPanel } from '../components/TechnicalPanel';
import { TimeframeLadder } from '../components/TimeframeLadder';
import { WhyPanel } from '../components/WhyPanel';
import {
  ALERT_LABEL,
  AVAILABILITY_LABEL,
  CONNECTION_LABEL,
  END_ORIGIN_LABEL,
  FRESHNESS_LABEL,
  INTEGRITY_LABEL,
  TIMELINE_LABEL,
  TRANSPORT_LABEL,
  acceptSnapshot,
  anyStale,
  applyEnvelope,
  availableCount,
  initialState,
  olderPageCursor,
  timelineCategory,
  withOlderEntries,
  withTransport,
  type LiveClientState,
  type TransportEvent,
} from '../domain/live';
import { formatTimestamp } from '../format/display';
import type { ExperienceMode } from './AnalysisWorkspace';
import '../components/Live.css';

/**
 * The Live Intelligence workspace (Phase 13 Part 2A). Simulated history only.
 *
 * ## What this screen is
 *
 * A stored historical dataset, played through the live streaming backend.
 * Every candle on it is real history somebody uploaded, arriving now. The
 * banner says so in every mode, and no label anywhere calls it a current
 * price, an exchange feed or a live market.
 *
 * ## Nothing starts by itself
 *
 * Opening this screen reads the capability, the stored datasets and the
 * sessions already running. It creates no session, opens no stream and runs no
 * analysis; each of those is a button.
 *
 * ## The browser keeps no state of its own about the market
 *
 * Every number shown arrived from the server. The screen decides only which
 * arriving message may replace what is shown - see `domain/live.ts` - and
 * when its own connection was lost, it re-reads the authoritative snapshot
 * rather than trusting the notifications it happened to receive.
 */

export interface LiveScreenProps {
  readonly mode: ExperienceMode;
  readonly onBack: () => void;
}

type TabId = 'overview' | 'timeframes' | 'analysis' | 'timeline' | 'alerts';

const TABS: readonly { readonly id: TabId; readonly label: string }[] = [
  { id: 'overview', label: 'GENEL BAKIŞ' },
  { id: 'timeframes', label: 'ZAMAN DİLİMLERİ' },
  { id: 'analysis', label: 'ANALİZ' },
  { id: 'timeline', label: 'ZAMAN ÇİZELGESİ' },
  { id: 'alerts', label: 'UYARILAR' },
];

const RESYNC_DELAY_MS = 1000;

function messageOf(cause: unknown, fallback: string): string | null {
  if (cause instanceof DOMException && cause.name === 'AbortError') return null;
  return cause instanceof ApiError ? cause.message : fallback;
}

function now(): string {
  // Transport log only: when *this browser* saw its connection change. Never
  // shown as, or compared with, a market or server time.
  return new Date().toISOString();
}

export function LiveScreen({ mode, onBack }: LiveScreenProps) {
  const [capability, setCapability] = useState<LiveCapabilityDto | null>(null);
  const [sources, setSources] = useState<readonly LiveSourceDto[]>([]);
  const [sessions, setSessions] = useState<LiveSessionListDto | null>(null);
  const [active, setActive] = useState<LiveClientState | null>(null);
  const [analysis, setAnalysis] = useState<LiveAnalysisDto | null>(null);
  const [analysing, setAnalysing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string>('');
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<TabId>('overview');

  const activeRef = useRef<LiveClientState | null>(null);
  const generation = useRef(0);
  const closeStream = useRef<(() => void) | null>(null);
  const reads = useRef<AbortController | null>(null);
  const analysisAbort = useRef<AbortController | null>(null);
  const resyncTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mounted = useRef(true);

  const commit = useCallback((next: LiveClientState | null) => {
    activeRef.current = next;
    setActive(next);
  }, []);

  const report = useCallback((cause: unknown, fallback: string) => {
    const message = messageOf(cause, fallback);
    if (message !== null && mounted.current) setError(message);
  }, []);

  const stopStream = useCallback(() => {
    closeStream.current?.();
    closeStream.current = null;
    if (resyncTimer.current !== null) {
      clearTimeout(resyncTimer.current);
      resyncTimer.current = null;
    }
  }, []);

  const logTransport = useCallback(
    (transport: LiveClientState['transport'], event: TransportEvent) => {
      const current = activeRef.current;
      if (current !== null) commit(withTransport(current, transport, event));
    },
    [commit],
  );

  // ------------------------------------------------------------------
  // The stream and its resynchronisation
  // ------------------------------------------------------------------

  const resyncRef = useRef<() => Promise<void>>(async () => undefined);

  const startStream = useCallback(
    (sessionId: string, after: number, ticket: number) => {
      stopStream();
      closeStream.current = openLiveEvents(sessionId, after, {
        onOpen: () => {
          if (ticket !== generation.current) return;
          logTransport('OPEN', { at: now(), kind: 'OPENED', detail: 'Olay akışı açıldı' });
        },
        onEnvelope: (envelope: LiveEnvelopeDto) => {
          const current = activeRef.current;
          if (current === null) return;
          const { state, action } = applyEnvelope(current, envelope, ticket);
          if (state !== current) commit(state);
          if (action === 'CLOSE') {
            stopStream();
            setNotice('Akış sona erdi.');
          } else if (action === 'RESYNC' && ticket === generation.current) {
            void resyncRef.current();
          }
        },
        onTransportLost: () => {
          if (ticket !== generation.current) return;
          logTransport('LOST', {
            at: now(),
            kind: 'LOST',
            detail: 'Sunucuyla tarayıcı bağlantısı koptu; yeniden eşitlenecek',
          });
          setNotice('Tarayıcı bağlantısı koptu. Sağlayıcının durumu bu değildir.');
          resyncTimer.current = setTimeout(() => void resyncRef.current(), RESYNC_DELAY_MS);
        },
        onInvalid: () => {
          if (ticket !== generation.current) return;
          logTransport('LOST', { at: now(), kind: 'INVALID', detail: 'Geçersiz olay alındı' });
          void resyncRef.current();
        },
      });
    },
    [commit, logTransport, stopStream],
  );

  const resync = useCallback(async () => {
    const current = activeRef.current;
    if (current === null) return;
    stopStream();
    reads.current?.abort();
    const controller = new AbortController();
    reads.current = controller;
    const ticket = ++generation.current;
    const sessionId = current.sessionId;
    try {
      const snapshot = await getLiveSession(sessionId, controller.signal);
      const page = await readLiveTimeline(sessionId, current.cursor, controller.signal);
      if (ticket !== generation.current || activeRef.current?.sessionId !== sessionId) return;
      const next = withTransport(
        acceptSnapshot(activeRef.current, snapshot, page, ticket),
        'CONNECTING',
        {
          at: now(),
          kind: 'RESYNC',
          detail: 'Yetkili anlık görüntüden yeniden eşitlendi',
        },
      );
      commit(next);
      if (snapshot.lifecycle === 'RUNNING') startStream(sessionId, snapshot.cursor, ticket);
      else
        commit(
          withTransport(next, 'CLOSED', { at: now(), kind: 'CLOSED', detail: 'Oturum sona ermiş' }),
        );
    } catch (cause) {
      if (ticket !== generation.current) return;
      if (cause instanceof ApiError && cause.status === 404) {
        setError(
          'Bu oturum sunucuda artık yok. Oturumlar geçicidir; sunucu yeniden başlatıldıysa eski akış geri getirilmez.',
        );
        logTransport('CLOSED', { at: now(), kind: 'CLOSED', detail: 'Oturum bulunamadı' });
        return;
      }
      report(cause, 'Yeniden eşitleme tamamlanamadı.');
      resyncTimer.current = setTimeout(() => void resyncRef.current(), RESYNC_DELAY_MS * 3);
    }
  }, [commit, logTransport, report, startStream, stopStream]);

  resyncRef.current = resync;

  // ------------------------------------------------------------------
  // Opening, switching, creating, cancelling
  // ------------------------------------------------------------------

  const refresh = useCallback(async () => {
    try {
      const [cap, list, running] = await Promise.all([
        getLiveCapability(),
        listLiveSources(),
        listLiveSessions(),
      ]);
      if (!mounted.current) return;
      setCapability(cap);
      setSources(list.items);
      setSessions(running);
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 503) {
        try {
          setCapability(await getLiveCapability());
        } catch (inner) {
          report(inner, 'Canlı akış yeteneği okunamadı.');
        }
        return;
      }
      report(cause, 'Canlı akış bilgileri okunamadı.');
    }
  }, [report]);

  const open = useCallback(
    async (sessionId: string) => {
      stopStream();
      reads.current?.abort();
      analysisAbort.current?.abort();
      const controller = new AbortController();
      reads.current = controller;
      const ticket = ++generation.current;
      setError(null);
      setAnalysis(null);
      setAnalysing(false);
      setTab('overview');
      try {
        const snapshot = await getLiveSession(sessionId, controller.signal);
        const page = await readLiveTimeline(sessionId, null, controller.signal);
        if (ticket !== generation.current) return; // another session was chosen meanwhile
        const state = initialState(snapshot, page, ticket);
        if (snapshot.lifecycle === 'RUNNING') {
          commit(state);
          startStream(sessionId, snapshot.cursor, ticket);
        } else {
          commit({ ...state, transport: 'CLOSED' });
        }
      } catch (cause) {
        if (ticket === generation.current) report(cause, 'Oturum açılamadı.');
      }
    },
    [commit, report, startStream, stopStream],
  );

  const create = useCallback(
    async (payload: CreateLivePayload) => {
      setBusy(true);
      setError(null);
      try {
        const created = await createLiveSession(payload);
        await open(created.id);
        await refresh();
      } catch (cause) {
        report(cause, 'Oturum başlatılamadı.');
      } finally {
        if (mounted.current) setBusy(false);
      }
    },
    [open, refresh, report],
  );

  const cancel = useCallback(async () => {
    const current = activeRef.current;
    if (current === null) return;
    setError(null);
    try {
      const snapshot = await cancelLiveSession(current.sessionId);
      const latest = activeRef.current;
      if (latest !== null && latest.sessionId === snapshot.id) {
        commit(acceptSnapshot(latest, snapshot, null, latest.generation));
      }
    } catch (cause) {
      report(cause, 'Oturum iptal edilemedi.');
    }
  }, [commit, report]);

  const release = useCallback(async () => {
    const current = activeRef.current;
    if (current === null) return;
    stopStream();
    reads.current?.abort();
    analysisAbort.current?.abort();
    generation.current += 1;
    commit(null);
    setAnalysis(null);
    try {
      await removeLiveSession(current.sessionId);
    } catch (cause) {
      report(cause, 'Oturum kaldırılamadı.');
    }
    await refresh();
  }, [commit, refresh, report, stopStream]);

  const analyse = useCallback(async () => {
    const current = activeRef.current;
    if (current === null || analysing) return; // one request at a time
    analysisAbort.current?.abort();
    const controller = new AbortController();
    analysisAbort.current = controller;
    const sessionId = current.sessionId;
    setAnalysing(true);
    setError(null);
    try {
      const result = await analyseLiveSession(sessionId, controller.signal);
      const latest = activeRef.current;
      if (latest === null || latest.sessionId !== sessionId) return; // switched away
      setAnalysis(result);
      commit(acceptSnapshot(latest, result.session, null, latest.generation));
    } catch (cause) {
      if (activeRef.current?.sessionId === sessionId) report(cause, 'Analiz tamamlanamadı.');
    } finally {
      if (mounted.current && activeRef.current?.sessionId === sessionId) setAnalysing(false);
    }
  }, [analysing, commit, report]);

  const loadOlder = useCallback(async () => {
    const current = activeRef.current;
    if (current === null) return;
    const after = olderPageCursor(current, 100);
    if (after === null) return;
    const { sessionId, generation: ticket } = current;
    try {
      const page = await readLiveTimeline(sessionId, after);
      const latest = activeRef.current;
      if (latest !== null) commit(withOlderEntries(latest, page, sessionId, ticket));
    } catch (cause) {
      report(cause, 'Eski kayıtlar okunamadı.');
    }
  }, [commit, report]);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => {
      mounted.current = false;
      stopStream();
      reads.current?.abort();
      analysisAbort.current?.abort();
    };
  }, [refresh, stopStream]);

  const disabled = capability?.state === 'DISABLED';

  return (
    <div className="live-screen">
      <SimulatedBanner />

      <div className="live-screen__controls">
        <button type="button" onClick={onBack}>
          Panele dön
        </button>
        {active !== null && (
          <button
            type="button"
            onClick={() => {
              stopStream();
              generation.current += 1;
              commit(null);
              setAnalysis(null);
              void refresh();
            }}
          >
            Oturum listesine dön
          </button>
        )}
      </div>

      <p className="live-screen__status" role="status" aria-live="polite">
        {notice}
      </p>

      {error !== null && (
        <p className="live-screen__error" role="alert">
          {error}
        </p>
      )}

      {mode === 'BEGINNER' && <BeginnerGuide />}

      {disabled && capability !== null && (
        <p className="live-screen__notice" role="note">
          {capability.detail}
        </p>
      )}

      {active === null ? (
        !disabled && (
          <>
            <SessionList sessions={sessions} onOpen={(id) => void open(id)} />
            <CreateForm sources={sources} capability={capability} busy={busy} onCreate={create} />
          </>
        )
      ) : (
        <Workspace
          mode={mode}
          state={active}
          analysis={analysis !== null && analysis.session_id === active.sessionId ? analysis : null}
          analysing={analysing}
          tab={tab}
          onTab={setTab}
          onAnalyse={() => void analyse()}
          onCancel={() => void cancel()}
          onRelease={() => void release()}
          onResync={() => void resync()}
          onLoadOlder={() => void loadOlder()}
        />
      )}
    </div>
  );
}

// ----------------------------------------------------------------------
// Always-visible provenance
// ----------------------------------------------------------------------

function SimulatedBanner() {
  return (
    <section className="live-screen__banner" role="note" aria-label="Veri kaynağı uyarısı">
      <p className="live-screen__banner-title">SİMÜLE GEÇMİŞ AKIŞ — SIMULATED HISTORICAL STREAM</p>
      <p>
        Bu akış, daha önce yüklenmiş geçmiş mumları canlı akış altyapısı üzerinden oynatır. Borsaya
        bağlı değildir; gösterilen fiyatlar güncel VİOP fiyatı değildir. Hiçbir işlem otomatik
        açılmaz ve emir gönderilmez.
      </p>
    </section>
  );
}

function BeginnerGuide() {
  return (
    <section className="live-guide" aria-labelledby="live-guide-heading">
      <h3 id="live-guide-heading">Bu ekranı okumak</h3>
      <ul>
        <li>
          <strong>Veri akışı</strong>, mumların tek tek ve sırayla sunucuya ulaşmasıdır. Burada akan
          mumlar geçmiş veridir; gerçek zamanlı piyasa değildir.
        </li>
        <li>
          <strong>“Sağlayıcı bağlı”</strong> yalnızca akışın açık olduğunu söyler. Geçmişin eksiksiz
          olduğunu, analizin güncel olduğunu ya da fiyatın bugünkü piyasa fiyatı olduğunu söylemez.
        </li>
        <li>
          <strong>Onaylı mum</strong>, süresi dolmuş ve değerleri artık değişmeyecek mumdur. Analiz
          yalnızca onaylı mumları kullanır.
        </li>
        <li>
          <strong>Oluşan mum</strong> henüz kapanmamıştır; yüksek, düşük ve kapanışı değişebilir. Bu
          yüzden analize girmez. Bu kaynak oluşan mum yayınlamaz.
        </li>
        <li>
          <strong>Eksik veya açıklanamayan boşluk</strong> olan bir zaman dilimi analize alınmaz;
          boşluk tahminle doldurulmaz.
        </li>
        <li>
          Veri kümesindeki sembol yalnızca bir etikettir: çarpan, tik büyüklüğü, teminat veya vade
          bilgisi buradan çıkarılmaz; gerçek vadeli sözleşme bilgisi doğrulanmış değildir.
        </li>
        <li>Hiçbir işlem otomatik açılmaz; kağıt veya gerçek pozisyon oluşturulmaz.</li>
      </ul>
    </section>
  );
}

// ----------------------------------------------------------------------
// Choosing and starting
// ----------------------------------------------------------------------

function SessionList({
  sessions,
  onOpen,
}: {
  readonly sessions: LiveSessionListDto | null;
  readonly onOpen: (id: string) => void;
}) {
  return (
    <section className="live-sessions" aria-labelledby="live-sessions-heading">
      <h3 id="live-sessions-heading">Bu sunucudaki akış oturumları</h3>
      {sessions === null || sessions.items.length === 0 ? (
        <p>
          Açık oturum yok. Oturumlar geçicidir: sunucu yeniden başlatılınca kaybolur ve geri
          getirilmez.
        </p>
      ) : (
        <ul className="live-sessions__list">
          {sessions.items.map((item) => (
            <li key={item.id}>
              <button type="button" onClick={() => onOpen(item.id)}>
                <span className="live-sessions__label">{item.instrument_label}</span>
                <span>{CONNECTION_LABEL[item.connection] ?? item.connection}</span>
                <span>{item.lifecycle === 'RUNNING' ? 'Çalışıyor' : 'Sona erdi'}</span>
                <span>{formatTimestamp(item.created_at)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {sessions !== null && (
        <p className="live-sessions__capacity">
          En fazla {sessions.capacity} oturum aynı anda tutulabilir.
        </p>
      )}
    </section>
  );
}

function CreateForm({
  sources,
  capability,
  busy,
  onCreate,
}: {
  readonly sources: readonly LiveSourceDto[];
  readonly capability: LiveCapabilityDto | null;
  readonly busy: boolean;
  readonly onCreate: (payload: CreateLivePayload) => void;
}) {
  const sourceId = useId();
  const windowId = useId();
  const paceId = useId();
  const [chosen, setChosen] = useState('');
  const [timeframes, setTimeframes] = useState<readonly string[]>([]);
  const [windowCandles, setWindowCandles] = useState(300);
  const [pace, setPace] = useState<CreateLivePayload['pace']>('NORMAL');

  const source = sources.find((item) => item.source_id === chosen) ?? null;
  const limits = capability?.limits ?? null;
  const low = limits?.min_window_candles ?? 50;
  const high = limits?.max_window_candles ?? 1000;
  const ready =
    source !== null &&
    source.streamable &&
    timeframes.length > 0 &&
    windowCandles >= low &&
    windowCandles <= high;

  return (
    <section className="live-create" aria-labelledby="live-create-heading">
      <h3 id="live-create-heading">Yeni simüle akış</h3>
      {sources.length === 0 ? (
        <p>
          Oynatılacak geçmiş veri kümesi yok. Önce “Geçmişe sarma” ekranından kendi geçmiş OHLCV
          verinizi yükleyin; aynı veri kümesi burada akış olarak oynatılabilir.
        </p>
      ) : (
        <>
          <label htmlFor={sourceId}>Geçmiş veri kümesi</label>
          <select
            id={sourceId}
            value={chosen}
            onChange={(event) => {
              setChosen(event.target.value);
              setTimeframes([]);
            }}
          >
            <option value="">Seçin</option>
            {sources.map((item) => (
              <option key={item.source_id} value={item.source_id} disabled={!item.streamable}>
                {item.instrument_label} · {item.timeframes.map((tf) => tf.timeframe).join(', ')}
                {item.streamable ? '' : ' · akış olarak oynatılamaz'}
              </option>
            ))}
          </select>
          {source !== null && !source.streamable && (
            <p className="live-create__hint">{source.refusal}</p>
          )}
          <p className="live-create__hint">
            Etiket bir sözleşme kimliği değildir; çarpan, tik veya teminat bilgisi türetilmez.
          </p>

          {source !== null && source.streamable && (
            <fieldset className="live-create__timeframes">
              <legend>Akıtılacak zaman dilimleri</legend>
              {source.timeframes.map((tf) => {
                const id = `live-tf-${tf.timeframe}`;
                return (
                  <span key={tf.timeframe}>
                    <input
                      id={id}
                      type="checkbox"
                      checked={timeframes.includes(tf.timeframe)}
                      onChange={(event) =>
                        setTimeframes((items) =>
                          event.target.checked
                            ? [...items, tf.timeframe]
                            : items.filter((item) => item !== tf.timeframe),
                        )
                      }
                    />
                    <label htmlFor={id}>
                      {tf.timeframe} ({tf.rows} mum)
                    </label>
                  </span>
                );
              })}
            </fieldset>
          )}

          <label htmlFor={windowId}>Pencere (en ince zaman diliminde mum sayısı)</label>
          <input
            id={windowId}
            type="number"
            min={low}
            max={high}
            value={windowCandles}
            onChange={(event) => setWindowCandles(event.target.valueAsNumber || 0)}
          />

          <label htmlFor={paceId}>Oynatma hızı</label>
          <select
            id={paceId}
            value={pace}
            onChange={(event) => setPace(event.target.value as CreateLivePayload['pace'])}
          >
            <option value="SLOW">Yavaş</option>
            <option value="NORMAL">Normal</option>
            <option value="FAST">Hızlı</option>
          </select>
          <p className="live-create__hint">
            Hız yalnızca mumların sunucuya ne sıklıkla ulaştığını değiştirir; mumların piyasa
            zamanlarını değiştirmez.
          </p>

          <button
            type="button"
            disabled={!ready || busy}
            onClick={() =>
              source !== null &&
              onCreate({
                source_id: source.source_id,
                timeframes,
                window_candles: windowCandles,
                pace,
              })
            }
          >
            AKIŞI BAŞLAT
          </button>
        </>
      )}
    </section>
  );
}

// ----------------------------------------------------------------------
// The workspace
// ----------------------------------------------------------------------

interface WorkspaceProps {
  readonly mode: ExperienceMode;
  readonly state: LiveClientState;
  readonly analysis: LiveAnalysisDto | null;
  readonly analysing: boolean;
  readonly tab: TabId;
  readonly onTab: (tab: TabId) => void;
  readonly onAnalyse: () => void;
  readonly onCancel: () => void;
  readonly onRelease: () => void;
  readonly onResync: () => void;
  readonly onLoadOlder: () => void;
}

function Workspace(props: WorkspaceProps) {
  const { state, mode } = props;
  const session = state.session;
  const isPro = mode === 'PRO';
  const running = session.lifecycle === 'RUNNING';
  const tabsId = useId();

  return (
    <div className="live-workspace">
      <section className="live-workspace__header" aria-labelledby="live-header-heading">
        <h3 id="live-header-heading">{session.identity.instrument_label}</h3>
        <ul className="live-chips" aria-label="Akışın niteliği">
          <li className="live-chip live-chip--simulated">Kaynak: SİMÜLE GEÇMİŞ AKIŞ</li>
          <li className="live-chip live-chip--historical">Piyasa güncelliği: GEÇMİŞ VERİ</li>
          <li className="live-chip">Sözleşme kimliği: DOĞRULANMADI</li>
          <li className="live-chip">Emir yürütme: DEVRE DIŞI</li>
        </ul>
        <StatusStrip state={state} />
        {anyStale(session) && (
          <p className="live-workspace__stale" role="alert">
            CANLI VERİ BAYAT (LIVE DATA STALE) — bayat zaman dilimleri için yeni onay üretilmez.
          </p>
        )}
        <div className="live-workspace__actions">
          <button type="button" onClick={props.onCancel} disabled={!running}>
            AKIŞI DURDUR
          </button>
          <button type="button" onClick={props.onRelease}>
            OTURUMU KAPAT
          </button>
          {isPro && (
            <button type="button" onClick={props.onResync}>
              Anlık görüntüden yeniden eşitle
            </button>
          )}
        </div>
        {isPro && (
          <dl className="live-facts">
            <dt>Oturum kimliği</dt>
            <dd>{session.id}</dd>
            <dt>Sağlayıcı</dt>
            <dd>{session.identity.provider_id}</dd>
            <dt>Köken</dt>
            <dd>{session.identity.provenance}</dd>
            <dt>MarketCurrency</dt>
            <dd>{session.identity.market_currency}</dd>
            <dt>Veri kümesi</dt>
            <dd>{session.identity.source_id}</dd>
            <dt>İmleç / en eski tutulan</dt>
            <dd>
              {state.cursor} / {session.oldest_retained}
            </dd>
            <dt>Okuyucu sayısı</dt>
            <dd>{session.subscribers}</dd>
            <dt>Anlık görüntü (sunucu saati)</dt>
            <dd>
              <time dateTime={session.snapshot_at}>{session.snapshot_at}</time>
            </dd>
          </dl>
        )}
      </section>

      <div className="live-tabs" role="tablist" aria-label="Canlı akış bölümleri">
        {TABS.map((item, index) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            id={`${tabsId}-tab-${item.id}`}
            aria-selected={props.tab === item.id}
            aria-controls={`${tabsId}-panel-${item.id}`}
            tabIndex={props.tab === item.id ? 0 : -1}
            onClick={() => props.onTab(item.id)}
            onKeyDown={(event: KeyboardEvent<HTMLButtonElement>) => {
              const last = TABS.length - 1;
              const target =
                event.key === 'ArrowRight'
                  ? index === last
                    ? 0
                    : index + 1
                  : event.key === 'ArrowLeft'
                    ? index === 0
                      ? last
                      : index - 1
                    : event.key === 'Home'
                      ? 0
                      : event.key === 'End'
                        ? last
                        : null;
              if (target === null) return;
              event.preventDefault();
              const next = TABS[target];
              if (next === undefined) return;
              props.onTab(next.id);
              document.getElementById(`${tabsId}-tab-${next.id}`)?.focus();
            }}
          >
            {item.label}
          </button>
        ))}
      </div>

      <section
        className="live-panel"
        role="tabpanel"
        id={`${tabsId}-panel-${props.tab}`}
        aria-labelledby={`${tabsId}-tab-${props.tab}`}
      >
        {props.tab === 'overview' && <Overview state={state} isPro={isPro} />}
        {props.tab === 'timeframes' && <Timeframes session={session} isPro={isPro} />}
        {props.tab === 'analysis' && (
          <AnalysisPanel
            session={session}
            analysis={props.analysis}
            analysing={props.analysing}
            isPro={isPro}
            onAnalyse={props.onAnalyse}
          />
        )}
        {props.tab === 'timeline' && (
          <Timeline state={state} isPro={isPro} onLoadOlder={props.onLoadOlder} />
        )}
        {props.tab === 'alerts' && <Alerts session={session} isPro={isPro} />}
      </section>
    </div>
  );
}

/** Connection and usable evidence, side by side - never one green light. */
function StatusStrip({ state }: { readonly state: LiveClientState }) {
  const session = state.session;
  const available = availableCount(session);
  return (
    <dl className="live-status">
      <div>
        <dt>Sağlayıcı bağlantısı</dt>
        <dd data-connection={session.connection}>
          {CONNECTION_LABEL[session.connection] ?? session.connection}
        </dd>
      </div>
      <div>
        <dt>Analize uygun zaman dilimi</dt>
        <dd>
          {available} / {session.timeframes.length}
        </dd>
      </div>
      <div>
        <dt>Tarayıcı bağlantısı</dt>
        <dd data-transport={state.transport}>{TRANSPORT_LABEL[state.transport]}</dd>
      </div>
      <div>
        <dt>Oturum</dt>
        <dd>
          {session.lifecycle === 'RUNNING'
            ? 'Çalışıyor'
            : `Sona erdi · ${END_ORIGIN_LABEL[session.end_origin ?? ''] ?? ''}`}
        </dd>
      </div>
    </dl>
  );
}

function CandleFacts({
  candle,
  isPro,
}: {
  readonly candle: LiveCandleDto;
  readonly isPro: boolean;
}) {
  return (
    <dl className="live-candle">
      <dt>Piyasa zamanı (mum başlangıcı)</dt>
      <dd>
        <time dateTime={candle.market_open_time}>{formatTimestamp(candle.market_open_time)}</time>
      </dd>
      <dt>Açılış / Yüksek / Düşük / Kapanış</dt>
      <dd>
        {candle.open} / {candle.high} / {candle.low} / {candle.close}
      </dd>
      <dt>Hacim</dt>
      <dd>{candle.volume}</dd>
      <dt>Alındı (sunucu saati)</dt>
      <dd>
        <time dateTime={candle.received_at}>{formatTimestamp(candle.received_at)}</time>
      </dd>
      {isPro && (
        <>
          <dt>Kapsam sonu (piyasa)</dt>
          <dd>{candle.market_coverage_end}</dd>
          <dt>Olay zamanı (piyasa)</dt>
          <dd>{candle.market_event_time}</dd>
          <dt>Alındı (tam)</dt>
          <dd>{candle.received_at}</dd>
          <dt>Sıra numarası</dt>
          <dd>{candle.sequence ?? '—'}</dd>
        </>
      )}
    </dl>
  );
}

function Overview({ state, isPro }: { readonly state: LiveClientState; readonly isPro: boolean }) {
  const session = state.session;
  return (
    <div className="live-overview">
      <p className="live-overview__window">
        Oynatılan geçmiş piyasa aralığı:{' '}
        <strong>{formatTimestamp(session.playback.market_window_start)}</strong> –{' '}
        <strong>{formatTimestamp(session.playback.market_window_end)}</strong>. Bu tarihler piyasa
        zamanıdır; mumların sunucuya ulaştığı an değildir.
      </p>
      {session.timeframes.map((item) => (
        <section key={item.timeframe} className="live-overview__timeframe">
          <h4>{item.timeframe}</h4>
          <p>
            Akış tazeliği: <strong>{FRESHNESS_LABEL[item.freshness]}</strong> · Piyasa güncelliği:{' '}
            <strong>GEÇMİŞ VERİ — güncel fiyat değil</strong> · Bütünlük:{' '}
            <strong>{INTEGRITY_LABEL[item.integrity]}</strong> ·{' '}
            <strong>{AVAILABILITY_LABEL[item.availability]}</strong>
          </p>
          <h5>Son onaylı mum</h5>
          {item.latest_confirmed === null ? (
            <p>Henüz onaylı mum yok.</p>
          ) : (
            <CandleFacts candle={item.latest_confirmed} isPro={isPro} />
          )}
          <h5>Oluşan mum (onaylı değil, analize girmez)</h5>
          {item.forming === null ? (
            <p>
              {session.playback.forming_candles_published
                ? 'Şu anda oluşan mum yok.'
                : 'Bu kaynak oluşan mum yayınlamaz: saklanan mumlar kapanmış mumlardır.'}
            </p>
          ) : (
            <CandleFacts candle={item.forming} isPro={isPro} />
          )}
        </section>
      ))}
    </div>
  );
}

function Timeframes({
  session,
  isPro,
}: {
  readonly session: LiveSessionDto;
  readonly isPro: boolean;
}) {
  return (
    <div className="live-table__scroll">
      <table className="live-table">
        <caption className="live-table__caption">
          Bir zaman dilimi yalnızca sağlayıcı bağlıyken, akış tazeyken, bütünlük TAM iken ve en az
          bir onaylı mum varken analize uygundur.
        </caption>
        <thead>
          <tr>
            <th scope="col">Zaman dilimi</th>
            <th scope="col">Tazelik</th>
            <th scope="col">Bütünlük</th>
            <th scope="col">Uygunluk</th>
            <th scope="col">Onaylı mum</th>
            <th scope="col">Nedenler</th>
            {isPro && <th scope="col">Ayrıntı</th>}
          </tr>
        </thead>
        <tbody>
          {session.timeframes.map((item) => (
            <tr key={item.timeframe}>
              <th scope="row">{item.timeframe}</th>
              <td>{FRESHNESS_LABEL[item.freshness]}</td>
              <td>{INTEGRITY_LABEL[item.integrity]}</td>
              <td>{AVAILABILITY_LABEL[item.availability]}</td>
              <td>{item.closed_count}</td>
              <td>
                {item.reasons.length === 0 ? (
                  '—'
                ) : (
                  <ul className="live-reasons">
                    {item.reasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                )}
              </td>
              {isPro && (
                <td>
                  <ProTimeframe item={item} />
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProTimeframe({ item }: { readonly item: LiveTimeframeDto }) {
  return (
    <dl className="live-facts live-facts--compact">
      <dt>Tazelik eşiği (alım saati)</dt>
      <dd>{item.freshness_threshold_seconds} sn</dd>
      <dt>Son sıra</dt>
      <dd>{item.last_sequence ?? '—'}</dd>
      <dt>Eksik sıra / taşma</dt>
      <dd>
        {item.missing_sequences} / {item.missing_overflowed ? 'evet' : 'hayır'}
      </dd>
      <dt>Zaman boşluğu / sıra uyuşmazlığı</dt>
      <dd>
        {item.temporal_gaps} / {item.sequence_mismatches}
      </dd>
      <dt>Çelişki / tekrar / geç dolum</dt>
      <dd>
        {item.conflicts} / {item.duplicates} / {item.late_fills}
      </dd>
      <dt>Süreklilik bekleniyor / oluşan önde</dt>
      <dd>
        {item.awaiting_continuity ? 'evet' : 'hayır'} / {item.forming_ahead ? 'evet' : 'hayır'}
      </dd>
      <dt>Kırpılan / çözülmeden kırpılan</dt>
      <dd>
        {item.trimmed} / {item.unresolved_trimmed}
      </dd>
      <dt>Defter sürümü</dt>
      <dd>{item.version}</dd>
      <dt>Son olay (piyasa)</dt>
      <dd>{item.last_market_event_time ?? '—'}</dd>
      <dt>Son alım (sunucu)</dt>
      <dd>{item.last_received_at ?? '—'}</dd>
    </dl>
  );
}

function AnalysisPanel({
  session,
  analysis,
  analysing,
  isPro,
  onAnalyse,
}: {
  readonly session: LiveSessionDto;
  readonly analysis: LiveAnalysisDto | null;
  readonly analysing: boolean;
  readonly isPro: boolean;
  readonly onAnalyse: () => void;
}) {
  const available = session.analysis.available_timeframes;
  const blocked = session.timeframes.filter((item) => item.availability === 'UNAVAILABLE');
  const current =
    analysis !== null &&
    session.analysis.last_current &&
    session.analysis.last_market_as_of === analysis.market_as_of;
  const model = analysis === null ? null : mapAnalysis(analysis.analysis);

  return (
    <div className="live-analysis">
      <p>
        Analiz yalnızca siz istediğinizde, yalnızca şu anda analize uygun zaman dilimlerinin{' '}
        <strong>onaylı</strong> mumlarıyla ve mevcut deterministik analiz motoruyla çalışır. Her mum
        için otomatik çalışmaz ve bir dil modeli çağırmaz.
      </p>
      <button
        type="button"
        onClick={onAnalyse}
        disabled={analysing || available.length === 0}
        aria-describedby="live-analysis-availability"
      >
        {analysing ? 'Analiz ediliyor…' : 'ONAYLI VERİYİ ANALİZ ET'}
      </button>
      <p id="live-analysis-availability">
        {available.length === 0
          ? 'Şu anda analize uygun zaman dilimi yok — BEKLE. Nedenler aşağıda.'
          : `Analize uygun: ${available.join(', ')}`}
      </p>
      {blocked.length > 0 && (
        <ul className="live-reasons">
          {blocked.map((item) => (
            <li key={item.timeframe}>
              {item.timeframe}: {item.reasons.join('; ')}
            </li>
          ))}
        </ul>
      )}

      {analysis !== null && model !== null && (
        <section className="live-analysis__result" aria-labelledby="live-analysis-heading">
          <h4 id="live-analysis-heading">Onaylı veri analizi</h4>
          <p className="live-analysis__currency">
            Analiz edilen piyasa anı (geçmiş):{' '}
            <strong>{formatTimestamp(analysis.market_as_of)}</strong>. İstek zamanı (sunucu saati):{' '}
            {formatTimestamp(analysis.requested_at)}. Kaynak: SİMÜLE GEÇMİŞ AKIŞ · Piyasa
            güncelliği: GEÇMİŞ VERİ.
          </p>
          <p className="live-analysis__currentness" data-current={current ? 'yes' : 'no'}>
            {current
              ? 'Bu analiz akışın şu anki onaylı verisini yansıtıyor (piyasanın bugünkü durumunu değil).'
              : 'GÜNCEL DEĞİL — akış bu analizden sonra değişti ya da artık analize uygun değil. Yeniden analiz edin.'}
          </p>
          {analysis.excluded.length > 0 && (
            <>
              <h5>Analize alınmayan zaman dilimleri</h5>
              <ul className="live-reasons">
                {analysis.excluded.map((item) => (
                  <li key={item.timeframe}>
                    {item.timeframe}: {item.reasons.join('; ')}
                  </li>
                ))}
              </ul>
            </>
          )}
          <FinalActionCard
            action={model.finalAction}
            systemStatus={model.systemStatus}
            allowedActions={model.allowedActions}
            {...(model.synthesis && model.synthesis.status !== 'SUCCESS'
              ? { detail: model.synthesis.detail }
              : {})}
          />
          <TimeframeLadder readings={model.timeframes} />
          {model.missing.length > 0 && (
            <section aria-labelledby="live-missing-heading">
              <h5 id="live-missing-heading">Eksik bilgi</h5>
              <ul>
                {model.missing.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>
          )}
          <WhyPanel explanations={model.why ?? []} showRaw={isPro} />
          {isPro && (
            <>
              <TechnicalPanel panels={model.technical ?? []} showRaw />
              <dl className="live-facts">
                <dt>Önbellekten mi</dt>
                <dd>{analysis.reused ? 'evet (girdiler aynı)' : 'hayır (yeni hesaplandı)'}</dd>
                <dt>Girdi parmak izi</dt>
                <dd>
                  {analysis.fingerprint
                    .map((item) => `${item.timeframe}@v${item.version}`)
                    .join(', ')}
                </dd>
                <dt>Sözleşme doğrulandı mı</dt>
                <dd>{model.context.contractVerified ? 'evet' : 'hayır'}</dd>
              </dl>
            </>
          )}
          <p className="live-analysis__note">
            Bu bir karar desteğidir, işlem talimatı değildir. Hiçbir pozisyon otomatik açılmaz.
          </p>
        </section>
      )}
    </div>
  );
}

function Timeline({
  state,
  isPro,
  onLoadOlder,
}: {
  readonly state: LiveClientState;
  readonly isPro: boolean;
  readonly onLoadOlder: () => void;
}) {
  const entries = [...state.timeline].sort((a, b) => b.seq - a.seq);
  const canLoadOlder = olderPageCursor(state, 100) !== null;
  return (
    <div className="live-timeline">
      {state.timelineGap && (
        <p className="live-timeline__gap" role="note">
          Bu liste eksiksiz değil: bazı eski kayıtlar sınır nedeniyle düşürüldü ya da bağlantı
          sırasında atlandı. Durum, sunucudaki yetkili anlık görüntüden okunur.
        </p>
      )}
      {entries.length === 0 ? (
        <p>Henüz kayıt yok.</p>
      ) : (
        <ol className="live-timeline__list" aria-label="Akış olayları, en yenisi önce">
          {entries.map((entry) => (
            <li key={entry.seq} data-kind={entry.kind}>
              <span className="live-timeline__category">{timelineCategory(entry.kind)}</span>
              <span className="live-timeline__label">
                {TIMELINE_LABEL[entry.kind] ?? entry.kind}
                {entry.timeframe !== null ? ` · ${entry.timeframe}` : ''}
                {entry.backfill ? ' · geri doldurma' : ''}
              </span>
              {entry.market_open_time !== null && (
                <span>Piyasa: {formatTimestamp(entry.market_open_time)}</span>
              )}
              <span>Kayıt (sunucu): {formatTimestamp(entry.recorded_at)}</span>
              {isPro && (
                <span className="live-timeline__raw">
                  #{entry.seq} {entry.code}
                  {entry.before !== null ? ` ${entry.before} → ${entry.after ?? ''}` : ''}
                  {entry.sequence !== null ? ` seq=${entry.sequence}` : ''}
                </span>
              )}
            </li>
          ))}
        </ol>
      )}
      <p className="live-timeline__range">
        Gösterilen: {entries.length} kayıt
        {entries.length > 0
          ? ` (#${entries[entries.length - 1]?.seq ?? ''}–#${entries[0]?.seq ?? ''})`
          : ''}
        . Sunucuda tutulan kayıtlar: #{state.session.oldest_retained}–#{state.session.cursor}.
      </p>
      {canLoadOlder && (
        <button type="button" onClick={onLoadOlder}>
          Daha eski kayıtları yükle
        </button>
      )}

      <section className="live-transport" aria-labelledby="live-transport-heading">
        <h4 id="live-transport-heading">Tarayıcı bağlantı günlüğü</h4>
        <p>
          Bu kayıtlar yalnızca bu tarayıcının sunucuyla bağlantısını anlatır. Piyasa olayı değildir;
          sağlayıcının durumu değildir.
        </p>
        {state.lastHeartbeatAt !== null && (
          <p>
            Son yaşam sinyali (sunucu saati): {formatTimestamp(state.lastHeartbeatAt)} — yeni mum
            değildir.
          </p>
        )}
        <ol>
          {[...state.transportLog].reverse().map((event, index) => (
            <li key={`${event.at}-${index}`}>{event.detail}</li>
          ))}
        </ol>
      </section>
    </div>
  );
}

function Alerts({ session, isPro }: { readonly session: LiveSessionDto; readonly isPro: boolean }) {
  return (
    <div className="live-alerts">
      <p>Bu uyarılar veri kalitesi hakkındadır; işlem talimatı değildir.</p>
      {session.alerts.length === 0 ? (
        <p>Şu anda veri kalitesi uyarısı yok.</p>
      ) : (
        <ul>
          {session.alerts.map((alert) => (
            <li key={`${alert.kind}-${alert.timeframe ?? 'all'}`} data-alert={alert.kind}>
              <strong>{ALERT_LABEL[alert.kind] ?? alert.kind}</strong>
              {alert.timeframe !== null ? ` · ${alert.timeframe}` : ''}
              {isPro ? ` · ${alert.kind} · ${alert.detail}` : ''}
            </li>
          ))}
        </ul>
      )}
      {Object.keys(session.rejection_counts).length > 0 && (
        <>
          <h4>Reddedilen gözlemler</h4>
          <ul>
            {Object.entries(session.rejection_counts).map(([code, count]) => (
              <li key={code}>
                {code}: {count}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
