import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { type CropRegion, FULL_REGION, isFullRegion, normaliseRegion } from './cropRegion';
import './ScreenshotCrop.css';

/**
 * Zoom and crop for a chart screenshot (§8, §9).
 *
 * ## Zoom changes nothing
 *
 * A CSS transform on the preview. The file is untouched, no bytes are read,
 * and the artifact that would be analysed is the same one either way. It is
 * presentation, and it is kept strictly separate from crop so that fact stays
 * obvious.
 *
 * ## Crop produces a *derived artifact*, and says so
 *
 * The dangerous version of this feature is a visual-only crop: the user frames
 * a region, the backend analyses the original, and the screen implies
 * otherwise. That is not what happens here.
 *
 * Cropping renders the chosen region to a canvas and produces **new PNG
 * bytes**. Those bytes are what gets uploaded, and Phase 6 treats them as it
 * treats any upload: full size / header / dimension / decode validation, and
 * an identity derived from the bytes themselves. The derived image is a
 * different artifact with a different identity, which is exactly what it is.
 *
 * The original is never mutated and never discarded — both previews stay on
 * screen, and the one that was analysed is labelled with the dimensions the
 * *server* reported, so the claim is checkable rather than asserted.
 *
 * ## Why numeric bounds rather than a drag rectangle
 *
 * A drag-select is the obvious UI and the worse one here. Percentages are
 * exactly reproducible, describable in an audit line, reachable by keyboard
 * without inventing a parallel interaction, and testable without synthesising
 * pointer gestures. The live outline shows the same region a drag would.
 *
 * ## No server-side parent link
 *
 * Phase 6 stores no screenshot, so nothing server-side can record that this
 * derived image came from that original. The relationship is shown in the UI
 * and is **not** persisted; claiming a stored lineage would be inventing the
 * artifact system §8 warns against. Stated rather than implied.
 */

export interface ScreenshotCropProps {
  readonly previewUrl: string;
  readonly slot: string;
  readonly region: CropRegion;
  readonly onRegionChange: (region: CropRegion) => void;
  readonly disabled?: boolean;
}

export function ScreenshotCrop({
  previewUrl,
  slot,
  region,
  onRegionChange,
  disabled = false,
}: ScreenshotCropProps) {
  const baseId = useId();
  // Held as the input's own string. Rendering it directly keeps the one
  // formatting rule in `display.ts` and avoids a local `.toFixed` - which
  // the architecture test correctly refused when this was a number.
  const [zoom, setZoom] = useState('1.0');
  const frameRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (disabled) setZoom('1.0');
  }, [disabled]);

  const box = useMemo(() => normaliseRegion(region), [region]);

  const update = useCallback(
    (patch: Partial<CropRegion>) => {
      onRegionChange(normaliseRegion({ ...box, ...patch }));
    },
    [box, onRegionChange],
  );

  const fields: ReadonlyArray<[keyof CropRegion, string]> = [
    ['left', 'Sol %'],
    ['top', 'Üst %'],
    ['width', 'Genişlik %'],
    ['height', 'Yükseklik %'],
  ];

  return (
    <div className="crop">
      <div className="crop__frame" ref={frameRef}>
        <img
          className="crop__image"
          src={previewUrl}
          alt={`${slot} önizleme`}
          style={{ transform: `scale(${zoom})` }}
        />
        {/* The outline is the same region the numbers describe. It is
            decoration over the preview and never touches the file. */}
        <span
          className="crop__outline"
          aria-hidden="true"
          style={{
            left: `${box.left}%`,
            top: `${box.top}%`,
            width: `${box.width}%`,
            height: `${box.height}%`,
          }}
        />
      </div>

      <div className="crop__controls">
        <div className="crop__zoom">
          <label htmlFor={`${baseId}-zoom`}>
            Yakınlaştırma ({zoom}×)
            <span className="crop__hint">yalnızca görüntüleme; dosyayı değiştirmez</span>
          </label>
          <input
            id={`${baseId}-zoom`}
            type="range"
            min={1}
            max={3}
            step={0.1}
            value={zoom}
            disabled={disabled}
            onChange={(event) => setZoom(event.target.value)}
          />
        </div>

        <fieldset className="crop__region">
          <legend>
            Kırpma alanı
            <span className="crop__hint">
              analiz edilen görüntüyü değiştirir: yeni bir görsel üretilir
            </span>
          </legend>
          <div className="crop__inputs">
            {fields.map(([key, label]) => (
              <div key={key}>
                <label htmlFor={`${baseId}-${key}`}>{label}</label>
                <input
                  id={`${baseId}-${key}`}
                  type="number"
                  min={0}
                  max={100}
                  step={1}
                  value={box[key]}
                  disabled={disabled}
                  onChange={(event) => update({ [key]: Number(event.target.value) })}
                />
              </div>
            ))}
          </div>
          <button
            type="button"
            className="crop__reset"
            disabled={disabled || isFullRegion(box)}
            onClick={() => onRegionChange(FULL_REGION)}
          >
            Tam görüntüye dön
          </button>
        </fieldset>
      </div>
    </div>
  );
}
