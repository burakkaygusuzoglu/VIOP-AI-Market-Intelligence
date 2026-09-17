import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ScreenshotSlots } from './ScreenshotSlots';

/**
 * Screenshot slots, correction workflow and confidence semantics (§15-§17).
 */

const analysisPayload = {
  screenshot_id: 'shot-1',
  slot: '1H',
  image_format: 'PNG',
  width: 1280,
  height: 720,
  detected_timeframe: '15M',
  timeframe_agreement: 'MISMATCH',
  mismatches: ['Beklenen 1H, algılanan 15M.'],
  observations: [
    { field: 'INDICATOR_READING', value: '99', kind: 'DIRECTLY_VISIBLE', confidence: '0.74' },
    { field: 'LAST_PRICE', value: '120.5', kind: 'VISUALLY_INFERRED', confidence: null },
  ],
  unreadable: ['VOLUME_CONTEXT'],
  quality: {
    score: 62,
    coverage: 0.8,
    evaluated_weight: 40,
    total_weight: 50,
    method_version: 'v1',
    dimensions: [],
  },
  warnings: [],
  model: 'fixture',
  prompt_version: 'v1',
};

const correctionPayload = {
  field: 'INDICATOR_READING',
  action: 'CONFIRMED',
  observed_screen_value: '99',
  observed_value_origin: 'CLIENT_REPLAYED_UNVERIFIED',
  user_value: '99',
  authoritative_value: '99',
  authoritative_source: 'USER_CONFIRMED',
  user_input_was_overridden: false,
  conflicts: [],
  agreeing: [],
  corrected_at: '2026-03-02T12:00:00+00:00',
};

function stub(handler: (url: string) => { ok: boolean; status: number; body: unknown }) {
  vi.stubGlobal('fetch', async (input: RequestInfo | URL) => {
    const { ok, status, body } = handler(String(input));
    return { ok, status, json: async () => body } as Response;
  });
}

/*
 * jsdom implements neither `createObjectURL` nor `revokeObjectURL`, so both
 * are stubbed as methods on the real `URL` rather than by replacing it -
 * spreading a class does not carry its static members across, and the first
 * version of this setup produced "revokeObjectURL is not a function".
 */
const createObjectURL = vi.fn(() => 'blob:preview');
const revokeObjectURL = vi.fn();

