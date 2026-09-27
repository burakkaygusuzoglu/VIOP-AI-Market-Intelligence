import { useId, useState, type FormEvent, type KeyboardEvent, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  getCalendarStatus,
  getMetadataStatus,
  getReviewPage,
  getSourceCapabilities,
  REVIEW_PAGE_SIZE,
  SYMBOL_PATTERN,
  type CalendarDto,
  type CapabilitiesDto,
  type CapabilityStatus,
  type MetadataDto,
  type MetadataQuery,
  type ReviewPageDto,
  type SourceAuthority,
  type SourceCategory,
} from '../api/sources';
import type { ExperienceMode } from '../components/AnalysisDashboard';
import '../components/Sources.css';

/**
 * The source-verification and provider-capability workspace (Phase 15 Part 2B).
 *
 * Read-only by construction. The backend exposes no route that submits,
 * approves or verifies a fact - this application has no authentication, so a
 * reviewer name typed into a browser would prove nothing - and so this screen
 * has no such control either. Where a person would expect a "Verify" button,
 * it explains the local operator process instead.
 *
 * Beginner and Pro show the same statuses; Pro adds the codes, references,
 * timestamps and flags behind them. Every word of status arrives from the
 * server: nothing here decides that a source is available or a fact current.
 */

type Tab = 'overview' | 'sources' | 'metadata' | 'calendar' | 'reviews';

const TABS: readonly Tab[] = ['overview', 'sources', 'metadata', 'calendar', 'reviews'];

const TAB_LABEL: Record<Tab, string> = {
  overview: 'Genel bakış',
  sources: 'Kaynaklar',
  metadata: 'Sözleşme bilgileri',
  calendar: 'Seans takvimi',
  reviews: 'İnceleme geçmişi',
};

const CATEGORY_LABEL: Record<SourceCategory, string> = {
  MARKET_DATA: 'Piyasa verisi (fiyat)',
  CONTRACT_METADATA: 'Sözleşme bilgileri',
  OPEN_INTEREST: 'Açık pozisyon',
  NEWS: 'Haberler',
  MARKET_BREADTH: 'Piyasa genişliği',
  SESSION_CALENDAR: 'Seans takvimi',
};

const CATEGORY_HINT: Record<SourceCategory, string> = {
  MARKET_DATA: 'Fiyat ve hacim. Lisanslı bir veri sağlayıcısı gerektirir.',
  CONTRACT_METADATA:
    'Çarpan, fiyat adımı, vade. Resmî belgeden doğrulanmadıkça hiçbir hesap bunları kullanmaz.',
  OPEN_INTEREST: 'Açık sözleşme sayısı. İşlem hacminden türetilemez.',
  NEWS: 'Lisanslı haber akışı. Uydurma başlık gösterilmez.',
  MARKET_BREADTH: 'Yükselen/düşen hisse dağılımı. Tanımlı bir evren ve payda gerektirir.',
  SESSION_CALENDAR:
    'İşlem saatleri, tatiller, özel seanslar. Doğrulanmış kayıt yoksa oturum iddiası yapılmaz.',
};

const CAPABILITY_LABEL: Record<CapabilityStatus, string> = {
  NOT_CONFIGURED: 'Yapılandırılmadı',
  NOT_LICENSED: 'Lisans yok',
  UNAVAILABLE: 'Kullanılamıyor',
  AVAILABLE: 'Kullanılabilir',
  STALE: 'Bayat',
};

const VERDICT_LABEL: Record<string, string> = {
  USABLE: 'Doğrulanmış kayıt geçerli',
  MISSING: 'Doğrulanmış kayıt yok',
  WRONG_CONTRACT: 'Kayıt başka bir sözleşmeye ait',
  NOT_AUTHORITATIVE: 'Kaynak yetkili değil',
  NOT_IN_EFFECT: 'Bu tarihte yürürlükte kayıt yok',
  NOT_YET_KNOWN: 'O tarihte sistem bu kaydı henüz bilmiyordu',
  STALE: 'Doğrulama eskimiş; yeniden doğrulanmalı',
  EXPIRED: 'Sözleşmenin vadesi dolmuş',
  CONFLICTING: 'Kaynaklar çelişiyor — hiçbir değer seçilmedi',
};

