import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { ApiError } from '../api/client';
import { getLiveSession, listLiveSessions, type LiveSessionListDto } from '../api/live';
import {
  cancelShadowRun,
  createShadowRun,
  getShadowCapability,
  getShadowRun,
  listShadowRuns,
  readShadowJournal,
  readShadowOutcomes,
  type CreateShadowPayload,
  type ShadowCapabilityDto,
  type ShadowEntryDto,
  type ShadowOutcomeDto,
  type ShadowRunDto,
} from '../api/shadow';
import {
  COMPLETENESS_LABEL,
  DEVELOPMENT_LABEL,
  END_REASON_LABEL,
  FINANCIAL_LABEL,
  JOURNAL_PAGE,
  LEVEL_LABEL,
  OUTCOME_LABEL,
  OUTCOME_PAGE,
  MAX_HELD_RUNS,
  RUN_PAGE,
  decisionsOf,
  describeDevelopment,
  describeEntry,
  emptyJournal,
  heldSummary,
  isSuperseded,
  labelOf,
  newAttemptKey,
  outcomeCounts,
  supersededBoundaries,
  whyNoDevelopment,
  withPage,
  withRunPage,
  type HeldJournal,
} from '../domain/shadow';
import { formatTimestamp } from '../format/display';
import type { ExperienceMode } from './AnalysisWorkspace';
import '../components/Live.css';
import '../components/Shadow.css';

/**
 * The Shadow research workspace (Phase 14 Part 2A). Observation only.
 *
 * ## What this screen is
 *
 * A place to watch what a registered strategy *would have decided* while a
 * stored historical dataset plays through the live stream, and where price
 * went afterwards. It places no order, opens no paper position and shows no
 * profit - there is no profit to show, because nothing here was a trade.
 *
 * ## Nothing starts by itself
 *
 * Opening it reads the capability, the run history and the live sessions. It
 * creates no run; that is a button, and the button carries an attempt key so a
 * double-click or a retried request cannot make two.
 *
 * ## A late answer cannot land on the wrong run
 *
 * Choosing a run takes a new generation ticket and aborts the previous reads.
 * Every page, refresh and outcome response is applied only if its ticket is
 * still current - so a slow journal page for run A never appears under run B,
 * and an old page never overwrites a newer one.
 */

export interface ShadowScreenProps {
  readonly mode: ExperienceMode;
  readonly onBack: () => void;
}

type Tab = 'overview' | 'decisions' | 'evidence' | 'outcomes' | 'history';

const TABS: readonly { readonly id: Tab; readonly label: string }[] = [
  { id: 'overview', label: 'Genel bakış' },
  { id: 'decisions', label: 'Kararlar' },
  { id: 'evidence', label: 'Kanıt' },
  { id: 'outcomes', label: 'Sonraki gelişme' },
  { id: 'history', label: 'Geçmiş gözlemler' },
];

type RunningSession = LiveSessionListDto['items'][number];

interface OutcomesHeld {
  readonly runId: string;
  readonly ticket: number;
  readonly items: readonly ShadowOutcomeDto[];
  readonly total: number;
  readonly nextAfter: number | null;
}

function messageOf(cause: unknown, fallback: string): string {
  if (cause instanceof ApiError) return cause.message;
  return fallback;
}

function aborted(cause: unknown): boolean {
  return cause instanceof DOMException && cause.name === 'AbortError';
}