beforeEach(() => {
  createObjectURL.mockClear();
  revokeObjectURL.mockClear();
  vi.spyOn(URL, 'createObjectURL').mockImplementation(createObjectURL);
  vi.spyOn(URL, 'revokeObjectURL').mockImplementation(revokeObjectURL);
  stub((url) =>
    url.includes('/corrections')
      ? { ok: true, status: 200, body: correctionPayload }
      : { ok: true, status: 200, body: analysisPayload },
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** The first observation's confirm button. */
function firstAction(name: string): HTMLElement {
  const button = screen.getAllByRole('button', { name })[0];
  if (!button) throw new Error(`no ${name} button rendered`);
  return button;
}

function png(name = 'chart.png') {
  return new File([new Uint8Array([137, 80, 78, 71])], name, { type: 'image/png' });
}

async function upload(user: ReturnType<typeof userEvent.setup>, name = 'chart.png') {
  await user.upload(screen.getByLabelText('1H (Eğilim) ekran görüntüsü'), png(name));
  // The "analysed artifact" line is the single place dimensions appear, and it
  // only renders once the server has answered.
  await waitFor(() => expect(screen.getByText(/Analiz edilen:/)).toBeInTheDocument());
}

describe('slots and upload state', () => {
  it('offers one slot per timeframe role', () => {
    render(<ScreenshotSlots expectedSymbol="X" />);
    for (const code of ['1D', '1H', '15M', '5M']) {
      expect(screen.getByLabelText(new RegExp(`^${code} `))).toBeInTheDocument();
    }
  });

  it('says up front that Vision is not configured here', () => {
    render(<ScreenshotSlots expectedSymbol="X" />);
    expect(screen.getByText(/VISION_NOT_CONFIGURED/)).toBeInTheDocument();
    expect(screen.getByText(/piyasa\s+hakkında bir şey söylemez/)).toBeInTheDocument();
  });

  it('states what happens to the file you chose', () => {
    // This assertion used to pin "no crop or zoom is applied", which was true
    // when written and stopped being true when the crop shipped. The guarantee
    // that survives is narrower and stronger: the original is neither changed
    // nor sent, and the file actually read is named.
    render(<ScreenshotSlots expectedSymbol="X" />);

    const note = screen.getByText(/özgün dosyanız değişmez ve gönderilmez/);

    expect(note).toBeInTheDocument();
    expect(note).toHaveTextContent(/hangi dosyanın okunduğu/);
    expect(note).toHaveTextContent(/yakınlaştırma yalnızca önizlemedir/i);
  });

  it('shows a preview and the result after upload', async () => {
    const user = userEvent.setup();
    render(<ScreenshotSlots expectedSymbol="X" />);
    await upload(user);

    // Two previews: the slot's own and the crop control's, both of the
    // original file. The analysed-artifact line names which was read.
    expect(screen.getAllByAltText('1H önizleme').length).toBeGreaterThan(0);
    expect(screen.getByText('chart.png')).toBeInTheDocument();
    expect(screen.getByText(/yüklediğiniz orijinal görüntü/)).toBeInTheDocument();
  });

  it('renders a hostile filename as text', async () => {
    const user = userEvent.setup();
    const { container } = render(<ScreenshotSlots expectedSymbol="X" />);
    await upload(user, '<img src=x onerror=alert(1)>.png');

    expect(screen.getByText('<img src=x onerror=alert(1)>.png')).toBeInTheDocument();
    // Every <img> is a preview this component rendered; none was created by
    // the filename, which is why they all carry the preview alt text.
    for (const image of container.querySelectorAll('img')) {
      expect(image.getAttribute('alt')).toBe('1H önizleme');
    }
  });

  it('revokes the object URL when the slot is cleared', async () => {
    const user = userEvent.setup();
    render(<ScreenshotSlots expectedSymbol="X" />);
    await upload(user);

    await user.click(screen.getByRole('button', { name: 'Kaldır' }));
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:preview');
  });
});

describe('mismatch is never hidden', () => {
  it('announces an expected-vs-detected disagreement', async () => {
    const user = userEvent.setup();
    render(<ScreenshotSlots expectedSymbol="X" />);
    await upload(user);

    expect(screen.getByText(/UYUŞMUYOR/)).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Beklenen 1H, algılanan 15M.');
  });

  it('lists unreadable fields rather than omitting them', async () => {
    const user = userEvent.setup();
    render(<ScreenshotSlots expectedSymbol="X" />);
    await upload(user);

    expect(screen.getByText(/VOLUME_CONTEXT/)).toBeInTheDocument();
  });
});

describe('vision confidence is extraction confidence (§17)', () => {
  it('labels it as the model saying how legible the text was', async () => {
    const user = userEvent.setup();
    render(<ScreenshotSlots expectedSymbol="X" />);
    await upload(user);

    expect(screen.getByText(/okunabilirlik 0\.74/)).toBeInTheDocument();
    expect(screen.getByText(/\(model beyanı\)/)).toBeInTheDocument();
  });

  it('never presents it as a probability or a percentage', async () => {
    const user = userEvent.setup();
    const { container } = render(<ScreenshotSlots expectedSymbol="X" />);
    await upload(user);
    const text = container.textContent ?? '';

    expect(text).not.toContain('74%');
    expect(text).not.toMatch(/olasılık|ihtimal|şans/i);
    expect(text).not.toMatch(/doğruluk oranı/i);
  });

  it('keeps a missing confidence missing rather than showing zero', async () => {
    const user = userEvent.setup();
    render(<ScreenshotSlots expectedSymbol="X" />);
    await upload(user);

    expect(screen.getByText('güven bildirilmedi')).toBeInTheDocument();
    expect(screen.queryByText(/okunabilirlik 0(\.0+)?$/)).toBeNull();
  });
});

describe('the correction workflow (§16)', () => {
  it('offers confirm, correct and reject', async () => {
    const user = userEvent.setup();
    render(<ScreenshotSlots expectedSymbol="X" />);
    await upload(user);

    expect(screen.getAllByRole('button', { name: 'ONAYLA' })).toHaveLength(2);
    expect(screen.getAllByRole('button', { name: 'DÜZELT' })).toHaveLength(2);
    expect(screen.getAllByRole('button', { name: 'REDDET' })).toHaveLength(2);
  });

  it('shows the original observation beside the result, never overwriting it', async () => {
    const user = userEvent.setup();
    const { container } = render(<ScreenshotSlots expectedSymbol="X" />);
    await upload(user);

    await user.click(firstAction('ONAYLA'));
    await waitFor(() => expect(screen.getByText('Orijinal gözlem')).toBeInTheDocument());

    const outcome = container.querySelector('.shot__outcome');
    expect(outcome?.textContent).toContain('CLIENT_REPLAYED_UNVERIFIED');
    expect(outcome?.textContent).toContain('USER_CONFIRMED');
  });

  it('says outright that a user confirmation is not validated market data', async () => {
    const user = userEvent.setup();
    render(<ScreenshotSlots expectedSymbol="X" />);
    await upload(user);

    await user.click(firstAction('ONAYLA'));
    await waitFor(() =>
      expect(screen.getByText(/doğrulanmış piyasa verisinden.*üstün değil/)).toBeInTheDocument(),
    );
  });

  it('never shows USER_CONFIRMED as STRUCTURED_MARKET_DATA', async () => {
    const user = userEvent.setup();
    const { container } = render(<ScreenshotSlots expectedSymbol="X" />);
    await upload(user);

    await user.click(firstAction('ONAYLA'));
    await waitFor(() => expect(screen.getByText('Geçerli kaynak')).toBeInTheDocument());

    expect(container.textContent).not.toContain('STRUCTURED_MARKET_DATA');
  });

  it('sends a correction with a replacement value', async () => {
    const user = userEvent.setup();
    const sent: unknown[] = [];
    stub((url) => {
      if (url.includes('/corrections')) {
        return { ok: true, status: 200, body: { ...correctionPayload, action: 'CORRECTED' } };
      }
      return { ok: true, status: 200, body: analysisPayload };
    });
    vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/corrections')) {
        sent.push(JSON.parse(String(init?.body)));
        return {
          ok: true,
          status: 200,
          json: async () => ({ ...correctionPayload, action: 'CORRECTED', user_value: '61' }),
        } as Response;
      }
      return { ok: true, status: 200, json: async () => analysisPayload } as Response;
    });

    render(<ScreenshotSlots expectedSymbol="X" />);
    await upload(user);

    await user.click(firstAction('DÜZELT'));
    await user.type(screen.getByLabelText('Doğru değer'), '61');
    await user.click(screen.getByRole('button', { name: 'Gönder' }));

    await waitFor(() => expect(sent).toHaveLength(1));
    const body = sent[0] as Record<string, unknown>;
    expect(body.action).toBe('CORRECTED');
    expect(body.corrected_value).toBe('61');
    // The request carries no way to claim authority.
    expect(Object.keys(body)).not.toContain('structured_value');
    expect(Object.keys(body)).not.toContain('source');
  });
});

