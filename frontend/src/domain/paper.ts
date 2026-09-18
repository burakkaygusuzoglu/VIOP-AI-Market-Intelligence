import type { PaperEventDto, PaperPositionDto, PaperSummaryDto } from '../api/paper';

/**
 * Paper-trading read model (Phase 9).
 *
 * One source of truth, two densities. Beginner and Pro read the same object;
 * Pro shows more of it. Nothing here computes a number - every price, quantity
 * and P&L is the backend's exact string, and every explanation is assembled
 * from the fields the backend's ledger event already carries.
 */

export type PaperState =
  | 'PENDING_ENTRY'
  | 'OPEN'
  | 'PARTIALLY_CLOSED'
  | 'AMBIGUOUS_HALTED'
  | 'CLOSED'
  | 'CANCELLED'
  | 'REJECTED'
  | 'UNKNOWN';

const STATES: readonly PaperState[] = [
  'PENDING_ENTRY',
  'OPEN',
  'PARTIALLY_CLOSED',
  'AMBIGUOUS_HALTED',
  'CLOSED',
  'CANCELLED',
  'REJECTED',
];

export function toPaperState(value: string): PaperState {
  return (STATES as readonly string[]).includes(value) ? (value as PaperState) : 'UNKNOWN';
}

/** Turkish label plus a word that says the same thing without colour (§49). */
export const STATE_LABEL: Record<PaperState, string> = {
  PENDING_ENTRY: 'Giriş bekleniyor',
  OPEN: 'Açık',
  PARTIALLY_CLOSED: 'Kısmen kapandı',
  AMBIGUOUS_HALTED: 'Belirsiz çubuk — donduruldu',
  CLOSED: 'Kapandı',
  CANCELLED: 'İptal edildi',
  REJECTED: 'Giriş reddedildi',
  UNKNOWN: 'Bilinmeyen durum',
};

export const DIRECTION_LABEL: Record<string, string> = {
  LONG: 'Uzun (LONG)',
  SHORT: 'Kısa (SHORT)',
};

export interface PaperActions {
  /** Whether the lifecycle state admits the action. The server still decides. */
  readonly canClose: boolean;
  readonly canMoveStop: boolean;
  readonly canCancel: boolean;
  readonly canObserve: boolean;
}

export function actionsFor(state: PaperState, closePending: boolean): PaperActions {
  const exposed = state === 'OPEN' || state === 'PARTIALLY_CLOSED' || state === 'AMBIGUOUS_HALTED';
  return {
    canClose: exposed && !closePending,
    canMoveStop: state === 'OPEN' || state === 'PARTIALLY_CLOSED',
    canCancel: state === 'PENDING_ENTRY',
    canObserve: state === 'PENDING_ENTRY' || exposed,
  };
}

export interface PaperSummary {
  readonly id: string;
  readonly symbol: string;
  readonly direction: string;
  readonly state: PaperState;
  readonly quantity: number;
  readonly remaining: number;
  readonly realizedGross: string;
  readonly realizedNet: string | null;
  readonly unrealizedGross: string | null;
  readonly updatedAt: string;
}

export interface PaperEventView {
  readonly sequence: number;
  readonly type: string;
  readonly marketTime: string | null;
  readonly title: string;
  readonly detail: string;
  /** True for events a person must not miss: a rejection, an ambiguity, a stop. */
  readonly important: boolean;
  readonly data: Readonly<Record<string, string>>;
}

export interface PaperPosition extends PaperSummary {
  readonly dto: PaperPositionDto;
  readonly events: readonly PaperEventView[];
  readonly actions: PaperActions;
}

export function mapPaperSummary(dto: PaperSummaryDto): PaperSummary {
  return {
    id: dto.id,
    symbol: dto.symbol,
    direction: dto.direction,
    state: toPaperState(dto.state),
    quantity: dto.quantity,
    remaining: dto.remaining,
    realizedGross: dto.realized_gross,
    realizedNet: dto.realized_net,
    unrealizedGross: dto.unrealized_gross,
    updatedAt: dto.updated_at,
  };
}

export function mapPaperPosition(dto: PaperPositionDto): PaperPosition {
  const state = toPaperState(dto.state);
  return {
    ...mapPaperSummary(dto),
    dto,
    events: dto.key_events.map(describeEvent),
    actions: actionsFor(state, dto.close_pending),
  };
}

const yes = (value: string | undefined): boolean => value === 'true';

