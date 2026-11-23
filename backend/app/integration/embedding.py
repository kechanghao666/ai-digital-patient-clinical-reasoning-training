from __future__ import annotations

import math

import httpx

from .config import IntegrationSettings
from .knowledge import BasisEntry


class EmbeddingError(Exception):
    """Raised when the embedding service is not configured or the call fails.

    Carries a short machine-readable reason; never the credential itself.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class EmbeddingClient:
    """Thin OpenAI-compatible text-embedding client used only for teacher-side semantic search."""

    def __init__(self, settings: IntegrationSettings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return self.settings.embedding_enabled

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.enabled:
            raise EmbeddingError("embedding_unconfigured")
        try:
            resp = httpx.post(
                self.settings.embedding_base_url,
                json={"model": self.settings.embedding_model, "input": texts},
                headers={"Authorization": f"Bearer {self.settings.embedding_api_key}"},
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError("embedding_network_error") from exc
        if resp.status_code != 200:
            raise EmbeddingError("embedding_http_error")
        try:
            items = resp.json()["data"]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError("embedding_bad_response") from exc
        if not isinstance(items, list) or len(items) != len(texts):
            # A response that cannot be aligned one-to-one with the inputs must never be used.
            raise EmbeddingError("embedding_bad_response")
        # Each item carries an `index` pointing back at the input position. Restore input order
        # from the index instead of trusting the array order, and reject duplicates, out-of-range
        # or non-integer indexes as well as malformed vectors. On any mismatch nothing is
        # returned, so callers never cache a plausible-looking wrong vector.
        ordered: list[list[float] | None] = [None] * len(texts)
        for item in items:
            try:
                index = item["index"]
                embedding = item["embedding"]
            except (KeyError, TypeError):
                raise EmbeddingError("embedding_bad_response")
            if (
                type(index) is not int
                or not 0 <= index < len(texts)
                or ordered[index] is not None
                or not isinstance(embedding, list)
                or not embedding
                or not all(_is_number(value) for value in embedding)
            ):
                raise EmbeddingError("embedding_bad_response")
            ordered[index] = embedding
        # Vectors of one batch are only comparable with each other, and 1e400 arrives as inf.
        if len({len(vector) for vector in ordered}) > 1:
            raise EmbeddingError("embedding_bad_response")
        return ordered


def _is_number(value: object) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False  # JSON integers need not fit in a float.


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class BasisSemanticSearch:
    """Ranks teaching-basis entries by embedding similarity to a teacher query.

    Entry vectors are cached by content_hash so repeated searches do not re-embed
    the library; the query is embedded on every call.
    """

    def __init__(self, client: EmbeddingClient):
        self.client = client
        self._cache: dict[str, list[float]] = {}

    @staticmethod
    def _text(entry: BasisEntry) -> str:
        return f"{entry.title} {' '.join(entry.tags)} {entry.text}"

    def search(self, entries: list[BasisEntry], query: str, limit: int = 5) -> list[tuple[float, BasisEntry]]:
        missing = [e for e in entries if e.content_hash not in self._cache]
        if missing:
            vectors = self.client.embed([self._text(e) for e in missing])
            # The cache only grows, so a new vector that cannot be compared with the cached ones
            # taking part in this search must not be stored: it would keep the scope unusable.
            cached_dimensions = {
                len(self._cache[e.content_hash]) for e in entries if e.content_hash in self._cache
            }
            if cached_dimensions and any(len(vector) not in cached_dimensions for vector in vectors):
                raise EmbeddingError("embedding_bad_response")
            for entry, vector in zip(missing, vectors):
                self._cache[entry.content_hash] = vector
        query_vec = self.client.embed([query])[0]
        comparable = [e for e in entries if e.content_hash in self._cache]
        if any(len(self._cache[e.content_hash]) != len(query_vec) for e in comparable):
            # cosine() zips, so a shorter vector would be scored on its leading elements alone.
            raise EmbeddingError("embedding_bad_response")
        scored = [(cosine(query_vec, self._cache[e.content_hash]), e) for e in comparable]
        scored.sort(key=lambda pair: (-pair[0], pair[1].basis_id))
        return scored[:limit]
