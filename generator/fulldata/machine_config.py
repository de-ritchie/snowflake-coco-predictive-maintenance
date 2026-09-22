"""Machine baselines, product mapping, and failure-mode signatures.

Values per LLD Module 2 SS2-3 and design doc SS5/SS7 (machine/product mapping
resolution, per-machine Weibull lambda).
"""

SENSOR_TYPES: list[str] = ["VIBRATION", "TEMPERATURE", "RPM"]
NOISE_STD_FACTOR = 0.3  # additive Gaussian noise_std = 0.3 * baseline std (LLD SS2)
CADENCE_MINUTES = 15
WEIBULL_SHAPE = 2.0
UNEXPLAINABLE_MEAN_HOURS = 400.0  # Exponential(mean=400h), design doc SS7

# 3 sensor-enabled machines. Only the 2 primary order series are generated
# (design doc SS5) -- EV Caliper (Boring+Milling), ICE Engine Head (Horizontal).
MACHINES: dict[str, dict] = {
    "CNC_BORING": {
        "equipment_name": "CNC Boring",
        "line_name": "Caliper",
        "product_id": "BRAKE_CALIPER",
        "variant": "EV",
        "throughput_units_per_hour": 15,
        "weibull_lambda_hours": 650.0,
        "sensor_baselines": {
            "VIBRATION": (2.5, 0.3),
            "TEMPERATURE": (45.0, 3.0),
            "RPM": (2400.0, 150.0),
        },
    },
    "CNC_MILLING": {
        "equipment_name": "CNC Milling",
        "line_name": "Caliper",
        "product_id": "BRAKE_CALIPER",
        "variant": "EV",
        "throughput_units_per_hour": 15,
        "weibull_lambda_hours": 600.0,
        "sensor_baselines": {
            "VIBRATION": (3.0, 0.4),
            "TEMPERATURE": (48.0, 3.0),
            "RPM": (3200.0, 200.0),
        },
    },
    "CNC_HORIZONTAL": {
        "equipment_name": "CNC Horizontal Machining Center",
        "line_name": "Engine Head",
        "product_id": "ENGINE_HEAD",
        "variant": "ICE",
        "throughput_units_per_hour": 10,
        "weibull_lambda_hours": 750.0,
        "sensor_baselines": {
            "VIBRATION": (2.2, 0.3),
            "TEMPERATURE": (42.0, 3.0),
            "RPM": (6000.0, 300.0),
        },
    },
}

