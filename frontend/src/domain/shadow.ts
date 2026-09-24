import type {
  ShadowDevelopmentDto,
  ShadowEntryDto,
  ShadowJournalPageDto,
  ShadowRunDto,
} from '../api/shadow';

/**
 * What the Shadow research screen may say, and what it may hold (Phase 14 Part 2A).
 *
 * ## Words, not colours, and never money
 *
 * Every state has a sentence. A colour may accompany it; it never replaces it.
 * No function here produces a profit, a return, a win rate or a count of
 * "successful" signals: the backend does not know whether anybody could have
 * traded any of this, so the browser must not imply it did. A touched level is
 * described as a touched level.
 *
 * ## The browser computes nothing financial
 *
 * Prices arrive as exact decimal strings and are shown as written. Nothing is
 * parsed into a number, summed or compared - a rounding here would be a price
 * the rules never saw.
 *
 * ## Held entries are bounded, and say so
 *
 * The journal is paged from the server and the screen keeps at most
 * `MAX_HELD_ENTRIES`. When it has stopped loading, it says how many exist
 * rather than presenting what it holds as the whole record.
 */

export const JOURNAL_PAGE = 50;
export const OUTCOME_PAGE = 50;
export const MAX_HELD_ENTRIES = 500;
export const RUN_PAGE = 25;
/** Runs the history list holds. Past this it stops loading and says so. */
export const MAX_HELD_RUNS = 200;

export const OUTCOME_LABEL: Readonly<Record<string, string>> = {
  NO_SIGNAL: 'Sinyal yok',
  WAIT: 'Bekle',
  ENTRY_INTENT: 'Giriş niyeti (işlem değil)',
  EXIT_INTENT: 'Çıkış niyeti (işlem değil)',
  UNAVAILABLE: 'Kanıt yetersiz — karar verilmedi',
  REFUSED: 'Reddedildi',
};

export const FINANCIAL_LABEL: Readonly<Record<string, string>> = {
  NOT_APPLICABLE: 'Uygulanmaz — giriş önerilmedi',
  NOT_CONFIGURED: 'Hesap/risk ayarı verilmedi — boyutlandırma yapılmadı',
  METADATA_UNAVAILABLE: 'Doğrulanmış sözleşme bilgisi yok — finansal onay verilemez',
  APPROVED: 'Risk motoru bir miktar onayladı — yine de pozisyon ya da emir değil',
  REFUSED: 'Risk motoru reddetti',
  UNDETERMINED: 'Risk motoru kesin miktar belirleyemedi',
};

export const DEVELOPMENT_LABEL: Readonly<Record<string, string>> = {
  NOT_EVALUATED: 'İzlenmedi — önerilen seviye yok',
  PENDING: 'Bekleniyor — gözlem penceresi açık',
  OBSERVED: 'Gözlendi',
  UNAVAILABLE: 'Belirlenemedi',
  INVALIDATED: 'Geçersiz — kararın dayandığı kanıt düzeltildi',
};

export const LEVEL_LABEL: Readonly<Record<string, string>> = {
  NONE_REACHED: 'Pencere boyunca stop da hedef de görülmedi',
  STOP_LEVEL_TOUCHED: 'Fiyat stop seviyesine dokundu (dolmuş emir değil, zarar değil)',
  TARGET_LEVEL_TOUCHED: 'Fiyat hedef seviyesine dokundu (dolmuş emir değil, kâr değil)',
  BOTH_LEVELS_TOUCHED_SAME_BAR:
    'Aynı mum hem stopa hem hedefe dokundu — hangisinin önce olduğu bilinemez',
  NOT_OBSERVED: 'Sonraki onaylı mum görülmedi',
};

