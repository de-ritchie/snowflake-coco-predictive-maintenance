"""No-op stub seam for the LLD SS10 fallback hand-patch (design doc SS11).

Not called from full_data_generator.py's main flow. Filled in by S-DATA-10.
"""


def apply_engine_head_patch(output_dir: str, *args, **kwargs) -> None:
    """Post-generation hand-patch of CNC Horizontal's trailing-30-day
    maintenance/sensor data, per LLD Module 2 SS10's accepted fallback.

    # TODO(S-DATA-10): implement the actual patch logic (push back the last
    # PM record, re-apply the SS4 health/reading formula with a larger t).
    """
    return
