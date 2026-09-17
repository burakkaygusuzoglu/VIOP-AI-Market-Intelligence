/**
 * Crop geometry, with no React in it (§8, §9).
 *
 * Split out of the component deliberately. These are the functions that decide
 * which pixels become the derived artifact, so they are worth testing and
 * reasoning about without a render, and a module that exports both a component
 * and a pile of helpers also breaks fast refresh.
 */

export interface CropRegion {
  readonly left: number;
  readonly top: number;
  readonly width: number;
  readonly height: number;
}

export const FULL_REGION: CropRegion = { left: 0, top: 0, width: 100, height: 100 };

const MIN_PERCENT = 5;

/** Clamp a region so it always describes a real, non-empty area of the image. */
export function normaliseRegion(region: CropRegion): CropRegion {
  const left = Math.min(Math.max(region.left, 0), 100 - MIN_PERCENT);
  const top = Math.min(Math.max(region.top, 0), 100 - MIN_PERCENT);
  return {
    left,
    top,
    width: Math.min(Math.max(region.width, MIN_PERCENT), 100 - left),
    height: Math.min(Math.max(region.height, MIN_PERCENT), 100 - top),
  };
}

export function isFullRegion(region: CropRegion): boolean {
  const normalised = normaliseRegion(region);
  return (
    normalised.left === 0 &&
    normalised.top === 0 &&
    normalised.width === 100 &&
    normalised.height === 100
  );
}

/**
 * Render the chosen region to new PNG bytes.
 *
 * Rejects rather than returning something approximate: a zero-area region, an
 * image the browser could not decode, or a canvas that produced no blob all
 * throw, because the alternative is uploading bytes that do not show what the
 * user framed.
 */
export async function cropToFile(file: File, region: CropRegion): Promise<File> {
  const source = await loadImage(file);
  const box = normaliseRegion(region);

  const sx = Math.round((box.left / 100) * source.width);
  const sy = Math.round((box.top / 100) * source.height);
  const sw = Math.max(Math.round((box.width / 100) * source.width), 1);
  const sh = Math.max(Math.round((box.height / 100) * source.height), 1);

  const canvas = document.createElement('canvas');
  canvas.width = sw;
  canvas.height = sh;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('canvas 2d context unavailable');
  context.drawImage(source, sx, sy, sw, sh, 0, 0, sw, sh);

  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'));
  if (!blob) throw new Error('crop produced no image data');

  // PNG regardless of the source format: the derived artifact is a new image,
  // and re-encoding a JPEG region as JPEG would add a second generation of
  // lossy compression to something a model has to read text off.
  return new File([blob], `kirpilmis-${file.name.replace(/\.[^.]+$/, '')}.png`, {
    type: 'image/png',
  });
}

function loadImage(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('image could not be decoded'));
    };
    image.src = url;
  });
}
