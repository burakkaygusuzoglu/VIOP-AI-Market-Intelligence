import { SystemStatus } from './components/SystemStatus';
import { useTranslations } from './i18n';

export function App() {
  const t = useTranslations();
  return (
    <main className="app">
      <header>
        <h1 className="app__title">{t.appName}</h1>
        <p className="app__tagline">{t.tagline}</p>
      </header>

      <SystemStatus />

      <section className="panel">
        <h2 className="panel__title">{t.executionMode.title}</h2>
        <p className="status-badge status-badge--degraded">
          <span className="status-badge__dot" aria-hidden="true" />
          {t.executionMode.value}
        </p>
        <p className="notice notice--warning">{t.executionMode.detail}</p>
      </section>

      <section className="panel">
        <h2 className="panel__title">{t.phase.label}</h2>
        <p>{t.phase.value}</p>
        <p className="notice">{t.phase.detail}</p>
      </section>
    </main>
  );
}
