"""CMMS log rows + technician-note templates (LLD SS5, design doc SS9)."""

import uuid
from datetime import datetime

import numpy as np

TECHNICIAN_NOTE_TEMPLATES: dict[str, list[str]] = {
    "BEARING_WEAR": [
        "Vibration trending up over past week, elevated bearing temp; bearing replaced.",
        "Audible bearing noise reported by operator; inspected and replaced worn bearing.",
        "Routine vibration check flagged early bearing wear; preemptively serviced.",
        "Bearing temperature alarm triggered; bearing housing serviced and repacked.",
    ],
    "TOOL_WEAR": [
        "Vibration spikes on boring passes; insert showing visible wear, replaced.",
        "Surface finish quality drift reported; tool insert swapped.",
        "Tool life counter exceeded threshold; insert replaced during scheduled check.",
        "Chatter noted during machining; worn insert identified and replaced.",
    ],
    "COOLANT_THERMAL": [
        "Elevated spindle temperature trend; coolant flow inspected and coolant topped off/replaced.",
        "Coolant concentration low, contributing to thermal drift; coolant system flushed and refilled.",
        "Thermal alarm on spindle housing; coolant line partially blocked, cleared and serviced.",
        "Temperature creeping above baseline over past days; coolant pump serviced.",
    ],
    "SERVO_RPM_INSTABILITY": [
        "RPM instability observed under load; servo drive parameters recalibrated.",
        "Intermittent speed fluctuation reported by operator; servo motor inspected and adjusted.",
        "RPM feedback drift trending outside tolerance; servo encoder cleaned/recalibrated.",
        "Speed control alarm triggered; servo drive board reseated and tested.",
    ],
    "UNEXPLAINABLE": [
        "No precursor signal; machine stopped unexpectedly. Root cause not identified -- restarted after inspection.",
        "Sudden stoppage, no sensor trend preceding event. Electrical fault suspected; reset and returned to service.",
        "Unplanned downtime with no clear cause; control system reset resolved the issue.",
        "Unexpected halt reported by operator; no fault found on inspection, machine restarted.",
    ],
}

PM_NOTE_TEMPLATES: list[str] = [
    "Scheduled preventive maintenance performed; all sensors inspected and serviced.",
    "Routine PM completed on schedule; general wear check and service performed.",
    "Preventive maintenance service completed; machine restored to baseline condition.",
]


def pick_technician_note(rng: np.random.Generator, mode: str) -> str:
    templates = TECHNICIAN_NOTE_TEMPLATES[mode]
    return templates[rng.integers(0, len(templates))]


def pick_pm_note(rng: np.random.Generator) -> str:
    return PM_NOTE_TEMPLATES[rng.integers(0, len(PM_NOTE_TEMPLATES))]


def make_event(
    equipment_id: str,
    event_type: str,
    event_start_ts: datetime,
    event_end_ts: datetime,
    technician_notes: str,
) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "equipment_id": equipment_id,
        "event_type": event_type,
        "event_start_ts": event_start_ts,
        "event_end_ts": event_end_ts,
        "technician_notes": technician_notes,
    }
