import type { BreakdownsDto, GroupDto, GroupSetDto } from '../api/performance';
import { DIRECTION_LABEL, STATUS_LABEL, metricText, percentText } from '../domain/performance';

/**
 * The same population, grouped (Phase 10).
 *
 * Long against short, instrument against instrument, timeframe against
 * timeframe - all computed by the same server engine, so no row can disagree
 * with the headline it sits under. The groups that cannot exist are listed by
 * name, because an absent breakdown should read as a decision.
 */

function GroupTable({
  caption,
  set,
  labelOf,
}: {
  readonly caption: string;
  readonly set: GroupSetDto;
  readonly labelOf?: (row: GroupDto) => string;
}) {
  const rows = set.rows;
  if (rows.length === 0) {
    return (
      <div className="perf-group">
        <h4>{caption}</h4>
        <p className="perf-empty">Bu kırılım için tamamlanmış işlem yok.</p>
      </div>
    );
  }
  return (
    <div className="perf-group">
      <h4>{caption}</h4>
      {!set.is_complete && (
        <p className="perf-incomplete" role="note">
          Bu liste eksik: {set.total} gruptan {set.returned} tanesi gösteriliyor, {set.omitted}{' '}
          tanesi listelenmedi. Üstteki toplamlar yine tüm seçimi kapsar.
        </p>
      )}
      <div className="perf-table-scroll">
        <table className="perf-table">
          <caption className="visually-hidden">{caption}</caption>
          <thead>
            <tr>
              <th scope="col">Grup</th>
              <th scope="col">Tamamlanan</th>
              <th scope="col">K / K / B</th>
              <th scope="col">Kazanç oranı</th>
              <th scope="col">Brüt K/Z</th>
              <th scope="col">Net K/Z</th>
              <th scope="col">Beklenti</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <th scope="row">{labelOf ? labelOf(row) : row.label}</th>
                <td>{row.sample_size}</td>
                <td>
                  {row.wins} / {row.losses} / {row.breakevens}
                </td>
                <td>{percentText(row.win_rate)}</td>
                <td>{metricText(row.realized_gross)}</td>
                <td title={row.realized_net.reason ?? undefined}>
                  {row.realized_net.status === 'AVAILABLE'
                    ? metricText(row.realized_net)
                    : STATUS_LABEL[row.realized_net.status]}
                </td>
                <td>{metricText(row.expectancy)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function PerformanceBreakdowns({ dto }: { readonly dto: BreakdownsDto }) {
  return (
    <section className="perf-breakdowns" aria-labelledby="perf-breakdowns-heading">
      <h3 id="perf-breakdowns-heading">Kırılımlar</h3>
      <GroupTable
        caption="Yöne göre"
        set={dto.by_direction}
        labelOf={(row) => DIRECTION_LABEL[row.key] ?? row.label}
      />
      <GroupTable caption="Enstrümana göre" set={dto.by_instrument} />
      <GroupTable caption="Zaman dilimine göre" set={dto.by_timeframe} />
      <div className="perf-group">
        <h4>Bu veri modelinde olmayan kırılımlar</h4>
        <ul className="perf-unavailable">
          {dto.unavailable_breakdowns.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      </div>
    </section>
  );
}
