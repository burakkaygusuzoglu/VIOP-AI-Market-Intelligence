import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import {
  advanceReplay,
  analyseReplay,
  createReplaySession,
  getReplaySession,
  listReplayPositions,
  listReplaySessions,
  readReplayPerformance,
  type CreateReplayPayload,
  type ReplayAnalysisDto,
  type ReplayPerformanceDto,
  type ReplaySessionDto,
  type ReplaySummaryDto,
} from '../api/replay';
import type { PaperPositionDto } from '../api/paper';
import { CandlestickChart } from '../components/CandlestickChart';
import { PerformanceOverview } from '../components/PerformanceOverview';
import { ReplayControls } from '../components/ReplayControls';
import { capability } from '../domain/capabilities';
import {
  REPLAY_STATE_LABEL,
  isNewerThan,
  timeframesOf,
  toChartSeries,
  windowOf,
  type PlaybackSpeed,
} from '../domain/replay';
import { formatTimestamp } from '../format/display';
import type { ExperienceMode } from './AnalysisWorkspace';
import '../components/Replay.css';

/**
 * The replay workspace (Phase 11). Simulation only.
 *
 * ## The screen shows what the server revealed, and nothing else
 *
 * Every candle drawn here came out of a response that had already been bounded
 * by the replay clock. There is no filtering step in this file, and there must
 * never be one: a future bar that had to be hidden would mean the browser was
 * holding it, and "hidden" is one devtools tab away from "shown".
 *
 * ## Play is a scheduler, not a simulator
 *
 * Play issues the same single step the button issues, on a timer. Speed changes
 * the delay between commands and nothing else - no request carries it, and the
 * server never hears about it. Pause stops issuing commands; it does not tell
 * the server to stop anything, because the server was not running.
 *
 * ## A late answer cannot move the cursor backwards
 *
 * Steps overlap easily when a person clicks quickly or plays at 5x. Each
 * response is accepted only if its cursor version is newer than the one on
 * screen, so an answer that arrives out of order is discarded rather than
 * rendered over a newer one.
 */

export interface ReplayScreenProps {
  readonly mode: ExperienceMode;
  readonly onBack: () => void;
}

const TIMEFRAMES = ['1D', '1H', '15M', '5M'] as const;

type TimeframeCode = (typeof TIMEFRAMES)[number];

interface FileEntry {
  readonly name: string;
  readonly content: string;
}

/** Read a chosen file as UTF-8 text. `FileReader` because jsdom has no `text()`. */
function readAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error('read failed'));
    reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '');
    reader.readAsText(file);
  });
}

function keyFor(prefix: string): string {
  const random = Math.random().toString(36).slice(2, 10);
  return `${prefix}-${Date.now().toString(36)}-${random}`;
}

