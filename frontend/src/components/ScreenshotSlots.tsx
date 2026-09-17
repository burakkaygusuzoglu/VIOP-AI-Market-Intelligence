import { useCallback, useEffect, useRef, useState } from 'react';
import {
  analyseScreenshot,
  submitCorrection,
  type CorrectionDto,
  type ObservationDto,
  type ScreenshotAnalysisDto,
} from '../api/screenshots';
import { ApiError } from '../api/client';
import { cropToFile, FULL_REGION, isFullRegion, type CropRegion } from './cropRegion';
import { ScreenshotCrop } from './ScreenshotCrop';
import { capability } from '../domain/capabilities';
import './ScreenshotSlots.css';

/**
 * Chart screenshot slots and the correction workflow (§15, §16, §17).
 *
 * ## Zoom, crop, and which artifact was analysed
 *
 * Zoom is a CSS transform on the preview. It reads no bytes and changes no
 * artifact.
 *
 * Crop produces a **derived artifact**: new PNG bytes rendered from the chosen
 * region, uploaded in place of the original. Phase 6 already derives an
 * identity from the bytes and validates whatever it is given, so a cropped
 * image is simply a different image with a different identity - not a special
 * case needing a new subsystem.
 *
 * What makes it honest rather than deceptive is that both artifacts stay on
 * screen and the analysed one is labelled with the dimensions **the server
 * reported**, so "what was actually read?" has one answer and it is checkable.
 * The original is never mutated and never discarded.
 *
 * Phase 6 stores no screenshot, so there is no server-side record that this
 * derived image came from that original. The relationship is shown here and is
 * not persisted; claiming a stored lineage would be inventing an artifact
 * system this phase does not have.
 *
 * ## The preview is local and revoked
 *
 * `URL.createObjectURL` leaks until revoked, and a replaced file leaks its
 * predecessor. Every object URL is released when its slot changes or the
 * component unmounts (§37).
 *
 * ## An unconfigured provider is a system state
 *
 * When Vision has no credential the endpoint answers `503
 * VISION_NOT_CONFIGURED`. That is surfaced as exactly that - a system status,
 * with no market meaning - and the capability matrix says so before anyone
 * uploads anything.
 */

const SLOTS = [
  { code: '1D', role: 'Rejim' },
  { code: '1H', role: 'Eğilim' },
  { code: '15M', role: 'Kurulum' },
  { code: '5M', role: 'Giriş' },
] as const;

type SlotCode = (typeof SLOTS)[number]['code'];

type SlotPhase = 'idle' | 'uploading' | 'done' | 'error';

interface SlotState {
  /** Exactly what the user chose. Never mutated, never replaced by a crop. */
  readonly file: File;
  readonly previewUrl: string;
  readonly phase: SlotPhase;
  readonly result?: ScreenshotAnalysisDto;
  readonly error?: string;

  readonly region: CropRegion;
  /** The derived artifact, when a crop was applied. A *different* image with
   *  its own bytes and its own server-assigned identity. */
  readonly analysedFile?: File;
  readonly analysedUrl?: string;
}

const AGREEMENT_LABEL: Record<string, string> = {
  AGREES: 'Beklenen zaman dilimiyle uyumlu',
  MISMATCH: 'UYUŞMUYOR',
  UNDETECTED: 'Zaman dilimi okunamadı',
  UNSUPPORTED_TIMEFRAME: 'Desteklenmeyen zaman dilimi',
};

/**
 * Model-reported extraction confidence, in words.
 *
 * §17: this is how legible the model found the *text on the picture*. It is
 * not a market confidence, not a probability that the reading is correct, and
 * not a probability that a trade will work. `0.74` means the model said 0.74;
 * nothing in this project has calibrated that against anything.
 */
function ConfidenceNote({ value }: { value: string | null }) {
  if (value === null) {
    // Missing stays missing. A model that omitted the field did not thereby
    // express low confidence.
    return <span className="shot__confidence shot__confidence--absent">güven bildirilmedi</span>;
  }
  return (
    <span
      className="shot__confidence"
      title="Modelin okunabilirlik beyanı; piyasa olasılığı değildir."
    >
      okunabilirlik {value} <span className="shot__confidence-note">(model beyanı)</span>
    </span>
  );
}

