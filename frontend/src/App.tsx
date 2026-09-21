import { useCallback, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { requestAnalysis, type AnalysisRequestPayload } from './api/analysis';
import { ApiError } from './api/client';
import { mapAnalysis } from './api/mapAnalysis';
import { ModeToggle } from './components/ModeToggle';
import type { AnalysisReadModel } from './domain/models';
import { AnalysisWorkspace, type ExperienceMode } from './screens/AnalysisWorkspace';
import { AnalyzeMarket } from './screens/AnalyzeMarket';
import { Dashboard } from './screens/Dashboard';
import { PaperTrading } from './screens/PaperTrading';
import { PerformanceScreen } from './screens/Performance';
import { BacktestScreen } from './screens/Backtest';
import { ReplayScreen } from './screens/Replay';
import { useTranslations } from './i18n';

/**
 * The workstation shell.
 *
 * Phase 8A's shell rendered one honest "no analysis exists" state, with a
 * failing test standing in for the query nobody could write yet. Phase 8 wires
 * `POST /api/analysis`, so that tripwire has been paid off: this component now
 * makes the real request.
 *
 * ## Navigation without a router, deliberately
 *
 * Three screens, and the analysis is **ephemeral**: it exists only in this
 * component's state and cannot survive a reload. A router would give each
 * screen a URL, and a URL for the workspace would be a promise the system
 * cannot keep - a bookmark or a refresh would land on an empty page that looks
 * broken. So navigation is a state machine, the workspace has no address, and
 * the screen says out loud that the result is not saved.
 *
 * Worth revisiting the moment analyses become persistent; until then a routing
 * dependency would buy an address for something with no permanent existence.
 *
 * ## A stale response cannot overwrite a newer one (§34)
 *
 * Phase 8A had no analysis request and correctly deferred this. Now there is
 * one, and two are easy to have in flight: submit, change a file, submit again.
 * If the first finishes second, its result must not replace the second's.
 *
 * Each submission takes a sequence number and an `AbortController`. Starting a
 * request aborts the previous one, and a result is written to state only if its
 * ticket is still the newest - so a slow answer is both cancelled and, should
 * it arrive anyway, ignored.
 */

type Screen =
  'dashboard' | 'analyze' | 'workspace' | 'paper' | 'performance' | 'replay' | 'backtest';

export function App() {
  const t = useTranslations();
  const [mode, setMode] = useState<ExperienceMode>('BEGINNER');
  const [screen, setScreen] = useState<Screen>('dashboard');
  const [analysis, setAnalysis] = useState<AnalysisReadModel | null>(null);

  const latestTicket = useRef(0);
  const inFlight = useRef<AbortController | null>(null);

  const mutation = useMutation({
    mutationFn: async (payload: AnalysisRequestPayload) => {
      inFlight.current?.abort();
      const controller = new AbortController();
      inFlight.current = controller;

      const ticket = ++latestTicket.current;
      const dto = await requestAnalysis(payload, controller.signal);
      return { ticket, model: mapAnalysis(dto) };
    },
    onSuccess: (result) => {
      // The guard. A response from an older submission is discarded rather
      // than rendered over a newer one.
      if (result.ticket !== latestTicket.current) return;
      setAnalysis(result.model);
      setScreen('workspace');
    },
  });

  const { mutate, reset } = mutation;

  const analyse = useCallback(
    (payload: AnalysisRequestPayload) => {
      mutate(payload);
    },
    [mutate],
  );

  const cancel = useCallback(() => {
    inFlight.current?.abort();
    // Invalidate whatever is outstanding, so a late success cannot land.
    latestTicket.current += 1;
    reset();
  }, [reset]);

  const error =
    mutation.error instanceof ApiError
      ? mutation.error.message
      : mutation.error
        ? 'Analiz isteği tamamlanamadı.'
        : null;

  return (
    <main className="app">
      <header className="app__header">
        <div>
          <h1 className="app__title">{t.appName}</h1>
          <p className="app__tagline">{t.tagline}</p>
        </div>
        <div className="app__header-controls">
          {screen !== 'dashboard' && (
            <button
              type="button"
              onClick={() => {
                cancel();
                setScreen('dashboard');
              }}
            >
              Panele dön
            </button>
          )}
          <ModeToggle mode={mode} onChange={setMode} />
        </div>
      </header>

      {screen === 'dashboard' && (
        <Dashboard
          onAnalyse={() => setScreen('analyze')}
          onPaper={() => setScreen('paper')}
          onPerformance={() => setScreen('performance')}
          onReplay={() => setScreen('replay')}
          onBacktest={() => setScreen('backtest')}
        />
      )}

      {screen === 'paper' && <PaperTrading mode={mode} />}

      {screen === 'replay' && <ReplayScreen mode={mode} onBack={() => setScreen('dashboard')} />}

      {screen === 'backtest' && <BacktestScreen mode={mode} />}

      {screen === 'performance' && (
        <PerformanceScreen mode={mode} onBack={() => setScreen('dashboard')} />
      )}

      {screen === 'analyze' && (
        <AnalyzeMarket
          onAnalyse={analyse}
          onCancel={cancel}
          busy={mutation.isPending}
          error={error}
        />
      )}

      {screen === 'workspace' && analysis && (
        <AnalysisWorkspace
          analysis={analysis}
          mode={mode}
          onBack={() => {
            setAnalysis(null);
            setScreen('analyze');
          }}
        />
      )}

      <section className="panel">
        <h2 className="panel__title">{t.executionMode.title}</h2>
        <p className="status-badge status-badge--degraded">
          <span className="status-badge__dot" aria-hidden="true" />
          {t.executionMode.value}
        </p>
        <p className="notice notice--warning">{t.executionMode.detail}</p>
      </section>
    </main>
  );
}
