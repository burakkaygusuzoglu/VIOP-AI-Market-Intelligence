import type {
  LiveEnvelopeDto,
  LiveSessionDto,
  LiveTimelineEntryDto,
  LiveTimelinePageDto,
} from '../api/live';

/**
 * The browser's view of one live session (Phase 13 Part 2A).
 *
 * Pure functions over server-sent state. Nothing here computes a market fact:
 * freshness, integrity, availability, alerts and every candle arrive from the
 * server, and this module only decides **whether an arriving message may
 * change what is on screen**.
 *
 * ## The three guards
 *
 * 1. **Session.** A message for another session is ignored. Switching from
 *    session A to B cannot let a late A event paint B.
 * 2. **Generation.** Every stream the screen opens gets a new generation
 *    number. A message from an older stream of the *same* session - one that
 *    was replaced after a resync - is ignored.
 * 3. **Continuity.** A timeline event is applied only if its cursor is exactly
 *    one past the last one applied. A skip means notifications were lost, and
 *    the answer is to re-read the authoritative snapshot, never to carry on as
 *    if the history were complete. A heartbeat that reveals a newer cursor
 *    means the same thing.
 *
 * A heartbeat is transport liveness. It never changes the session snapshot,
 * the candles or their freshness.
 */

export type TransportState = 'CONNECTING' | 'OPEN' | 'LOST' | 'CLOSED';

export interface TransportEvent {
  readonly at: string;
  readonly kind: 'OPENED' | 'LOST' | 'RESYNC' | 'CLOSED' | 'INVALID';
  readonly detail: string;
}

export interface LiveClientState {
  readonly sessionId: string;
  readonly generation: number;
  readonly session: LiveSessionDto;
  readonly cursor: number;
  readonly timeline: readonly LiveTimelineEntryDto[];
  /** True when the client knows its timeline is missing entries. */
  readonly timelineGap: boolean;
  readonly transport: TransportState;
  readonly transportLog: readonly TransportEvent[];
  readonly lastHeartbeatAt: string | null;
  readonly resyncRequired: boolean;
  readonly resyncReason: string | null;
}

export type EnvelopeAction = 'NONE' | 'RESYNC' | 'CLOSE';

/** The server retains 500 entries per session; the client can hold them all. */
export const TIMELINE_LIMIT = 500;
export const TRANSPORT_LOG_LIMIT = 20;

export function initialState(
  session: LiveSessionDto,
  page: LiveTimelinePageDto | null,
  generation: number,
): LiveClientState {
  const entries = page === null ? [] : page.entries;
  return {
    sessionId: session.id,
    generation,
    session,
    cursor: session.cursor,
    timeline: bounded(sortBySeq(entries)),
    timelineGap: page === null ? true : page.gap,
    transport: 'CONNECTING',
    transportLog: [],
    lastHeartbeatAt: null,
    resyncRequired: false,
    resyncReason: null,
  };
}

function sortBySeq(entries: readonly LiveTimelineEntryDto[]): LiveTimelineEntryDto[] {
  return [...entries].sort((a, b) => a.seq - b.seq);
}

function bounded(entries: readonly LiveTimelineEntryDto[]): LiveTimelineEntryDto[] {
  return entries.length > TIMELINE_LIMIT ? entries.slice(-TIMELINE_LIMIT) : [...entries];
}

function merge(
  existing: readonly LiveTimelineEntryDto[],
  incoming: readonly LiveTimelineEntryDto[],
): LiveTimelineEntryDto[] {
  const bySeq = new Map<number, LiveTimelineEntryDto>();
  for (const entry of existing) bySeq.set(entry.seq, entry);
  for (const entry of incoming) bySeq.set(entry.seq, entry);
  return bounded(sortBySeq([...bySeq.values()]));
}

/**
 * Apply one message from the event stream.
 *
 * Returns the next state and what the screen must do: nothing, resync from
 * the snapshot, or close the stream because the session ended.
 */
