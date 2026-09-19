/**
 * What the backend can actually do today (§2, §3).
 *
 * The UI is not allowed to imply a capability the runtime cannot perform, and
 * the reliable way to enforce that is to make the claim a value rather than a
 * memory. Every screen that offers an action checks here first.
 *
 * Derived from the live OpenAPI surface and the composition root, not from
 * what the codebase *contains*. Phase 8 moved three entries, and the reasons
 * are worth keeping:
 *
 * * `deterministic-analysis` and `market-data` became `AVAILABLE_NOW` because
 *   `POST /api/analysis` now runs the Phase 1-4 engines over user-supplied
 *   OHLCV. They were `APPLICATION_ONLY` while the engines existed and no HTTP
 *   surface reached them - an internal Python function is not a product
 *   feature.
 * * `screenshot-analysis` left `ENDPOINT_NOT_WIRED` because the composition
 *   root now builds the analyzer when Vision is configured, proven by a test
 *   that installs no dependency override. It is `REQUIRES_CONFIGURATION`
 *   rather than `AVAILABLE_NOW`: this deployment has no credential.
 * * `synthesis` left `DEFERRED` because a trusted `SynthesisContext` now has a
 *   producer. It is an optional step inside the analysis flow, not an endpoint
 *   of its own.
 */

export type CapabilityState =
  /** Reachable over HTTP right now, and a call can actually succeed. */
  | 'AVAILABLE_NOW'
  /**
   * The route exists and is documented, but no adapter is composed behind it,
   * so every call returns a typed failure.
   *
   * This is deliberately distinct from `AVAILABLE_NOW`. A route in the OpenAPI
   * document is not a working feature, and the difference is invisible unless
   * someone traces the dependency to the composition root - which is exactly
   * the kind of gap this matrix exists to keep out of the UI.
   *
   * No capability is in this state after Phase 8. The member is kept because
   * it names a condition that recurs, and because deleting it would delete the
   * distinction that caught the Phase 6 wiring gap.
   */
  | 'ENDPOINT_NOT_WIRED'
  /**
   * Composed at the root **when configured**, and typed NOT_CONFIGURED when
   * not. The difference from `ENDPOINT_NOT_WIRED` is that a credential would
   * actually make it work: there is a configuration that reaches the provider,
   * proven by a composition-root test that installs no dependency override.
   *
   * A UI may describe such a feature and must not offer it as working until
   * the runtime says it is - which is why `isLive()` stays false here.
   */
  | 'REQUIRES_CONFIGURATION'
  /** Implemented and tested in the backend, but not exposed over HTTP. */
  | 'APPLICATION_ONLY'
  /** Intentionally postponed to a later phase, with a known reason. */
  | 'DEFERRED'
  /** No implementation exists at any layer. */
  | 'NOT_IMPLEMENTED';

export interface Capability {
  readonly id: string;
  /** Turkish label, shown wherever the state is surfaced to a user. */
  readonly label: string;
  readonly state: CapabilityState;
  /** The HTTP surface, when there is one. */
  readonly endpoint: string | null;
  /** Why it is in this state. Shown in the developer/audit view. */
  readonly detail: string;
}

