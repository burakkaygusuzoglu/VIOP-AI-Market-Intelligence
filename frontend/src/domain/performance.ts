import type { MetricDto, PerformanceDto } from '../api/performance';

/**
 * Turning a performance response into words (Phase 10).
 *
 * This module names things and decides how to *say* them. It does not compute
 * any of them: there is no addition, no division and no comparison of amounts
 * anywhere in this file. A metric that arrives without a value is rendered as
 * the reason it has none, never as zero or a dash with no explanation.
 */

export const MISSING = '—';

export type MetricStatus = MetricDto['status'];

export const BASIS_LABEL: Record<string, string> = {
  REALIZED_GROSS: 'brüt (ücretler hariç)',
  REALIZED_NET: 'net (ücretler dahil)',
};

export const OUTCOME_LABEL: Record<string, string> = {
  WIN: 'Kazanç',
  LOSS: 'Kayıp',
  BREAKEVEN: 'Başabaş',
};

export const POPULATION_LABEL: Record<string, string> = {
  PENDING_ENTRY: 'Giriş bekliyor',
  OPEN: 'Açık',
  PARTIALLY_CLOSED: 'Kısmi kapandı',
  AMBIGUOUS_HALTED: 'Belirsiz çubuk — donduruldu',
  CLOSED: 'Kapandı',
  CANCELLED: 'İptal edildi',
  REJECTED: 'Reddedildi',
};

export const DIRECTION_LABEL: Record<string, string> = {
  LONG: 'Uzun (LONG)',
  SHORT: 'Kısa (SHORT)',
};

export const STATUS_LABEL: Record<MetricStatus, string> = {
  AVAILABLE: 'Hesaplandı',
  UNAVAILABLE: 'Hesaplanamıyor',
  PARTIAL_COVERAGE: 'Kısmi kapsam',
  NOT_IMPLEMENTED: 'Bu veri modelinde yok',
};

/** The server's exact decimal string, or the word for "not applicable". */
export function metricText(metric: MetricDto): string {
  return metric.status === 'AVAILABLE' && metric.value !== null ? metric.value : MISSING;
}

/** A percentage the server computed, shown with its own units. */
export function percentText(metric: MetricDto): string {
  return metric.status === 'AVAILABLE' && metric.value !== null ? `%${metric.value}` : MISSING;
}

/** Why a metric has no value. Always the server's sentence, never invented here. */
export function metricReason(metric: MetricDto): string | null {
  return metric.status === 'AVAILABLE' ? null : metric.reason;
}

export function isAvailable(metric: MetricDto): boolean {
  return metric.status === 'AVAILABLE';
}

export interface StreakSentence {
  readonly text: string;
  readonly kind: string | null;
}

/** "Şu anda 3 kazançlı işlemlik seri" - from the server's own counts. */
export function streakSentence(dto: PerformanceDto): StreakSentence {
  const { current_kind: kind, current_length: length } = dto.streaks;
  if (kind === null || length === 0) {
    return { text: 'Şu anda açık bir seri yok.', kind: null };
  }
  const word = kind === 'WIN' ? 'kazançlı' : 'kayıplı';
  return { text: `Şu anda ${length} ${word} işlemlik seri.`, kind };
}

/** The one-line summary of what the numbers are about. */
export function coverageSentence(dto: PerformanceDto): string {
  const { covered, total } = dto.fee_coverage;
  if (total === 0) return 'Henüz tamamlanmış işlem yok.';
  if (covered === total) {
    return `Tamamlanan ${total} işlemin tamamında ücret modellendi; sonuçlar net.`;
  }
  if (covered === 0) {
    return `Tamamlanan ${total} işlemde ücret modellenmedi; sonuçlar brüt ve net bilinmiyor.`;
  }
  return `Tamamlanan ${total} işlemin ${covered} tanesinde ücret modellendi; net toplam bu seçim için bilinmiyor.`;
}

/** Turkish for an ISO market timestamp, labelled UTC like the rest of the app. */
export function formatMarketTime(iso: string | null): string {
  if (iso === null) return MISSING;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return `${parsed.toLocaleDateString('tr-TR', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  })} ${parsed.toLocaleTimeString('tr-TR', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
  })} UTC`;
}

export interface TimelineRow {
  readonly positionId: string;
  readonly when: string;
  readonly amount: string;
  readonly cumulative: string;
}

/**
 * The cumulative realized curve as rows.
 *
 * Presentation only: every amount is the server's string, in the server's
 * order. Nothing is re-accumulated here, which is why a chart and this table
 * can never disagree.
 */
export function timelineRows(dto: PerformanceDto): TimelineRow[] {
  return dto.timeline.map((point) => ({
    positionId: point.position_id,
    when: formatMarketTime(point.terminal_time),
    amount: point.amount,
    cumulative: point.cumulative,
  }));
}

/** The plot points for the sparkline, scaled to the box - never new financial data. */
export function sparklinePoints(dto: PerformanceDto, width: number, height: number): string {
  const values = dto.timeline.map((point) => Number(point.cumulative));
  if (values.length === 0) return '';
  const highest = Math.max(...values, 0);
  const lowest = Math.min(...values, 0);
  const span = highest - lowest || 1;
  const step = values.length > 1 ? width / (values.length - 1) : 0;
  // Integer geometry: these are pixel coordinates for a decorative line, and
  // number formatting belongs to the formatting module, not here.
  return values
    .map((value, index) => {
      const x = Math.round(values.length > 1 ? index * step : width / 2);
      const y = Math.round(height - ((value - lowest) / span) * height);
      return `${x},${y}`;
    })
    .join(' ');
}