const FIELD_LABEL: Record<MetadataDto['fields'][number]['name'], string> = {
  multiplier: 'Çarpan',
  tick_size: 'Fiyat adımı',
  tick_value: 'Adım değeri',
  expiry_date: 'Vade tarihi',
  initial_margin: 'Başlangıç teminatı',
  maintenance_margin: 'Sürdürme teminatı',
};

const FIELD_STATE_LABEL: Record<MetadataDto['fields'][number]['state'], string> = {
  VERIFIED: 'Doğrulandı',
  MISSING: 'Kayıtta belirtilmemiş',
  NOT_REVIEWABLE: 'Bu sürümde doğrulanamaz',
  UNAVAILABLE: 'Bilinmiyor',
};

const AUTHORITY_LABEL: Record<SourceAuthority, string> = {
  EXCHANGE_OFFICIAL: 'Borsanın resmî belgesi',
  LICENSED_PROVIDER: 'Lisanslı sağlayıcı',
  SECONDARY: 'İkincil kaynak (yetkili değil)',
  UNKNOWN: 'Bilinmeyen kaynak (yetkili değil)',
};

const RESULT_LABEL: Record<'APPROVED' | 'REJECTED' | 'REFUSED', string> = {
  APPROVED: 'Onaylandı',
  REJECTED: 'Reddedildi',
  REFUSED: 'Onay isteği sınırda geri çevrildi',
};

const CHECK_LABEL: Record<keyof MetadataDto['checks'], string> = {
  source_claims_value: 'Bir kaynak değer belirtiyor',
  operator_examined_evidence: 'Operatör belgeyi incelediğini beyan etti',
  source_authority_assessed: 'Kaynağın yetkisi değerlendirildi',
  applicable_to_contract: 'Kayıt bu sözleşmeye ait',
  applicable_at_market_time: 'Kayıt istenen piyasa zamanında yürürlükte',
  known_by_requested_time: 'Sistem kaydı o zamana kadar biliyordu',
  current: 'Doğrulama güncel',
  financial_use_enabled: 'Finansal hesaplarda kullanılabilir',
};

/** The most review entries this screen keeps in memory at once. */
const RETAINED_REVIEWS = 200;

function yesNo(value: boolean): string {
  return value ? 'Evet' : 'Hayır';
}

function when(iso: string | null): string {
  return iso ?? '—';
}

export interface SourcesScreenProps {
  readonly mode: ExperienceMode;
  readonly onBack: () => void;
}