export const CAPABILITIES: readonly Capability[] = [
  {
    id: 'health',
    label: 'Sistem durumu',
    state: 'AVAILABLE_NOW',
    endpoint: 'GET /api/health',
    detail: 'Canlılık, hazırlık ve bileşen sağlığı.',
  },
  {
    id: 'screenshot-analysis',
    label: 'Ekran görüntüsü analizi',
    state: 'REQUIRES_CONFIGURATION',
    endpoint: 'POST /api/screenshots/analyse',
    detail:
      'Faz 8 kompozisyon kökündeki bağlantıyı kurdu: görüntü modeli yapılandırıldığında gerçek çözümleyici oluşturulur. Yapılandırılmadığında tipli 503 VISION_NOT_CONFIGURED döner - bu bir sistem durumudur, piyasa görüşü değildir. Bu kurulumda kimlik bilgisi yok.',
  },
  {
    id: 'screenshot-correction',
    label: 'Gözlem düzeltme',
    state: 'AVAILABLE_NOW',
    endpoint: 'POST /api/screenshots/corrections',
    detail:
      'ONAYLA / DÜZELT / REDDET. Uç nokta çalışır ve orijinal gözlem asla silinmez; ancak düzeltilecek gözlemi üreten görüntü analizi bağlı olmadığı için kullanıcı akışında henüz erişilebilir değildir.',
  },
  {
    id: 'deterministic-analysis',
    label: 'Deterministik piyasa analizi',
    state: 'AVAILABLE_NOW',
    endpoint: 'POST /api/analysis',
    detail:
      'Kullanıcının verdiği geçmiş OHLCV verisi üzerinde Faz 1-4 motorları çalışır. Sonuç geçicidir: kaydedilmez ve geri çağrılamaz.',
  },
  {
    id: 'market-data',
    label: 'Piyasa verisi yükleme',
    state: 'AVAILABLE_NOW',
    endpoint: 'POST /api/analysis',
    detail:
      'Kullanıcı CSV yükler. Sistem kendi başına piyasa verisi indirmez; canlı veri Faz 13, dış sağlayıcılar Faz 15 kapsamındadır.',
  },
  {
    id: 'synthesis',
    label: 'Claude sentezi',
    state: 'REQUIRES_CONFIGURATION',
    endpoint: 'POST /api/analysis',
    detail:
      'Faz 8 gerçek analiz bağlamını üretti; sentez artık bu akışın isteğe bağlı bir adımıdır. Yapılandırılmadığında deterministik analiz yine döner ve sentez durumu NOT_CONFIGURED olur. Ayrı bir sentez uç noktası yoktur.',
  },
  {
    id: 'persisted-analysis',
    label: 'Kaydedilmiş analiz',
    state: 'NOT_IMPLEMENTED',
    endpoint: null,
    detail: 'Analiz yaşam döngüsü ve kalıcılık henüz yok.',
  },
  {
    id: 'historical-analysis',
    label: 'Geçmiş analiz kaydı',
    state: 'NOT_IMPLEMENTED',
    endpoint: null,
    detail: 'Kalıcılık gerektirir.',
  },
  {
    id: 'live-analysis',
    label: 'Canlı analiz',
    state: 'NOT_IMPLEMENTED',
    endpoint: null,
    detail: 'Canlı piyasa verisi sağlayıcısı yok.',
  },
  {
    id: 'paper-trading',
    label: 'Kağıt üzerinde işlem (simülasyon)',
    // Phase 9. The engine, the ledger, persistence and every route exist and are
    // reachable, but no verified contract metadata provider is composed, so the
    // server refuses every new position with PRODUCT_METADATA_UNAVAILABLE. That is
    // exactly ENDPOINT_NOT_WIRED: a documented route whose calls return a typed
    // failure. Not AVAILABLE_NOW, and never offered as if it were.
    state: 'ENDPOINT_NOT_WIRED',
    endpoint: 'POST /api/paper/positions',
    detail:
      'Simülasyon motoru, olay defteri ve kalıcılık hazır; ancak bu kurulumda doğrulanmış sözleşme meta verisi sağlayıcısı yok, bu yüzden sunucu her yeni kağıt pozisyonu reddeder. Gerçek emir yürütme kalıcı olarak devre dışıdır (ana şartname 120).',
  },
  {
    id: 'paper-performance',
    label: 'Kağıt işlem performansı ve günlük (simülasyon)',
    // Phase 10. The analytics read the append-only ledger, so the endpoint works
    // whatever is in it: with no positions it truthfully reports that nothing has
    // completed. Writing a note works as soon as a position exists.
    state: 'AVAILABLE_NOW',
    endpoint: 'GET /api/paper/performance',
    detail:
      'Kaydedilmiş kağıt pozisyonların değiştirilemez olay defterinden hesaplanır. Hesaplanamayan ölçümler sıfır olarak değil, sebebiyle birlikte gösterilir. Notlar ve etiketler kullanıcıya aittir ve hiçbir finansal değeri değiştirmez.',
  },
  {
    id: 'market-replay',
    label: 'Geçmişe sarma (deterministik simülasyon)',
    // Phase 11. The session, the cursor and every read are reachable and work
    // against real storage; opening a position inside a replay goes through the
    // Phase 9 engine and is refused for the same reason it is elsewhere - no
    // verified contract metadata is composed here.
    state: 'AVAILABLE_NOW',
    endpoint: 'POST /api/replay/sessions',
    detail:
      'Kendi yüklediğiniz değiştirilemez geçmiş veri üzerinde mum mum ilerlersiniz. Her sayı, oturumun geçmiş piyasa anına göre üretilir; kapanışı o andan sonra olan hiçbir mum okunmaz. Oturumda kağıt pozisyon açmak, bu kurulumda doğrulanmış sözleşme meta verisi olmadığı için reddedilir.',
  },
] as const;

export function capability(id: string): Capability | undefined {
  return CAPABILITIES.find((item) => item.id === id);
}

/** Whether a feature may be offered as a working action in the product UI. */
export function isLive(id: string): boolean {
  return capability(id)?.state === 'AVAILABLE_NOW';
}

/**
 * Whether any runtime path can produce an analysis to display.
 *
 * True since Phase 8. It stays a function rather than becoming a constant so
 * the shell keeps asking the matrix rather than remembering the answer.
 */
export function analysisIsAvailable(): boolean {
  return isLive('deterministic-analysis');
}

/**
 * Whether a capability is described but gated on deployment configuration.
 *
 * Distinct from `isLive`: the UI may explain such a feature and must not
 * present it as working. The runtime's own typed status is the authority.
 */
export function requiresConfiguration(id: string): boolean {
  return capability(id)?.state === 'REQUIRES_CONFIGURATION';
}