export function ShadowScreen({ mode, onBack }: ShadowScreenProps) {
  const [capability, setCapability] = useState<ShadowCapabilityDto | null>(null);
  const [runs, setRuns] = useState<readonly ShadowRunDto[]>([]);
  const [runTotal, setRunTotal] = useState(0);
  const [sessions, setSessions] = useState<readonly RunningSession[]>([]);
  const [run, setRun] = useState<ShadowRunDto | null>(null);
  const [journal, setJournal] = useState<HeldJournal | null>(null);
  const [outcomes, setOutcomes] = useState<OutcomesHeld | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>('overview');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The generation of the *selected run*. Every read tied to a run carries it.
  const generation = useRef(0);
  const reads = useRef<AbortController | null>(null);
  const runRef = useRef<ShadowRunDto | null>(null);
  runRef.current = run;

  const report = useCallback((cause: unknown, fallback: string) => {
    if (aborted(cause)) return;
    setError(messageOf(cause, fallback));
  }, []);

  const refreshLists = useCallback(async () => {
    try {
      const [found, history] = await Promise.all([
        getShadowCapability(),
        listShadowRuns(0, RUN_PAGE),
      ]);
      setCapability(found);
      setRuns(history.items);
      setRunTotal(history.total);
    } catch (cause) {
      // Disabled answers 503 with a readable reason; the capability says so.
      report(cause, 'Gölge modu bilgisi okunamadı.');
    }
    try {
      const live = await listLiveSessions();
      setSessions(live.items.filter((item) => item.lifecycle === 'RUNNING'));
    } catch {
      setSessions([]); // no live workspace here means no session to watch
    }
  }, [report]);

  useEffect(() => {
    void refreshLists();
    return () => reads.current?.abort();
  }, [refreshLists]);

  /** Choose a run: new ticket, abort the old reads, load its first page. */
  const openRun = useCallback(
    async (runId: string) => {
      reads.current?.abort();
      const controller = new AbortController();
      reads.current = controller;
      const ticket = ++generation.current;
      setError(null);
      setSelectedKey(null);
      setOutcomes(null);
      setNotice('Gözlem yükleniyor…');
      try {
        const [found, page] = await Promise.all([
          getShadowRun(runId, controller.signal),
          readShadowJournal(runId, 0, JOURNAL_PAGE, controller.signal),
        ]);
        if (ticket !== generation.current) return; // another run was chosen meanwhile
        setRun(found);
        setJournal(withPage(emptyJournal(runId, ticket), page, runId, ticket));
        setNotice(`${found.instrument_label} gözlemi açıldı.`);
      } catch (cause) {
        if (ticket === generation.current) {
          report(cause, 'Gözlem açılamadı.');
          setNotice('');
        }
      }
    },
    [report],
  );

  const loadOlderRuns = useCallback(async () => {
    try {
      const page = await listShadowRuns(runs.length, RUN_PAGE);
      setRuns((held) => withRunPage(held, page.items));
      setRunTotal(page.total);
    } catch (cause) {
      report(cause, 'Daha eski gözlemler okunamadı.');
    }
  }, [runs.length, report]);

  const loadMore = useCallback(async () => {
    const held = journal;
    if (held === null || held.nextAfter === null) return;
    const { runId, ticket, nextAfter } = held;
    try {
      const page = await readShadowJournal(runId, nextAfter, JOURNAL_PAGE, reads.current?.signal);
      setJournal((current) =>
        current === null ? current : withPage(current, page, runId, ticket),
      );
    } catch (cause) {
      if (ticket === generation.current) report(cause, 'Sonraki sayfa okunamadı.');
    }
  }, [journal, report]);

  const loadOutcomes = useCallback(
    async (after: number) => {
      const current = runRef.current;
      if (current === null) return;
      const ticket = generation.current;
      const runId = current.run_id;
      try {
        const page = await readShadowOutcomes(runId, after, OUTCOME_PAGE, reads.current?.signal);
        if (ticket !== generation.current || page.run_id !== runId) return;
        setOutcomes((held) => {
          const base =
            held !== null && held.runId === runId && held.ticket === ticket && after > 0
              ? held.items
              : [];
          const known = new Set(base.map((item) => item.outcome_key));
          return {
            runId,
            ticket,
            items: [...base, ...page.items.filter((item) => !known.has(item.outcome_key))],
            total: page.total,
            nextAfter: page.items.length === 0 ? null : page.next_after,
          };
        });
      } catch (cause) {
        if (ticket === generation.current) report(cause, 'Sonraki gelişmeler okunamadı.');
      }
    },
    [report],
  );

  useEffect(() => {
    if (tab !== 'outcomes' || run === null) return;
    if (outcomes !== null && outcomes.runId === run.run_id) return;
    void loadOutcomes(0);
  }, [tab, run, outcomes, loadOutcomes]);

  const cancel = useCallback(async () => {
    const current = runRef.current;
    if (current === null) return;
    const ticket = generation.current;
    setBusy(true);
    try {
      const ended = await cancelShadowRun(current.run_id);
      if (ticket !== generation.current) return;
      setRun(ended);
      setNotice('Gözlem durduruldu. Kaydedilen her şey yerinde duruyor.');
      void refreshLists();
    } catch (cause) {
      if (ticket === generation.current) report(cause, 'Gözlem durdurulamadı.');
    } finally {
      setBusy(false);
    }
  }, [refreshLists, report]);

  const create = useCallback(
    async (payload: CreateShadowPayload): Promise<boolean> => {
      setBusy(true);
      setError(null);
      try {
        const created = await createShadowRun(payload);
        setNotice('Gölge gözlemi başlatıldı. Emir verilmez, pozisyon açılmaz.');
        await refreshLists();
        await openRun(created.run_id);
        setTab('overview');
        return true;
      } catch (cause) {
        report(cause, 'Gözlem başlatılamadı.');
        return false;
      } finally {
        setBusy(false);
      }
    },
    [openRun, refreshLists, report],
  );

  const closeRun = useCallback(() => {
    reads.current?.abort();
    generation.current += 1;
    setRun(null);
    setJournal(null);
    setOutcomes(null);
    setSelectedKey(null);
    setTab('overview');
    void refreshLists();
  }, [refreshLists]);

  const disabled = capability !== null && !capability.available;
  const selected =
    journal?.entries.find(
      (entry) => entry.decision_key === selectedKey && entry.kind === 'DECISION',
    ) ?? null;

  return (
    <div className="live-screen shadow-screen">
      <ShadowBanner />

      <div className="live-screen__controls">
        <button type="button" onClick={onBack}>
          Panele dön
        </button>
        {run !== null && (
          <button type="button" onClick={closeRun}>
            Gözlem listesine dön
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

      {capability !== null && <CapabilityFacts capability={capability} />}

      {run === null || journal === null ? (
        !disabled && (
          <>
            <RunHistory
              runs={runs}
              total={runTotal}
              onOpen={(id) => void openRun(id)}
              onMore={() => void loadOlderRuns()}
              isPro={mode === 'PRO'}
            />
            <CreateForm capability={capability} sessions={sessions} busy={busy} onCreate={create} />
          </>
        )
      ) : (
        <Workspace
          mode={mode}
          run={run}
          journal={journal}
          outcomes={outcomes !== null && outcomes.runId === run.run_id ? outcomes : null}
          runs={runs}
          runTotal={runTotal}
          selected={selected}
          tab={tab}
          busy={busy}
          onTab={setTab}
          onSelect={(key) => {
            setSelectedKey(key);
            setTab('evidence');
          }}
          onLoadMore={() => void loadMore()}
          onMoreOutcomes={() => {
            if (outcomes?.nextAfter != null) void loadOutcomes(outcomes.nextAfter);
          }}
          onRefresh={() => void openRun(run.run_id)}
          onCancel={() => void cancel()}
          onOpen={(id) => void openRun(id)}
          onMoreRuns={() => void loadOlderRuns()}
        />
      )}
    </div>
  );
}

// ----------------------------------------------------------------------
// Always-visible truth
// ----------------------------------------------------------------------

function ShadowBanner() {
  return (
    <section className="live-screen__banner" aria-labelledby="shadow-banner-title">
      <p id="shadow-banner-title" className="live-screen__banner-title">
        GÖLGE MODU — YALNIZCA GÖZLEM
      </p>
      <p>
        Kayıtlı bir stratejinin ne karar <em>vereceğini</em> kaydeder. Gerçek emir verilmez, kâğıt
        pozisyon açılmaz. Veri, saklanan geçmiş verinin simüle akışıdır; güncel piyasa fiyatı
        değildir.
      </p>
    </section>
  );
}

function BeginnerGuide() {
  return (
    <section className="shadow-guide" aria-labelledby="shadow-guide-title">
      <h3 id="shadow-guide-title">Gölge modu nedir?</h3>
      <ul>
        <li>Gölge modu, bir stratejinin varsayımsal kararlarını izler ve kaydeder.</li>
        <li>Gerçek emir verilmez. Kâğıt işlem pozisyonu da kendiliğinden açılmaz.</li>
        <li>Akış, geçmiş verinin simüle edilmiş oynatımıdır; canlı borsa verisi değildir.</li>
        <li>“Giriş niyeti” bir işlem değildir: kural girmek isterdi, ama hiçbir emir dolmadı.</li>
        <li>
          Fiyatın hedef seviyesine dokunması kâr demek değildir; stopa dokunması da zarar demek
          değildir. Kimse o piyasada değildi.
        </li>
        <li>
          Veride boşluk varsa, sonrasında ne olduğu “belirlenemedi” olarak yazılır; tahmin edilmez.
        </li>
        <li>
          Doğrulanmış vadeli sözleşme bilgisi olmadan hiçbir miktar ya da risk tutarı finansal
          olarak onaylanamaz.
        </li>
      </ul>
    </section>
  );
}

function CapabilityFacts({ capability }: { readonly capability: ShadowCapabilityDto }) {
  return (
    <ul className="live-chips" aria-label="Gölge modunun niteliği">
      <li className="live-chip live-chip--simulated">Kaynak: SİMÜLE GEÇMİŞ AKIŞ</li>
      <li className="live-chip live-chip--historical">Piyasa güncelliği: GEÇMİŞ VERİ</li>
      <li className="live-chip">Emir yürütme: DEVRE DIŞI</li>
      <li className="live-chip">
        Finansal onay:{' '}
        {capability.financial_metadata_available
          ? 'doğrulanmış sözleşme bilgisiyle mümkün'
          : 'KULLANILAMAZ — doğrulanmış sözleşme bilgisi yok'}
      </li>
      {!capability.available && (
        <li className="live-chip" role="note">
          Durum: KAPALI — {capability.reason}
        </li>
      )}
    </ul>
  );
}

// ----------------------------------------------------------------------
// Choosing and creating a run
// ----------------------------------------------------------------------

function RunHistory({
  runs,
  total,
  onOpen,
  onMore,
  isPro,
}: {
  readonly runs: readonly ShadowRunDto[];
  readonly total: number;
  readonly onOpen: (runId: string) => void;
  readonly onMore: () => void;
  readonly isPro: boolean;
}) {
  const capped = runs.length >= MAX_HELD_RUNS && runs.length < total;
  return (
    <section className="shadow-history" aria-labelledby="shadow-history-title">
      <h3 id="shadow-history-title">Geçmiş gölge gözlemleri</h3>
      {runs.length === 0 ? (
        <p>Henüz gölge gözlemi yok.</p>
      ) : (
        <>
          <p>
            {total} gözlemin en yeni {runs.length} tanesi gösteriliyor.
            {capped && ' Ekran sınırına ulaşıldı; daha eski gözlemler sunucuda eksiksiz duruyor.'}
          </p>
          <ul className="shadow-history__list">
            {runs.map((item) => (
              <li key={item.run_id}>
                <button type="button" onClick={() => onOpen(item.run_id)}>
                  {item.instrument_label} · {item.strategy_id} {item.strategy_version} ·{' '}
                  {labelOf(COMPLETENESS_LABEL, item.completeness)}
                </button>
                <span className="shadow-muted">
                  {' '}
                  başladı <time dateTime={item.started_at}>{formatTimestamp(item.started_at)}</time>
                </span>
                {isPro && <span className="shadow-code"> · {item.run_id}</span>}
              </li>
            ))}
          </ul>
          {runs.length < total && !capped && (
            <button type="button" onClick={onMore}>
              Daha eski gözlemleri yükle
            </button>
          )}
        </>
      )}
    </section>
  );
}

type Tf = CreateShadowPayload['driver'];
const ALL_TIMEFRAMES: readonly Tf[] = ['5M', '15M', '1H', '1D'];

function CreateForm({
  capability,
  sessions,
  busy,
  onCreate,
}: {
  readonly capability: ShadowCapabilityDto | null;
  readonly sessions: readonly RunningSession[];
  readonly busy: boolean;
  readonly onCreate: (payload: CreateShadowPayload) => Promise<boolean>;
}) {
  const formId = useId();
  const [sessionId, setSessionId] = useState('');
  const [offered, setOffered] = useState<readonly Tf[]>([]);
  const [driver, setDriver] = useState<Tf | ''>('');
  const [strategy, setStrategy] = useState('');
  const [withAnalysis, setWithAnalysis] = useState(false);
  // One key per intended run: a retry or a double-click reuses it, so the
  // server answers with the run it already made rather than a second one.
  const attempt = useRef(newAttemptKey());

  const choices = (capability?.strategies ?? []).flatMap((item) =>
    item.versions.map((version) => `${item.strategy_id}@${version}`),
  );

  useEffect(() => {
    if (sessionId === '') {
      setOffered([]);
      return;
    }
    const controller = new AbortController();
    getLiveSession(sessionId, controller.signal)
      .then((session) => {
        const found = session.timeframes
          .map((item) => item.timeframe)
          .filter((value): value is Tf => (ALL_TIMEFRAMES as readonly string[]).includes(value));
        setOffered(found);
        setDriver((current) => (current !== '' && found.includes(current) ? current : ''));
      })
      .catch(() => setOffered([]));
    return () => controller.abort();
  }, [sessionId]);

  const renew = () => {
    attempt.current = newAttemptKey(); // different rules are a different attempt
  };

  const ready = sessionId !== '' && driver !== '' && strategy !== '' && !busy;

  return (
    <form
      className="shadow-create"
      aria-labelledby={`${formId}-title`}
      onSubmit={(event) => {
        event.preventDefault();
        if (!ready) return;
        const [strategyId = '', version = ''] = strategy.split('@');
        void onCreate({
          session_id: sessionId,
          strategy_id: strategyId,
          strategy_version: version,
          driver,
          timeframes: [driver],
          analysis_evidence: withAnalysis,
          attempt_key: attempt.current,
        }).then((created) => {
          if (created) renew();
        });
      }}
    >
      <h3 id={`${formId}-title`}>Yeni gölge gözlemi</h3>
      {sessions.length === 0 ? (
        <p role="note">
          İzlenecek çalışan bir canlı oturum yok. Önce Canlı Akış ekranından bir oturum başlatın.
        </p>
      ) : null}
      <label htmlFor={`${formId}-session`}>Canlı oturum</label>
      <select
        id={`${formId}-session`}
        value={sessionId}
        onChange={(event) => {
          setSessionId(event.target.value);
          renew();
        }}
      >
        <option value="">Seçin</option>
        {sessions.map((item) => (
          <option key={item.id} value={item.id}>
            {item.instrument_label} ({item.id})
          </option>
        ))}
      </select>

      <label htmlFor={`${formId}-strategy`}>Kayıtlı strateji ve sürüm</label>
      <select
        id={`${formId}-strategy`}
        value={strategy}
        onChange={(event) => {
          setStrategy(event.target.value);
          renew();
        }}
      >
        <option value="">Seçin</option>
        {choices.map((value) => (
          <option key={value} value={value}>
            {value.replace('@', ' sürüm ')}
          </option>
        ))}
      </select>

      <label htmlFor={`${formId}-driver`}>Karar zaman dilimi</label>
      <select
        id={`${formId}-driver`}
        value={driver}
        disabled={offered.length === 0}
        onChange={(event) => {
          setDriver(event.target.value as Tf | '');
          renew();
        }}
      >
        <option value="">{offered.length === 0 ? 'Önce oturum seçin' : 'Seçin'}</option>
        {offered.map((value) => (
          <option key={value} value={value}>
            {value}
          </option>
        ))}
      </select>

      <div className="shadow-create__check">
        <input
          id={`${formId}-analysis`}
          type="checkbox"
          checked={withAnalysis}
          onChange={(event) => {
            setWithAnalysis(event.target.checked);
            renew();
          }}
        />
        <label htmlFor={`${formId}-analysis`}>
          Giriş niyetinin yanında mevcut analizi (rejim, uygunluk, kurulum kalitesi) kaydet
        </label>
      </div>

      <p className="shadow-muted">
        Gözlem, oluşturulduktan sonraki ilk onaylı mumdan başlar; oturumun daha önce oynattığı
        geçmişe geriye dönük karar yazılmaz.
      </p>
      <button type="submit" disabled={!ready}>
        Gölge gözlemini başlat
      </button>
    </form>
  );
}

// ----------------------------------------------------------------------
// The workspace
// ----------------------------------------------------------------------

interface WorkspaceProps {
  readonly mode: ExperienceMode;
  readonly run: ShadowRunDto;
  readonly journal: HeldJournal;
  readonly outcomes: OutcomesHeld | null;
  readonly runs: readonly ShadowRunDto[];
  readonly runTotal: number;
  readonly selected: ShadowEntryDto | null;
  readonly tab: Tab;
  readonly busy: boolean;
  readonly onTab: (tab: Tab) => void;
  readonly onSelect: (decisionKey: string) => void;
  readonly onLoadMore: () => void;
  readonly onMoreOutcomes: () => void;
  readonly onRefresh: () => void;
  readonly onCancel: () => void;
  readonly onOpen: (runId: string) => void;
  readonly onMoreRuns: () => void;
}

function Workspace(props: WorkspaceProps) {
  const { run, mode } = props;
  const isPro = mode === 'PRO';
  const tabsId = useId();
  const contested = supersededBoundaries(props.journal.entries);

  return (
    <div className="live-workspace">
      <section className="live-workspace__header" aria-labelledby="shadow-run-heading">
        <h3 id="shadow-run-heading">
          {run.instrument_label} · {run.strategy_id} sürüm {run.strategy_version}
        </h3>
        <dl className="live-status">
          <div>
            <dt>Gözlem durumu</dt>
            <dd data-completeness={run.completeness}>
              {labelOf(COMPLETENESS_LABEL, run.completeness)}
            </dd>
          </div>
          {run.end_reason !== null && (
            <div>
              <dt>Bitiş nedeni</dt>
              <dd>{labelOf(END_REASON_LABEL, run.end_reason)}</dd>
            </div>
          )}
          <div>
            <dt>Gözlem aralığı (piyasa zamanı)</dt>
            <dd>
              {formatTimestamp(run.first_boundary)} → {formatTimestamp(run.last_boundary)}
            </dd>
          </div>
        </dl>
        <div className="live-workspace__actions">
          <button type="button" onClick={props.onRefresh}>
            Yenile
          </button>
          <button
            type="button"
            onClick={props.onCancel}
            disabled={run.status !== 'OBSERVING' || props.busy}
          >
            GÖZLEMİ DURDUR
          </button>
        </div>
        {isPro && (
          <dl className="live-facts">
            <dt>Gözlem kimliği</dt>
            <dd>{run.run_id}</dd>
            <dt>Yapılandırma parmak izi</dt>
            <dd>{run.configuration}</dd>
            <dt>Köken</dt>
            <dd>{run.provenance}</dd>
            <dt>MarketCurrency</dt>
            <dd>{run.market_currency}</dd>
            <dt>Veri kümesi</dt>
            <dd>{run.source_id}</dd>
            <dt>Karar / gerekli zaman dilimleri</dt>
            <dd>
              {run.driver} / {run.required_timeframes.join(', ')}
            </dd>
            <dt>Başlangıç / bitiş (sunucu saati)</dt>
            <dd>
              {run.started_at} / {run.ended_at ?? '—'}
            </dd>
            {run.failure_code !== null && (
              <>
                <dt>Hata kodu</dt>
                <dd>{run.failure_code}</dd>
              </>
            )}
          </dl>
        )}
      </section>

      <div className="live-tabs" role="tablist" aria-label="Gölge gözlemi bölümleri">
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
        {props.tab === 'overview' && <Overview run={run} journal={props.journal} />}
        {props.tab === 'decisions' && (
          <Decisions
            run={run}
            journal={props.journal}
            contested={contested}
            isPro={isPro}
            onSelect={props.onSelect}
            onLoadMore={props.onLoadMore}
          />
        )}
        {props.tab === 'evidence' && (
          <Evidence
            run={run}
            entry={props.selected}
            superseded={props.selected !== null && isSuperseded(props.selected, contested)}
            isPro={isPro}
          />
        )}
        {props.tab === 'outcomes' && (
          <Outcomes held={props.outcomes} isPro={isPro} onMore={props.onMoreOutcomes} />
        )}
        {props.tab === 'history' && (
          <RunHistory
            runs={props.runs}
            total={props.runTotal}
            onOpen={props.onOpen}
            onMore={props.onMoreRuns}
            isPro={isPro}
          />
        )}
      </section>
    </div>
  );
}

function Overview({ run, journal }: { readonly run: ShadowRunDto; readonly journal: HeldJournal }) {
  const counts = outcomeCounts(journal.entries);
  const decisions = decisionsOf(journal.entries);
  return (
    <div className="live-overview">
      <p>
        {run.status === 'OBSERVING'
          ? 'Gözlem sürüyor; toplam sayılar gözlem bittiğinde kesinleşir.'
          : `Bu gözlem ${run.observations} onaylı sınırı işledi.`}{' '}
        Aşağıdaki sayılar kural <strong>cevaplarının</strong> sayısıdır; işlem sayısı değildir.
        Hiçbiri işlem olmadığı için kazanma oranı ya da kâr gösterilmez.
      </p>
      <p className="shadow-muted">{heldSummary(journal)}</p>
      {decisions.length === 0 ? (
        <p role="note">
          {run.status === 'OBSERVING'
            ? 'Henüz karar yok: gözlem, bir sonraki onaylı mumu bekliyor.'
            : 'Bu gözlem hiçbir karar kaydetmedi. Boş bir kayıt, uydurulmuş kararlarla doldurulmaz.'}
        </p>
      ) : (
        <table className="shadow-table">
          <caption>Yüklenen kararların dağılımı</caption>
          <thead>
            <tr>
              <th scope="col">Karar</th>
              <th scope="col">Adet</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(counts).map(([outcome, count]) => (
              <tr key={outcome}>
                <th scope="row">{labelOf(OUTCOME_LABEL, outcome)}</th>
                <td>{count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Decisions({
  run,
  journal,
  contested,
  isPro,
  onSelect,
  onLoadMore,
}: {
  readonly run: ShadowRunDto;
  readonly journal: HeldJournal;
  readonly contested: readonly string[];
  readonly isPro: boolean;
  readonly onSelect: (key: string) => void;
  readonly onLoadMore: () => void;
}) {
  if (journal.entries.length === 0) {
    return <p role="note">Günlükte henüz kayıt yok.</p>;
  }
  return (
    <div className="live-timeline">
      <p className="shadow-muted">{heldSummary(journal)}</p>
      <ol className="shadow-journal">
        {journal.entries.map((entry) => {
          const superseded = isSuperseded(entry, contested);
          return (
            <li
              key={entry.sequence}
              className={entry.kind === 'OPERATIONAL' ? 'shadow-journal__operational' : undefined}
            >
              <span className="shadow-journal__when">
                {entry.market_boundary !== null ? (
                  <time dateTime={entry.market_boundary}>
                    {formatTimestamp(entry.market_boundary)}
                  </time>
                ) : (
                  'işlem kaydı'
                )}
              </span>{' '}
              <strong>{describeEntry(entry)}</strong>
              {superseded && <span className="shadow-flag"> · KANITI DÜZELTİLDİ</span>}
              {entry.kind === 'DECISION' && (
                <>
                  {' '}
                  <span className="shadow-muted">
                    {labelOf(FINANCIAL_LABEL, entry.financial_state)}
                  </span>
                  {entry.outcome === 'ENTRY_INTENT' && (
                    <span className="shadow-muted">
                      {' '}
                      ·{' '}
                      {entry.development !== null
                        ? describeDevelopment(entry.development)
                        : whyNoDevelopment(entry, run)}
                    </span>
                  )}{' '}
                  <button type="button" onClick={() => onSelect(entry.decision_key)}>
                    Kanıtı göster
                  </button>
                </>
              )}
              {entry.kind === 'OPERATIONAL' && entry.reason !== '' && (
                <span className="shadow-muted"> — {entry.reason}</span>
              )}
              {isPro && (
                <span className="shadow-code">
                  {' '}
                  #{entry.sequence} · {entry.decision_key.slice(0, 12)}
                </span>
              )}
            </li>
          );
        })}
      </ol>
      {journal.nextAfter !== null && (
        <button type="button" onClick={onLoadMore}>
          Sonraki {JOURNAL_PAGE} kaydı yükle
        </button>
      )}
    </div>
  );
}

function Evidence({
  run,
  entry,
  superseded,
  isPro,
}: {
  readonly run: ShadowRunDto;
  readonly entry: ShadowEntryDto | null;
  readonly superseded: boolean;
  readonly isPro: boolean;
}) {
  if (entry === null) {
    return <p role="note">Kanıtını görmek için Kararlar sekmesinden bir karar seçin.</p>;
  }
  const evidence = entry.evidence;
  return (
    <div className="live-analysis">
      <h4>
        {describeEntry(entry)} —{' '}
        {entry.market_boundary !== null ? formatTimestamp(entry.market_boundary) : '—'}
      </h4>
      {superseded && (
        <p className="live-workspace__stale" role="note">
          Bu kararın okuduğu bir mum daha sonra çelişen bir düzeltmeyle karşılaştı. Karar
          yayımlandığı gibi duruyor; değiştirilmedi.
        </p>
      )}
      <p>{entry.reason}</p>
      <dl className="live-facts">
        <dt>Finansal durum</dt>
        <dd>{labelOf(FINANCIAL_LABEL, entry.financial_state)}</dd>
        {entry.risk_reason !== null && (
          <>
            <dt>Risk motoru</dt>
            <dd>{entry.risk_reason}</dd>
          </>
        )}
        {entry.entry !== null && (
          <>
            <dt>Önerilen giriş / stop</dt>
            <dd>
              {entry.entry.intended_entry} / {entry.entry.stop}
            </dd>
            <dt>Hedefler</dt>
            <dd>{entry.entry.targets.map(([price]) => price).join(', ')}</dd>
            <dt>Onaylanan miktar</dt>
            <dd>
              {entry.entry.approved_quantity === null
                ? 'Onaylanmadı'
                : `${entry.entry.approved_quantity} (pozisyon ya da emir değil)`}
            </dd>
          </>
        )}
        <dt>Sonraki gelişme</dt>
        <dd>
          {entry.development !== null
            ? describeDevelopment(entry.development)
            : whyNoDevelopment(entry, run)}
        </dd>
      </dl>
      {evidence !== null && (
        <>
          <h5>Kullanılan zaman dilimleri</h5>
          <p>{evidence.included.length > 0 ? evidence.included.join(', ') : 'Hiçbiri'}</p>
          {evidence.excluded.length > 0 && (
            <>
              <h5>Dışarıda kalanlar ve nedenleri</h5>
              <ul>
                {evidence.excluded.map((item) => (
                  <li key={item.timeframe}>
                    {item.timeframe}: {item.reasons.join('; ') || 'neden bildirilmedi'}
                  </li>
                ))}
              </ul>
            </>
          )}
          {(evidence.regime !== null || evidence.setup_quality !== null) && (
            <p>
              Rejim: {evidence.regime ?? '—'} · Uygunluk: {evidence.suitability ?? '—'} · Kurulum
              kalitesi: {evidence.setup_quality ?? '—'}
            </p>
          )}
          {isPro && (
            <dl className="live-facts">
              <dt>Bağlantı</dt>
              <dd>{evidence.connection}</dd>
              <dt>Mevcut bar</dt>
              <dd>{evidence.bars_available}</dd>
              {evidence.readings.map((reading) => (
                <FragmentReading key={reading.timeframe} reading={reading} />
              ))}
              <dt>Girdi parmak izi</dt>
              <dd>{entry.input_fingerprint ?? '—'}</dd>
              <dt>Karar kimliği / sıra</dt>
              <dd>
                {entry.decision_key} / #{entry.sequence}
              </dd>
              <dt>Kaydedilme (sunucu saati)</dt>
              <dd>{entry.recorded_at}</dd>
            </dl>
          )}
        </>
      )}
    </div>
  );
}

function FragmentReading({
  reading,
}: {
  readonly reading: NonNullable<ShadowEntryDto['evidence']>['readings'][number];
}) {
  return (
    <>
      <dt>{reading.timeframe} göstergeleri</dt>
      <dd>
        EMA hızlı {reading.ema_fast ?? '—'} · EMA yavaş {reading.ema_slow ?? '—'} · RSI{' '}
        {reading.rsi ?? '—'} · ATR {reading.atr ?? '—'} · ADX {reading.adx ?? '—'}
      </dd>
    </>
  );
}

function Outcomes({
  held,
  isPro,
  onMore,
}: {
  readonly held: OutcomesHeld | null;
  readonly isPro: boolean;
  readonly onMore: () => void;
}) {
  if (held === null) return <p role="status">Sonraki gelişmeler yükleniyor…</p>;
  if (held.items.length === 0) {
    return (
      <p role="note">
        Yayımlanmış bir sonraki gelişme yok. Yalnızca giriş niyetleri izlenir; sinyal yok, bekle ve
        reddedilen kararların izlenecek seviyesi yoktur.
      </p>
    );
  }
  return (
    <div className="live-timeline">
      <p>
        Bunlar fiyatın, önerilen seviyelere göre sonradan nereye gittiğinin gözlemleridir. Bir
        seviyeye dokunmak dolmuş emir, kâr ya da zarar değildir. {held.total} kaydın{' '}
        {held.items.length} tanesi gösteriliyor.
      </p>
      <ol className="shadow-journal">
        {held.items.map((item) => (
          <li key={item.outcome_key}>
            <span className="shadow-journal__when">
              Karar sınırı{' '}
              <time dateTime={item.decision_boundary}>
                {formatTimestamp(item.decision_boundary)}
              </time>
            </span>{' '}
            <strong>{labelOf(DEVELOPMENT_LABEL, item.development.state)}</strong>
            {item.development.state === 'OBSERVED' && (
              <span> — {labelOf(LEVEL_LABEL, item.development.event)}</span>
            )}
            {item.development.unresolved_reason !== null && (
              <span className="shadow-muted"> — {item.development.unresolved_reason}</span>
            )}
            {item.development.ambiguous && <span className="shadow-flag"> · SIRA BELİRSİZ</span>}
            <span className="shadow-muted">
              {' '}
              · {item.development.candles_observed} mum
              {item.development.event_at !== null &&
                `, olay ${formatTimestamp(item.development.event_at)}`}
            </span>
            {isPro && (
              <span className="shadow-code">
                {' '}
                · en iyi {item.development.best_price ?? '—'} / en kötü{' '}
                {item.development.worst_price ?? '—'} · {item.development.rules} · #{item.sequence}
              </span>
            )}
          </li>
        ))}
      </ol>
      {held.nextAfter !== null && held.items.length < held.total && (
        <button type="button" onClick={onMore}>
          Sonraki {OUTCOME_PAGE} gelişmeyi yükle
        </button>
      )}
    </div>
  );
}
