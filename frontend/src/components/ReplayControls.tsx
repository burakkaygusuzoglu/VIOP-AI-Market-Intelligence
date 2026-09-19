import { useId, useState } from 'react';
import { PLAYBACK_SPEEDS, type PlaybackSpeed } from '../domain/replay';

/**
 * Replay transport controls (Phase 11).
 *
 * Every control here issues a *command*. None of them carries a market time, a
 * cursor or a candle, and the speed selector is not sent anywhere at all: it
 * changes how long the browser waits between identical commands, which is a
 * presentation choice, not a market one.
 *
 * Play and Pause are conveniences over the same step. Pause stops issuing
 * commands; there is nothing on the server to pause, because a replay only
 * moves when it is asked to.
 */

export interface ReplayControlsProps {
  readonly atEnd: boolean;
  readonly busy: boolean;
  readonly playing: boolean;
  readonly speed: PlaybackSpeed;
  readonly maxSteps: number;
  readonly onStep: (steps: number) => void;
  readonly onPlay: () => void;
  readonly onPause: () => void;
  readonly onSpeed: (speed: PlaybackSpeed) => void;
  readonly onAnalyse: () => void;
  readonly onMeasure: () => void;
  readonly onClose: () => void;
}

export function ReplayControls(props: ReplayControlsProps) {
  const speedId = useId();
  const stepsId = useId();
  const [steps, setSteps] = useState(10);
  const bounded = Math.max(1, Math.min(steps, props.maxSteps));

  return (
    <section className="replay-controls" aria-labelledby="replay-controls-heading">
      <h3 id="replay-controls-heading">Geçmişe sarma kontrolleri</h3>

      {props.atEnd && (
        <p className="replay-controls__end" role="status">
          Bu veri kümesindeki her mum açıklandı. Aynı veriyi yeniden izlemek için yeni bir oturum
          başlatın; geçmişe sarma yalnızca ileri gider.
        </p>
      )}

      <div className="replay-controls__row">
        <button
          type="button"
          className="replay-controls__step"
          disabled={props.atEnd || props.busy}
          onClick={() => props.onStep(1)}
        >
          BİR MUM İLERLE
        </button>

        {props.playing ? (
          <button type="button" onClick={props.onPause}>
            DURAKLAT
          </button>
        ) : (
          <button type="button" disabled={props.atEnd} onClick={props.onPlay}>
            OYNAT
          </button>
        )}

        <label htmlFor={speedId}>Oynatma hızı</label>
        <select
          id={speedId}
          value={props.speed}
          onChange={(event) => props.onSpeed(Number(event.target.value) as PlaybackSpeed)}
        >
          {PLAYBACK_SPEEDS.map((value) => (
            <option key={value} value={value}>
              {value}x
            </option>
          ))}
        </select>
      </div>

      <p className="replay-controls__note">
        Oynatma hızı yalnızca komutlar arasındaki bekleme süresini değiştirir. Aynı komut dizisi,
        hangi hızda izlenirse izlensin aynı mumları, aynı dolumları ve aynı sonuçları üretir.
      </p>

      <div className="replay-controls__row">
        <label htmlFor={stepsId}>Kaç mum</label>
        <input
          id={stepsId}
          type="number"
          min={1}
          max={props.maxSteps}
          value={steps}
          onChange={(event) => setSteps(Number(event.target.value))}
        />
        <button
          type="button"
          disabled={props.atEnd || props.busy}
          onClick={() => props.onStep(bounded)}
        >
          {bounded} MUM İLERLE
        </button>
        <span className="replay-controls__limit">En fazla {props.maxSteps} mum</span>
      </div>

      <div className="replay-controls__row">
        <button type="button" disabled={props.busy} onClick={props.onAnalyse}>
          BU ANI ANALİZ ET
        </button>
        <button type="button" disabled={props.busy} onClick={props.onMeasure}>
          OTURUM PERFORMANSI
        </button>
        <button type="button" onClick={props.onClose}>
          OTURUM LİSTESİ
        </button>
      </div>
      <p className="replay-controls__note">
        Analiz yalnızca siz istediğinizde çalışır: ilerlemek tek başına analiz üretmez ve hiçbir dil
        modeli çağrısı başlatmaz.
      </p>
    </section>
  );
}
