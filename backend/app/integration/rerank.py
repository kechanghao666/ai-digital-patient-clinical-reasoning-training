from __future__ import annotations

import math

import httpx

from .config import IntegrationSettings


class RerankError(Exception):
    """Raised when the rerank service is not configured or the call fails.

    Carries a short machine-readable reason; never the credential itself.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class RerankClient:
    """Read-only rerank client used only for the teacher-side basis shortlist.

    Sends the query plus the already-narrowed candidate documents in one call and
    returns scores ordered by relevance. The response array order is never trusted:
    each item carries an `index` pointing back at the submitted document position,
    and ranking is computed from `relevance_score`, not array position.
    """

    def __init__(self, settings: IntegrationSettings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.settings.rerank_enabled

    def rerank(self, query: str, documents: list[str]) -> list[tuple[float, int]]:
        """Return [(score, document_index)] sorted by score desc; never reorder the input."""
        if not self.enabled:
            raise RerankError("rerank_unconfigured")
        try:
            resp = httpx.post(
                self.settings.rerank_base_url,
                json={"model": self.settings.rerank_model, "query": query, "documents": documents},
                headers={"Authorization": f"Bearer {self.settings.rerank_api_key}"},
                timeout=20.0,
            )
        except httpx.TimeoutException as exc:
            raise RerankError("rerank_timeout") from exc
        except httpx.HTTPError as exc:
            raise RerankError("rerank_network_error") from exc
        if resp.status_code != 200:
            raise RerankError("rerank_http_error")
        try:
            body = resp.json()
        except (TypeError, ValueError) as exc:
            raise RerankError("rerank_bad_response") from exc
        items = body.get("results") if isinstance(body, dict) else None
        if items is None:
            items = body.get("data") if isinstance(body, dict) else None
        if not isinstance(items, list) or len(items) != len(documents):
            raise RerankError("rerank_bad_response")
        scores: list[float | None] = [None] * len(documents)
        for item in items:
            if not isinstance(item, dict):
                raise RerankError("rerank_bad_response")
            index, score = item.get("index"), item.get("relevance_score")
            if (
                type(index) is not int
                or not 0 <= index < len(documents)
                or scores[index] is not None
                or isinstance(score, bool)
                or not isinstance(score, (int, float))
            ):
                raise RerankError("rerank_bad_response")
            try:
                scores[index] = float(score)
            except OverflowError as exc:
                raise RerankError("rerank_bad_response") from exc
            if not math.isfinite(scores[index]):
                raise RerankError("rerank_bad_response")
        if any(score is None for score in scores):
            raise RerankError("rerank_bad_response")
        ordered = sorted(range(len(documents)), key=lambda i: (-scores[i], i))
        return [(scores[i], i) for i in ordered]
