"""Order series generation + trailing smoothing (LLD SS7, design doc SS5).

Only the 2 primary product/variant series are generated (design doc SS5) --
secondary variants (ICE Caliper, EV Engine Head) are dropped entirely.

Baseline order-volume magnitudes (BASELINE_EV_CALIPER_UNITS,
BASELINE_ICE_ENGINE_HEAD_UNITS) are a build-time numeric fill-in -- the LLD/
design doc fix the trend/ramp *shape* precisely but not the absolute
magnitude; chosen here so required-hours sits at a moderate fraction of
weekly capacity historically and approaches (but doesn't exceed) capacity
during the look-ahead spike.
"""

import numpy as np

# line_name -> (product_id, variant), per design doc SS5's resolved mapping.
PRODUCTS: dict[str, tuple[str, str]] = {
    "Caliper": ("BRAKE_CALIPER", "EV"),
    "Engine Head": ("ENGINE_HEAD", "ICE"),
}

BASELINE_EV_CALIPER_UNITS = 1400.0
BASELINE_ICE_ENGINE_HEAD_UNITS = 950.0
ORDER_NOISE_STD_FACTOR = 0.05  # +/-5% weekly noise (LLD SS7)
SMOOTHING_WINDOW_WEEKS = 4


def _lookahead_ramp_ev(week_num: int, historical_weeks: int) -> float:
    """EV Caliper: ramps 1.0 -> 1.6 across look-ahead weeks 1-4, holds at 1.6
    through weeks 5-8 (LLD SS7)."""
    look_ahead_week = week_num - historical_weeks
    if look_ahead_week <= 0:
        return 1.0
    if look_ahead_week <= 4:
        return 1.0 + 0.6 * (look_ahead_week / 4.0)
    return 1.6


def _lookahead_bump_ice(week_num: int, historical_weeks: int) -> float:
    """ICE Engine Head: flat at 1.0 through look-ahead weeks 1-4, then ramps
    1.0 -> 1.3 across weeks 5-8 (LLD SS7)."""
    look_ahead_week = week_num - historical_weeks
    if look_ahead_week <= 4:
        return 1.0
    return 1.0 + 0.3 * ((look_ahead_week - 4) / 4.0)


def generate_order_series(
    rng: np.random.Generator, total_weeks: int, historical_weeks: int
) -> dict[str, list[float]]:
    """Returns {line_name: [order_units for week 1..total_weeks]} (raw, unsmoothed)."""
    ev_series: list[float] = []
    ice_series: list[float] = []
    for w in range(1, total_weeks + 1):
        trend_ev = 1 + 0.20 * w / historical_weeks
        noise_ev = rng.normal(0.0, ORDER_NOISE_STD_FACTOR * BASELINE_EV_CALIPER_UNITS)
        ev_units = BASELINE_EV_CALIPER_UNITS * trend_ev * _lookahead_ramp_ev(w, historical_weeks) + noise_ev
        ev_series.append(max(0.0, ev_units))

        trend_ice = 1 - 0.15 * w / historical_weeks
        noise_ice = rng.normal(0.0, ORDER_NOISE_STD_FACTOR * BASELINE_ICE_ENGINE_HEAD_UNITS)
        ice_units = (
            BASELINE_ICE_ENGINE_HEAD_UNITS * trend_ice * _lookahead_bump_ice(w, historical_weeks) + noise_ice
        )
        ice_series.append(max(0.0, ice_units))

    return {"Caliper": ev_series, "Engine Head": ice_series}


def smoothed_orders(series: list[float], week_num: int, window: int = SMOOTHING_WINDOW_WEEKS) -> float:
    """Trailing `window`-week moving average through week_num (1-indexed)."""
    start = max(1, week_num - window + 1)
    values = series[start - 1 : week_num]
    return sum(values) / len(values)
