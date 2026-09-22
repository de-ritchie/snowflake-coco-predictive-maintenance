"""Finished-goods + spare-part inventory snapshot generation (LLD SS7, design doc SS10)."""

from dataclasses import dataclass, field

import pandas as pd

from generator.fulldata.machine_config import SPARE_PART_LEAD_TIME_DAYS
from generator.fulldata.orders import PRODUCTS
from generator.fulldata.sim_calendar import week_start

FG_TARGET_DAYS_HISTORICAL = 14
FG_TARGET_DAYS_LOOKAHEAD_SPIKE = 7  # tightens during weeks 157-164 (design doc SS10)
SPARE_PART_INITIAL_STOCK = 5
SPARE_PART_CONSUMPTION_LOOKBACK_WEEKS = 12


def _fg_target_days(week_num: int, historical_weeks: int) -> int:
    return FG_TARGET_DAYS_LOOKAHEAD_SPIKE if week_num > historical_weeks else FG_TARGET_DAYS_HISTORICAL


def generate_fg_snapshot(
    order_series: dict[str, list[float]],
    historical_weeks: int,
    lookahead_weeks: int,
    base_monday,
) -> pd.DataFrame:
    """Finished-goods snapshot tracking target days-of-supply against orders
    (LLD SS7 / design doc SS10) -- no dedicated formula given beyond the
    target days-of-supply parameter, so fg_units_on_hand is a direct
    daily-demand-times-target-days computation."""
    total_weeks = historical_weeks + lookahead_weeks
    rows = []
    for line_name, (product_id, variant) in PRODUCTS.items():
        series = order_series[line_name]
        for w in range(1, total_weeks + 1):
            order_units = series[w - 1]
            target_days = _fg_target_days(w, historical_weeks)
            daily_demand = order_units / 7.0
            fg_units_on_hand = daily_demand * target_days
            rows.append(
                {
                    "snapshot_week": week_start(base_monday, w),
                    "product_id": product_id,
                    "variant": variant,
                    "fg_units_on_hand": round(fg_units_on_hand),
                }
            )
    return pd.DataFrame(rows)


@dataclass
class _SparePartState:
    units_on_hand: int
    pending_arrival_week: int | None = None
    pending_qty: int = 0
    consumption_by_week: dict[int, int] = field(default_factory=dict)


class SparePartsSimulator:
    """Tracks spare-part stock per (equipment_id, part_name) across the run.

    Reorder policy (design doc SS10): reorder point = lead_time_days worth of
    that part's average weekly consumption (trailing window, no additional
    safety-stock buffer); decrement by 1 per repair event consuming that
    part; replenish after lead_time_days.
    """

    def __init__(self, equipment_ids: list[str], initial_stock: int = SPARE_PART_INITIAL_STOCK):
        self._state: dict[tuple[str, str], _SparePartState] = {
            (equipment_id, part_name): _SparePartState(units_on_hand=initial_stock)
            for equipment_id in equipment_ids
            for part_name in SPARE_PART_LEAD_TIME_DAYS
        }

    @property
    def part_count(self) -> int:
        return len(self._state)

    def consume(self, equipment_id: str, part_name: str, week_num: int) -> None:
        state = self._state[(equipment_id, part_name)]
        state.units_on_hand = max(0, state.units_on_hand - 1)
        state.consumption_by_week[week_num] = state.consumption_by_week.get(week_num, 0) + 1

    def snapshot_week(self, week_num: int, lookback_weeks: int = SPARE_PART_CONSUMPTION_LOOKBACK_WEEKS) -> list[dict]:
        """Applies any due arrivals, evaluates the reorder point, and returns
        this week's snapshot rows for every tracked (equipment, part)."""
        rows = []
        for (equipment_id, part_name), state in self._state.items():
            lead_time_days = SPARE_PART_LEAD_TIME_DAYS[part_name]

            if state.pending_arrival_week is not None and state.pending_arrival_week <= week_num:
                state.units_on_hand += state.pending_qty
                state.pending_arrival_week = None
                state.pending_qty = 0

            window_start = max(1, week_num - lookback_weeks + 1)
            window_weeks = range(window_start, week_num + 1)
            total_consumed = sum(state.consumption_by_week.get(w, 0) for w in window_weeks)
            avg_weekly_consumption = total_consumed / len(window_weeks)
            reorder_point = (lead_time_days / 7.0) * avg_weekly_consumption

            if state.units_on_hand <= reorder_point and state.pending_arrival_week is None:
                qty = max(1, round(avg_weekly_consumption * (lead_time_days / 7.0)))
                lead_time_weeks = max(1, round(lead_time_days / 7.0))
                state.pending_arrival_week = week_num + lead_time_weeks
                state.pending_qty = qty

            rows.append(
                {
                    "equipment_id": equipment_id,
                    "spare_part_name": part_name,
                    "units_on_hand": state.units_on_hand,
                    "lead_time_days": lead_time_days,
                }
            )
        return rows
