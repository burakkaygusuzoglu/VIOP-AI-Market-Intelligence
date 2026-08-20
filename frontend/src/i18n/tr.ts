/**
 * Turkish is the primary language (master spec section 4).
 *
 * The dictionary is a plain typed object rather than an i18n library: Phase 0
 * needs no runtime translation machinery, and adding one now would be an
 * unjustified dependency (master spec section 106). English is added later by
 * introducing a second dictionary with the same shape.
 */
export const tr = {
  appName: 'VİOP AI Piyasa Zekâsı',
  tagline: 'Analiz, eğitim ve risk yönetimi destek sistemi',
  systemStatus: {
    title: 'Sistem Durumu',
    checking: 'Kontrol ediliyor…',
    ok: 'Çalışıyor',
    degraded: 'Kısıtlı çalışıyor',
    unreachable: 'Sunucuya ulaşılamıyor',
    environment: 'Ortam',
    version: 'Sürüm',
    checkedAt: 'Kontrol zamanı',
    components: 'Bileşenler',
    database: 'Veritabanı',
    latency: 'Gecikme',
    retry: 'Tekrar dene',
  },
  executionMode: {
    title: 'Çalışma Modu',
    value: 'YALNIZCA ANALİZ VE SİNYAL',
    detail:
      'Bu uygulama gerçek emir göndermez. Tüm gerçek işlemleri kullanıcı kendi aracı kurumunda manuel olarak girer.',
  },
  phase: {
    label: 'Geliştirme aşaması',
    value: 'Faz 0 — Temel altyapı',
    detail:
      'Teknik göstergeler, piyasa yapısı, risk motoru ve yapay zekâ katmanı henüz uygulanmadı.',
  },
} as const;

export type Translations = typeof tr;
