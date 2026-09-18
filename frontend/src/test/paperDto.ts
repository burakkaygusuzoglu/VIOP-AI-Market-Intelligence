import type { PaperEventDto, PaperPositionDto } from '../api/paper';

/**
 * A paper-position response as the backend sends it (Phase 9 tests).
 *
 * TEST_FIXTURE data. The numbers deliberately include one that does not "add up"
 * (net is not gross minus fees below) so a test can prove the UI shows what the
 * server said rather than recomputing it.
 */

export function event(
  sequence: number,
  type: string,
  data: Record<string, string> = {},
  marketTime: string | null = '2026-03-02T11:00:00+00:00',
): PaperEventDto {
  return { sequence, type, market_time: marketTime, data, recorded_at: null };
}

export function paperDto(changes: Partial<PaperPositionDto> = {}): PaperPositionDto {
  return {
    id: 'PP-0123456789abcdef01234567',
    simulated: true,
    symbol: 'TEST_FIXTURE_FUT',
    asset_class: 'FUTURES',
    direction: 'LONG',
    state: 'PARTIALLY_CLOSED',
    quantity: 4,
    remaining: 2,
    entry_fill_price: '100.00',
    stop: '98.00',
    last_mark: '104.25',
    realized_gross: '80.00',
    fees_total: '12.00',
    realized_net: '67.77',
    unrealized_gross: '85.00',
    close_pending: false,
    created_at: '2026-03-02T10:00:00+00:00',
    updated_at: '2026-03-02T11:05:00+00:00',
    origin: 'USER_CREATED',
    timeframe: '1H',
    decision_time: '2026-03-02T10:00:00+00:00',
    intended_entry: '100.00',
    initial_stop: '98.00',
    closed_quantity: 2,
    targets: [
      { index: 1, price: '104.00', quantity: 2, filled: true, fill_price: '104.00' },
      { index: 2, price: '106.00', quantity: 2, filled: false, fill_price: null },
    ],
    last_bar_time: '2026-03-02T11:00:00+00:00',
    bars_applied: 2,
    event_count: 5,
    simulation: {
      rules_version: 'paper-sim/v1',
      same_bar: 'STOP_FIRST',
      slippage_mode: 'ZERO',
      slippage_points: null,
      fee_mode: 'USER_DEFINED_PER_UNIT',
      fee_per_unit: '2',
      entry_model: 'NEXT_BAR_OPEN',
      stop_fill_model: 'STOP_PRICE_OR_GAPPED_OPEN',
      target_fill_model: 'TARGET_PRICE_NO_IMPROVEMENT',
      manual_exit_model: 'NEXT_BAR_OPEN',
    },
    risk: { outcome: 'ALLOWED', allowed_units: 20, reason: 'risk allows 50 contract(s)' },
    provenance: {
      fills: 'SIMULATED',
      market_data: 'USER_SUPPLIED_HISTORICAL_BARS',
      origin: 'USER_CREATED',
      asset_class: 'FUTURES',
      asset_class_status: 'UNVERIFIED',
      point_value: '10',
      point_value_status: 'VERIFIED_CURRENT_FACT',
      point_value_source: 'test fixture',
      unit: 'contract',
    },
    note: null,
    key_events: [
      event(
        1,
        'POSITION_CREATED',
        {
          risk_outcome: 'ALLOWED',
          intended_entry: '100.00',
          stop: '98.00',
          rules_version: 'paper-sim/v1',
        },
        '2026-03-02T10:00:00+00:00',
      ),
      event(
        3,
        'ENTRY_FILLED',
        {
          intended_entry: '100.00',
          reference_price: '100.00',
          slippage: '0',
          fill_price: '100.00',
          quantity: '4',
        },
        '2026-03-02T10:00:00+00:00',
      ),
      event(5, 'TARGET_FILLED', {
        target: '1',
        trigger_price: '104.00',
        fill_price: '104.00',
        quantity: '2',
        remaining: '2',
        gap: 'false',
        gross_pnl: '80.00',
      }),
    ],
    idempotent_replay: false,
    ...changes,
  };
}