export function ReplayScreen({ mode, onBack }: ReplayScreenProps) {
  const [sessions, setSessions] = useState<readonly ReplaySummaryDto[]>([]);
  const [session, setSession] = useState<ReplaySessionDto | null>(null);
  const [chart, setChart] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<ReplayAnalysisDto | null>(null);
  const [positions, setPositions] = useState<readonly PaperPositionDto[]>([]);
  const [performance, setPerformance] = useState<ReplayPerformanceDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<PlaybackSpeed>(1);

  const replay = capability('market-replay');
  const current = useRef<ReplaySessionDto | null>(null);
  current.current = session;

  const report = useCallback((cause: unknown) => {
    if (cause instanceof DOMException && cause.name === 'AbortError') return;
    setError(cause instanceof ApiError ? cause.message : 'İstek tamamlanamadı.');
  }, []);

  const accept = useCallback((incoming: ReplaySessionDto) => {
    // The guard. A response older than what is on screen is dropped.
    if (!isNewerThan(incoming, current.current)) return;
    current.current = incoming;
    setSession(incoming);
  }, []);

  const refreshList = useCallback(async () => {
    try {
      const page = await listReplaySessions();
      setSessions(page.items);
    } catch (cause) {
      report(cause);
    }
  }, [report]);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  const open = useCallback(
    async (sessionId: string, timeframe: string | null = null) => {
      setBusy(true);
      setError(null);
      try {
        const opened = await getReplaySession(sessionId, timeframe);
        current.current = null;
        accept(opened);
        setChart(timeframe ?? opened.plan.driver_timeframe);
        setAnalysis(null);
        setPerformance(null);
        setPositions(await listReplayPositions(sessionId));
      } catch (cause) {
        report(cause);
      } finally {
        setBusy(false);
      }
    },
    [accept, report],
  );

  const step = useCallback(
    async (steps: number) => {
      const active = current.current;
      if (active === null) return;
      setError(null);
      try {
        const result = await advanceReplay(active.id, steps, {
          chart,
          expectedVersion: active.cursor.version,
          idempotencyKey: keyFor('replay-advance'),
        });
        accept(result.session);
        if (result.observed_positions > 0) {
          setPositions(await listReplayPositions(active.id));
        }
      } catch (cause) {
        setPlaying(false);
        report(cause);
      }
    },
    [accept, chart, report],
  );

  // Play is a timer around the same command. It holds no market state, and
  // stopping it stops nothing on the server.
  useEffect(() => {
    if (!playing || session === null) return undefined;
    if (session.cursor.state === 'END_OF_DATASET') {
      setPlaying(false);
      return undefined;
    }
    const timer = setTimeout(() => {
      void step(1);
    }, 1000 / speed);
    return () => clearTimeout(timer);
  }, [playing, session, speed, step]);

  const analyse = useCallback(async () => {
    const active = current.current;
    if (active === null) return;
    setBusy(true);
    setError(null);
    try {
      setAnalysis(await analyseReplay(active.id));
    } catch (cause) {
      report(cause);
    } finally {
      setBusy(false);
    }
  }, [report]);

  const measure = useCallback(async () => {
    const active = current.current;
    if (active === null) return;
    setBusy(true);
    setError(null);
    try {
      setPerformance(await readReplayPerformance(active.id));
    } catch (cause) {
      report(cause);
    } finally {
      setBusy(false);
    }
  }, [report]);

  const create = useCallback(
    async (payload: CreateReplayPayload) => {
      setBusy(true);
      setError(null);
      try {
        const created = await createReplaySession(payload, keyFor('replay-create'));
        current.current = null;
        accept(created);
        setChart(created.plan.driver_timeframe);
        setAnalysis(null);
        setPerformance(null);
        setPositions([]);
        await refreshList();
      } catch (cause) {
        report(cause);
      } finally {
        setBusy(false);
      }
    },
    [accept, refreshList, report],
  );

  return (
    <div className="replay-screen">
      <section className="replay-screen__banner" role="note" aria-label="Simülasyon uyarısı">
        <p className="replay-screen__banner-title">GEÇMİŞE SARMA — SİMÜLASYON</p>
        <p>
          Kendi yüklediğiniz geçmiş veriyi mum mum ileri sararsınız. Her sayı, seçtiğiniz geçmiş ana
          göre hesaplanır; o anda kapanmamış hiçbir mum gösterilmez. Gerçek emir oluşturulmaz.
        </p>
      </section>

      {replay && replay.state !== 'AVAILABLE_NOW' && (
        <p className="replay-screen__notice" role="note">
          {replay.detail}
        </p>
      )}

      <div className="replay-screen__controls">
        <button type="button" onClick={onBack}>
          Panele dön
        </button>
      </div>

      {error !== null && (
        <p className="replay-screen__error" role="alert">
          {error}
        </p>
      )}

      {session === null ? (
        <>
          <CreateReplayForm onCreate={create} busy={busy} />
          <SessionList items={sessions} onOpen={(id) => void open(id)} />
        </>
      ) : (
        <Workspace
          mode={mode}
          session={session}
          chart={chart}
          analysis={analysis}
          positions={positions}
          performance={performance}
          busy={busy}
          playing={playing}
          speed={speed}
          onChart={(timeframe) => {
            setChart(timeframe);
            void open(session.id, timeframe);
          }}
          onStep={(steps) => void step(steps)}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onSpeed={setSpeed}
          onAnalyse={() => void analyse()}
          onMeasure={() => void measure()}
          onClose={() => {
            setPlaying(false);
            setSession(null);
            current.current = null;
            void refreshList();
          }}
        />
      )}
    </div>
  );
}