describe('an unconfigured provider is a system state, not a verdict', () => {
  it('shows the typed failure without any market language', async () => {
    const user = userEvent.setup();
    stub(() => ({
      ok: false,
      status: 503,
      body: {
        detail: {
          code: 'VISION_NOT_CONFIGURED',
          detail: 'Görüntü analizi servisi yapılandırılmamış.',
        },
      },
    }));

    const { container } = render(<ScreenshotSlots expectedSymbol="X" />);
    await user.upload(screen.getByLabelText('1H (Eğilim) ekran görüntüsü'), png());

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert')).toHaveTextContent('Sistem durumu');
    for (const word of ['AL', 'SAT', 'BEKLE', 'İŞLEM YOK']) {
      expect(container.textContent, word).not.toContain(`>${word}<`);
    }
  });
});

describe('crop produces a derived artifact, not a visual lie (§8)', () => {
  it('labels the original as the analysed artifact when nothing was cropped', async () => {
    const user = userEvent.setup();
    render(<ScreenshotSlots expectedSymbol="X" />);
    await upload(user);

    const analysed = screen.getByText(/yüklediğiniz orijinal görüntü/);

    expect(analysed).toBeInTheDocument();
    // Scoped to the artifact line: the standing note above mentions cropping in
    // general, and a document-wide query would match that instead.
    expect(analysed.closest('.shot__analysed')).not.toHaveTextContent(/kırpılmış görüntü/);
  });

  it('offers a re-analyse action whose label tracks the region', async () => {
    const user = userEvent.setup();
    render(<ScreenshotSlots expectedSymbol="X" />);
    await upload(user);

    expect(
      screen.getByRole('button', { name: 'Tam görüntüyü yeniden analiz et' }),
    ).toBeInTheDocument();

    // A single digit, deliberately. The inputs are controlled and clamped on
    // every keystroke, so typing "40" would be re-normalised mid-word; one
    // digit expresses the same intent without fighting the control.
    const left = screen.getByLabelText('Sol %');
    await user.clear(left);
    await user.type(left, '9');

    expect(
      screen.getByRole('button', { name: 'Kırpılmış görüntüyü analiz et' }),
    ).toBeInTheDocument();
  });

  it('uploads the original again when the region is the whole image', async () => {
    const user = userEvent.setup();
    const uploaded: string[] = [];
    vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/analyse') && init?.body instanceof FormData) {
        const file = init.body.get('file');
        uploaded.push(file instanceof File ? file.name : 'none');
      }
      return {
        ok: true,
        status: 200,
        json: async () => (url.includes('/corrections') ? correctionPayload : analysisPayload),
      } as Response;
    });

    render(<ScreenshotSlots expectedSymbol="X" />);
    await upload(user);
    await user.click(screen.getByRole('button', { name: 'Tam görüntüyü yeniden analiz et' }));

    await waitFor(() => expect(uploaded.length).toBe(2));
    // No crop was requested, so the bytes sent are the ones the user chose.
    expect(uploaded).toEqual(['chart.png', 'chart.png']);
  });

  it('says the crop changes the analysed image and zoom does not', async () => {
    const user = userEvent.setup();
    render(<ScreenshotSlots expectedSymbol="X" />);
    await upload(user);

    expect(screen.getByText(/yalnızca görüntüleme; dosyayı değiştirmez/)).toBeInTheDocument();
    expect(screen.getByText(/yeni bir görsel üretilir/)).toBeInTheDocument();
  });

  it('revokes the derived preview URL when the slot is cleared', async () => {
    const user = userEvent.setup();
    render(<ScreenshotSlots expectedSymbol="X" />);
    await upload(user);

    await user.click(screen.getByRole('button', { name: 'Kaldır' }));
    expect(revokeObjectURL).toHaveBeenCalled();
  });
});
