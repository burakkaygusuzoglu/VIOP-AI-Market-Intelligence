import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import {
  ApiError,
  getPaperPosition,
  listPaperPositions,
  type PaperPositionDto,
} from '../api/paper';
import { PaperCreateForm } from '../components/PaperCreateForm';
import { PaperPositionDetail } from '../components/PaperPositionDetail';
import { capability } from '../domain/capabilities';
import { DIRECTION_LABEL, STATE_LABEL, mapPaperSummary } from '../domain/paper';
import { formatTimestamp, formatValue } from '../format/display';
import type { ExperienceMode } from './AnalysisWorkspace';
import '../components/PaperTrading.css';

/**
 * The paper-trading surface (Phase 9). Simulation only.
 *
 * ## It says what it is, first
 *
 * The first thing on the screen is that nothing here is a real order. The
 * second, when it applies, is that this deployment cannot open new positions -
 * no verified contract metadata is configured, so the server refuses every
 * plan. The form is still shown, because the server is the authority and its
 * refusal explains itself; the screen never pretends the refusal will not come.
 *
 * ## A late answer cannot overwrite a newer view
 *
 * Every read is keyed by position id. A slow response for one position lands in
 * that position's cache entry and is never rendered as another one's detail.
 */

export interface PaperTradingProps {
  readonly mode: ExperienceMode;
}

const LIST_KEY = ['paper', 'positions'] as const;

export function PaperTrading({ mode }: PaperTradingProps) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const paper = capability('paper-trading');

  const list = useQuery({
    queryKey: LIST_KEY,
    queryFn: ({ signal }) => listPaperPositions(0, signal),
  });

  const detail = useQuery({
    queryKey: ['paper', 'position', selected],
    queryFn: ({ signal }) => getPaperPosition(selected ?? '', signal),
    enabled: selected !== null,
  });

  const remember = (position: PaperPositionDto) => {
    queryClient.setQueryData(['paper', 'position', position.id], position);
    void queryClient.invalidateQueries({ queryKey: LIST_KEY });
  };

  const listError =
    list.error instanceof ApiError ? list.error.message : list.error ? 'Liste yüklenemedi.' : null;
  const items = list.data?.items.map(mapPaperSummary) ?? [];
  const shown =
    selected !== null && detail.data && detail.data.id === selected ? detail.data : null;

  return (
    <div className="paper-screen">
      <section className="paper-screen__banner" role="note" aria-label="Simülasyon uyarısı">
        <p className="paper-screen__banner-title">KAĞIT İŞLEM — SİMÜLASYON</p>
        <p>
          Burada hiçbir gerçek emir oluşturulmaz, iletilmez veya bir aracı kuruma gönderilmez.
          Dolumlar yalnızca sizin yüklediğiniz kapanmış geçmiş çubuklardan, açıkça belirtilen
          kurallarla simüle edilir.
        </p>
      </section>

      {paper && paper.state !== 'AVAILABLE_NOW' && (
        <p className="paper-screen__notice" role="note">
          {paper.detail}
        </p>
      )}

      <section className="paper-screen__list" aria-labelledby="paper-list-heading">
        <div className="paper-screen__row">
          <h2 className="paper-screen__heading" id="paper-list-heading">
            Kağıt pozisyonlar
          </h2>
          <button type="button" onClick={() => setCreating((value) => !value)}>
            {creating ? 'Planı kapat' : 'Yeni simülasyon planı'}
          </button>
        </div>

        {listError && (
          <p className="paper-detail__error" role="alert">
            {listError}
          </p>
        )}
        {list.isPending && <p>Yükleniyor…</p>}
        {!list.isPending && !listError && items.length === 0 && (
          <p className="paper-screen__empty">Henüz kağıt pozisyon yok.</p>
        )}

        {items.length > 0 && (
          <div className="paper-table-scroll">
            <table className="paper-table">
              <caption>
                {list.data?.total ?? 0} pozisyon (en yeni önce, sayfa başına en fazla 20)
              </caption>
              <thead>
                <tr>
                  <th scope="col">Sembol</th>
                  <th scope="col">Yön</th>
                  <th scope="col">Durum</th>
                  <th scope="col">Kalan</th>
                  <th scope="col">Gerçekleşen brüt</th>
                  <th scope="col">Güncelleme</th>
                  <th scope="col">
                    <span className="visually-hidden">Aç</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id} aria-current={item.id === selected ? 'true' : undefined}>
                    <th scope="row">{item.symbol}</th>
                    <td>{DIRECTION_LABEL[item.direction] ?? item.direction}</td>
                    <td>{STATE_LABEL[item.state]}</td>
                    <td>
                      {item.remaining} / {item.quantity}
                    </td>
                    <td>{formatValue(item.realizedGross, 'price')}</td>
                    <td>{formatTimestamp(item.updatedAt)}</td>
                    <td>
                      <button
                        type="button"
                        onClick={() => setSelected(item.id)}
                        aria-label={`${item.symbol} pozisyonunu aç`}
                      >
                        Aç
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {creating && (
        <section className="paper-screen__create" aria-labelledby="paper-create-heading">
          <h2 className="paper-screen__heading" id="paper-create-heading">
            Yeni simülasyon planı
          </h2>
          <PaperCreateForm
            onCreated={(position) => {
              remember(position);
              setCreating(false);
              setSelected(position.id);
            }}
          />
        </section>
      )}

      {selected !== null && detail.isPending && !shown && <p>Pozisyon yükleniyor…</p>}
      {detail.error && (
        <p className="paper-detail__error" role="alert">
          {detail.error instanceof ApiError ? detail.error.message : 'Pozisyon yüklenemedi.'}
        </p>
      )}
      {shown && <PaperPositionDetail position={shown} mode={mode} onUpdated={remember} />}
    </div>
  );
}