export const COMPLETENESS_LABEL: Readonly<Record<string, string>> = {
  OBSERVING: 'Gözlem sürüyor',
  COMPLETE: 'Tamamlandı — veri kümesinin sonuna kadar gözlendi',
  PARTIAL: 'Kısmi — iptal edildi, sınıra ulaştı ya da uygulama kapandı',
  INTERRUPTED: 'Kesildi — gözlem yarıda kaldı; eksik kısım uydurulmadı',
};

export const END_REASON_LABEL: Readonly<Record<string, string>> = {
  STREAM_ENDED: 'Veri kümesi bitti',
  CANCELLED: 'Kullanıcı iptal etti',
  OBSERVATION_LIMIT: 'Gözlem sınırına ulaşıldı',
  PROVIDER_ERROR: 'Akış hatası',
  EVALUATION_ERROR: 'Kural değerlendirmesi başarısız oldu',
  SHUTDOWN: 'Uygulama kapatıldı',
  INTERRUPTED: 'Uygulama gözlem sırasında durdu',
};

export const OPERATIONAL_LABEL: Readonly<Record<string, string>> = {
  RUN_OPENED: 'Gözlem başladı',
  RUN_ENDED: 'Gözlem bitti',
  PROVIDER_DISCONNECTED: 'Sağlayıcı bağlantısı koptu',
  PROVIDER_RECOVERING: 'Sağlayıcı yeniden bağlanıyor',
  DATA_GAP: 'Veride boşluk',
  CONFLICTING_CORRECTION: 'Çelişen düzeltme karantinaya alındı',
  LATE_FILL: 'Geç gelen mum bir boşluğu doldurdu',
  OBSERVATION_REFUSED: 'Akış bir gözlemi reddetti',
  DECISION_SUPERSEDED: 'Önceki kararların dayandığı kanıt düzeltildi (kararlar değiştirilmedi)',
  EVALUATION_FAILED: 'Değerlendirme başarısız oldu',
};

export function labelOf(table: Readonly<Record<string, string>>, value: string | null): string {
  if (value === null) return '—';
  return table[value] ?? value;
}

/** One sentence for an entry, in the order a person reads it. */
export function describeEntry(entry: ShadowEntryDto): string {
  if (entry.kind === 'OPERATIONAL') return labelOf(OPERATIONAL_LABEL, entry.operational);
  return labelOf(OUTCOME_LABEL, entry.outcome);
}

/**
 * What price did after a decision, as a sentence - or why nothing can be said.
 *
 * `null` means no development was published for this decision. That is not
 * "nothing happened": a quiet decision is never followed, and an intent may
 * still be pending. The caller says which.
 */
export function describeDevelopment(development: ShadowDevelopmentDto | null): string {
  if (development === null) return 'Sonraki gelişme yayımlanmadı.';
  const state = labelOf(DEVELOPMENT_LABEL, development.state);
  if (development.state === 'UNAVAILABLE' || development.state === 'INVALIDATED') {
    return `${state}: ${development.unresolved_reason ?? 'neden bildirilmedi'}`;
  }
  if (development.state === 'PENDING') return state;
  return `${state}: ${labelOf(LEVEL_LABEL, development.event)}`;
}

/** Why a decision has no development, stated rather than left blank. */
export function whyNoDevelopment(entry: ShadowEntryDto, run: ShadowRunDto): string {
  if (entry.outcome !== 'ENTRY_INTENT') {
    return 'Bu karar bir giriş önermedi; sonrasında izlenecek seviye yok.';
  }
  return run.status === 'OBSERVING'
    ? 'Gözlem penceresi hâlâ açık; sonuç henüz yayımlanmadı.'
    : 'Bu karar için sonuç yayımlanmadı (izleme sınırı dolmuş olabilir).';
}

export function isSuperseding(entry: ShadowEntryDto): boolean {
  return entry.operational === 'DECISION_SUPERSEDED';
}

/**
 * Which decisions a superseding entry names: every decision at or after its
 * boundary read the contested candle. Boundaries are ISO strings from the same
 * server clock format, so string order is time order.
 */
