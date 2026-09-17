import { useCallback, useId, useMemo, useState } from 'react';
import type { AnalysisRequestPayload } from '../api/analysis';
import { ScreenshotSlots } from '../components/ScreenshotSlots';
import { capability } from '../domain/capabilities';
import './AnalyzeMarket.css';

/**
 * The input screen (§27).
 *
 * The user must understand, **before** pressing Analyse, what will and will not
 * happen. So this screen states its own limitations up front rather than
 * letting the result explain them afterwards:
 *
 * * which timeframes are present and which are missing;
 * * that a typed instrument code is not verified contract metadata;
 * * that risk sizing needs verified metadata plus an entry and a stop, and
 *   will otherwise report itself unavailable;
 * * whether Vision and synthesis are configured at all.
 *
 * None of that is a warning bolted on at the end. A user who submits without
 * a 5M file should already know the entry timeframe will be missing.
 */

const TIMEFRAMES = [
  { code: '1D', role: 'Rejim' },
  { code: '1H', role: 'Eğilim' },
  { code: '15M', role: 'Kurulum' },
  { code: '5M', role: 'Giriş' },
] as const;

type TimeframeCode = (typeof TIMEFRAMES)[number]['code'];

interface FileEntry {
  readonly name: string;
  readonly content: string;
  readonly rows: number;
}

/**
 * Read a chosen file as UTF-8 text.
 *
 * `FileReader` rather than the shorter `file.text()`. Two reasons, and the
 * second is the one that decided it: `text()` is a newer API with a longer
 * tail of unsupported environments, and it is **not implemented in jsdom**, so
 * every test of this screen would have had to stub the file object rather than
 * exercise the real read path. A read that only works in a browser is a read
 * nobody can test.
 */
function readAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error('read failed'));
    reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : '');
    reader.readAsText(file);
  });
}

export interface AnalyzeMarketProps {
  readonly onAnalyse: (payload: AnalysisRequestPayload) => void;
  readonly onCancel?: (() => void) | undefined;
  readonly busy: boolean;
  readonly error: string | null;
}