export function applyEnvelope(
  state: LiveClientState,
  envelope: LiveEnvelopeDto,
  generation: number,
): { state: LiveClientState; action: EnvelopeAction } {
  // Guards 1 and 2: another session, or a stream this screen has replaced.
  if (envelope.session_id !== state.sessionId || generation !== state.generation) {
    return { state, action: 'NONE' };
  }
  switch (envelope.kind) {
    case 'HEARTBEAT': {
      const next = { ...state, lastHeartbeatAt: envelope.server_time, transport: 'OPEN' as const };
      if (envelope.cursor > state.cursor) {
        // The server has moved on further than anything received: something
        // was missed in between.
        return {
          state: { ...next, resyncRequired: true, resyncReason: 'MISSED_NOTIFICATIONS' },
          action: 'RESYNC',
        };
      }
      return { state: next, action: 'NONE' };
    }
    case 'STATE': {
      if (envelope.session === null || envelope.cursor < state.cursor) {
        return { state, action: 'NONE' };
      }
      return {
        state: {
          ...state,
          session: envelope.session,
          cursor: envelope.cursor,
          transport: 'OPEN',
          resyncRequired: false,
          resyncReason: null,
        },
        action: 'NONE',
      };
    }
    case 'TIMELINE': {
      if (envelope.cursor <= state.cursor) return { state, action: 'NONE' }; // already applied
      if (envelope.cursor !== state.cursor + 1 || envelope.entry === null) {
        // Guard 3: a skipped notification. Do not apply anything past it.
        return {
          state: {
            ...state,
            resyncRequired: true,
            resyncReason: 'CURSOR_GAP',
            timelineGap: true,
          },
          action: 'RESYNC',
        };
      }
      return {
        state: {
          ...state,
          session: envelope.session ?? state.session,
          cursor: envelope.cursor,
          timeline: merge(state.timeline, [envelope.entry]),
          transport: 'OPEN',
        },
        action: 'NONE',
      };
    }
    case 'RESYNC_REQUIRED':
      return {
        state: {
          ...state,
          resyncRequired: true,
          resyncReason: envelope.reason ?? 'RESYNC_REQUIRED',
          timelineGap: true,
        },
        action: 'RESYNC',
      };
    case 'END':
      return {
        state: {
          ...state,
          session: envelope.session ?? state.session,
          cursor: Math.max(state.cursor, envelope.cursor),
          transport: 'CLOSED',
        },
        action: 'CLOSE',
      };
  }
}

/**
 * Accept an authoritative snapshot read over REST, unless it is older than
 * what is already shown or belongs to another session.
 */
export function acceptSnapshot(
  state: LiveClientState,
  snapshot: LiveSessionDto,
  page: LiveTimelinePageDto | null,
  generation: number,
): LiveClientState {
  if (snapshot.id !== state.sessionId) return state;
  if (snapshot.cursor < state.cursor) {
    // An older answer than what is shown: keep the content, but the stream
    // about to open is still the current one.
    return { ...state, generation };
  }
  return {
    ...state,
    generation,
    session: snapshot,
    cursor: snapshot.cursor,
    timeline: page === null ? state.timeline : merge(state.timeline, page.entries),
    timelineGap: page === null ? state.timelineGap : state.timelineGap || page.gap,
    resyncRequired: false,
    resyncReason: null,
  };
}

/**
 * Add an older page of the timeline, read on request.
 *
 * Only for the session and stream generation that asked: a page arriving
 * after a switch or a resync is discarded. Entries are merged by `seq`, so a
 * page overlapping what is shown adds nothing twice.
 */
export function withOlderEntries(
  state: LiveClientState,
  page: LiveTimelinePageDto,
  sessionId: string,
  generation: number,
): LiveClientState {
  if (state.sessionId !== sessionId || state.generation !== generation) return state;
  return { ...state, timeline: merge(state.timeline, page.entries) };
}

/** The `after` cursor that reads the page just before the oldest shown entry. */
export function olderPageCursor(state: LiveClientState, pageSize: number): number | null {
  const oldest = state.timeline[0]?.seq;
  if (oldest === undefined || oldest <= state.session.oldest_retained) return null;
  return Math.max(state.session.oldest_retained - 1, oldest - pageSize - 1);
}

export function withTransport(
  state: LiveClientState,
  transport: TransportState,
  event: TransportEvent,
): LiveClientState {
  const log = [...state.transportLog, event];
  return {
    ...state,
    transport,
    transportLog: log.length > TRANSPORT_LOG_LIMIT ? log.slice(-TRANSPORT_LOG_LIMIT) : log,
  };
}

// ----------------------------------------------------------------------
// Labels. Text, always - colour never carries a state on its own.
// ----------------------------------------------------------------------

export const FRESHNESS_LABEL: Record<string, string> = {
  NO_DATA: 'VERİ YOK',
  FRESH: 'AKIŞ TAZE',
  STALE: 'CANLI VERİ BAYAT',
};

export const INTEGRITY_LABEL: Record<string, string> = {
  COMPLETE: 'TAM',
  GAPPED: 'EKSİK MUM',
  DISCONTINUOUS: 'AÇIKLANAMAYAN ZAMAN BOŞLUĞU',
  CONFLICTED: 'ÇELİŞKİLİ DÜZELTME',
  UNVERIFIED: 'SÜREKLİLİK DOĞRULANMADI',
};

