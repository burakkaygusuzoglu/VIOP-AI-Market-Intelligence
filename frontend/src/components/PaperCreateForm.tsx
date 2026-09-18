import { useId, useRef, useState } from 'react';
import {
  ApiError,
  createPaperPosition,
  newIdempotencyKey,
  type CreatePaperPositionPayload,
  type PaperPositionDto,
} from '../api/paper';
import './PaperTrading.css';

/**
 * The plan for a simulated trade (Phase 9).
 *
 * Every field is an intention: symbol, direction, units, levels, the account the
 * risk engine should size against, and the simulation choices. There is no
 * field for a fill, a P&L or an approval - the server runs the risk engine and
 * refuses a plan it does not permit.
 *
 * ## One plan, one idempotency key
 *
 * A key is generated when the plan is first submitted and reused if the same
 * submission is retried, so a double click or a flaky network cannot record two
 * positions. Editing the plan after a response starts a new key.
 */

export interface PaperCreateFormProps {
  readonly onCreated: (position: PaperPositionDto) => void;
}

interface TargetRow {
  readonly price: string;
  readonly quantity: string;
}

const EMPTY_TARGET: TargetRow = { price: '', quantity: '' };

export function PaperCreateForm({ onCreated }: PaperCreateFormProps) {
  const ids = {
    symbol: useId(),
    quantity: useId(),
    entry: useId(),
    stop: useId(),
    timeframe: useId(),
    decision: useId(),
    equity: useId(),
    risk: useId(),
    sameBar: useId(),
    slippage: useId(),
    fee: useId(),
    note: useId(),
    status: useId(),
  };

  const [symbol, setSymbol] = useState('');
  const [direction, setDirection] = useState<'LONG' | 'SHORT'>('LONG');
  const [quantity, setQuantity] = useState('');
  const [entry, setEntry] = useState('');
  const [stop, setStop] = useState('');
  const [targets, setTargets] = useState<readonly TargetRow[]>([EMPTY_TARGET]);
  const [timeframe, setTimeframe] = useState<CreatePaperPositionPayload['timeframe']>('1H');
  const [decision, setDecision] = useState('');
  const [equity, setEquity] = useState('');
  const [fixedRisk, setFixedRisk] = useState('');
  const [sameBar, setSameBar] = useState<'STOP_FIRST' | 'HALT'>('STOP_FIRST');
  const [slippage, setSlippage] = useState('');
  const [fee, setFee] = useState('');
  const [note, setNote] = useState('');

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = useRef<string | null>(null);

  const edited = () => {
    // A changed plan is a different request; it must not reuse the old key.
    key.current = null;
    setError(null);
  };

  const setTarget = (index: number, field: keyof TargetRow, value: string) => {
    edited();
    setTargets((rows) => rows.map((row, i) => (i === index ? { ...row, [field]: value } : row)));
  };

  const submit = async () => {
    if (busy) return;
    const payload: CreatePaperPositionPayload = {
      symbol: symbol.trim(),
      direction,
      quantity: Number.parseInt(quantity, 10),
      intended_entry: entry.trim(),
      stop: stop.trim(),
      targets: targets
        .filter((row) => row.price.trim() !== '')
        .map((row) => ({ price: row.price.trim(), quantity: Number.parseInt(row.quantity, 10) })),
      timeframe,
      // The input is labelled UTC; this appends the offset rather than converting.
      decision_time: decision ? `${decision}:00Z` : '',
      account: { equity: equity.trim(), used_margin: '0' },
      risk: { mode: 'FIXED', fixed_risk: fixedRisk.trim() },
      simulation: {
        same_bar: sameBar,
        slippage_mode: slippage.trim() ? 'FIXED_POINTS' : 'ZERO',
        ...(slippage.trim() ? { slippage_points: slippage.trim() } : {}),
        fee_mode: fee.trim() ? 'USER_DEFINED_PER_UNIT' : 'NOT_MODELLED',
        ...(fee.trim() ? { fee_per_unit: fee.trim() } : {}),
      },
      ...(note.trim() ? { note: note.trim() } : {}),
    };

    key.current ??= newIdempotencyKey();
    setBusy(true);
    setError(null);
    try {
      const created = await createPaperPosition(payload, key.current);
      key.current = null;
      onCreated(created);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Kağıt pozisyon isteği tamamlanamadı.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="paper-form"
      aria-describedby={ids.status}
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <p className="paper-form__intro">
        Bu bir <strong>simülasyon planıdır</strong>. Hiçbir aracı kuruma emir gönderilmez. Sunucu
        risk motorunu yeniden çalıştırır; izin vermediği bir plan kaydedilmez.
      </p>

      <fieldset className="paper-form__group">
        <legend>Enstrüman ve yön</legend>
        <label htmlFor={ids.symbol}>Sembol</label>
        <input
          id={ids.symbol}
          value={symbol}
          maxLength={64}
          autoComplete="off"
          onChange={(e) => {
            edited();
            setSymbol(e.target.value);
          }}
        />
        <div className="paper-form__radios" role="radiogroup" aria-label="Yön">
          {(['LONG', 'SHORT'] as const).map((value) => (
            <label key={value} className="paper-form__radio">
              <input
                type="radio"
                name="paper-direction"
                value={value}
                checked={direction === value}
                onChange={() => {
                  edited();
                  setDirection(value);
                }}
              />
              {value === 'LONG' ? 'Uzun (LONG)' : 'Kısa (SHORT)'}
            </label>
          ))}
        </div>
        <label htmlFor={ids.quantity}>Miktar (tam birim)</label>
        <input
          id={ids.quantity}
          inputMode="numeric"
          value={quantity}
          onChange={(e) => {
            edited();
            setQuantity(e.target.value);
          }}
        />
      </fieldset>

      <fieldset className="paper-form__group">
        <legend>Seviyeler</legend>
        <label htmlFor={ids.entry}>Planlanan giriş</label>
        <input
          id={ids.entry}
          inputMode="decimal"
          value={entry}
          onChange={(e) => {
            edited();
            setEntry(e.target.value);
          }}
        />
        <label htmlFor={ids.stop}>Stop</label>
        <input
          id={ids.stop}
          inputMode="decimal"
          value={stop}
          onChange={(e) => {
            edited();
            setStop(e.target.value);
          }}
        />
        {targets.map((row, index) => (
          <div key={index} className="paper-form__target">
            <label>
              Hedef {index + 1} fiyatı
              <input
                inputMode="decimal"
                value={row.price}
                onChange={(e) => setTarget(index, 'price', e.target.value)}
              />
            </label>
            <label>
              Hedef {index + 1} miktarı
              <input
                inputMode="numeric"
                value={row.quantity}
                onChange={(e) => setTarget(index, 'quantity', e.target.value)}
              />
            </label>
          </div>
        ))}
        {targets.length < 5 && (
          <button
            type="button"
            onClick={() => {
              edited();
              setTargets((rows) => [...rows, EMPTY_TARGET]);
            }}
          >
            Hedef ekle
          </button>
        )}
      </fieldset>

      <fieldset className="paper-form__group">
        <legend>Zaman ve hesap</legend>
        <label htmlFor={ids.timeframe}>Çubuk zaman dilimi</label>
        <select
          id={ids.timeframe}
          value={timeframe}
          onChange={(e) => {
            edited();
            setTimeframe(e.target.value as CreatePaperPositionPayload['timeframe']);
          }}
        >
          {(['5M', '15M', '1H', '1D'] as const).map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
        <label htmlFor={ids.decision}>Karar zamanı (UTC)</label>
        <input
          id={ids.decision}
          type="datetime-local"
          value={decision}
          onChange={(e) => {
            edited();
            setDecision(e.target.value);
          }}
        />
        <label htmlFor={ids.equity}>Hesap bakiyesi</label>
        <input
          id={ids.equity}
          inputMode="decimal"
          value={equity}
          onChange={(e) => {
            edited();
            setEquity(e.target.value);
          }}
        />
        <label htmlFor={ids.risk}>İşlem başına risk (tutar)</label>
        <input
          id={ids.risk}
          inputMode="decimal"
          value={fixedRisk}
          onChange={(e) => {
            edited();
            setFixedRisk(e.target.value);
          }}
        />
      </fieldset>

      <fieldset className="paper-form__group">
        <legend>Simülasyon kuralları</legend>
        <label htmlFor={ids.sameBar}>Aynı çubukta stop ve hedef</label>
        <select
          id={ids.sameBar}
          value={sameBar}
          onChange={(e) => {
            edited();
            setSameBar(e.target.value as 'STOP_FIRST' | 'HALT');
          }}
        >
          <option value="STOP_FIRST">Önce stop varsay (temkinli)</option>
          <option value="HALT">Karar verme, pozisyonu dondur</option>
        </select>
        <label htmlFor={ids.slippage}>Kayma (fiyat puanı, boş = sıfır)</label>
        <input
          id={ids.slippage}
          inputMode="decimal"
          value={slippage}
          onChange={(e) => {
            edited();
            setSlippage(e.target.value);
          }}
        />
        <label htmlFor={ids.fee}>Birim başına ücret (boş = modellenmez)</label>
        <input
          id={ids.fee}
          inputMode="decimal"
          value={fee}
          onChange={(e) => {
            edited();
            setFee(e.target.value);
          }}
        />
        <label htmlFor={ids.note}>Not</label>
        <input
          id={ids.note}
          maxLength={280}
          value={note}
          onChange={(e) => {
            edited();
            setNote(e.target.value);
          }}
        />
      </fieldset>

      <p id={ids.status} className="visually-hidden" role="status" aria-live="polite">
        {busy ? 'Simülasyon planı gönderiliyor.' : ''}
      </p>
      {error && (
        <p className="paper-form__error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" className="paper-form__submit" disabled={busy}>
        {busy ? 'Gönderiliyor…' : 'SİMÜLASYON POZİSYONU OLUŞTUR'}
      </button>
    </form>
  );
}
