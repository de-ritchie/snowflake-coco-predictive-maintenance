"""Weekly PM/breakdown crew-capacity resolution (LLD SS5, design doc SS8).

Only the pure decision logic lives here -- the two-pass-per-week orchestration
(Monday decision, then day-by-day tick simulation) lives in simulate.py, which
calls into this module for each decision point.
"""

from datetime import date

import numpy as np

from generator.fulldata.sim_calendar import BACKGROUND_PLANT_DRAW_PROBABILITY

PM_DUE_INTERVAL_DAYS = 30


def background_plant_draw(rng: np.random.Generator, p: float = BACKGROUND_PLANT_DRAW_PROBABILITY) -> bool:
    """True if this week's/weekend's single crew slot is already consumed
    elsewhere in the plant (LLD SS5 step 2)."""
    return bool(rng.random() < p)


def is_pm_due(current_monday: date, last_service_date: date) -> bool:
    """PM due every 30 calendar days per machine (LLD SS5)."""
    return (current_monday - last_service_date).days >= PM_DUE_INTERVAL_DAYS


def decide_pm_winner(
    rng: np.random.Generator,
    due_machines: list[str],
    machine_line: dict[str, str],
    order_units_by_line: dict[str, float],
    days_since_service: dict[str, int],
) -> str | None:
    """Among machines with PM due, the line with the higher this-week raw
    order volume wins the slot; ties within the winning line broken by the
    more-overdue machine (design doc SS8 step 3, documented default).
    """
    if not due_machines:
        return None

    by_line: dict[str, list[str]] = {}
    for m in due_machines:
        by_line.setdefault(machine_line[m], []).append(m)

    winning_line = max(by_line, key=lambda ln: order_units_by_line.get(ln, 0.0))
    candidates = by_line[winning_line]
    if len(candidates) == 1:
        return candidates[0]
    return max(candidates, key=lambda m: days_since_service[m])
