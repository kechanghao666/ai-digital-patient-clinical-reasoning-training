"""Teacher-side semantic teaching-basis search.

These tests replace the embedding service with a deterministic offline stand-in so they prove the
API contract only (bounded, sourced, draft-labelled, read-only, vector reuse). They are NOT evidence
that a real embedding provider is authorised or reachable; that is recorded separately.
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.integration.embedding import EmbeddingClient, EmbeddingError
from app.integration.runtime import IntegrationRuntime
from test_integration import settings_for

TEACHER = {"X-Owner-Id": "teacher-basis"}
STUDENT = {"X-Owner-Id": "student-basis"}
DEMO = {"X-Owner-Id": "demo-basis"}
PATH = "/api/teacher/feedback/basis/search"

# Deterministic bag-of-keywords vectors make similarity controllable offline without the network.
KEYWORDS = ("心电图", "肌钙蛋白", "胸痛", "再灌注", "阿司匹林", "夹层", "肺栓塞", "反馈", "病史")


def vector(text: str) -> list[float]:
    return [1.0 if keyword in text else 0.0 for keyword in KEYWORDS]


def make_runtime(tmp_path, configured: bool) -> IntegrationRuntime:
    overrides = (
        {"embedding_base_url": "http://embed.test/v1", "embedding_api_key": "offline-test-key",
         "embedding_model": "offline-model"}
        if configured else {}
    )
    return IntegrationRuntime(settings_for(tmp_path, **overrides),
                              httpx.MockTransport(lambda request: httpx.Response(500)))


def install(tmp_path, monkeypatch, configured=True):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("APP_AUTH_MODE", raising=False)
    runtime = make_runtime(tmp_path, configured)
    monkeypatch.setattr(main, "RUNTIME", runtime)
    calls: list[list[str]] = []

    def fake_embed(self, texts):  # noqa: ANN001 - patched onto EmbeddingClient.embed
        calls.append(list(texts))
        return [vector(text) for text in texts]

    monkeypatch.setattr(EmbeddingClient, "embed", fake_embed)
    return runtime, TestClient(main.app), calls


def search(client, query: str, headers=TEACHER):
    return client.post(PATH, headers=headers, json={"query": query})


def test_search_is_bounded_sourced_and_draft_labelled(tmp_path, monkeypatch):
    runtime, client, _ = install(tmp_path, monkeypatch)
    response = search(client, "心电图 肌钙蛋白 胸痛 再灌注 阿司匹林")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    assert body["status"] == runtime.knowledge.status == "draft_unreviewed"
    assert body["notice"] == "草稿，未医学审核"
    assert len(body["results"]) == 3  # six entries match; the shortlist is capped at three
    for item in body["results"]:
        assert item["title"] and item["text"]
        assert item["sources"] and item["stages"] and item["applies_to_cases"]
        assert isinstance(item["score"], float)


def test_search_reuses_library_vectors_across_queries(tmp_path, monkeypatch):
    runtime, client, calls = install(tmp_path, monkeypatch)
    assert search(client, "心电图").status_code == 200
    assert len(calls) == 2  # one batch for the fixed library, one for the query
    assert len(calls[0]) == len(runtime.knowledge.entries) == 9
    assert calls[1] == ["心电图"]
    assert search(client, "肌钙蛋白").status_code == 200
    assert len(calls) == 3  # the library was not re-sent; only the new query was embedded
    assert calls[2] == ["肌钙蛋白"]
    library_texts = [text for batch in calls for text in batch if text not in ("心电图", "肌钙蛋白")]
    assert len(library_texts) == 9


def test_search_reports_no_reliable_match(tmp_path, monkeypatch):
    _, client, _ = install(tmp_path, monkeypatch)
    response = search(client, "今天天气怎么样")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True and body["results"] == []


def test_search_reports_unconfigured_embedding(tmp_path, monkeypatch):
    _, client, _ = install(tmp_path, monkeypatch, configured=False)
    response = search(client, "心电图")
    assert response.status_code == 200
    assert response.json() == {"available": False, "reason": "embedding_unconfigured", "results": []}


def test_search_survives_embedding_service_failure(tmp_path, monkeypatch):
    _, client, _ = install(tmp_path, monkeypatch)

    def boom(self, texts):  # noqa: ANN001
        raise EmbeddingError("embedding_http_error")

    monkeypatch.setattr(EmbeddingClient, "embed", boom)
    response = search(client, "心电图")
    assert response.status_code == 200
    assert response.json() == {"available": False, "reason": "embedding_http_error", "results": []}


@pytest.mark.parametrize("headers", [{}, DEMO, STUDENT])
def test_search_requires_teacher(tmp_path, monkeypatch, headers):
    _, client, calls = install(tmp_path, monkeypatch)
    response = search(client, "心电图", headers)
    assert response.status_code in (401, 403)
    assert calls == []  # a rejected caller never reaches the embedding service
    assert search(client, "心电图").status_code == 200