function ObservationRow({
  observation,
  slot,
  screenshotId,
}: {
  observation: ObservationDto;
  slot: string;
  screenshotId: string;
}) {
  const [outcome, setOutcome] = useState<CorrectionDto | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [replacement, setReplacement] = useState('');

  const send = useCallback(
    async (action: 'CONFIRMED' | 'CORRECTED' | 'REJECTED', corrected?: string) => {
      setError(null);
      try {
        setOutcome(
          await submitCorrection({
            screenshot_id: screenshotId,
            slot,
            field: observation.field,
            replayed_observation: observation.value,
            action,
            ...(corrected !== undefined ? { corrected_value: corrected } : {}),
          }),
        );
        setEditing(false);
      } catch (cause) {
        setError(cause instanceof ApiError ? cause.message : 'Düzeltme gönderilemedi.');
      }
    },
    [observation.field, observation.value, screenshotId, slot],
  );

  return (
    <li className="shot__observation">
      <div className="shot__observation-head">
        <span className="shot__field">{observation.field}</span>
        <span className="shot__value">{observation.value}</span>
        <span className="shot__kind">{observation.kind}</span>
        <ConfidenceNote value={observation.confidence} />
      </div>

      <div className="shot__actions">
        <button type="button" onClick={() => void send('CONFIRMED')}>
          ONAYLA
        </button>
        <button type="button" onClick={() => setEditing((current) => !current)}>
          DÜZELT
        </button>
        <button type="button" onClick={() => void send('REJECTED')}>
          REDDET
        </button>
      </div>

      {editing && (
        <div className="shot__edit">
          <label htmlFor={`fix-${screenshotId}-${observation.field}`}>Doğru değer</label>
          <input
            id={`fix-${screenshotId}-${observation.field}`}
            value={replacement}
            onChange={(event) => setReplacement(event.target.value)}
          />
          <button type="button" onClick={() => void send('CORRECTED', replacement)}>
            Gönder
          </button>
        </div>
      )}

      {error && <p className="shot__error">{error}</p>}

      {outcome && (
        <dl className="shot__outcome">
          <dt>Orijinal gözlem</dt>
          {/* Never overwritten. The original reading survives the correction. */}
          <dd>{outcome.observed_screen_value ?? '—'}</dd>
          <dt>Gözlem kaynağı</dt>
          <dd>{outcome.observed_value_origin}</dd>
          <dt>Sizin değeriniz</dt>
          <dd>{outcome.user_value ?? '—'}</dd>
          <dt>Geçerli değer</dt>
          <dd>{outcome.authoritative_value ?? '—'}</dd>
          <dt>Geçerli kaynak</dt>
          <dd>
            {outcome.authoritative_source ?? '—'}
            {outcome.authoritative_source === 'USER_CONFIRMED' && (
              /* USER_CONFIRMED must never read as STRUCTURED_MARKET_DATA. It
                 outranks a screenshot reading and does not outrank validated
                 market data, and the copy says both. */
              <span className="shot__rank">
                {' '}
                — kullanıcı teyidi: ekran okumasından üstün, doğrulanmış piyasa verisinden üstün
                değil
              </span>
            )}
          </dd>
          {outcome.user_input_was_overridden && (
            <>
              <dt>Not</dt>
              <dd className="shot__overridden">
                Girdiğiniz değer, daha yetkili bir kaynak tarafından geçersiz kılındı.
              </dd>
            </>
          )}
        </dl>
      )}
    </li>
  );
}

function ObservationList({
  observations,
  slot,
  screenshotId,
}: {
  observations: readonly ObservationDto[];
  slot: string;
  screenshotId: string;
}) {
  return (
    <ul className="shot__observations">
      {observations.map((observation) => (
        <ObservationRow
          key={observation.field}
          observation={observation}
          slot={slot}
          screenshotId={screenshotId}
        />
      ))}
    </ul>
  );
}

export interface ScreenshotSlotsProps {
  readonly expectedSymbol: string;
}

