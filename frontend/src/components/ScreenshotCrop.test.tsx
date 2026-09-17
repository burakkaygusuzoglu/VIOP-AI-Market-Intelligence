import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { FULL_REGION, isFullRegion, normaliseRegion, type CropRegion } from './cropRegion';
import { ScreenshotCrop } from './ScreenshotCrop';

/**
 * Crop and zoom safety (§8, §9).
 *
 * The bounds arithmetic is tested directly because it is what decides which
 * pixels become the derived artifact. `cropToFile` itself needs a real canvas
 * and a real image decoder, neither of which jsdom implements — that is stated
 * as a limitation rather than faked with a stub that would only prove the stub
 * works.
 */

describe('crop bounds can never describe an impossible region', () => {
  it('clamps a negative origin', () => {
    expect(normaliseRegion({ left: -50, top: -10, width: 50, height: 50 })).toEqual({
      left: 0,
      top: 0,
      width: 50,
      height: 50,
    });
  });

  it('refuses a zero-size region', () => {
    const region = normaliseRegion({ left: 10, top: 10, width: 0, height: 0 });
    expect(region.width).toBeGreaterThan(0);
    expect(region.height).toBeGreaterThan(0);
  });

  it('never lets a region run past the right or bottom edge', () => {
    const region = normaliseRegion({ left: 80, top: 90, width: 90, height: 90 });
    expect(region.left + region.width).toBeLessThanOrEqual(100);
    expect(region.top + region.height).toBeLessThanOrEqual(100);
  });

  it('keeps an origin inside the image even when it is absurd', () => {
    const region = normaliseRegion({ left: 500, top: 500, width: 10, height: 10 });
    expect(region.left).toBeLessThanOrEqual(95);
    expect(region.top).toBeLessThanOrEqual(95);
  });

  it('recognises the full image', () => {
    expect(isFullRegion(FULL_REGION)).toBe(true);
    expect(isFullRegion({ left: 0, top: 0, width: 99, height: 100 })).toBe(false);
  });

  it('is idempotent', () => {
    const once = normaliseRegion({ left: 120, top: -3, width: 400, height: 0 });
    expect(normaliseRegion(once)).toEqual(once);
  });
});

describe('the control separates presentation from evidence', () => {
  function show(
    region: CropRegion = FULL_REGION,
    onRegionChange: (next: CropRegion) => void = () => {},
  ) {
    return render(
      <ScreenshotCrop
        previewUrl="blob:preview"
        slot="1H"
        region={region}
        onRegionChange={onRegionChange}
      />,
    );
  }

  it('says zoom does not change the file', () => {
    show();
    expect(screen.getByText(/yalnızca görüntüleme; dosyayı değiştirmez/)).toBeInTheDocument();
  });

  it('says a crop changes the analysed image', () => {
    show();
    expect(screen.getByText(/yeni bir görsel üretilir/)).toBeInTheDocument();
  });

  it('zooming emits no region change', async () => {
    const user = userEvent.setup();
    const changes: CropRegion[] = [];
    show(FULL_REGION, (region) => changes.push(region));

    const zoom = screen.getByLabelText(/Yakınlaştırma/);
    await user.click(zoom);

    expect(changes).toEqual([]);
  });

  it('exposes every crop bound as a labelled keyboard control', () => {
    show();
    for (const label of ['Sol %', 'Üst %', 'Genişlik %', 'Yükseklik %']) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
  });

  it('reports a clamped value rather than an impossible one', async () => {
    const user = userEvent.setup();
    const changes: CropRegion[] = [];
    show(FULL_REGION, (region) => changes.push(region));

    const left = screen.getByLabelText('Sol %');
    await user.clear(left);
    await user.type(left, '150');

    const last = changes.at(-1);
    if (!last) throw new Error('no region change was emitted');
    expect(last.left + last.width).toBeLessThanOrEqual(100);
  });

  it('offers a way back to the whole image', async () => {
    const user = userEvent.setup();
    const changes: CropRegion[] = [];
    show({ left: 10, top: 10, width: 40, height: 40 }, (region) => changes.push(region));

    await user.click(screen.getByRole('button', { name: 'Tam görüntüye dön' }));
    expect(changes).toContainEqual(FULL_REGION);
  });

  it('disables the reset when the whole image is already selected', () => {
    show(FULL_REGION);
    expect(screen.getByRole('button', { name: 'Tam görüntüye dön' })).toBeDisabled();
  });

  it('draws the outline from the same numbers the inputs hold', () => {
    const { container } = show({ left: 10, top: 20, width: 30, height: 40 });
    const outline = container.querySelector('.crop__outline') as HTMLElement;

    expect(outline.style.left).toBe('10%');
    expect(outline.style.top).toBe('20%');
    expect(outline.style.width).toBe('30%');
    expect(outline.style.height).toBe('40%');
  });

  it('hides the decorative outline from assistive technology', () => {
    const { container } = show();
    expect(container.querySelector('.crop__outline')).toHaveAttribute('aria-hidden', 'true');
  });
});
