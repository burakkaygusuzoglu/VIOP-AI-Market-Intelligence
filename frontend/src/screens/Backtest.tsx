import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import {
  abandonBacktestRun,
  createBacktestRun,
  getBacktestRun,
  listBacktestPositions,
  listBacktestRuns,
  listDatasets,
  listStrategies,
  readBacktestPerformance,
  readCapability,
  readPositionEvents,
  readTrace,
  type BacktestDatasetDto,
  type BacktestEventListDto,
  type BacktestPerformanceDto,
  type BacktestPositionDto,
  type BacktestRunDto,
  type BacktestRunSummaryDto,
  type BacktestTraceDto,
  type CapabilityDto,
  type CreateBacktestPayload,
  type StrategyDto,
} from '../api/backtest';
import { PerformanceOverview } from '../components/PerformanceOverview';
import { formatTimestamp } from '../format/display';
import type { ExperienceMode } from './AnalysisWorkspace';
import '../components/Backtest.css';

/**
 * The backtesting workspace (Phase 12 Part 2A). Simulation only.
 *
 * ## Nothing on this screen is calculated here
 *
 * Every figure rendered below arrived as a string from an endpoint that got it
 * from Phase 9 or Phase 10. There is no arithmetic in this file - no sum, no
 * ratio, no percentage, no net from a gross. A metric the backend reports as
 * unavailable is shown as unavailable, with its reason, and an unmodelled fee
 * produces no net figure at all rather than one equal to the gross.
 *
 * ## A run's status is the only thing that says a result is final
 *
 * A PENDING run has counts - it was created, and something may have stopped
 * part-way - and those counts are not a result. The screen keys off
 * `results_are_final`, which the server derives from the status alone, so a
 * half-finished run can never be dressed as a report.
 *
 * ## A slow answer cannot overwrite a newer one
 *
 * Selecting runs quickly puts several reads in flight. Each selection takes a
 * ticket and an `AbortController`: starting a read aborts the previous one, and
 * a response is written to state only if its ticket is still the newest. This
 * is the same rule the analysis and replay screens follow.
 *
 * ## What the strategy form does not offer
 *
 * The reference strategy's parameters are pinned in code. The catalogue says
 * so, and the form renders them read-only rather than as inputs the backend
 * would ignore. There is no optimiser, no parameter sweep and no ranking.
 */

export interface BacktestScreenProps {
  readonly mode: ExperienceMode;
}

type Tab = 'configure' | 'history' | 'results' | 'trace' | 'positions';

const TABS: readonly { readonly id: Tab; readonly label: string }[] = [
  { id: 'configure', label: 'Kur' },
  { id: 'history', label: 'Geçmiş' },
  { id: 'results', label: 'Sonuçlar' },
  { id: 'trace', label: 'Karar izi' },
  { id: 'positions', label: 'Pozisyonlar' },
];

const STATUS_LABEL: Record<string, string> = {
  PENDING: 'Beklemede',
  RUNNING: 'Çalışıyor',
  COMPLETED: 'Tamamlandı',
  FAILED: 'Başarısız',
};

const OUTCOME_LABEL: Record<string, string> = {
  NO_SIGNAL: 'Sinyal yok',
  WAIT: 'Bekle',
  ENTERED: 'Pozisyon açıldı',
  REFUSED_BY_RISK: 'Risk reddetti',
  REFUSED_BY_ENGINE: 'Motor reddetti',
  EXIT_REQUESTED: 'Çıkış istendi',
  HOLDING: 'Pozisyon taşınıyor',
};

const TRACE_PAGE = 25;
const POSITION_PAGE = 25;