// ----------------------------------------------------------------------

interface WorkspaceProps {
  readonly mode: ExperienceMode;
  readonly session: ReplaySessionDto;
  readonly chart: string | null;
  readonly analysis: ReplayAnalysisDto | null;
  readonly positions: readonly PaperPositionDto[];
  readonly performance: ReplayPerformanceDto | null;
  readonly busy: boolean;
  readonly playing: boolean;
  readonly speed: PlaybackSpeed;
  readonly onChart: (timeframe: string) => void;
  readonly onStep: (steps: number) => void;
  readonly onPlay: () => void;
  readonly onPause: () => void;
  readonly onSpeed: (speed: PlaybackSpeed) => void;
  readonly onAnalyse: () => void;
  readonly onMeasure: () => void;
  readonly onClose: () => void;
}

function Workspace(props: WorkspaceProps) {
  const { session, mode } = props;
  const shown = props.chart ?? session.plan.driver_timeframe;
  const window = windowOf(session, shown);

  return (
    <div className="replay-workspace">
      <section className="replay-workspace__cursor" aria-labelledby="replay-cursor-heading">
        <h3 id="replay-cursor-heading">Geçmiş piyasa anı</h3>
        <p className="replay-workspace__clock">{formatTimestamp(session.cursor.replay_as_of)}</p>
        <dl className="replay-workspace__facts">
          <div>
            <dt>Enstrüman</dt>
            <dd>{session.plan.symbol}</dd>
          </div>
          <div>
            <dt>Adım zaman dilimi</dt>
            <dd>{session.plan.driver_timeframe}</dd>
          </div>
          <div>
            <dt>Durum</dt>
            <dd>{REPLAY_STATE_LABEL[session.cursor.state] ?? session.cursor.state}</dd>
          </div>
          <div>
            <dt>Açıklanan mum</dt>
            <dd>
              {session.cursor.revealed_driver_candles} / {session.cursor.driver_total_candles}
            </dd>
          </div>
          {mode === 'PRO' && (
            <>
              <div>
                <dt>Sürüm</dt>
                <dd>{session.cursor.version}</dd>
              </div>
              <div>
                <dt>Veri kümesi</dt>
                <dd>{session.dataset.dataset_id}</dd>
              </div>
              <div>
                <dt>Başlangıç</dt>
                <dd>{formatTimestamp(session.plan.replay_start)}</dd>
              </div>
            </>
          )}
        </dl>
        <p className="replay-workspace__provenance">
          Veri: kullanıcı yüklemesi geçmiş veri · Dolumlar: simülasyon · Emir yürütme: devre dışı
        </p>
      </section>

      <ReplayControls
        atEnd={session.cursor.state === 'END_OF_DATASET'}
        busy={props.busy}
        playing={props.playing}
        speed={props.speed}
        maxSteps={session.max_advance_steps}
        onStep={props.onStep}
        onPlay={props.onPlay}
        onPause={props.onPause}
        onSpeed={props.onSpeed}
        onAnalyse={props.onAnalyse}
        onMeasure={props.onMeasure}
        onClose={props.onClose}
      />

      <section className="replay-workspace__chart" aria-labelledby="replay-chart-heading">
        <h3 id="replay-chart-heading">Açıklanmış mumlar</h3>
        <div className="replay-workspace__tabs" role="tablist" aria-label="Zaman dilimi">
          {timeframesOf(session).map((timeframe) => (
            <button
              key={timeframe}
              type="button"
              role="tab"
              aria-selected={timeframe === shown}
              onClick={() => props.onChart(timeframe)}
            >
              {timeframe}
            </button>
          ))}
        </div>
        {window === null ? (
          <p>Bu zaman dilimi bu veri kümesinde yok.</p>
        ) : (
          <>
            <p className="replay-workspace__window">
              {window.revealed} mum açıklandı, {window.datasetTotal} mum veri kümesinde var
              {window.truncated
                ? `; grafikte son ${window.candles.length} tanesi çizildi.`
                : '; hepsi çizildi.'}
            </p>
            <CandlestickChart series={toChartSeries(window)} symbol={session.plan.symbol} />
          </>
        )}
      </section>

      <section className="replay-workspace__availability" aria-labelledby="replay-avail-heading">
        <h3 id="replay-avail-heading">Zaman dilimi uygunluğu</h3>
        <div className="replay-table__scroll">
          <table className="replay-table">
            <caption className="replay-table__caption">
              Bir mum, ancak kapanışı geçmiş piyasa anına eşit veya ondan önceyse görünür.
            </caption>
            <thead>
              <tr>
                <th scope="col">Zaman dilimi</th>
                <th scope="col">Açıklanan</th>
                <th scope="col">Veri kümesinde</th>
              </tr>
            </thead>
            <tbody>
              {session.availability.map((item) => (
                <tr key={item.timeframe}>
                  <th scope="row">{item.timeframe}</th>
                  <td>{item.revealed}</td>
                  <td>{item.dataset_total}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {props.analysis !== null && (
        <section className="replay-workspace__analysis" aria-labelledby="replay-analysis-heading">
          <h3 id="replay-analysis-heading">Bu andaki analiz</h3>
          <p>
            Analiz anı: <strong>{formatTimestamp(props.analysis.replay_as_of)}</strong>
          </p>
          <p>
            {props.analysis.analysis.technical_available
              ? 'Deterministik analiz, yalnızca bu ana kadar kapanmış mumlarla üretildi.'
              : 'Bu anda analiz için yeterli kapanmış mum yok.'}
          </p>
          <ul className="replay-workspace__timeframes">
            {props.analysis.analysis.timeframes.map((item) => (
              <li key={item.timeframe}>
                {item.timeframe}: {item.candle_count} mum
                {item.usable ? '' : ' · analize katılmadı'}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="replay-workspace__positions" aria-labelledby="replay-positions-heading">
        <h3 id="replay-positions-heading">Bu oturumun simülasyon pozisyonları</h3>
        {props.positions.length === 0 ? (
          <p>Bu oturumda henüz pozisyon açılmadı.</p>
        ) : (
          <div className="replay-table__scroll">
            <table className="replay-table">
              <thead>
                <tr>
                  <th scope="col">Pozisyon</th>
                  <th scope="col">Yön</th>
                  <th scope="col">Durum</th>
                  <th scope="col">Gerçekleşen (brüt)</th>
                  <th scope="col">İşlenen çubuk</th>
                </tr>
              </thead>
              <tbody>
                {props.positions.map((item) => (
                  <tr key={item.id}>
                    <th scope="row">{item.id}</th>
                    <td>{item.direction}</td>
                    <td>{item.state}</td>
                    <td>{item.realized_gross}</td>
                    <td>{item.bars_applied}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {props.performance !== null && (
        <section className="replay-workspace__performance">
          <p className="replay-workspace__scope">
            Bu ölçümler yalnızca bu oturumun {props.performance.position_ids.length} pozisyonunu
            kapsar.
          </p>
          <PerformanceOverview dto={props.performance.performance} mode={mode} />
        </section>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------

interface SessionListProps {
  readonly items: readonly ReplaySummaryDto[];
  readonly onOpen: (sessionId: string) => void;
}

function SessionList({ items, onOpen }: SessionListProps) {
  return (
    <section className="replay-sessions" aria-labelledby="replay-sessions-heading">
      <h3 id="replay-sessions-heading">Kayıtlı geçmişe sarma oturumları</h3>
      {items.length === 0 ? (
        <p>Henüz oturum yok. Aşağıdan geçmiş veri yükleyerek başlayın.</p>
      ) : (
        <ul className="replay-sessions__list">
          {items.map((item) => (
            <li key={item.id}>
              <button type="button" onClick={() => onOpen(item.id)}>
                <span className="replay-sessions__symbol">{item.symbol}</span>
                <span>{formatTimestamp(item.replay_as_of)}</span>
                <span>
                  {item.revealed_driver_candles} / {item.driver_total_candles} mum
                </span>
                <span>{REPLAY_STATE_LABEL[item.state] ?? item.state}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ----------------------------------------------------------------------

interface CreateReplayFormProps {
  readonly onCreate: (payload: CreateReplayPayload) => void;
  readonly busy: boolean;
}

function CreateReplayForm({ onCreate, busy }: CreateReplayFormProps) {
  const symbolId = useId();
  const startId = useId();
  const driverId = useId();
  const [symbol, setSymbol] = useState('');
  const [start, setStart] = useState('');
  const [driver, setDriver] = useState<TimeframeCode>('5M');
  const [files, setFiles] = useState<Partial<Record<TimeframeCode, FileEntry>>>({});

  const choose = useCallback(async (code: TimeframeCode, file: File) => {
    const content = await readAsText(file);
    setFiles((entries) => ({ ...entries, [code]: { name: file.name, content } }));
  }, []);

  const supplied = TIMEFRAMES.filter((code) => files[code] !== undefined);
  const ready =
    supplied.length > 0 && files[driver] !== undefined && symbol.trim() !== '' && start !== '';

  return (
    <section className="replay-create" aria-labelledby="replay-create-heading">
      <h3 id="replay-create-heading">Yeni geçmişe sarma oturumu</h3>
      <p>
        Her zaman dilimi için kendi geçmiş OHLCV dosyanızı yükleyin. Adım zaman dilimi, bir adımın
        kaç mum ilerlettiğini belirler ve yüklenen dosyalar arasında olmalıdır.
      </p>

      <label htmlFor={symbolId}>Enstrüman kodu</label>
      <input
        id={symbolId}
        value={symbol}
        onChange={(event) => setSymbol(event.target.value)}
        placeholder="Örn. dosyanızdaki kod"
      />
      <p className="replay-create__hint">
        Yazdığınız kod doğrulanmış sözleşme meta verisi değildir: çarpan, tik veya teminat bilgisi
        buradan türetilmez.
      </p>

      <label htmlFor={startId}>Başlangıç anı (UTC)</label>
      <input
        id={startId}
        type="datetime-local"
        value={start}
        onChange={(event) => setStart(event.target.value)}
      />
      <p className="replay-create__hint">
        Bu andan önce kapanmış mumlar ısınma geçmişi olarak kullanılabilir; sonrası yalnızca adım
        adım açılır.
      </p>

      <label htmlFor={driverId}>Adım zaman dilimi</label>
      <select
        id={driverId}
        value={driver}
        onChange={(event) => setDriver(event.target.value as TimeframeCode)}
      >
        {TIMEFRAMES.map((code) => (
          <option key={code} value={code}>
            {code}
          </option>
        ))}
      </select>

      <ul className="replay-create__files">
        {TIMEFRAMES.map((code) => (
          <li key={code}>
            <label htmlFor={`replay-file-${code}`}>{code} verisi</label>
            <input
              id={`replay-file-${code}`}
              type="file"
              accept=".csv,text/csv,text/plain"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void choose(code, file);
              }}
            />
            <span>{files[code]?.name ?? 'Yüklenmedi'}</span>
          </li>
        ))}
      </ul>

      <button
        type="button"
        disabled={!ready || busy}
        onClick={() =>
          onCreate({
            symbol: symbol.trim(),
            driver_timeframe: driver,
            replay_start: new Date(`${start}Z`).toISOString(),
            datasets: supplied.map((code) => ({
              timeframe: code,
              content: files[code]?.content ?? '',
              source_name: files[code]?.name ?? `${code}.csv`,
            })),
          })
        }
      >
        OTURUMU BAŞLAT
      </button>
    </section>
  );
}
