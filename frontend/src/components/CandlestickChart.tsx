import { useId, useMemo } from 'react';
import type { Candle, ChartSeries, Zone } from '../domain/models';
import { formatValue } from '../format/display';
import './CandlestickChart.css';

/**
 * A real candlestick chart, drawn from real candles (§24, §25).
 *
 * ## Why SVG rather than a charting library
 *
 * §24 forbids a fake chart made of decorative divs and suggests a library such
 * as Lightweight Charts. This draws genuine OHLC geometry from the DTO's own
 * bars — a wick from high to low and a body from open to close, positioned by
 * a linear scale over the actual price range — so it is not the fake the rule
 * is about. Choosing SVG over a dependency was a judgement with a cost:
 *
 * * what Phase 8 needs is candles, support/resistance bands and a timeframe
 *   switch. It does not need pan, zoom, crosshairs, drawing tools or a
 *   streaming API, which is most of what a charting library is for;
 * * a canvas chart is opaque to assistive technology and to tests. Every bar
 *   here is a DOM node, so §25's textual alternative is the same data rather
 *   than a parallel description that can drift;
 * * the project's dependency rule asks for a stated reason, and "we need three
 *   of its forty features" is a weak one.
 *
 * If pan/zoom or indicator overlays become requirements, this should be
 * revisited rather than grown — that is the point at which a library earns its
 * place. Recorded as a limitation, not as a claim of parity.
 *
 * ## It cannot invent a price
 *
 * The only numbers this component reads are `candle.open/high/low/close` and
 * zone bounds. It computes **pixel positions** from them, which is geometry,
 * not finance: no indicator, no average, no derived price is produced, and
 * nothing it draws can appear that was not in the payload. The scale is
 * deliberately the plain min/max of the supplied bars.
 */

const VIEW_WIDTH = 1000;
const VIEW_HEIGHT = 360;
const PADDING = { top: 12, right: 56, bottom: 24, left: 8 };

interface Scaled {
  readonly x: number;
  readonly openY: number;
  readonly highY: number;
  readonly lowY: number;
  readonly closeY: number;
  readonly rising: boolean;
  readonly candle: Candle;
}

/**
 * Parse a decimal string for *drawing only*.
 *
 * This is the one place a price becomes a JavaScript number, and it is safe
 * because the result is a pixel coordinate that is thrown away on the next
 * render. Nothing computed here is ever displayed as a value, stored, or sent
 * back — every number the user reads comes from `display.ts` operating on the
 * exact string.
 */
function toPixelNumber(value: string): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function useGeometry(series: ChartSeries | null) {
  return useMemo(() => {
    const candles = series?.candles ?? [];
    if (candles.length === 0) {
      return {
        bars: [] as Scaled[],
        min: 0,
        max: 0,
        bandWidth: 0,
        zones: [] as Zone[],
        highText: '',
        lowText: '',
      };
    }

    // The *displayed* extremes are the exact backend strings from the bars
    // that hold them. Only the selection uses a parsed number; the value the
    // user reads is never float-derived, and never re-rounded here (§5).
    let highest = candles[0] as Candle;
    let lowest = candles[0] as Candle;
    for (const candle of candles) {
      if (toPixelNumber(candle.high) > toPixelNumber(highest.high)) highest = candle;
      if (toPixelNumber(candle.low) < toPixelNumber(lowest.low)) lowest = candle;
    }
    const rawMin = toPixelNumber(lowest.low);
    const rawMax = toPixelNumber(highest.high);
    // A flat series would divide by zero; pad it so the line sits mid-plot.
    const pad = rawMax === rawMin ? Math.max(Math.abs(rawMax) * 0.01, 1) : (rawMax - rawMin) * 0.05;
    const min = rawMin - pad;
    const max = rawMax + pad;

    const plotWidth = VIEW_WIDTH - PADDING.left - PADDING.right;
    const plotHeight = VIEW_HEIGHT - PADDING.top - PADDING.bottom;
    const bandWidth = plotWidth / candles.length;

    const y = (price: number) => PADDING.top + ((max - price) / (max - min)) * plotHeight;

    const bars = candles.map((candle, index): Scaled => {
      const open = toPixelNumber(candle.open);
      const close = toPixelNumber(candle.close);
      return {
        x: PADDING.left + index * bandWidth + bandWidth / 2,
        openY: y(open),
        highY: y(toPixelNumber(candle.high)),
        lowY: y(toPixelNumber(candle.low)),
        closeY: y(close),
        rising: close >= open,
        candle,
      };
    });

    return {
      bars,
      min,
      max,
      bandWidth,
      zones: series?.zones ?? [],
      y,
      highText: highest.high,
      lowText: lowest.low,
    };
  }, [series]);
}

/**
 * Split an overlay into drawable runs.
 *
 * A `null` is a candle the indicator had no value for - its warm-up had not
 * elapsed. The line stops and restarts rather than spanning the gap: a segment
 * drawn across missing values would depict a number that was never computed.
 */
function overlaySegments(
  values: readonly (string | null)[],
  bars: readonly Scaled[],
  y: (price: number) => number,
): string[] {
  const segments: string[] = [];
  let current: string[] = [];

  values.forEach((value, index) => {
    const bar = bars[index];
    if (value === null || bar === undefined) {
      if (current.length > 1) segments.push(current.join(' '));
      current = [];
      return;
    }
    current.push(`${bar.x},${y(toPixelNumber(value))}`);
  });
  if (current.length > 1) segments.push(current.join(' '));
  return segments;
}