function newKey(): string {
  return `bt-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function BacktestScreen({ mode }: BacktestScreenProps) {
  const pro = mode === 'PRO';
  const formId = useId();

  const [tab, setTab] = useState<Tab>('configure');
  const [capability, setCapability] = useState<CapabilityDto | null>(null);
  const [strategies, setStrategies] = useState<readonly StrategyDto[]>([]);
  const [datasets, setDatasets] = useState<readonly BacktestDatasetDto[]>([]);
  const [runs, setRuns] = useState<readonly BacktestRunSummaryDto[]>([]);

  const [datasetId, setDatasetId] = useState('');
  const [driver, setDriver] = useState('5M');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [equity, setEquity] = useState('100000');
  const [fixedRisk, setFixedRisk] = useState('1000');
  const [feeMode, setFeeMode] = useState('NOT_MODELLED');
  const [feePerUnit, setFeePerUnit] = useState('');
  const [slippageMode, setSlippageMode] = useState('ZERO');
  const [slippagePoints, setSlippagePoints] = useState('');
  const [sameBar, setSameBar] = useState('STOP_FIRST');

  const [run, setRun] = useState<BacktestRunDto | null>(null);
  const [performance, setPerformance] = useState<BacktestPerformanceDto | null>(null);
  const [trace, setTrace] = useState<BacktestTraceDto | null>(null);
  const [traceOffset, setTraceOffset] = useState(0);
  const [positions, setPositions] = useState<readonly BacktestPositionDto[]>([]);
  const [positionTotal, setPositionTotal] = useState(0);
  const [events, setEvents] = useState<BacktestEventListDto | null>(null);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const ticket = useRef(0);
  const inFlight = useRef<AbortController | null>(null);

  const strategy = strategies[0] ?? null;

  const report = useCallback((cause: unknown) => {
    if (cause instanceof DOMException && cause.name === 'AbortError') return;
    setError(cause instanceof ApiError ? cause.message : 'Beklenmeyen bir hata oluştu.');
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const [caps, catalogue, available, history] = await Promise.all([
          readCapability(controller.signal),
          listStrategies(controller.signal),
          listDatasets(25, controller.signal),
          listBacktestRuns(20, controller.signal),
        ]);
        setCapability(caps);
        setStrategies(catalogue.strategies);
        setDatasets(available.items);
        setRuns(history.items);
        const [first] = available.items;
        if (first !== undefined) {
          setDatasetId(first.dataset_id);
          const frame = first.timeframes.find((item) => item.timeframe === '5M');
          if (frame?.first_open_time) setStart(frame.first_open_time.slice(0, 16));
          if (frame?.last_open_time) setEnd(frame.last_open_time.slice(0, 16));
        }
      } catch (cause) {
        report(cause);
      }
    })();
    return () => controller.abort();
  }, [report]);

  /** Load one run's detail, discarding anything a newer selection supersedes. */
  const select = useCallback(
    async (runId: string) => {
      inFlight.current?.abort();
      const controller = new AbortController();
      inFlight.current = controller;
      const mine = ++ticket.current;
      setBusy(true);
      setError(null);
      try {
        const detail = await getBacktestRun(runId, controller.signal);
        if (mine !== ticket.current) return;
        setRun(detail);
        setEvents(null);
        setTraceOffset(0);

        if (detail.results_are_final) {
          const [metrics, firstTrace, page] = await Promise.all([
            readBacktestPerformance(runId, controller.signal),
            readTrace(runId, 0, TRACE_PAGE, controller.signal),
            listBacktestPositions(runId, 0, POSITION_PAGE, controller.signal),
          ]);
          if (mine !== ticket.current) return;
          setPerformance(metrics);
          setTrace(firstTrace);
          setPositions(page.items);
          setPositionTotal(page.total);
        } else {
          setPerformance(null);
          setTrace(null);
          setPositions([]);
          setPositionTotal(0);
        }
        setTab('results');
      } catch (cause) {
        if (mine === ticket.current) report(cause);
      } finally {
        if (mine === ticket.current) setBusy(false);
      }
    },
    [report],
  );

  const submit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setBusy(true);
      setError(null);
      setNotice(null);
      const payload: CreateBacktestPayload = {
        dataset_id: datasetId,
        driver_timeframe: driver,
        start: new Date(start).toISOString(),
        end: new Date(end).toISOString(),
        strategy_id: strategy?.identifier ?? '',
        strategy_version: strategy?.version ?? '',
        account: { equity, used_margin: '0' },
        risk: { mode: 'FIXED', fixed_risk: fixedRisk },
        simulation: {
          same_bar: sameBar,
          slippage_mode: slippageMode,
          slippage_points: slippageMode === 'FIXED_POINTS' ? slippagePoints : null,
          fee_mode: feeMode,
          fee_per_unit: feeMode === 'USER_DEFINED_PER_UNIT' ? feePerUnit : null,
        },
      };
      try {
        const created = await createBacktestRun(payload, newKey());
        setRun(created);
        setNotice(`Koşu ${created.run_id} oluşturuldu.`);
        const history = await listBacktestRuns(20);
        setRuns(history.items);
        await select(created.run_id);
      } catch (cause) {
        report(cause);
      } finally {
        setBusy(false);
      }
    },
    [
      datasetId,
      driver,
      start,
      end,
      strategy,
      equity,
      fixedRisk,
      sameBar,
      slippageMode,
      slippagePoints,
      feeMode,
      feePerUnit,
      select,
      report,
    ],
  );

  const abandon = useCallback(async () => {
    if (run === null) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await abandonBacktestRun(run.run_id);
      setRun(updated);
      setNotice('Yarıda kalan koşu sonlandırıldı. Yeniden denemek yeni bir koşu açar.');
    } catch (cause) {
      report(cause);
    } finally {
      setBusy(false);
    }
  }, [run, report]);

  const loadTrace = useCallback(
    async (offset: number) => {
      if (run === null) return;
      try {
        const page = await readTrace(run.run_id, offset, TRACE_PAGE);
        setTrace(page);
        setTraceOffset(offset);
      } catch (cause) {
        report(cause);
      }
    },
    [run, report],
  );

  const loadEvents = useCallback(
    async (positionId: string) => {
      if (run === null) return;
      try {
        setEvents(await readPositionEvents(run.run_id, positionId, 100));
      } catch (cause) {
        report(cause);
      }
    },
    [run, report],
  );

  const blocked = capability !== null && !capability.financial_execution_available;

  return (
    <section className="backtest" aria-labelledby="backtest-heading">
      <header className="backtest__header">
        <h2 id="backtest-heading">Geriye dönük test</h2>
        <p className="backtest__lede">
          Sabit strateji kuralları geçmiş veriler üzerinde adım adım çalıştırılır. Girişler ve
          çıkışlar <strong>simülasyondur</strong>; hiçbir emir gönderilmez. Geçmişteki sonuçlar
          gelecekteki getiriyi garanti etmez.
        </p>
      </header>

      {mode === 'BEGINNER' ? <BeginnerNotes /> : null}

      {blocked ? (
        <div className="backtest__banner backtest__banner--blocked" role="status">
          <strong>Finansal test şu anda kullanılamıyor.</strong>
          <p>{capability?.reason}</p>
          {pro && capability?.refusal_code ? <code>{capability.refusal_code}</code> : null}
        </div>
      ) : null}

      <nav className="backtest__tabs" aria-label="Geriye dönük test bölümleri">
        <ul role="tablist">
          {TABS.map((item) => (
            <li key={item.id} role="presentation">
              <button
                type="button"
                role="tab"
                id={`${formId}-tab-${item.id}`}
                aria-selected={tab === item.id}
                aria-controls={`${formId}-panel-${item.id}`}
                className={tab === item.id ? 'is-active' : undefined}
                onClick={() => setTab(item.id)}
              >
                {item.label}
              </button>
            </li>
          ))}
        </ul>
      </nav>

      <div aria-live="polite" className="backtest__announce">
        {busy ? 'Yükleniyor…' : null}
        {notice}
      </div>
      {error !== null ? (
        <p className="backtest__error" role="alert">
          {error}
        </p>
      ) : null}

      <div
        role="tabpanel"
        id={`${formId}-panel-${tab}`}
        aria-labelledby={`${formId}-tab-${tab}`}
        className="backtest__panel"
      >
        {tab === 'configure' ? (
          <form className="backtest__form" onSubmit={submit}>
            <fieldset disabled={busy || blocked}>
              <legend>Veri ve pencere</legend>
              <label htmlFor={`${formId}-dataset`}>Geçmiş veri seti</label>
              <select
                id={`${formId}-dataset`}
                value={datasetId}
                onChange={(event) => setDatasetId(event.target.value)}
              >
                {datasets.map((item) => (
                  <option key={item.dataset_id} value={item.dataset_id}>
                    {item.symbol} — {item.total_rows} mum
                  </option>
                ))}
              </select>
              {datasets.length === 0 ? (
                <p className="backtest__empty">
                  Kayıtlı geçmiş veri seti yok. Önce Replay ekranından veri yükleyin.
                </p>
              ) : null}

              <label htmlFor={`${formId}-driver`}>Sürücü zaman dilimi</label>
              <select
                id={`${formId}-driver`}
                value={driver}
                onChange={(event) => setDriver(event.target.value)}
              >
                {['5M', '15M', '1H', '1D'].map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>

              <label htmlFor={`${formId}-start`}>Başlangıç</label>
              <input
                id={`${formId}-start`}
                type="datetime-local"
                value={start}
                onChange={(event) => setStart(event.target.value)}
                required
              />

              <label htmlFor={`${formId}-end`}>Bitiş</label>
              <input
                id={`${formId}-end`}
                type="datetime-local"
                value={end}
                onChange={(event) => setEnd(event.target.value)}
                required
              />
            </fieldset>

            {strategy !== null ? <StrategyCard strategy={strategy} pro={pro} /> : null}

            <fieldset disabled={busy || blocked}>
              <legend>Hesap ve risk</legend>
              <label htmlFor={`${formId}-equity`}>Hesap büyüklüğü</label>
              <input
                id={`${formId}-equity`}
                inputMode="decimal"
                value={equity}
                onChange={(event) => setEquity(event.target.value)}
                required
              />
              <label htmlFor={`${formId}-risk`}>İşlem başına risk</label>
              <input
                id={`${formId}-risk`}
                inputMode="decimal"
                value={fixedRisk}
                onChange={(event) => setFixedRisk(event.target.value)}
                required
              />
            </fieldset>

            <fieldset disabled={busy || blocked}>
              <legend>Simülasyon varsayımları</legend>
              <label htmlFor={`${formId}-samebar`}>Aynı mumda hem stop hem hedef</label>
              <select
                id={`${formId}-samebar`}
                value={sameBar}
                onChange={(event) => setSameBar(event.target.value)}
              >
                <option value="STOP_FIRST">Önce stop varsayılır (kötümser)</option>
                <option value="HALT">Karar verilmez, pozisyon dondurulur</option>
              </select>

              <label htmlFor={`${formId}-slippage`}>Kayma</label>
              <select
                id={`${formId}-slippage`}
                value={slippageMode}
                onChange={(event) => setSlippageMode(event.target.value)}
              >
                <option value="ZERO">Modellenmiyor</option>
                <option value="FIXED_POINTS">Sabit puan</option>
              </select>
              {slippageMode === 'FIXED_POINTS' ? (
                <>
                  <label htmlFor={`${formId}-slippage-points`}>Kayma (puan)</label>
                  <input
                    id={`${formId}-slippage-points`}
                    inputMode="decimal"
                    value={slippagePoints}
                    onChange={(event) => setSlippagePoints(event.target.value)}
                  />
                </>
              ) : null}

              <label htmlFor={`${formId}-fee`}>Komisyon</label>
              <select
                id={`${formId}-fee`}
                value={feeMode}
                onChange={(event) => setFeeMode(event.target.value)}
              >
                <option value="NOT_MODELLED">Modellenmiyor</option>
                <option value="USER_DEFINED_PER_UNIT">Birim başına, kullanıcı tanımlı</option>
              </select>
              {feeMode === 'USER_DEFINED_PER_UNIT' ? (
                <>
                  <label htmlFor={`${formId}-fee-per-unit`}>Birim başına komisyon</label>
                  <input
                    id={`${formId}-fee-per-unit`}
                    inputMode="decimal"
                    value={feePerUnit}
                    onChange={(event) => setFeePerUnit(event.target.value)}
                  />
                </>
              ) : (
                <p className="backtest__hint">
                  Komisyon modellenmediğinde <strong>net sonuç hiç raporlanmaz</strong>. Bilinmeyen
                  maliyet sıfır maliyet demek değildir.
                </p>
              )}
            </fieldset>

            <button type="submit" disabled={busy || blocked || datasets.length === 0}>
              Testi çalıştır
            </button>
          </form>
        ) : null}

        {tab === 'history' ? <RunHistory runs={runs} onSelect={select} pro={pro} /> : null}

        {tab === 'results' ? (
          <Results run={run} performance={performance} pro={pro} onAbandon={abandon} busy={busy} />
        ) : null}

        {tab === 'trace' ? (
          <Trace
            trace={trace}
            offset={traceOffset}
            onPage={loadTrace}
            finalised={run?.results_are_final ?? false}
          />
        ) : null}

        {tab === 'positions' ? (
          <Positions
            positions={positions}
            total={positionTotal}
            events={events}
            onInspect={loadEvents}
            pro={pro}
            finalised={run?.results_are_final ?? false}
          />
        ) : null}
      </div>
    </section>
  );
}

function BeginnerNotes() {
  return (
    <aside className="backtest__beginner" aria-label="Nasıl okunur">
      <h3>Bu ekran ne yapar?</h3>
      <ul>
        <li>Geçmiş mum verileri üzerinde sabit kurallar adım adım çalıştırılır.</li>
        <li>Kararları yapay zekâ değil, önceden yazılmış kurallar üretir.</li>
        <li>Alım ve satımlar simülasyondur; gerçek bir emir oluşmaz.</li>
        <li>
          Geçmiş sonuçlar gelecekteki getiriyi <strong>garanti etmez</strong>.
        </li>
        <li>
          Sözleşme bilgileri (çarpan, tik, teminat) doğrulanmadan para hesabı yapılamaz; bu yüzden
          test reddedilebilir.
        </li>
        <li>Komisyon bilinmiyorsa net sonuç sıfır sayılmaz, hiç gösterilmez.</li>
      </ul>
    </aside>
  );
}

function StrategyCard({ strategy, pro }: { strategy: StrategyDto; pro: boolean }) {
  return (
    <section className="backtest__strategy" aria-labelledby="backtest-strategy-heading">
      <h3 id="backtest-strategy-heading">Strateji</h3>
      <p>{strategy.summary}</p>
      <dl>
        <dt>Isınma</dt>
        <dd>{strategy.warm_up_bars} mum</dd>
        <dt>Yön</dt>
        <dd>{strategy.directions.join(', ')}</dd>
        <dt>Pozisyon politikası</dt>
        <dd>{strategy.exposure}</dd>
        <dt>Stop</dt>
        <dd>{strategy.stop_model}</dd>
        <dt>Hedef</dt>
        <dd>{strategy.target_model}</dd>
        <dt>Giriş zamanlaması</dt>
        <dd>{strategy.entry_timing}</dd>
        {pro ? (
          <>
            <dt>Kimlik</dt>
            <dd>
              {strategy.identifier} v{strategy.version}
            </dd>
          </>
        ) : null}
      </dl>
      <p className="backtest__hint">
        Parametreler kod içinde sabittir ve bu ekrandan değiştirilemez.
      </p>
      <ul className="backtest__params">
        {strategy.parameters.map((item) => (
          <li key={item.name}>
            <span>{item.name}</span>
            <output>{item.value}</output>
            {item.configurable ? null : <span className="backtest__pin">sabit</span>}
          </li>
        ))}
      </ul>
    </section>
  );
}

function RunHistory({
  runs,
  onSelect,
  pro,
}: {
  runs: readonly BacktestRunSummaryDto[];
  onSelect: (runId: string) => void;
  pro: boolean;
}) {
  if (runs.length === 0) {
    return <p className="backtest__empty">Henüz hiç test çalıştırılmadı.</p>;
  }
  return (
    <table className="backtest__table">
      <caption>Çalıştırılan testler</caption>
      <thead>
        <tr>
          <th scope="col">Durum</th>
          <th scope="col">Sembol</th>
          <th scope="col">Değerlendirilen sınır</th>
          <th scope="col">Pozisyon</th>
          {pro ? <th scope="col">Yapılandırma</th> : null}
          <th scope="col"> </th>
        </tr>
      </thead>
      <tbody>
        {runs.map((item) => (
          <tr key={item.run_id}>
            <td>
              <span className={`backtest__status backtest__status--${item.status.toLowerCase()}`}>
                {STATUS_LABEL[item.status] ?? item.status}
              </span>
            </td>
            <td>{item.symbol}</td>
            <td>{item.boundaries_evaluated}</td>
            <td>{item.position_count}</td>
            {pro ? (
              <td>
                <code>{item.configuration}</code>
              </td>
            ) : null}
            <td>
              <button type="button" onClick={() => onSelect(item.run_id)}>
                Aç
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Results({
  run,
  performance,
  pro,
  onAbandon,
  busy,
}: {
  run: BacktestRunDto | null;
  performance: BacktestPerformanceDto | null;
  pro: boolean;
  onAbandon: () => void;
  busy: boolean;
}) {
  if (run === null) {
    return <p className="backtest__empty">Görüntülemek için geçmişten bir test seçin.</p>;
  }

  return (
    <div className="backtest__results">
      <header>
        <span className={`backtest__status backtest__status--${run.status.toLowerCase()}`}>
          {STATUS_LABEL[run.status] ?? run.status}
        </span>
        <p>{run.provenance}</p>
      </header>

      {run.status === 'FAILED' ? (
        <div className="backtest__banner backtest__banner--failed" role="status">
          <strong>Bu koşu tamamlanmadı.</strong>
          <p>{run.failure_reason}</p>
          {pro && run.failure_code ? <code>{run.failure_code}</code> : null}
          <p>Performans raporu yoktur — başarısız bir koşunun metriği yoktur, sıfır değildir.</p>
        </div>
      ) : null}

      {run.status === 'PENDING' || run.status === 'RUNNING' ? (
        <div className="backtest__banner backtest__banner--pending" role="status">
          <strong>Bu koşu yarıda kalmış olabilir.</strong>
          <p>
            Yapılandırma ve sayaçlar görünür, ancak <strong>sonuç yoktur</strong>. Kesintiye uğramış
            bir koşuyu sonlandırıp yeni bir koşu açabilirsiniz.
          </p>
          <button type="button" onClick={onAbandon} disabled={busy}>
            Koşuyu sonlandır
          </button>
        </div>
      ) : null}

      <dl className="backtest__totals">
        <dt>Değerlendirilen sınır</dt>
        <dd>{run.totals.boundaries_evaluated}</dd>
        <dt>Karar kaydı</dt>
        <dd>{run.totals.decision_count}</dd>
        <dt>Üretilen pozisyon</dt>
        <dd>{run.totals.position_count}</dd>
        <dt>Pencere</dt>
        <dd>
          {formatTimestamp(run.configuration.interval_start)} —{' '}
          {formatTimestamp(run.configuration.interval_end)}
        </dd>
        <dt>Strateji</dt>
        <dd>
          {run.configuration.strategy_id} v{run.configuration.strategy_version}
        </dd>
        {pro ? (
          <>
            <dt>Veri seti</dt>
            <dd>
              <code>{run.configuration.dataset_id}</code>
            </dd>
            <dt>Yapılandırma izi</dt>
            <dd>
              <code>{run.configuration_fingerprint}</code>
            </dd>
            <dt>Sonuç izi</dt>
            <dd>
              <code>{run.totals.result_digest ?? 'yok'}</code>
            </dd>
          </>
        ) : null}
      </dl>

      {pro ? (
        <section aria-labelledby="backtest-assumptions">
          <h3 id="backtest-assumptions">Simülasyon varsayımları</h3>
          <ul className="backtest__kv">
            {Object.entries(run.configuration.simulation).map(([key, value]) => (
              <li key={key}>
                <span>{key}</span>
                <output>{value}</output>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {run.results_are_final && performance !== null ? (
        <PerformanceOverview dto={performance.performance} mode={pro ? 'PRO' : 'BEGINNER'} />
      ) : (
        <p className="backtest__empty">Bu koşu için kesinleşmiş bir performans raporu yok.</p>
      )}
    </div>
  );
}

function Trace({
  trace,
  offset,
  onPage,
  finalised,
}: {
  trace: BacktestTraceDto | null;
  offset: number;
  onPage: (offset: number) => void;
  finalised: boolean;
}) {
  if (!finalised) {
    return (
      <p className="backtest__empty">Karar izi yalnızca tamamlanmış bir koşu için gösterilir.</p>
    );
  }
  if (trace === null || trace.items.length === 0) {
    return <p className="backtest__empty">Karar kaydı yok.</p>;
  }
  return (
    <div>
      <p className="backtest__hint">
        {trace.total} karar kaydından {offset + 1}–{offset + trace.items.length} arası. Bir
        reddediliş işlem değildir; “sinyal yok” da bir emir değildir.
      </p>
      <table className="backtest__table">
        <caption>Karar izi</caption>
        <thead>
          <tr>
            <th scope="col">#</th>
            <th scope="col">Piyasa anı</th>
            <th scope="col">Sonuç</th>
            <th scope="col">Gerekçe</th>
            <th scope="col">Risk</th>
          </tr>
        </thead>
        <tbody>
          {trace.items.map((item) => (
            <tr key={item.sequence}>
              <td>{item.sequence}</td>
              <td>{formatTimestamp(item.as_of)}</td>
              <td>{OUTCOME_LABEL[item.outcome] ?? item.outcome}</td>
              <td>{item.reason}</td>
              <td>{item.risk_reason ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="backtest__pager">
        <button
          type="button"
          disabled={offset === 0}
          onClick={() => onPage(Math.max(0, offset - TRACE_PAGE))}
        >
          Önceki
        </button>
        <button
          type="button"
          disabled={offset + trace.items.length >= trace.total}
          onClick={() => onPage(offset + TRACE_PAGE)}
        >
          Sonraki
        </button>
      </div>
    </div>
  );
}

function Positions({
  positions,
  total,
  events,
  onInspect,
  pro,
  finalised,
}: {
  positions: readonly BacktestPositionDto[];
  total: number;
  events: BacktestEventListDto | null;
  onInspect: (positionId: string) => void;
  pro: boolean;
  finalised: boolean;
}) {
  if (!finalised) {
    return (
      <p className="backtest__empty">Pozisyonlar yalnızca tamamlanmış bir koşu için gösterilir.</p>
    );
  }
  if (positions.length === 0) {
    return (
      <p className="backtest__empty">
        Bu koşuda hiç pozisyon açılmadı. Bu bir hata değildir — kurallar sinyal üretmemiş veya risk
        motoru izin vermemiş olabilir.
      </p>
    );
  }
  return (
    <div>
      <p className="backtest__hint">{total} simüle pozisyon.</p>
      <table className="backtest__table">
        <caption>Simüle pozisyonlar</caption>
        <thead>
          <tr>
            <th scope="col">#</th>
            <th scope="col">Yön</th>
            <th scope="col">Durum</th>
            <th scope="col">Giriş</th>
            <th scope="col">Stop</th>
            <th scope="col">Gerçekleşen brüt</th>
            <th scope="col">Net</th>
            <th scope="col"> </th>
          </tr>
        </thead>
        <tbody>
          {positions.map((item) => (
            <tr key={item.position_id}>
              <td>{item.ordinal}</td>
              <td>{item.direction === 'LONG' ? 'Alış' : 'Satış'}</td>
              <td>{item.state}</td>
              <td>{item.entry_fill_price ?? 'dolmadı'}</td>
              <td>{item.stop}</td>
              <td>{item.realized_gross}</td>
              <td>{item.realized_net ?? 'komisyon modellenmedi'}</td>
              <td>
                <button type="button" onClick={() => onInspect(item.position_id)}>
                  Defteri aç
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {events !== null ? (
        <section aria-labelledby="backtest-ledger">
          <h3 id="backtest-ledger">İşlem defteri</h3>
          <p className="backtest__hint">
            {events.total} kayıttan ilk {events.items.length} tanesi.
          </p>
          <ol className="backtest__ledger">
            {events.items.map((item) => (
              <li key={item.sequence}>
                <span>{item.type}</span>
                <time>{item.market_time === null ? '—' : formatTimestamp(item.market_time)}</time>
                {pro ? <code>{JSON.stringify(item.data)}</code> : null}
              </li>
            ))}
          </ol>
        </section>
      ) : null}
    </div>
  );
}