export function AnalyzeMarket({ onAnalyse, onCancel, busy, error }: AnalyzeMarketProps) {
  const symbolId = useId();
  const equityId = useId();
  const currencyId = useId();
  const riskId = useId();
  const entryId = useId();
  const stopId = useId();

  const [symbol, setSymbol] = useState('');
  const [files, setFiles] = useState<Partial<Record<TimeframeCode, FileEntry>>>({});
  const [equity, setEquity] = useState('');
  const [currency, setCurrency] = useState('');
  const [fixedRisk, setFixedRisk] = useState('');
  const [entry, setEntry] = useState('');
  const [stop, setStop] = useState('');
  const [readError, setReadError] = useState<string | null>(null);

  const vision = capability('screenshot-analysis');
  const synthesis = capability('synthesis');

  const supplied = TIMEFRAMES.filter((item) => files[item.code]);
  const missing = TIMEFRAMES.filter((item) => !files[item.code]);
  const canSubmit = symbol.trim().length > 0 && supplied.length > 0 && !busy;

  const readFile = useCallback(async (code: TimeframeCode, file: File) => {
    setReadError(null);
    try {
      const content = await readAsText(file);
      // Counted for the summary only. The backend parses and validates; a row
      // count here is not a claim that the file is usable.
      const rows = Math.max(content.trim().split('\n').length - 1, 0);
      setFiles((current) => ({ ...current, [code]: { name: file.name, content, rows } }));
    } catch {
      setReadError(`${code} dosyası okunamadı.`);
    }
  }, []);

  const clear = useCallback((code: TimeframeCode) => {
    // Rebuilt without the key rather than `delete`d: a dynamic delete is both
    // a lint error here and a deoptimisation, and filtering states the intent.
    setFiles((current) =>
      Object.fromEntries(Object.entries(current).filter(([key]) => key !== code)),
    );
  }, []);

  const payload = useMemo((): AnalysisRequestPayload => {
    const base: AnalysisRequestPayload = {
      symbol: symbol.trim(),
      datasets: supplied.map((item) => {
        const file = files[item.code];
        return {
          timeframe: item.code,
          content: file?.content ?? '',
          source_name: file?.name ?? item.code,
        };
      }),
      ...(equity.trim()
        ? {
            account: {
              equity: equity.trim(),
              used_margin: '0',
              ...(currency.trim() ? { currency: currency.trim().toUpperCase() } : {}),
            },
          }
        : {}),
      ...(fixedRisk.trim() ? { risk: { mode: 'FIXED', fixed_risk: fixedRisk.trim() } } : {}),
      ...(entry.trim() ? { entry_price: entry.trim() } : {}),
      ...(stop.trim() ? { stop_price: stop.trim() } : {}),
    };
    return base;
  }, [symbol, supplied, files, equity, currency, fixedRisk, entry, stop]);

  return (
    <section className="analyze" aria-labelledby="analyze-heading">
      <h2 className="analyze__heading" id="analyze-heading">
        Piyasa Analizi
      </h2>
      <p className="analyze__intro">
        Kendi geçmiş OHLCV verinizi yükleyin. Analiz, verdiğiniz veriden deterministik motorlarla
        hesaplanır; sistem sizin adınıza piyasa verisi indirmez.
      </p>

      <form
        className="analyze__form"
        onSubmit={(event) => {
          event.preventDefault();
          if (canSubmit) onAnalyse(payload);
        }}
      >
        <fieldset className="analyze__section">
          <legend>1. Enstrüman</legend>
          <label htmlFor={symbolId}>Sözleşme / sembol</label>
          <input
            id={symbolId}
            value={symbol}
            onChange={(event) => setSymbol(event.target.value)}
            maxLength={64}
            autoComplete="off"
            required
          />
          {/* §10: typing a code does not make it verified metadata. Said here,
              before submission, not discovered in the risk panel afterwards. */}
          <p className="analyze__note">
            Yazdığınız kod <strong>doğrulanmış sözleşme bilgisi değildir</strong>. Çarpan, tik
            büyüklüğü ve teminat doğrulanmış bir sağlayıcıdan gelmediği sürece bu değerlere bağlı
            hesaplamalar yapılmaz.
          </p>
        </fieldset>

        <fieldset className="analyze__section">
          <legend>2. Zaman dilimi verileri</legend>
          <p className="analyze__note">
            CSV sütunları: <code>open_time,open,high,low,close,volume</code>. Eksik bir zaman dilimi{' '}
            <strong>nötr sayılmaz</strong>; eksik olarak raporlanır.
          </p>
          <ul className="analyze__timeframes">
            {TIMEFRAMES.map((item) => {
              const file = files[item.code];
              return (
                <li key={item.code} className="analyze__timeframe">
                  <div className="analyze__timeframe-label">
                    <strong>{item.code}</strong>
                    <span>{item.role}</span>
                  </div>
                  <input
                    type="file"
                    accept=".csv,text/csv"
                    aria-label={`${item.code} (${item.role}) CSV dosyası`}
                    onChange={(event) => {
                      const chosen = event.target.files?.[0];
                      if (chosen) void readFile(item.code, chosen);
                    }}
                  />
                  {file ? (
                    <span className="analyze__file">
                      {/* Rendered as text. A filename is user-controlled and
                          never interpolated into markup (§36). */}
                      <span className="analyze__file-name">{file.name}</span>
                      <span className="analyze__file-rows">~{file.rows} satır</span>
                      <button type="button" onClick={() => clear(item.code)}>
                        Kaldır
                      </button>
                    </span>
                  ) : (
                    <span className="analyze__file analyze__file--empty">Dosya seçilmedi</span>
                  )}
                </li>
              );
            })}
          </ul>
        </fieldset>

        <fieldset className="analyze__section">
          <legend>3. Hesap ve risk (isteğe bağlı)</legend>
          <div className="analyze__grid">
            <div>
              <label htmlFor={equityId}>Hesap bakiyesi</label>
              <input
                id={equityId}
                value={equity}
                onChange={(event) => setEquity(event.target.value)}
                inputMode="decimal"
                autoComplete="off"
              />
            </div>
            <div>
              <label htmlFor={currencyId}>Para birimi</label>
              <input
                id={currencyId}
                value={currency}
                onChange={(event) => setCurrency(event.target.value)}
                maxLength={3}
                placeholder="TRY"
                autoComplete="off"
              />
            </div>
            <div>
              <label htmlFor={riskId}>İşlem başına risk</label>
              <input
                id={riskId}
                value={fixedRisk}
                onChange={(event) => setFixedRisk(event.target.value)}
                inputMode="decimal"
                autoComplete="off"
              />
            </div>
            <div>
              <label htmlFor={entryId}>Planlanan giriş</label>
              <input
                id={entryId}
                value={entry}
                onChange={(event) => setEntry(event.target.value)}
                inputMode="decimal"
                autoComplete="off"
              />
            </div>
            <div>
              <label htmlFor={stopId}>Stop</label>
              <input
                id={stopId}
                value={stop}
                onChange={(event) => setStop(event.target.value)}
                inputMode="decimal"
                autoComplete="off"
              />
            </div>
          </div>
          <p className="analyze__note">
            Para birimi yalnızca sizin belirttiğiniz koddur; sistem bunu tahmin etmez. Kod
            girilmezse tutarlar birimsiz gösterilir.
          </p>
        </fieldset>

        <fieldset className="analyze__section">
          <legend>4. Ekran görüntüleri (isteğe bağlı)</legend>
          <ScreenshotSlots expectedSymbol={symbol.trim()} />
        </fieldset>

        <section className="analyze__summary" aria-labelledby="analyze-summary-heading">
          <h3 id="analyze-summary-heading">Gönderim öncesi durum</h3>
          <ul className="analyze__summary-list">
            <li>
              <strong>Verilen zaman dilimleri:</strong>{' '}
              {supplied.length > 0 ? supplied.map((item) => item.code).join(', ') : 'yok'}
            </li>
            <li>
              <strong>Eksik zaman dilimleri:</strong>{' '}
              {missing.length > 0 ? missing.map((item) => item.code).join(', ') : 'yok'}
            </li>
            <li>
              <strong>Risk hesaplaması:</strong>{' '}
              {entry.trim() && stop.trim() && equity.trim()
                ? 'girdiler tamam; yine de doğrulanmış sözleşme bilgisi gerekir'
                : 'giriş, stop ve bakiye olmadan hesaplanamaz'}
            </li>
            {/* The last thing read before pressing ANALYSE. Naming Vision's
                availability here without naming its scope would let a user
                reasonably conclude an uploaded chart takes part in the result
                they are about to request. It does not. */}
            <li>
              <strong>Ekran görüntüsü analizi:</strong> {vision?.label}{' '}
              {vision?.state === 'AVAILABLE_NOW' ? 'kullanılabilir' : 'bu kurulumda kapalı'}{' '}
              <em className="analyze__scope">
                — ayrı bir gözlem aracıdır; bu analize ve yapay zekâ sentezine dahil edilmez.
              </em>
            </li>
            <li>
              <strong>Yapay zekâ sentezi:</strong>{' '}
              {synthesis?.state === 'AVAILABLE_NOW'
                ? 'yapılandırılmış'
                : 'yapılandırılmamış; deterministik analiz yine de üretilir'}
            </li>
          </ul>
        </section>

        {readError && <p className="analyze__error">{readError}</p>}
        {error && (
          <p className="analyze__error" role="alert">
            {error}
          </p>
        )}

        {/* Progress is announced, not only drawn (§12).
            The button's label changing to "Analiz ediliyor..." is a visual
            cue only: a screen reader user who has moved past the button hears
            nothing at all. A polite live region states the same fact without
            interrupting, and the workspace heading takes focus when the result
            arrives, so both ends of the wait are spoken. */}
        <p className="visually-hidden" role="status" aria-live="polite">
          {busy ? 'Analiz ediliyor, lütfen bekleyin.' : ''}
        </p>
        <div className="analyze__actions">
          <button type="submit" className="analyze__submit" disabled={!canSubmit}>
            {busy ? 'Analiz ediliyor…' : 'ANALİZ ET'}
          </button>
          {busy && onCancel && (
            <button type="button" onClick={onCancel}>
              İptal
            </button>
          )}
        </div>
      </form>
    </section>
  );
}