export function ScreenshotSlots({ expectedSymbol }: ScreenshotSlotsProps) {
  const [slots, setSlots] = useState<Partial<Record<SlotCode, SlotState>>>({});
  const urls = useRef<string[]>([]);
  const vision = capability('screenshot-analysis');

  // Release every object URL this component created.
  useEffect(
    () => () => {
      urls.current.forEach((url) => URL.revokeObjectURL(url));
      urls.current = [];
    },
    [],
  );

  const track = useCallback((url: string) => {
    urls.current.push(url);
    return url;
  }, []);

  /**
   * Upload one artifact and record the outcome.
   *
   * `analysed` is what actually goes to the backend: the original, or the
   * derived crop. It is kept on the slot so the screen can name the artifact
   * that was read rather than the one that was chosen.
   */
  const send = useCallback(
    async (code: SlotCode, analysed: File) => {
      try {
        const result = await analyseScreenshot(code, analysed, expectedSymbol);
        setSlots((current) => {
          const existing = current[code];
          if (!existing) return current;
          return { ...current, [code]: { ...existing, phase: 'done', result } };
        });
      } catch (cause) {
        const message =
          cause instanceof ApiError ? cause.message : 'Görüntü analizi tamamlanamadı.';
        setSlots((current) => {
          const existing = current[code];
          if (!existing) return current;
          return { ...current, [code]: { ...existing, phase: 'error', error: message } };
        });
      }
    },
    [expectedSymbol],
  );

  const choose = useCallback(
    async (code: SlotCode, file: File) => {
      setSlots((current) => {
        const previous = current[code];
        if (previous) {
          URL.revokeObjectURL(previous.previewUrl);
          if (previous.analysedUrl) URL.revokeObjectURL(previous.analysedUrl);
        }
        const previewUrl = track(URL.createObjectURL(file));
        return {
          ...current,
          [code]: { file, previewUrl, phase: 'uploading', region: FULL_REGION },
        };
      });
      await send(code, file);
    },
    [send, track],
  );

  /**
   * Re-analyse a slot using the current crop region.
   *
   * The derived bytes are produced here and uploaded here, so the image the
   * backend validates is exactly the image the preview shows. There is no path
   * by which a region is chosen and the original is analysed instead.
   */
  const applyCrop = useCallback(
    async (code: SlotCode) => {
      const existing = slots[code];
      if (!existing) return;

      setSlots((current) => {
        const slot = current[code];
        if (!slot) return current;
        return { ...current, [code]: { ...slot, phase: 'uploading' } };
      });

      let derived: File;
      try {
        derived = isFullRegion(existing.region)
          ? existing.file
          : await cropToFile(existing.file, existing.region);
      } catch {
        setSlots((current) => {
          const slot = current[code];
          if (!slot) return current;
          return {
            ...current,
            [code]: { ...slot, phase: 'error', error: 'Görüntü kırpılamadı.' },
          };
        });
        return;
      }

      setSlots((current) => {
        const slot = current[code];
        if (!slot) return current;
        if (slot.analysedUrl) URL.revokeObjectURL(slot.analysedUrl);
        const cropped = derived !== slot.file;
        return {
          ...current,
          [code]: {
            ...slot,
            ...(cropped
              ? { analysedFile: derived, analysedUrl: track(URL.createObjectURL(derived)) }
              : { analysedFile: undefined, analysedUrl: undefined }),
          },
        };
      });

      await send(code, derived);
    },
    [slots, send, track],
  );

  const remove = useCallback((code: SlotCode) => {
    setSlots((current) => {
      const previous = current[code];
      if (previous) {
        URL.revokeObjectURL(previous.previewUrl);
        if (previous.analysedUrl) URL.revokeObjectURL(previous.analysedUrl);
      }
      return Object.fromEntries(Object.entries(current).filter(([key]) => key !== code));
    });
  }, []);

  return (
    <section className="shots" aria-labelledby="shots-heading">
      <h3 className="shots__heading" id="shots-heading">
        Grafik ekran görüntüleri (isteğe bağlı)
      </h3>

      {vision?.state !== 'AVAILABLE_NOW' && (
        <p className="shots__unconfigured">
          Bu kurulumda görüntü modeli yapılandırılmamış. Yükleyebilirsiniz, ancak sunucu tipli bir
          <code> VISION_NOT_CONFIGURED</code> durumu döndürür. Bu bir sistem durumudur; piyasa
          hakkında bir şey söylemez ve deterministik analizi etkilemez.
        </p>
      )}

      {/* §5-C: Vision does not feed the deterministic analysis in this phase,
          and the screen must not imply that it does. The section sits in the
          Analyze Market form, so without this a user would reasonably assume
          uploading a chart changes the result. It does not. */}
      <p className="shots__scope" role="note">
        <strong>Bu okumalar analizi etkilemez.</strong> Ekran görüntüsü okuma ve düzeltme, bu fazda
        ayrı bir araçtır: deterministik analiz yalnızca yüklediğiniz OHLCV verisinden hesaplanır.
        Buradan okunan hiçbir değer kanıtlara, riske veya senteze girmez.
      </p>

      {/* This note used to say no crop is ever applied. That was true when it
          was written and stopped being true when the crop shipped, so it now
          states the guarantee that actually holds: a crop produces a separate
          file, and whichever file was read is named per slot below. */}
      <p className="shots__note">
        Kırparsanız, kırpılmış görüntü ayrı bir dosya olarak yüklenir; özgün dosyanız değişmez ve
        gönderilmez. Her görüntünün altında hangi dosyanın okunduğu adıyla ve sunucunun bildirdiği
        boyutlarıyla yazar. Yakınlaştırma yalnızca önizlemedir, yüklenen veriyi değiştirmez.
        Görüntüden okunan hiçbir değer hesaplanmış veri yerine geçmez.
      </p>

      <ul className="shots__list">
        {SLOTS.map((slot) => {
          const state = slots[slot.code];
          return (
            <li key={slot.code} className="shot">
              <div className="shot__label">
                <strong>{slot.code}</strong>
                <span>{slot.role}</span>
              </div>

              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                aria-label={`${slot.code} (${slot.role}) ekran görüntüsü`}
                onChange={(event) => {
                  const chosen = event.target.files?.[0];
                  if (chosen) void choose(slot.code, chosen);
                }}
              />

              {state && (
                <div className="shot__body">
                  <div className="shot__preview">
                    {/* The filename is rendered as text, never as markup. */}
                    <img src={state.previewUrl} alt={`${slot.code} önizleme`} />
                    <span className="shot__filename">{state.file.name}</span>
                    <button type="button" onClick={() => remove(slot.code)}>
                      Kaldır
                    </button>
                  </div>

                  <ScreenshotCrop
                    previewUrl={state.previewUrl}
                    slot={slot.code}
                    region={state.region}
                    disabled={state.phase === 'uploading'}
                    onRegionChange={(region) =>
                      setSlots((current) => {
                        const existing = current[slot.code];
                        if (!existing) return current;
                        return { ...current, [slot.code]: { ...existing, region } };
                      })
                    }
                  />

                  <button
                    type="button"
                    className="shot__reanalyse"
                    disabled={state.phase === 'uploading'}
                    onClick={() => void applyCrop(slot.code)}
                  >
                    {isFullRegion(state.region)
                      ? 'Tam görüntüyü yeniden analiz et'
                      : 'Kırpılmış görüntüyü analiz et'}
                  </button>

                  {/* Which artifact was actually read. Labelled with the
                      dimensions the *server* reported, so the claim is
                      checkable rather than asserted. */}
                  {state.phase === 'done' && state.result && (
                    <p className="shot__analysed">
                      {state.analysedFile ? (
                        <>
                          <strong>Analiz edilen:</strong> kırpılmış görüntü (
                          <span className="shot__filename">{state.analysedFile.name}</span>,{' '}
                          {state.result.width}×{state.result.height}). Orijinal dosya değişmedi ve
                          yukarıda duruyor.
                        </>
                      ) : (
                        <>
                          <strong>Analiz edilen:</strong> yüklediğiniz orijinal görüntü (
                          {state.result.width}×{state.result.height}).
                        </>
                      )}
                    </p>
                  )}

                  {state.phase === 'uploading' && <p className="shot__status">Analiz ediliyor…</p>}

                  {state.phase === 'error' && (
                    <p className="shot__status shot__status--error" role="alert">
                      Sistem durumu: {state.error}
                    </p>
                  )}

                  {state.phase === 'done' && state.result && (
                    <div className="shot__result">
                      {/* Dimensions live on the "analysed artifact" line and
                          nowhere else. Printing them here too meant the same
                          numbers appeared twice, which invites the reader to
                          wonder whether they describe two different things. */}
                      <p className="shot__meta">
                        {state.result.image_format} · kalite{' '}
                        {state.result.quality.score === null
                          ? '—'
                          : `${state.result.quality.score} / 100`}
                      </p>
                      <p
                        className={`shot__agreement shot__agreement--${state.result.timeframe_agreement.toLowerCase()}`}
                      >
                        Algılanan zaman dilimi: {state.result.detected_timeframe ?? '—'} ·{' '}
                        {AGREEMENT_LABEL[state.result.timeframe_agreement] ??
                          state.result.timeframe_agreement}
                      </p>

                      {state.result.mismatches.length > 0 && (
                        <ul className="shot__mismatches" role="alert">
                          {state.result.mismatches.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                      )}

                      {state.result.unreadable.length > 0 && (
                        <p className="shot__unreadable">
                          Okunamayan alanlar: {state.result.unreadable.join(', ')}
                        </p>
                      )}

                      {state.result.observations.length > 0 && (
                        <ObservationList
                          observations={state.result.observations}
                          slot={slot.code}
                          screenshotId={state.result.screenshot_id}
                        />
                      )}
                    </div>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
