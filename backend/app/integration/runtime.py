from __future__ import annotations

from functools import cached_property

import httpx

from ..adapters import PatientAdapter, SimulatorPatientAdapter
from .config import IntegrationSettings
from .feedback import FeedbackService, FeedbackStore
from .knowledge import KnowledgeStore
from .nexent_client import NexentClient, NexentError
from .patient_adapter import NexentPatientAdapter
from .score_reviews import ScoreReviewStore
from .turns import TurnStore


class IntegrationRuntime:
    """Lazily built integration objects, so importing the API never touches integration files."""

    def __init__(self, settings: IntegrationSettings | None = None, nexent_transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._transport = nexent_transport

    @cached_property
    def settings(self) -> IntegrationSettings:
        # Read once per process; changing the environment requires a restart.
        return self._settings or IntegrationSettings.from_env()

    @cached_property
    def turns(self) -> TurnStore:
        return TurnStore(self.settings.integration_db_path)

    @cached_property
    def knowledge(self) -> KnowledgeStore:
        return KnowledgeStore(self.settings.knowledge_dir)

    @cached_property
    def feedback_store(self) -> FeedbackStore:
        return FeedbackStore(self.settings.integration_db_path)

    @cached_property
    def score_reviews(self) -> ScoreReviewStore:
        return ScoreReviewStore(self.settings.integration_db_path)

    @cached_property
    def feedback_service(self) -> FeedbackService:
        client = None
        if self.settings.uses_nexent and self._transport is not None:
            client = NexentClient(self.settings, self._transport)
        return FeedbackService(self.settings, self.turns, self.feedback_store, self.knowledge, client)

    def patient_adapter(self) -> PatientAdapter:
        settings = self.settings
        if settings.mode == "simulator":
            return SimulatorPatientAdapter()
        if settings.mode == "nexent":
            return NexentPatientAdapter(settings, self.turns, NexentClient(settings, self._transport))
        raise NexentError("unavailable", "unsupported NEXENT_MODE")