export const AVAILABILITY_LABEL: Record<string, string> = {
  AVAILABLE: 'ANALİZE UYGUN',
  UNAVAILABLE: 'ANALİZE UYGUN DEĞİL',
};

export const CONNECTION_LABEL: Record<string, string> = {
  INITIALIZING: 'BAŞLATILIYOR',
  CONNECTED: 'SAĞLAYICI BAĞLI',
  DISCONNECTED: 'SAĞLAYICI KOPTU',
  RECOVERING: 'YENİDEN BAĞLANDI — SÜREKLİLİK DOĞRULANIYOR',
  TERMINATED: 'AKIŞ SONA ERDİ',
};

export const TRANSPORT_LABEL: Record<TransportState, string> = {
  CONNECTING: 'Tarayıcı bağlantısı kuruluyor',
  OPEN: 'Tarayıcı bağlantısı açık',
  LOST: 'Tarayıcı bağlantısı koptu (sağlayıcı değil)',
  CLOSED: 'Tarayıcı bağlantısı kapalı',
};

export const END_ORIGIN_LABEL: Record<string, string> = {
  STREAM: 'Akış kendi sona erdi',
  USER_CANCELLED: 'Kullanıcı iptal etti',
  DEADLINE: 'Azami oturum süresi doldu',
  SHUTDOWN: 'Sunucu kapandı',
};

export const ALERT_LABEL: Record<string, string> = {
  DATA_STALE: 'Veri bayat',
  PROVIDER_DISCONNECTED: 'Sağlayıcı bağlantısı koptu',
  RECOVERY_PENDING: 'Kurtarma bekleniyor',
  DATA_GAP: 'Eksik mum',
  DATA_DISCONTINUITY: 'Açıklanamayan zaman boşluğu',
  DATA_CONFLICT: 'Çelişkili düzeltme',
  CONTINUITY_UNPROVEN: 'Süreklilik kanıtlanmadı',
  INVALID_OBSERVATIONS: 'Reddedilen gözlemler',
  STREAM_OVERLOADED: 'Akış aşırı yüklendi',
  STREAM_ENDED: 'Akış sona erdi',
};

export const TIMELINE_LABEL: Record<string, string> = {
  SESSION_STARTED: 'Oturum başladı',
  PROVIDER_SIGNAL: 'Sağlayıcı sinyali',
  CONNECTION_CHANGED: 'Sağlayıcı bağlantı durumu değişti',
  CANDLE_CONFIRMED: 'Kapanmış mum onaylandı',
  CANDLE_LATE_FILL: 'Geç gelen mum boşluğu doldurdu',
  FORMING_UPDATED: 'Oluşan mum güncellendi',
  DUPLICATE_IGNORED: 'Tekrar gelen mum yok sayıldı',
  CONFLICT_QUARANTINED: 'Çelişkili mum karantinaya alındı',
  OBSERVATION_REJECTED: 'Gözlem reddedildi',
  TIMEFRAME_STATUS_CHANGED: 'Zaman dilimi durumu değişti',
  ANALYSIS_COMPLETED: 'Onaylı analiz tamamlandı',
  ANALYSIS_UNAVAILABLE: 'Analiz yapılamadı',
  SESSION_ENDED: 'Oturum sona erdi',
};

/** What kind of fact a timeline entry records. Shown with every entry. */
export function timelineCategory(kind: string): string {
  switch (kind) {
    case 'CANDLE_CONFIRMED':
    case 'CANDLE_LATE_FILL':
      return 'Onaylı mum';
    case 'FORMING_UPDATED':
      return 'Oluşan mum';
    case 'DUPLICATE_IGNORED':
    case 'CONFLICT_QUARANTINED':
    case 'OBSERVATION_REJECTED':
      return 'Piyasa gözlemi';
    case 'PROVIDER_SIGNAL':
    case 'CONNECTION_CHANGED':
      return 'Sağlayıcı';
    case 'TIMEFRAME_STATUS_CHANGED':
      return 'Veri bütünlüğü';
    case 'ANALYSIS_COMPLETED':
    case 'ANALYSIS_UNAVAILABLE':
      return 'Analiz';
    default:
      return 'Oturum';
  }
}

export function availableCount(session: LiveSessionDto): number {
  return session.timeframes.filter((item) => item.availability === 'AVAILABLE').length;
}

export function anyStale(session: LiveSessionDto): boolean {
  return session.timeframes.some((item) => item.freshness === 'STALE');
}