export interface CandlestickChartProps {
  readonly series: ChartSeries | null;
  readonly symbol: string;
}

export function CandlestickChart({ series, symbol }: CandlestickChartProps) {
  const titleId = useId();
  const descId = useId();
  const geometry = useGeometry(series);

  if (!series || series.candles.length === 0) {
    return (
      <div className="chart chart--empty">
        <p className="chart__empty">Bu zaman dilimi için çizilecek mum yok.</p>
      </div>
    );
  }

  const { bars, bandWidth, zones, highText, lowText } = geometry;
  const y = geometry.y as (price: number) => number;
  const first = series.candles[0];
  const last = series.candles[series.candles.length - 1];
  const bodyWidth = Math.max(bandWidth * 0.6, 1);

  return (
    <div className="chart">
      <svg
        className="chart__svg"
        viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
        preserveAspectRatio="none"
        role="img"
        aria-labelledby={`${titleId} ${descId}`}
      >
        {/* One template string, not four children: React treats a `<title>`
            with multiple children as an error because a title element can only
            hold text, and it warned about exactly this. */}
        <title id={titleId}>{`${symbol} ${series.timeframe} mum grafiği`}</title>
        {/* The description is the accessible summary §25 requires. It is not
            the *only* place this information exists - the figure caption below
            repeats it as visible text, so it cannot drift unnoticed. */}
        <desc id={descId}>
          {`${series.candles.length} mum. En düşük ${formatValue(lowText, 'price')}, ` +
            `en yüksek ${formatValue(highText, 'price')}.` +
            (zones.length > 0 ? ` ${zones.length} destek/direnç bölgesi.` : '')}
        </desc>

        {zones.map((zone) => {
          const upper = y(toPixelNumber(zone.upper));
          const lower = y(toPixelNumber(zone.lower));
          return (
            <rect
              key={zone.id}
              className={`chart__zone chart__zone--${zone.kind.toLowerCase()}`}
              x={PADDING.left}
              y={Math.min(upper, lower)}
              width={VIEW_WIDTH - PADDING.left - PADDING.right}
              height={Math.max(Math.abs(lower - upper), 1)}
            />
          );
        })}

        {/* §11: indicator overlays, drawn from the backend's own per-bar
            series. The values are Phase 1's, sliced to exactly this window, so
            a point never appears beside a bar that is not drawn - and nothing
            here recomputes an average. A gap in the series (warm-up) breaks
            the line rather than being interpolated across, because a drawn
            segment there would be a value nobody calculated. */}
        {series.overlays.map((overlay, overlayIndex) => (
          <g key={overlay.key} className={`chart__overlay chart__overlay--${overlayIndex % 4}`}>
            {overlaySegments(overlay.values, bars, y).map((segment, index) => (
              <polyline key={index} className="chart__overlay-line" points={segment} />
            ))}
          </g>
        ))}

        {bars.map((bar) => (
          <g
            key={bar.candle.openTime}
            className={`chart__candle chart__candle--${bar.rising ? 'up' : 'down'}`}
            data-forming={bar.candle.isClosed ? undefined : 'true'}
          >
            <line className="chart__wick" x1={bar.x} x2={bar.x} y1={bar.highY} y2={bar.lowY} />
            <rect
              className="chart__body"
              x={bar.x - bodyWidth / 2}
              y={Math.min(bar.openY, bar.closeY)}
              width={bodyWidth}
              height={Math.max(Math.abs(bar.closeY - bar.openY), 1)}
            />
          </g>
        ))}
      </svg>

      {/* §25: the critical information exists outside the graphic. A reader who
          cannot see the chart still gets the range, the period and the zones,
          and no action or risk decision depends on reading the picture. */}
      <figcaption className="chart__caption">
        <span>
          {series.candles.length} mum · {first?.openTime.slice(0, 10)} →{' '}
          {last?.openTime.slice(0, 10)} · aralık {formatValue(lowText, 'price')}–
          {formatValue(highText, 'price')}
          {/* §4: the drawn window is not the analysed dataset, and the caption
              says so rather than describing itself as the whole picture. An
              omitted bar is absent - it is never summarised into a synthetic
              candle - and it still moves the analysis. */}
          {series.omittedCount > 0 && (
            <>
              {' · '}
              <span className="chart__window">
                {series.analysedCount} mum analiz edildi; en son {series.candles.length} tanesi
                çiziliyor ({series.omittedCount} mum çizilmiyor, özetlenmiyor)
              </span>
            </>
          )}
        </span>
        <span className="chart__legend">
          <span className="chart__swatch chart__swatch--support" aria-hidden="true" /> destek
          <span className="chart__swatch chart__swatch--resistance" aria-hidden="true" /> direnç
          {series.overlays.map((overlay, index) => (
            <span key={overlay.key} className="chart__legend-item">
              <span
                className={`chart__swatch chart__swatch--overlay chart__swatch--${index % 4}`}
                aria-hidden="true"
              />
              {overlay.label}
            </span>
          ))}
        </span>
      </figcaption>
    </div>
  );
}
