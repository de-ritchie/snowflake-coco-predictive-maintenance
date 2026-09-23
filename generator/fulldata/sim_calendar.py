"""Working-day/holiday calendar + week indexing (design doc SS6, LLD SS1).

Week numbering is anchored to `--now`: week 1 starts on the Monday on/before
`now - 3 years`; weeks are Mon-Sun; week `historical_weeks` (default 156) is
the last full historical week; weeks after that are the look-ahead.
"""

from datetime import date, timedelta

# 10 fixed generic holidays/year, recurring on the same month/day every year
# of the run regardless of weekday (design doc SS6).
HOLIDAYS_MONTH_DAY: list[tuple[int, int]] = [
    (1, 1),   # New Year's Day
    (2, 15),  # Winter Break Day
    (4, 10),  # Spring Holiday
    (5, 1),   # Labor Day
    (6, 19),  # Mid-Year Founders Day
    (7, 4),   # Summer Holiday
    (9, 1),   # Harvest Day
    (10, 31),  # Autumn Holiday
    (11, 27),  # Thanksgiving-style Day
    (12, 25),  # Winter Holiday
]

RESIDUAL_IDLE_PROBABILITY = 0.025  # ~2.5%, mid-range of LLD's 2-3% (design doc SS6)
BACKGROUND_PLANT_DRAW_PROBABILITY = 0.25  # LLD SS5 mid-range default


def is_holiday(d: date) -> bool:
    return (d.month, d.day) in HOLIDAYS_MONTH_DAY


def is_working_day(d: date) -> bool:
    """Mon-Fri and not a holiday."""
    return d.weekday() < 5 and not is_holiday(d)


def run_start_monday(now: date, years_back: int = 3) -> date:
    """Monday on/before `now - years_back` years."""
    anchor = now.replace(year=now.year - years_back)
    return anchor - timedelta(days=anchor.weekday())


def week_start(base_monday: date, week_num: int) -> date:
    """Monday date for the given 1-indexed week number."""
    return base_monday + timedelta(weeks=week_num - 1)


def working_days_in_week(week_monday: date) -> list[date]:
    """Mon-Fri dates for the week, minus holidays."""
    candidates = [week_monday + timedelta(days=i) for i in range(5)]
    return [d for d in candidates if is_working_day(d)]


def calendar_dates(base_monday: date, total_weeks: int) -> list[date]:
    """Every date from `base_monday` through the last week's Sunday
    (`base_monday` .. `week_start(base_monday, total_weeks + 1) - 1 day`),
    no gaps -- used for RAW.CALENDAR (LLD SS1, design doc SS9 invariant 6)."""
    end_date = week_start(base_monday, total_weeks + 1) - timedelta(days=1)
    num_days = (end_date - base_monday).days + 1
    return [base_monday + timedelta(days=i) for i in range(num_days)]


def is_maintenance_weekend(week_num: int) -> bool:
    """Every other week-end (~26/year) is a designated maintenance weekend."""
    return week_num % 2 == 0


def maintenance_weekend_date(week_monday: date) -> date:
    """The Saturday of this week, used as the nominal maintenance-weekend day."""
    return week_monday + timedelta(days=5)
