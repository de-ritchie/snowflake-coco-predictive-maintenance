"""Health/reading formulas and Weibull/Exponential T_fail draws (LLD SS4).

Degradation only accrues during actual operating hours -- callers are
responsible for only invoking `generate_reading` once per simulated tick, not
for holiday/idle/downtime hours.
"""

import numpy as np

from generator.fulldata.machine_config import (
    FAILURE_MODE_NAMES,
    FAILURE_MODE_PROBABILITIES,
    FAILURE_MODES,
    NOISE_STD_FACTOR,
    UNEXPLAINABLE_MEAN_HOURS,
    WEIBULL_SHAPE,
)


def draw_failure_mode(rng: np.random.Generator) -> str:
    """Draw a failure mode per maintenance cycle, per LLD SS3 probabilities."""
    return rng.choice(FAILURE_MODE_NAMES, p=FAILURE_MODE_PROBABILITIES)


def draw_t_fail(rng: np.random.Generator, mode: str, weibull_lambda_hours: float) -> float:
    """T_fail ~ Weibull(shape=2.0, scale=lambda) for explainable modes;
    Exponential(mean=400h) for unexplainable -- LLD SS4."""
    if mode == "UNEXPLAINABLE":
        return rng.exponential(UNEXPLAINABLE_MEAN_HOURS)
    return rng.weibull(WEIBULL_SHAPE) * weibull_lambda_hours


def health(h0: float, sensitivity: float, t_hours: float, t_fail_hours: float) -> float:
    """H_s(t) = H0_s - sensitivity_s * (t / T_fail)^2 -- LLD SS4, generalized
    with a per-sensor starting health H0_s so that maintenance-event
    restoration (LLD SS5, Uniform(0.90,0.95)/Uniform(0.70,0.80)/unchanged)
    can be represented as the next cycle's starting point rather than
    assuming every cycle starts at a perfect H=1.0."""
    return max(0.0, h0 - sensitivity * (t_hours / t_fail_hours) ** 2)


def generate_reading(
    rng: np.random.Generator,
    sensor: str,
    baseline_mean: float,
    baseline_std: float,
    mode: str,
    h0: float,
    t_hours: float,
    t_fail_hours: float,
) -> float:
    """reading_s(t) per LLD SS4.

    Unexplainable mode: flat baseline + noise for every tick, no precursor,
    right up through the tick where T_fail is reached (design doc SS7).
    """
    noise = rng.normal(0.0, NOISE_STD_FACTOR * baseline_std)
    if mode == "UNEXPLAINABLE":
        return baseline_mean + noise

    sensitivity = FAILURE_MODES[mode]["sensitivity"][sensor]
    amplitude = 4 * baseline_std
    h = health(h0, sensitivity, t_hours, t_fail_hours)
    return baseline_mean + (1 - h) * amplitude + noise