/**
 * A ledger event in words.
 *
 * Every figure in the sentence is a field of the event the backend recorded;
 * the sentence only says which field is which. A reason the engine wrote itself
 * - a rejection, an ambiguity - is shown as the engine wrote it.
 */
export function describeEvent(event: PaperEventDto): PaperEventView {
  const d = event.data;
  const base = {
    sequence: event.sequence,
    type: event.type,
    marketTime: event.market_time,
    data: d,
  };
  switch (event.type) {
    case 'POSITION_CREATED':
      return {
        ...base,
        title: 'Simülasyon pozisyonu oluşturuldu',
        detail: `Risk motoru sonucu: ${d.risk_outcome ?? '—'}. Planlanan giriş ${d.intended_entry ?? '—'}, stop ${d.stop ?? '—'}. Kurallar: ${d.rules_version ?? '—'}.`,
        important: false,
      };
    case 'ENTRY_FILLED':
      return {
        ...base,
        title: 'Giriş simüle edildi',
        detail: `Planlanan ${d.intended_entry ?? '—'}; sonraki çubuğun açılışı ${d.reference_price ?? '—'}, kayma ${d.slippage ?? '—'} → simüle dolum ${d.fill_price ?? '—'} (${d.quantity ?? '—'} birim).`,
        important: false,
      };
    case 'ENTRY_REJECTED':
      return {
        ...base,
        title: 'Giriş reddedildi — pozisyon açılmadı',
        detail: d.reason ?? '',
        important: true,
      };
    case 'TARGET_FILLED':
      return {
        ...base,
        title: `Hedef ${d.target ?? '?'} simüle edildi`,
        detail: `Hedef fiyatı ${d.trigger_price ?? '—'} → dolum ${d.fill_price ?? '—'}${yes(d.gap) ? ' (açılış boşluğu; fiyat iyileşmesi varsayılmadı)' : ''}. ${d.quantity ?? '—'} birim kapandı, ${d.remaining ?? '—'} kaldı.`,
        important: false,
      };
    case 'STOP_FILLED':
      return {
        ...base,
        title: yes(d.ambiguous) ? 'Stop simüle edildi — belirsiz çubuk' : 'Stop simüle edildi',
        detail: `Tetik ${d.trigger_price ?? '—'} → dolum ${d.fill_price ?? '—'}${yes(d.gap) ? ' (açılış stopun ötesindeydi; ilk mevcut fiyat kullanıldı)' : ''}. ${d.quantity ?? '—'} birim kapandı.${yes(d.ambiguous) ? ` Aynı çubukta hedef ${d.targets_also_touched ?? ''} de görüldü; sıra bilinemediği için stop önce varsayıldı.` : ''}`,
        important: true,
      };
    case 'SAME_BAR_AMBIGUITY':
      return {
        ...base,
        title: 'Aynı çubukta stop ve hedef',
        detail: `OHLC verisi çubuk içindeki sırayı kaydetmez. Uygulanan politika: ${d.policy === 'HALT' ? 'dondur (dolum simüle edilmedi)' : 'önce stop'}.`,
        important: true,
      };
    case 'CLOSE_REQUESTED':
      return {
        ...base,
        title: 'Kapanış istendi',
        detail: `Kalan ${d.remaining ?? '—'} birim sonraki çubuğun açılışında simüle edilecek.`,
        important: false,
      };
    case 'MANUAL_EXIT_FILLED':
      return {
        ...base,
        title: 'Manuel çıkış simüle edildi',
        detail: `Açılış ${d.reference_price ?? '—'}, kayma ${d.slippage ?? '—'} → dolum ${d.fill_price ?? '—'}. ${d.quantity ?? '—'} birim kapandı.`,
        important: false,
      };
    case 'STOP_MOVED_TO_BREAKEVEN':
      return {
        ...base,
        title: 'Stop başabaşa taşındı',
        detail: `${d.from ?? '—'} → ${d.to ?? '—'}; kalan ${d.protects ?? '—'} birimi korur.`,
        important: false,
      };
    case 'POSITION_CANCELLED':
      return {
        ...base,
        title: 'İptal edildi',
        detail: 'Giriş gerçekleşmeden iptal edildi.',
        important: false,
      };
    case 'POSITION_CLOSED':
      return {
        ...base,
        title: 'Pozisyon kapandı',
        detail: `Gerçekleşen brüt K/Z ${d.realized_gross ?? '—'}${d.realized_net ? `, net ${d.realized_net}` : ''}.`,
        important: true,
      };
    default:
      return { ...base, title: event.type, detail: '', important: false };
  }
}