export function supersededBoundaries(entries: readonly ShadowEntryDto[]): readonly string[] {
  return entries
    .filter(isSuperseding)
    .map((entry) => entry.market_boundary)
    .filter((boundary): boundary is string => boundary !== null);
}

export function isSuperseded(entry: ShadowEntryDto, contested: readonly string[]): boolean {
  if (entry.kind !== 'DECISION' || entry.market_boundary === null) return false;
  const boundary = entry.market_boundary;
  return contested.some((from) => boundary >= from);
}

// ----------------------------------------------------------------------
// Held journal state
// ----------------------------------------------------------------------

export interface HeldJournal {
  readonly runId: string;
  readonly ticket: number;
  readonly entries: readonly ShadowEntryDto[];
  readonly total: number;
  readonly nextAfter: number | null;
  /** True when the screen stopped loading because it reached its own bound. */
  readonly capped: boolean;
}

export function emptyJournal(runId: string, ticket: number): HeldJournal {
  return { runId, ticket, entries: [], total: 0, nextAfter: 0, capped: false };
}

/**
 * Add one page, or refuse it.
 *
 * A page for another run, or from an older request ticket, is dropped: a slow
 * answer for the run a person left must not appear under the run they chose.
 * Entries already held are not duplicated when a page overlaps.
 */
export function withPage(
  held: HeldJournal,
  page: ShadowJournalPageDto,
  runId: string,
  ticket: number,
): HeldJournal {
  if (runId !== held.runId || ticket !== held.ticket || page.run_id !== held.runId) return held;
  const known = new Set(held.entries.map((entry) => entry.sequence));
  const fresh = page.items.filter((entry) => !known.has(entry.sequence));
  const merged = [...held.entries, ...fresh].sort((a, b) => a.sequence - b.sequence);
  const capped = merged.length >= MAX_HELD_ENTRIES;
  const exhausted = page.items.length === 0 || page.next_after === null;
  return {
    ...held,
    entries: capped ? merged.slice(0, MAX_HELD_ENTRIES) : merged,
    total: page.total,
    nextAfter: capped || exhausted ? null : page.next_after,
    capped,
  };
}

export function heldSummary(held: HeldJournal): string {
  if (held.capped) {
    return `${held.total} kaydın ilk ${held.entries.length} tanesi gösteriliyor — ekran sınırına ulaşıldı; kayıtlar sunucuda eksiksiz duruyor.`;
  }
  if (held.nextAfter !== null && held.entries.length < held.total) {
    return `${held.total} kaydın ${held.entries.length} tanesi yüklendi.`;
  }
  return `${held.total} kaydın tamamı yüklendi.`;
}

/** Add one page of run history; runs already held are not duplicated. */
export function withRunPage(
  held: readonly ShadowRunDto[],
  page: readonly ShadowRunDto[],
): readonly ShadowRunDto[] {
  const known = new Set(held.map((run) => run.run_id));
  return [...held, ...page.filter((run) => !known.has(run.run_id))].slice(0, MAX_HELD_RUNS);
}

export function decisionsOf(entries: readonly ShadowEntryDto[]): readonly ShadowEntryDto[] {
  return entries.filter((entry) => entry.kind === 'DECISION');
}

/**
 * How many held decisions carry each outcome - a count of *answers*, never of
 * trades. There is no win rate: nothing here was a trade.
 */
export function outcomeCounts(
  entries: readonly ShadowEntryDto[],
): Readonly<Record<string, number>> {
  const counts: Record<string, number> = {};
  for (const entry of decisionsOf(entries)) {
    const key = entry.outcome ?? 'UNKNOWN';
    counts[key] = (counts[key] ?? 0) + 1;
  }
  return counts;
}

/** A fresh idempotency key for a creation attempt. Kept until it succeeds. */
export function newAttemptKey(): string {
  const random =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`;
  return `ui-${random}`.slice(0, 64);
}