# Non-sensor-enabled value-stream stages, for equipment master completeness
# (FR-DG-02 / BRD SS5.1). These never generate sensor telemetry or accrue wear.
NON_SENSOR_EQUIPMENT: list[dict] = [
    # Line A -- Brake Caliper (BRD SS5.1)
    {"equipment_id": "CASTING_CALIPER", "equipment_name": "Casting Receipt & Inspection (Caliper)",
     "line_name": "Caliper", "product_id": "BRAKE_CALIPER", "variant": "EV", "throughput_units_per_hour": 15},
    {"equipment_id": "DRILLING_TAPPING", "equipment_name": "Drilling/Tapping",
     "line_name": "Caliper", "product_id": "BRAKE_CALIPER", "variant": "EV", "throughput_units_per_hour": 15},
    {"equipment_id": "DEBURR_WASH_CALIPER", "equipment_name": "Deburr & Wash (Caliper)",
     "line_name": "Caliper", "product_id": "BRAKE_CALIPER", "variant": "EV", "throughput_units_per_hour": 15},
    {"equipment_id": "LEAK_PRESSURE_TEST", "equipment_name": "Leak/Pressure Test",
     "line_name": "Caliper", "product_id": "BRAKE_CALIPER", "variant": "EV", "throughput_units_per_hour": 15},
    {"equipment_id": "COATING_PAINTING", "equipment_name": "Coating/Painting",
     "line_name": "Caliper", "product_id": "BRAKE_CALIPER", "variant": "EV", "throughput_units_per_hour": 15},
    {"equipment_id": "ASSEMBLY_CALIPER", "equipment_name": "Assembly (Caliper)",
     "line_name": "Caliper", "product_id": "BRAKE_CALIPER", "variant": "EV", "throughput_units_per_hour": 15},
    {"equipment_id": "FINAL_TEST_PACKAGING_CALIPER", "equipment_name": "Final Test & Packaging (Caliper)",
     "line_name": "Caliper", "product_id": "BRAKE_CALIPER", "variant": "EV", "throughput_units_per_hour": 15},
    # Line B -- Engine Head (BRD SS5.1)
    {"equipment_id": "CASTING_ENGINE_HEAD", "equipment_name": "Casting Receipt & Inspection (Engine Head)",
     "line_name": "Engine Head", "product_id": "ENGINE_HEAD", "variant": "ICE", "throughput_units_per_hour": 10},
    {"equipment_id": "VALVE_SEAT_PRESS_FIT", "equipment_name": "Valve Seat/Guide Press-Fit",
     "line_name": "Engine Head", "product_id": "ENGINE_HEAD", "variant": "ICE", "throughput_units_per_hour": 10},
    {"equipment_id": "DEBURR_WASH_ENGINE_HEAD", "equipment_name": "Deburr & Wash (Engine Head)",
     "line_name": "Engine Head", "product_id": "ENGINE_HEAD", "variant": "ICE", "throughput_units_per_hour": 10},
    {"equipment_id": "HYDRO_LEAK_TEST", "equipment_name": "Hydro/Leak Test",
     "line_name": "Engine Head", "product_id": "ENGINE_HEAD", "variant": "ICE", "throughput_units_per_hour": 10},
    {"equipment_id": "FINAL_INSPECTION_PACKAGING", "equipment_name": "Final Inspection & Packaging",
     "line_name": "Engine Head", "product_id": "ENGINE_HEAD", "variant": "ICE", "throughput_units_per_hour": 10},
]

# 5 failure modes per LLD Module 2 SS3. Sensitivity is None for the
# unexplainable mode (no buildup, see degradation.py).
FAILURE_MODES: dict[str, dict] = {
    "BEARING_WEAR": {
        "probability": 0.28,
        "sensitivity": {"VIBRATION": 0.8, "TEMPERATURE": 0.6, "RPM": 0.1},
    },
    "TOOL_WEAR": {
        "probability": 0.24,
        "sensitivity": {"VIBRATION": 0.9, "TEMPERATURE": 0.1, "RPM": 0.05},
    },
    "COOLANT_THERMAL": {
        "probability": 0.20,
        "sensitivity": {"VIBRATION": 0.05, "TEMPERATURE": 0.9, "RPM": 0.05},
    },
    "SERVO_RPM_INSTABILITY": {
        "probability": 0.18,
        "sensitivity": {"VIBRATION": 0.15, "TEMPERATURE": 0.05, "RPM": 0.85},
    },
    "UNEXPLAINABLE": {
        "probability": 0.10,
        "sensitivity": None,
    },
}

FAILURE_MODE_NAMES: list[str] = list(FAILURE_MODES)
FAILURE_MODE_PROBABILITIES: list[float] = [FAILURE_MODES[m]["probability"] for m in FAILURE_MODE_NAMES]

# Spare part consumed by a breakdown repair for each explainable mode
# (build-time fill-in: LLD/design doc specify the reorder-point *policy*, SS10,
# but not exact part names/lead times -- chosen here as concrete, reasonable
# values). Unexplainable-mode breakdowns consume no part (no fault found).
SPARE_PART_BY_MODE: dict[str, str] = {
    "BEARING_WEAR": "BEARING",
    "TOOL_WEAR": "TOOL_INSERT",
    "COOLANT_THERMAL": "COOLANT_PUMP",
    "SERVO_RPM_INSTABILITY": "SERVO_DRIVE",
}

SPARE_PART_LEAD_TIME_DAYS: dict[str, int] = {
    "BEARING": 14,
    "TOOL_INSERT": 7,
    "COOLANT_PUMP": 10,
    "SERVO_DRIVE": 21,
}