export function SourcesScreen({ mode, onBack }: SourcesScreenProps) {
  const [tab, setTab] = useState<Tab>('overview');
  const pro = mode === 'PRO';
  const capabilities = useQuery({
    queryKey: ['source-capabilities'],
    queryFn: ({ signal }) => getSourceCapabilities(signal),
  });

  return (
    <div className="sources-screen">
      <section className="sources-banner" role="note" aria-label="Kaynak durumu uyarısı">
        <p className="sources-banner__title">VERİ KAYNAKLARI VE DOĞRULAMA — YALNIZCA OKUMA</p>
        <p>
          Bu kurulumda <strong>gerçek bir borsa veri sağlayıcısı bağlı değildir</strong>. Canlı
          borsa fiyatı gösterilmez. Doğrulanmış sözleşme bilgileri finansal hesaplarda{' '}
          <strong>kullanılmaz</strong>; risk motoru kendi kararını verir.
        </p>
      </section>

      <div className="sources-screen__row">
        <button type="button" onClick={onBack}>
          Panoya dön
        </button>
        <h2 className="sources-screen__heading">Veri kaynakları</h2>
      </div>

      <div className="sources-tabs" role="tablist" aria-label="Kaynak görünümleri">
        {TABS.map((name, index) => (
          <button
            key={name}
            type="button"
            role="tab"
            id={`sources-tab-${name}`}
            aria-selected={tab === name}
            aria-controls={`sources-panel-${name}`}
            tabIndex={tab === name ? 0 : -1}
            className={tab === name ? 'sources-tab sources-tab--active' : 'sources-tab'}
            onClick={() => setTab(name)}
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
              setTab(next);
              document.getElementById(`sources-tab-${next}`)?.focus();
            }}
          >
            {TAB_LABEL[name]}
          </button>
        ))}
      </div>

      <p className="visually-hidden" role="status" aria-live="polite">
        {capabilities.isLoading
          ? 'Kaynak durumu yükleniyor.'
          : capabilities.data
            ? `${TAB_LABEL[tab]} gösteriliyor. Gerçek veri sağlayıcısı bağlı değil.`
            : ''}
      </p>

      {capabilities.isError && (
        <p className="sources-error" role="alert">
          {capabilities.error instanceof Error
            ? capabilities.error.message
            : 'Kaynak durumu okunamadı.'}
        </p>
      )}

      <div
        id={`sources-panel-${tab}`}
        role="tabpanel"
        aria-labelledby={`sources-tab-${tab}`}
        tabIndex={-1}
        className="sources-panel"
      >
        {tab === 'overview' && <Overview dto={capabilities.data} pro={pro} />}
        {tab === 'sources' && <Categories dto={capabilities.data} pro={pro} />}
        {tab === 'metadata' && <Metadata pro={pro} />}
        {tab === 'calendar' && <Calendar pro={pro} />}
        {tab === 'reviews' && <Reviews pro={pro} />}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function Overview({ dto, pro }: { dto: CapabilitiesDto | undefined; pro: boolean }) {
  if (!dto) return <p className="sources-empty">Yükleniyor…</p>;
  const unavailable = dto.categories.filter((c) => c.status !== 'AVAILABLE');
  return (
    <div className="sources-grid">
      <section className="sources-card" aria-labelledby="sources-deployment">
        <h3 id="sources-deployment">Bu kurulum</h3>
        <dl className="sources-facts">
          <dt>Gerçek veri sağlayıcısı</dt>
          <dd>Bağlı değil</dd>
          <dt>Simüle geçmiş akış</dt>
          <dd>{dto.deployment.simulated_market_data ? 'Açık (borsa verisi değil)' : 'Kapalı'}</dd>
          <dt>Seans takvimi kaynağı</dt>
          <dd>{dto.deployment.calendar_source_composed ? 'Var' : 'Yok'}</dd>
          <dt>Finansal kullanım</dt>
          <dd>KAPALI</dd>
          <dt>Doğrulanmış sözleşme kaydı</dt>
          <dd>{dto.journal.records}</dd>
        </dl>
        {pro && (
          <p className="sources-code">
            market_data_provider={dto.deployment.market_data_provider} · verification_writes=
            {dto.deployment.verification_writes} · reviewer_identity=
            {dto.deployment.reviewer_identity}
          </p>
        )}
      </section>

      {!pro && (
        <section className="sources-card sources-explain" aria-labelledby="sources-why">
          <h3 id="sources-why">Neden önemli?</h3>
          <ul>
            <li>
              <strong>Fiyat verisi ile sözleşme bilgisi farklıdır.</strong> Bir fiyat akışı,
              sözleşmenin çarpanını veya fiyat adımını söylemez; bunlar borsanın resmî belgelerinden
              ayrıca doğrulanır.
            </li>
            <li>
              <strong>Yanlış çarpan, doğru görünen yanlış bir sonuç üretir.</strong> Bu yüzden
              doğrulanmamış bilgi hiçbir risk veya pozisyon hesabına girmez.
            </li>
            <li>
              <strong>Seans takvimi yoksa oturum iddiası yoktur.</strong> Sistem işlem saatlerini
              tahmin etmez; doğrulanmış kayıt yoksa &quot;bilinmiyor&quot; der.
            </li>
            <li>
              <strong>Canlı borsa fiyatı gösterilmez,</strong> çünkü lisanslı bir sağlayıcı bağlı
              değil. Canlı izleme ekranı yalnızca kaydedilmiş geçmiş veriyi oynatır.
            </li>
          </ul>
        </section>
      )}

      <section className="sources-card" aria-labelledby="sources-missing">
        <h3 id="sources-missing">Şu anda kullanılamayan kaynaklar</h3>
        <ul className="sources-list">
          {unavailable.map((c) => (
            <li key={c.category}>
              <span className="sources-list__name">{CATEGORY_LABEL[c.category]}</span>
              <span className={`sources-status sources-status--${c.status}`}>
                {CAPABILITY_LABEL[c.status]}
              </span>
              {pro && <code className="sources-code">{c.status}</code>}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function Categories({ dto, pro }: { dto: CapabilitiesDto | undefined; pro: boolean }) {
  if (!dto) return <p className="sources-empty">Yükleniyor…</p>;
  const flags = ['configured', 'licensed', 'connected', 'available', 'fresh', 'verified'] as const;
  const flagLabel: Record<(typeof flags)[number], string> = {
    configured: 'Yapılandırıldı',
    licensed: 'Lisanslı',
    connected: 'Bağlı',
    available: 'Veri geliyor',
    fresh: 'Güncel',
    verified: 'Doğrulanmış kayıt',
  };
  return (
    <div className="sources-table-wrap">
      <table className="sources-table">
        <caption>
          Her kategori ayrı ayrı değerlendirilir. &quot;Yapılandırıldı&quot;, &quot;lisanslı&quot;,
          &quot;bağlı&quot; ve &quot;güncel&quot; aynı şey değildir.
        </caption>
        <thead>
          <tr>
            <th scope="col">Kategori</th>
            <th scope="col">Durum</th>
            {pro &&
              flags.map((f) => (
                <th key={f} scope="col">
                  {flagLabel[f]}
                </th>
              ))}
            {pro && <th scope="col">Bu sürümde bağdaştırıcı</th>}
          </tr>
        </thead>
        <tbody>
          {dto.categories.map((c) => (
            <tr key={c.category}>
              <th scope="row">
                {CATEGORY_LABEL[c.category]}
                {!pro && <span className="sources-hint">{CATEGORY_HINT[c.category]}</span>}
              </th>
              <td>
                <span className={`sources-status sources-status--${c.status}`}>
                  {CAPABILITY_LABEL[c.status]}
                </span>
                {pro && <span className="sources-hint">{c.reason}</span>}
              </td>
              {pro && flags.map((f) => <td key={f}>{yesNo(c[f])}</td>)}
              {pro && <td>{yesNo(c.adapter_in_build)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------

function SymbolForm({
  label,
  onSubmit,
  extra,
}: {
  label: string;
  onSubmit: (symbol: string) => void;
  extra?: ReactNode;
}) {
  const id = useId();
  const [value, setValue] = useState('');
  const [error, setError] = useState<string | null>(null);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const symbol = value.trim();
    if (!SYMBOL_PATTERN.test(symbol)) {
      setError('Sembol yalnızca harf, rakam, nokta, alt çizgi ve tire içerebilir (en çok 32).');
      return;
    }
    setError(null);
    onSubmit(symbol);
  };
  return (
    <form className="sources-form" onSubmit={submit} aria-label={label}>
      <label htmlFor={id}>Sözleşme sembolü</label>
      <input id={id} value={value} onChange={(e) => setValue(e.target.value)} maxLength={32} />
      {extra}
      <button type="submit">Sorgula</button>
      {error && (
        <p className="sources-error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

function Metadata({ pro }: { pro: boolean }) {
  const [query, setQuery] = useState<MetadataQuery | null>(null);
  const [retrospective, setRetrospective] = useState(false);
  const [day, setDay] = useState('');
  const dayId = useId();
  const retroId = useId();
  const result = useQuery({
    queryKey: ['source-metadata', query],
    queryFn: ({ signal }) => getMetadataStatus(query as MetadataQuery, signal),
    enabled: query !== null,
  });
  return (
    <div className="sources-stack">
      <SymbolForm
        label="Sözleşme bilgisi sorgusu"
        onSubmit={(symbol) =>
          setQuery(
            day
              ? { symbol, appliesAt: `${day}T12:00:00Z`, retrospective }
              : { symbol, retrospective },
          )
        }
        extra={
          <>
            <label htmlFor={dayId}>Piyasa tarihi (UTC, isteğe bağlı)</label>
            <input id={dayId} type="date" value={day} onChange={(e) => setDay(e.target.value)} />
            <label className="sources-check" htmlFor={retroId}>
              <input
                id={retroId}
                type="checkbox"
                checked={retrospective}
                onChange={(e) => setRetrospective(e.target.checked)}
              />
              Geriye dönük bak (bugün bildiklerimizle)
            </label>
          </>
        }
      />
      {result.isError && (
        <p className="sources-error" role="alert">
          {result.error instanceof Error ? result.error.message : 'Sorgu tamamlanamadı.'}
        </p>
      )}
      {result.data && <MetadataResult dto={result.data} pro={pro} />}
      {!query && (
        <p className="sources-empty">
          Bir sözleşme sembolü girin. Sistem sembolden hiçbir değer türetmez; yalnızca doğrulama
          günlüğünde o sözleşme için yayımlanmış kayıtları gösterir.
        </p>
      )}
    </div>
  );
}

function MetadataResult({ dto, pro }: { dto: MetadataDto; pro: boolean }) {
  return (
    <section className="sources-card" aria-label={`${dto.symbol} sözleşme bilgileri`}>
      <h3>{dto.symbol}</h3>
      <p className={`sources-verdict sources-verdict--${dto.verdict}`}>
        {VERDICT_LABEL[dto.verdict] ?? dto.verdict}
        {pro && <code className="sources-code"> {dto.verdict}</code>}
      </p>
      <p className="sources-note">
        {dto.retrospective
          ? 'Geriye dönük görünüm: bugün bilinenlerle o dönem hakkında. O tarihte sistemin bunu bildiği anlamına gelmez.'
          : `Bilinen haliyle: ${when(dto.known_by)} itibarıyla sistemin elindeki kayıtlar.`}
      </p>
      <p className="sources-note">
        Finansal kullanım: KAPALI. Risk motoru kendi veto yetkisini korur.
      </p>
      {pro && <p className="sources-hint">{dto.reason}</p>}

      <div className="sources-table-wrap">
        <table className="sources-table">
          <thead>
            <tr>
              <th scope="col">Alan</th>
              <th scope="col">Durum</th>
              <th scope="col">Değer</th>
              {pro && <th scope="col">Kaynak</th>}
              {pro && <th scope="col">Doğrulama zamanı</th>}
            </tr>
          </thead>
          <tbody>
            {dto.fields.map((f) => (
              <tr key={f.name}>
                <th scope="row">{FIELD_LABEL[f.name]}</th>
                <td>{FIELD_STATE_LABEL[f.state]}</td>
                <td>{f.value ?? '—'}</td>
                {pro && <td>{f.source ?? '—'}</td>}
                {pro && <td>{when(f.verified_at)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {dto.conflicts.length > 0 && (
        <div className="sources-conflicts" role="note" aria-label="Kaynak çelişkileri">
          <h4>Çelişkiler</h4>
          <ul>
            {dto.conflicts.map((c) => (
              <li key={`${c.fact}-${c.chosen_record}-${c.other_record}`}>
                {c.fact}: {c.chosen_record} = {c.chosen_value} / {c.other_record} = {c.other_value}
                {pro && <span className="sources-hint"> — {c.resolution}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {pro && (
        <>
          <h4>Ayrı sorular</h4>
          <ul className="sources-checks">
            {(Object.keys(CHECK_LABEL) as (keyof MetadataDto['checks'])[]).map((key) => (
              <li key={key}>
                {CHECK_LABEL[key]}: <strong>{yesNo(dto.checks[key])}</strong>
              </li>
            ))}
          </ul>
          <h4>Kayıtlar ({dto.records.length})</h4>
          {dto.records.length === 0 ? (
            <p className="sources-empty">Bu sözleşme için yayımlanmış kayıt yok.</p>
          ) : (
            <ul className="sources-records">
              {dto.records.map((r) => (
                <li key={r.record_id}>
                  <code>{r.record_id}</code> · {AUTHORITY_LABEL[r.authority]} · {r.reference}
                  <br />
                  Yürürlük: {r.effective_from} → {r.effective_until ?? 'açık uçlu'} · Doğrulama:{' '}
                  {r.verified_at} · Sistemce bilinme: {when(r.known_at)}
                  {r.corrects && <> · Düzelttiği kayıt: {r.corrects}</>}
                  {dto.superseded.includes(r.record_id) && <> · (düzeltildi, kullanılmıyor)</>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------

function Calendar({ pro }: { pro: boolean }) {
  const [symbol, setSymbol] = useState<string | null>(null);
  const result = useQuery({
    queryKey: ['source-calendar', symbol],
    queryFn: ({ signal }) => getCalendarStatus(symbol as string, signal),
    enabled: symbol !== null,
  });
  return (
    <div className="sources-stack">
      {!pro && (
        <p className="sources-note">
          İşlem saatleri, tatiller ve özel seanslar borsa duyurularıyla değişir. Sistem bunları
          tahmin etmez; doğrulanmış bir takvim kaydı yoksa cevap &quot;bilinmiyor&quot;dur ve canlı
          izlemedeki veri boşlukları seans arası sayılmaz.
        </p>
      )}
      <SymbolForm label="Seans takvimi sorgusu" onSubmit={setSymbol} />
      {result.isError && (
        <p className="sources-error" role="alert">
          {result.error instanceof Error ? result.error.message : 'Sorgu tamamlanamadı.'}
        </p>
      )}
      {result.data && <CalendarResult dto={result.data} pro={pro} />}
    </div>
  );
}

const SESSION_LABEL: Record<CalendarDto['status'], string> = {
  IN_SESSION: 'Seans içinde',
  OUT_OF_SESSION: 'Seans dışında',
  UNAVAILABLE: 'Bilinmiyor — doğrulanmış takvim yok',
};

function CalendarResult({ dto, pro }: { dto: CalendarDto; pro: boolean }) {
  return (
    <section className="sources-card" aria-label={`${dto.symbol} seans durumu`}>
      <h3>{dto.symbol}</h3>
      <p className={`sources-verdict sources-verdict--${dto.status}`}>
        {SESSION_LABEL[dto.status]}
      </p>
      <p className="sources-note">
        Takvim kaynağı: {dto.calendar_source_composed ? 'var' : 'bu kurulumda yok'}
      </p>
      {pro && (
        <dl className="sources-facts">
          <dt>Kod</dt>
          <dd>{dto.status}</dd>
          <dt>An</dt>
          <dd>{dto.at}</dd>
          <dt>Gerekçe</dt>
          <dd>{dto.reason}</dd>
          <dt>Kaynak</dt>
          <dd>{dto.source ?? '—'}</dd>
        </dl>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------

function Reviews({ pro }: { pro: boolean }) {
  const [pages, setPages] = useState<number[]>([0]);
  return (
    <div className="sources-stack">
      <section className="sources-card sources-explain" aria-labelledby="sources-process">
        <h3 id="sources-process">Bir bilgi nasıl doğrulanır?</h3>
        <p>
          Bu uygulamada kimlik doğrulama yok; bu yüzden tarayıcıdan onay verilemez. Doğrulama,
          sunucuya erişimi olan operatörün yerel komutuyla yapılır: operatör resmî belgeyi açar,
          değeri, sözleşmeyi, yürürlük tarihini ve yayımlayanı kontrol eder ve bunu açıkça beyan
          eder. İnceleyen adı bir <strong>beyandır</strong>, kimlik kanıtı değildir.
        </p>
        <p>
          CSV ile yüklenen bilgi hiçbir zaman doğrulanmış sayılmaz; resmî görünen bir bağlantı da
          tek başına yetki vermez. Kayıtlar silinmez ve değiştirilmez; düzeltme yeni bir kayıttır.
        </p>
        {pro && (
          <pre className="sources-command">
            python -m app.operator.fact_review submit --id … --symbol … --fact MULTIPLIER --value …
            --reference … --authority EXCHANGE_OFFICIAL --effective-from … --submitted-by …{'\n'}
            python -m app.operator.fact_review decide --id … --reviewer … --outcome APPROVED
            --document-checked{'\n'}
            python -m app.operator.fact_review publish --record-id … --underlying … --name …
            --multiplier … --tick-size …
          </pre>
        )}
      </section>
      {pages.map((after, index) => (
        <ReviewPage
          key={after}
          after={after}
          pro={pro}
          last={index === pages.length - 1}
          canLoadMore={pages.length * REVIEW_PAGE_SIZE < RETAINED_REVIEWS}
          onMore={(next) => setPages((current) => [...current, next])}
        />
      ))}
    </div>
  );
}

function ReviewPage({
  after,
  pro,
  last,
  canLoadMore,
  onMore,
}: {
  after: number;
  pro: boolean;
  last: boolean;
  canLoadMore: boolean;
  onMore: (next: number) => void;
}) {
  const page = useQuery({
    queryKey: ['source-reviews', after],
    queryFn: ({ signal }) => getReviewPage(after, signal),
  });
  if (page.isError)
    return (
      <p className="sources-error" role="alert">
        {page.error instanceof Error ? page.error.message : 'İnceleme geçmişi okunamadı.'}
      </p>
    );
  if (!page.data) return <p className="sources-empty">Yükleniyor…</p>;
  const dto: ReviewPageDto = page.data;
  if (after === 0 && dto.items.length === 0)
    return <p className="sources-empty">Henüz hiçbir doğrulama kaydı yok.</p>;
  return (
    <>
      {after === 0 && <p className="sources-note">Toplam {dto.total} başvuru.</p>}
      <ol className="sources-reviews">
        {dto.items.map((item) => (
          <li key={item.submission_id}>
            <p>
              <strong>{item.symbol}</strong> · {item.fact} = {item.claimed_value ?? '—'} ·{' '}
              {AUTHORITY_LABEL[item.authority]}
            </p>
            <p>
              {item.decision
                ? `${RESULT_LABEL[item.decision.result]} — inceleyen (beyan): ${item.decision.reviewer}`
                : 'Karar bekliyor'}
              {item.decision?.refusal_code && (
                <>
                  {' '}
                  · <code>{item.decision.refusal_code}</code>
                </>
              )}
            </p>
            {pro && (
              <p className="sources-hint">
                #{item.sequence} · {item.submission_id} · kaynak: {item.reference} · köken:{' '}
                {item.origin} · yürürlük: {when(item.effective_from)} →{' '}
                {item.effective_until ?? 'açık uçlu'} · başvuru: {item.submitted_at} (beyan,{' '}
                {item.submitted_by}) · günlüğe yazıldı: {item.recorded_at}
                {item.decision && (
                  <>
                    {' '}
                    · karar: {item.decision.decided_at} · belge kontrol edildi:{' '}
                    {yesNo(item.decision.document_checked)} · {item.decision.reviewer_identity}
                  </>
                )}
              </p>
            )}
          </li>
        ))}
      </ol>
      {last && dto.next_after !== null && canLoadMore && (
        <button type="button" onClick={() => onMore(dto.next_after as number)}>
          Daha fazla göster
        </button>
      )}
      {last && dto.next_after !== null && !canLoadMore && (
        <p className="sources-note">
          Ekranda en çok {RETAINED_REVIEWS} kayıt tutulur; tamamı için operatör komutunun history
          çıktısını kullanın.
        </p>
      )}
    </>
  );
}
