from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class ActionRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    action: Literal["ask", "wait", "diagnose"]
    text: str = Field(default="", max_length=2000)


class PublicEvent(BaseModel):
    type: str
    message: str


class SessionState(BaseModel):
    session_id: str
    owner: str
    status: Literal["active", "sealed"] = "active"
    round: int = 0
    facts: list[str] = Field(default_factory=list)
    events: list[PublicEvent] = Field(default_factory=list)
    last_error: str | None = None
    score_status: Literal["not_scored", "complete", "needs_review"] = "not_scored"
    score: int | None = None
    replay: dict[str, dict] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ActionResponse(BaseModel):
    session_id: str
    status: str
    round: int
    message: str
    score_status: str
    score: int | None = None
    error: str | None = None
    events: list[PublicEvent]


def new_session(owner: str) -> SessionState:
    return SessionState(session_id=str(uuid4()), owner=owner)
