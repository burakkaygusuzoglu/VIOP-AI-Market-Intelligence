import { useEffect, useId, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ApiError, saveAnnotation, type JournalRowDto } from '../api/performance';
import {
  DIRECTION_LABEL,
  MISSING,
  OUTCOME_LABEL,
  POPULATION_LABEL,
  formatMarketTime,
} from '../domain/performance';

/**
 * A person's own notes beside immutable simulated facts (Phase 10).
 *
 * The two halves of a row are deliberately not alike. The facts come from the
 * ledger and cannot be edited here; the note and tags are the person's, clearly
 * labelled as theirs, and saving them changes no financial value.
 *
 * Notes are rendered as text. There is no `dangerouslySetInnerHTML` anywhere in
 * this file, so a note containing markup stays the characters that were typed.
 */

function TradeFacts({ row }: { readonly row: JournalRowDto }) {
  return (
    <dl className="journal-facts">
      <div>
        <dt>Enstrüman</dt>
        <dd>
          {row.symbol} · {row.asset_class}
        </dd>
      </div>
      <div>
        <dt>Yön / miktar</dt>
        <dd>
          {DIRECTION_LABEL[row.direction] ?? row.direction} · {row.quantity}
        </dd>
      </div>
      <div>
        <dt>Durum</dt>
        <dd>{POPULATION_LABEL[row.population] ?? row.population}</dd>
      </div>
      <div>
        <dt>Sonuç</dt>
        <dd>
          {row.outcome ? (
            <>
              {OUTCOME_LABEL[row.outcome] ?? row.outcome}
              <span className="journal-basis"> ({row.outcome_basis})</span>
            </>
          ) : (
            'Tamamlanmadı'
          )}
        </dd>
      </div>
      <div>
        <dt>Kendi sonucu (brüt / net)</dt>
        <dd>
          {row.outcome_gross ? (OUTCOME_LABEL[row.outcome_gross] ?? row.outcome_gross) : '—'} /{' '}
          {row.outcome_net ? (OUTCOME_LABEL[row.outcome_net] ?? row.outcome_net) : 'Bilinmiyor'}
        </dd>
      </div>
      <div>
        <dt>Brüt / ücret / net</dt>
        <dd>
          {row.realized_gross} / {row.fees_total ?? 'Modellenmedi'} /{' '}
          {row.realized_net ?? 'Bilinmiyor'}
        </dd>
      </div>
      <div>
        <dt>Kapanış (piyasa zamanı)</dt>
        <dd>{formatMarketTime(row.terminal_time)}</dd>
      </div>
    </dl>
  );
}

export interface JournalEntryProps {
  readonly row: JournalRowDto;
}

export function JournalEntry({ row }: JournalEntryProps) {
  const client = useQueryClient();
  const noteId = useId();
  const tagsId = useId();
  const statusId = useId();
  const [note, setNote] = useState(row.annotation.note ?? '');
  const [tags, setTags] = useState(row.annotation.tags.join(', '));
  const [version, setVersion] = useState(row.annotation.version);
  const [status, setStatus] = useState<string>('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setNote(row.annotation.note ?? '');
    setTags(row.annotation.tags.join(', '));
    setVersion(row.annotation.version);
  }, [row.annotation.note, row.annotation.tags, row.annotation.version]);

  const save = useMutation({
    mutationFn: () =>
      saveAnnotation(row.position_id, {
        note: note.trim() === '' ? null : note,
        tags: tags
          .split(',')
          .map((tag) => tag.trim())
          .filter((tag) => tag !== ''),
        expectedVersion: version,
      }),
    onSuccess: (saved) => {
      setVersion(saved.version);
      setNote(saved.note ?? '');
      setTags(saved.tags.join(', '));
      setError(null);
      setStatus('Not kaydedildi. Bu not hiçbir finansal değeri değiştirmez.');
      void client.invalidateQueries({ queryKey: ['paper-journal'] });
    },
    onError: (cause) => {
      setStatus('');
      setError(
        cause instanceof ApiError ? cause.message : 'Not kaydedilemedi. Değişiklik uygulanmadı.',
      );
    },
  });

  return (
    <li className="journal-entry">
      <div className="journal-entry__facts">
        <h4 className="journal-entry__heading">
          <span className="journal-tag">SİMÜLASYON KAYDI</span> {row.symbol}
        </h4>
        <TradeFacts row={row} />
      </div>

      <form
        className="journal-entry__form"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <h4 className="journal-entry__heading journal-entry__heading--mine">
          <span className="journal-tag journal-tag--mine">SİZİN NOTUNUZ</span>
        </h4>
        <label htmlFor={noteId}>Not (yalnızca sizin yazdığınız metin)</label>
        <textarea
          id={noteId}
          value={note}
          maxLength={4000}
          rows={4}
          onChange={(event) => setNote(event.target.value)}
        />
        <label htmlFor={tagsId}>Etiketler (virgülle ayırın)</label>
        <input
          id={tagsId}
          value={tags}
          onChange={(event) => setTags(event.target.value)}
          aria-describedby={`${tagsId}-hint`}
        />
        <p id={`${tagsId}-hint`} className="journal-hint">
          Etiket sizin sınıflandırmanızdır; sistemin doğruladığı bir kurulum ya da rejim bilgisi
          değildir.
        </p>
        <div className="journal-entry__actions">
          <button type="submit" disabled={save.isPending}>
            {save.isPending ? 'Kaydediliyor…' : 'Notu kaydet'}
          </button>
          <span className="journal-version">sürüm {version}</span>
        </div>
        <p id={statusId} className="visually-hidden" role="status" aria-live="polite">
          {status}
        </p>
        {error && (
          <p className="journal-error" role="alert">
            {error}
          </p>
        )}
      </form>
    </li>
  );
}

export interface JournalPanelProps {
  readonly rows: readonly JournalRowDto[];
  readonly total: number;
}

export function JournalPanel({ rows, total }: JournalPanelProps) {
  return (
    <section className="journal-panel" aria-labelledby="journal-heading">
      <h3 id="journal-heading">Günlük</h3>
      {rows.length === 0 ? (
        <p className="perf-empty">Bu filtrelerle eşleşen pozisyon yok.</p>
      ) : (
        <>
          <p className="journal-count">{total} pozisyon</p>
          <ul className="journal-list">
            {rows.map((row) => (
              <JournalEntry key={row.position_id} row={row} />
            ))}
          </ul>
        </>
      )}
      <p className="journal-footnote">
        Notlar ve etiketler değiştirilebilir kullanıcı içeriğidir ({MISSING} sürüm geçmişi
        tutulmaz). Simülasyon defteri ise değiştirilemez.
      </p>
    </section>
  );
}
