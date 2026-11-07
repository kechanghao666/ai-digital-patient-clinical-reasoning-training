from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class IntegrationSettings:
    """Server-side integration settings. Values are read from the process environment only."""

    mode: str
    nexent_base_url: str
    nexent_api_key: str
    patient_agent_name: str
    feedback_agent_name: str
    connect_timeout_s: float
    first_event_timeout_s: float
    total_timeout_s: float
    turn_ttl_s: float
    mcp_service_token: str
    integration_db_path: Path
    knowledge_dir: Path

    @classmethod
    def from_env(cls) -> "IntegrationSettings":
        return cls(
            mode=os.getenv("NEXENT_MODE", "simulator").strip().lower() or "simulator",
            nexent_base_url=os.getenv("NEXENT_BASE_URL", "").strip().rstrip("/"),
            nexent_api_key=os.getenv("NEXENT_API_KEY", ""),
            patient_agent_name=os.getenv("NEXENT_PATIENT_AGENT", os.getenv("NEXENT_AGENT_NAME", "")).strip(),
            feedback_agent_name=os.getenv("NEXENT_FEEDBACK_AGENT", "").strip(),
            connect_timeout_s=_float("NEXENT_CONNECT_TIMEOUT_S", 5.0),
            first_event_timeout_s=_float("NEXENT_FIRST_EVENT_TIMEOUT_S", 20.0),
            total_timeout_s=_float("NEXENT_TOTAL_TIMEOUT_S", 55.0),
            turn_ttl_s=_float("TURN_TTL_S", 120.0),
            mcp_service_token=os.getenv("MCP_SERVICE_TOKEN", ""),
            integration_db_path=Path(
                os.getenv("INTEGRATION_DB_PATH", str(ROOT / "backend" / "data" / "integration.db"))
            ),
            knowledge_dir=Path(os.getenv("KNOWLEDGE_DIR", str(ROOT / "knowledge"))),
        )

    @property
    def uses_nexent(self) -> bool:
        return self.mode == "nexent"
