from __future__ import annotations

from typing import Any


ALLOWED_EVENT_TYPES = {"patient_answer", "authorized_result", "teaching_state"}


def filter_student_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Return only the public, reviewed projection of an internal event."""
    if event.get("type") not in ALLOWED_EVENT_TYPES:
        return None
    allowed = {"type", "content", "sim_time"}
    filtered = {key: value for key, value in event.items() if key in allowed}
    if "state" in event and isinstance(event["state"], dict):
        filtered["state"] = {
            key: value
            for key, value in event["state"].items()
            if key in {"workflow", "clinical_state", "sim_time_minutes"}
        }
    if "result" in event and isinstance(event["result"], dict):
        filtered["result"] = {
            key: value
            for key, value in event["result"].items()
            if key in {"answer", "status", "message"}
        }
    return filtered
