import { useId, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  getBreakdowns,
  getJournalPage,
  getPerformance,
  getTags,
  type PerformanceFilters,
} from '../api/performance';
import { JournalPanel } from '../components/JournalPanel';
import { PerformanceBreakdowns } from '../components/PerformanceBreakdowns';
import { PerformanceOverview } from '../components/PerformanceOverview';
import type { ExperienceMode } from '../components/AnalysisDashboard';
import '../components/Performance.css';

/**
 * The performance and journal workspace (Phase 10).
 *
 * One filter state drives all three requests, so the headline, the breakdowns
 * and the journal always describe the same population - a filtered chart beside
 * unfiltered headline numbers would be a lie told by layout.
 *
 * Every number on this screen arrives from the server. The screen decides only
 * what to call things and where to put them.
 */

type Tab = 'overview' | 'breakdowns' | 'journal';

const TAB_LABEL: Record<Tab, string> = {
  overview: 'Genel bakış',
  breakdowns: 'Kırılımlar',
  journal: 'Günlük',
};

export interface PerformanceScreenProps {
  readonly mode: ExperienceMode;
  readonly onBack: () => void;
}

export function PerformanceScreen({ mode, onBack }: PerformanceScreenProps) {
  const [filters, setFilters] = useState<PerformanceFilters>({});
  const [tab, setTab] = useState<Tab>('overview');
  const directionId = useId();
  const timeframeId = useId();
  const symbolId = useId();
  const tagId = useId();
  const fromId = useId();
  const toId = useId();

  const key = ['paper-performance', filters] as const;
  const performance = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => getPerformance(filters, signal),
  });
  const breakdowns = useQuery({
    queryKey: ['paper-breakdowns', filters] as const,
    queryFn: ({ signal }) => getBreakdowns(filters, signal),
    enabled: tab === 'breakdowns',
  });
  const journal = useQuery({
    queryKey: ['paper-journal', filters] as const,
    queryFn: ({ signal }) => getJournalPage(filters, 0, signal),
    enabled: tab === 'journal',
  });
  const tags = useQuery({ queryKey: ['paper-tags'], queryFn: ({ signal }) => getTags(signal) });

  /** Merge a change and drop cleared fields, so an empty control means "all". */
  const update = (change: Record<string, string | undefined>) =>
    setFilters((current) =>
      Object.fromEntries(
        Object.entries({ ...current, ...change }).filter(
          ([, value]) => value !== '' && value !== undefined,
        ),
      ),
    );

  return (
    <div className="perf-screen">
      <section className="perf-screen__banner" role="note" aria-label="Simülasyon uyarısı">
        <p className="perf-screen__banner-title">KAĞIT İŞLEM PERFORMANSI — SİMÜLASYON</p>
        <p>
          Buradaki her rakam, kendi yüklediğiniz çubuklar üzerinde çalıştırılan simülasyonun
          sonucudur. Borsa ya da aracı kurum tarafından doğrulanmış bir işlem geçmişi değildir ve
          gelecekteki sonuçlar hakkında bir şey söylemez.
        </p>
      </section>

      <div className="perf-screen__row">
        <button type="button" onClick={onBack}>
          Panoya dön
        </button>
      </div>

      <form className="perf-filters" aria-label="Filtreler">
        <p className="perf-filters__rule">
          Tarih aralığı, işlemin <strong>piyasa zamanındaki kapanışına</strong> göre uygulanır ve üç
          görünüme de aynı şekilde yansır.
        </p>
        <div className="perf-filters__grid">
          <div>
            <label htmlFor={fromId}>Başlangıç (UTC)</label>
            <input
              id={fromId}
              type="date"
              value={filters.from?.slice(0, 10) ?? ''}
              onChange={(event) =>
                update({ from: event.target.value ? `${event.target.value}T00:00:00Z` : undefined })
              }
            />
          </div>
          <div>
            <label htmlFor={toId}>Bitiş (UTC)</label>
            <input
              id={toId}
              type="date"
              value={filters.to?.slice(0, 10) ?? ''}
              onChange={(event) =>
                update({ to: event.target.value ? `${event.target.value}T23:59:59Z` : undefined })
              }
            />
          </div>
          <div>
            <label htmlFor={directionId}>Yön</label>
            <select
              id={directionId}
              value={filters.direction ?? ''}
              onChange={(event) =>
                update({ direction: (event.target.value || undefined) as 'LONG' | 'SHORT' })
              }
            >
              <option value="">Hepsi</option>
              <option value="LONG">Uzun (LONG)</option>
              <option value="SHORT">Kısa (SHORT)</option>
            </select>
          </div>
          <div>
            <label htmlFor={timeframeId}>Zaman dilimi</label>
            <select
              id={timeframeId}
              value={filters.timeframe ?? ''}
              onChange={(event) =>
                update({
                  timeframe: (event.target.value || undefined) as '5M' | '15M' | '1H' | '1D',
                })
              }
            >
              <option value="">Hepsi</option>
              <option value="5M">5M</option>
              <option value="15M">15M</option>
              <option value="1H">1H</option>
              <option value="1D">1D</option>
            </select>
          </div>
          <div>
            <label htmlFor={symbolId}>Sembol</label>
            <input
              id={symbolId}
              value={filters.symbol ?? ''}
              onChange={(event) => update({ symbol: event.target.value || undefined })}
            />
          </div>
          <div>
            <label htmlFor={tagId}>Etiket</label>
            <select
              id={tagId}
              value={filters.tag ?? ''}
              onChange={(event) => update({ tag: event.target.value || undefined })}
            >
              <option value="">Hepsi</option>
              {(tags.data?.items ?? []).map((item) => (
                <option key={item.tag} value={item.tag}>
                  {item.tag} ({item.positions})
                </option>
              ))}
            </select>
          </div>
        </div>
      </form>

      <div className="perf-tabs" role="tablist" aria-label="Performans görünümleri">
        {(Object.keys(TAB_LABEL) as Tab[]).map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            id={`perf-tab-${name}`}
            aria-selected={tab === name}
            aria-controls={`perf-panel-${name}`}
            className={tab === name ? 'perf-tab perf-tab--active' : 'perf-tab'}
            onClick={() => setTab(name)}
          >
            {TAB_LABEL[name]}
          </button>
        ))}
      </div>

      {performance.isError && (
        <p className="perf-error" role="alert">
          {performance.error instanceof Error ? performance.error.message : 'Performans okunamadı.'}
        </p>
      )}

      <div
        id={`perf-panel-${tab}`}
        role="tabpanel"
        aria-labelledby={`perf-tab-${tab}`}
        tabIndex={-1}
      >
        {tab === 'overview' &&
          (performance.data ? (
            <PerformanceOverview dto={performance.data} mode={mode} />
          ) : (
            <p className="perf-empty">{performance.isLoading ? 'Yükleniyor…' : ''}</p>
          ))}

        {tab === 'breakdowns' &&
          (breakdowns.data ? (
            <PerformanceBreakdowns dto={breakdowns.data} />
          ) : (
            <p className="perf-empty">{breakdowns.isLoading ? 'Yükleniyor…' : ''}</p>
          ))}

        {tab === 'journal' &&
          (journal.data ? (
            <JournalPanel rows={journal.data.items} total={journal.data.total} />
          ) : (
            <p className="perf-empty">{journal.isLoading ? 'Yükleniyor…' : ''}</p>
          ))}
      </div>
    </div>
  );
}
