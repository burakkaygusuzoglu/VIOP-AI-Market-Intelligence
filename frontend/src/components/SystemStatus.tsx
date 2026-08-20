import { Fragment } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchHealth, healthQueryKey } from '../api/health';
import { useTranslations } from '../i18n';

/**
 * Phase 0 view: proves the frontend reaches the backend through a
 * schema-validated client. It renders no market information, because no
 * analytical engine exists yet.
 */
export function SystemStatus() {
  const t = useTranslations().systemStatus;
  const { data, error, isPending, refetch } = useQuery({
    queryKey: healthQueryKey,
    queryFn: fetchHealth,
    retry: false,
  });

  if (isPending) {
    return (
      <section className="panel">
        <h2 className="panel__title">{t.title}</h2>
        <p>{t.checking}</p>
      </section>
    );
  }

  if (error || !data) {
    return (
      <section className="panel">
        <h2 className="panel__title">{t.title}</h2>
        <p className="status-badge status-badge--error">
          <span className="status-badge__dot" aria-hidden="true" />
          {t.unreachable}
        </p>
        <button type="button" onClick={() => void refetch()}>
          {t.retry}
        </button>
      </section>
    );
  }

  const isOk = data.status === 'ok';
  return (
    <section className="panel">
      <h2 className="panel__title">{t.title}</h2>
      <p className={`status-badge ${isOk ? 'status-badge--ok' : 'status-badge--degraded'}`}>
        <span className="status-badge__dot" aria-hidden="true" />
        {isOk ? t.ok : t.degraded}
      </p>
      <dl className="detail-grid">
        <dt>{t.environment}</dt>
        <dd>{data.app_env}</dd>
        <dt>{t.version}</dt>
        <dd>{data.version}</dd>
        <dt>{t.checkedAt}</dt>
        <dd>{data.checked_at}</dd>
      </dl>
      <h3 className="panel__title" style={{ marginTop: 'var(--space-4)' }}>
        {t.components}
      </h3>
      <dl className="detail-grid">
        {data.components.map((component) => (
          <Fragment key={component.name}>
            <dt>{component.name === 'database' ? t.database : component.name}</dt>
            <dd>
              {component.healthy ? t.ok : t.degraded} — {component.detail}
              {component.latency_ms !== null ? ` (${t.latency}: ${component.latency_ms} ms)` : ''}
            </dd>
          </Fragment>
        ))}
      </dl>
    </section>
  );
}
