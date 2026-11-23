"""Teacher-side semantic teaching-basis search.

These tests replace the embedding service with a deterministic offline stand-in so they prove the
API contract only (case/stage-scoped candidates, bounded, sourced, draft-labelled, read-only, vector
reuse). They are NOT evidence that a real embedding provider is authorised or reachable; that is
recorded separately.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.integration.embedding import EmbeddingClient, EmbeddingError
from app.integration.runtime import IntegrationRuntime
from app.state_machine import StateMachine
from app.storage import SessionRepository
from test_integration import settings_for

TEACHER = {"X-Owner-Id": "teacher-basis"}
STUDENT = {"X-Owner-Id": "student-basis"}
DEMO = {"X-Owner-Id": "demo-basis"}
OWNER = "student-basis"
PATH = "/api/teacher/feedback/basis/search"

# Deterministic bag-of-keywords vectors make similarity controllable offline without the network.
KEYWORDS = ("心电图", "肌钙蛋白", "胸痛", "再灌注", "阿司匹林", "夹层", "肺栓塞", "反馈", "病史")
STEMI_QUERY = "心电图 肌钙蛋白 胸痛 再灌注 阿司匹林"


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
    repository = SessionRepository(tmp_path / "app.db")
    monkeypatch.setattr(main, "REPOSITORY", repository)
    monkeypatch.setattr(main, "MACHINE", StateMachine(main.CASE_STORE, repository))
    runtime = make_runtime(tmp_path, configured)
    monkeypatch.setattr(main, "RUNTIME", runtime)
    calls: list[list[str]] = []

    def fake_embed(self, texts):  # noqa: ANN001 - patched onto EmbeddingClient.embed
        calls.append(list(texts))
        return [vector(text) for text in texts]

    monkeypatch.setattr(EmbeddingClient, "embed", fake_embed)
    return runtime, TestClient(main.app), calls, repository


def seal_session(client, case_id: str = "CASE-001", owner: str = OWNER) -> str:
    created = client.post(f"/api/sessions?case_id={case_id}", headers={"X-Owner-Id": owner})
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]
    sealed = client.post(f"/api/sessions/{session_id}/seal", headers={"X-Owner-Id": owner},
                         json={"request_id": "seal"})
    assert sealed.status_code == 200, sealed.text
    return session_id


def search(client, query: str, session_id: str, *, stage=None, owner=OWNER, headers=TEACHER):
    return client.post(PATH, headers=headers, json={
        "session_id": session_id, "owner_id": owner, "query": query, "stage": stage,
    })


def test_search_is_scoped_to_the_case_and_bounded(tmp_path, monkeypatch):
    runtime, client, _, _ = install(tmp_path, monkeypatch)
    session_id = seal_session(client)
    response = search(client, STEMI_QUERY, session_id)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    assert body["status"] == runtime.knowledge.status == "draft_unreviewed"
    assert body["notice"] == "草稿，未医学审核"
    assert body["scope"] == {"case_id": "STEMI-001", "case_version": 3, "stage": None,
                             "clinical_candidates": 8}
    assert len(body["results"]) == 3  # several entries match; the shortlist is capped at three
    for item in body["results"]:
        assert item["title"] and item["text"]
        assert item["sources"] and item["stages"]
        assert "STEMI-001" in item["applies_to_cases"]
        assert item["basis_id"] != "kb_feedback_principles"  # guidance is never clinical evidence
        assert isinstance(item["score"], float)


def test_search_with_a_stage_only_ranks_that_stages_entries(tmp_path, monkeypatch):
    _, client, _, _ = install(tmp_path, monkeypatch)
    session_id = seal_session(client)
    body = search(client, STEMI_QUERY, session_id, stage="检查选择").json()
    assert body["scope"]["stage"] == "检查选择"
    assert body["scope"]["clinical_candidates"] == 2  # kb_ecg_first and kb_troponin_role only
    assert body["results"]
    assert all("检查选择" in item["stages"] for item in body["results"])


def test_search_never_returns_another_specialtys_material(tmp_path, monkeypatch):
    """普外 training: the same cardiology worded query must not surface cardiology entries."""
    runtime, client, _, repository = install(tmp_path, monkeypatch)
    session_id = seal_session(client, case_id="CASE-002")  # 普外
    assert repository.get(session_id, OWNER).case_id == "APPY-001"

    # The gap this closes: ranking the whole library would have offered a cardiology entry.
    unscoped = runtime.basis_search.search(
        list(runtime.knowledge.entries.values()), STEMI_QUERY, limit=3
    )
    assert any(entry.applies_to_cases == ("STEMI-001",) for _, entry in unscoped)

    body = search(client, STEMI_QUERY, session_id, stage="检查选择").json()
    assert body["available"] is True
    assert body["scope"] == {"case_id": "APPY-001", "case_version": 2, "stage": "检查选择",
                             "clinical_candidates": 0}
    assert body["results"] == []
    assert "STEMI-001" not in json.dumps(body["results"])


def test_search_reuses_vectors_across_queries_and_stages(tmp_path, monkeypatch):
    runtime, client, calls, repository = install(tmp_path, monkeypatch)
    session_id = seal_session(client)
    state = repository.get(session_id, OWNER)
    clinical = [e for e in runtime.knowledge.for_case(state.case_id, state.case_version)
                if e.kind == "clinical"]
    assert len(clinical) == 8 and len(runtime.knowledge.entries) == 9
    assert search(client, "心电图", session_id).status_code == 200
    assert len(calls) == 2  # one batch for the scoped candidates, one for the query
    assert len(calls[0]) == len(clinical)
    assert calls[1] == ["心电图"]
    # A new query on a narrower stage reuses the cached vectors; the library is never re-sent.
    assert search(client, "肌钙蛋白", session_id, stage="检查选择").status_code == 200
    assert len(calls) == 3
    assert calls[2] == ["肌钙蛋白"]


def test_search_reports_no_reliable_match(tmp_path, monkeypatch):
    _, client, _, _ = install(tmp_path, monkeypatch)
    session_id = seal_session(client)
    body = search(client, "今天天气怎么样", session_id).json()
    assert body["available"] is True and body["results"] == []
    assert body["scope"]["clinical_candidates"] == 8  # applicable entries exist, none scored high


def test_search_reports_unconfigured_embedding(tmp_path, monkeypatch):
    _, client, _, _ = install(tmp_path, monkeypatch, configured=False)
    session_id = seal_session(client)
    body = search(client, "心电图", session_id).json()
    assert body["available"] is False
    assert body["reason"] == "embedding_unconfigured"
    assert body["results"] == []


def test_search_survives_embedding_service_failure(tmp_path, monkeypatch):
    _, client, _, _ = install(tmp_path, monkeypatch)
    session_id = seal_session(client)

    def boom(self, texts):  # noqa: ANN001
        raise EmbeddingError("embedding_http_error")

    monkeypatch.setattr(EmbeddingClient, "embed", boom)
    body = search(client, "心电图", session_id).json()
    assert body["available"] is False and body["reason"] == "embedding_http_error"
    assert body["results"] == []


def test_search_rejects_unknown_or_unsealed_session(tmp_path, monkeypatch):
    _, client, calls, _ = install(tmp_path, monkeypatch)
    assert search(client, "心电图", "does-not-exist").status_code == 404
    created = client.post("/api/sessions?case_id=CASE-001", headers={"X-Owner-Id": OWNER})
    unsealed = search(client, "心电图", created.json()["session_id"])
    assert unsealed.status_code == 409
    assert calls == []  # no sealed training, no embedding call


def test_search_rejects_unknown_stage(tmp_path, monkeypatch):
    _, client, calls, _ = install(tmp_path, monkeypatch)
    session_id = seal_session(client)
    assert search(client, "心电图", session_id, stage="不存在的环节").status_code == 422
    assert calls == []


def test_search_requires_the_case_scope(tmp_path, monkeypatch):
    _, client, calls, _ = install(tmp_path, monkeypatch)
    response = client.post(PATH, headers=TEACHER, json={"query": "心电图"})
    assert response.status_code == 422
    assert calls == []


@pytest.mark.parametrize("headers", [{}, DEMO, STUDENT])
def test_search_requires_teacher(tmp_path, monkeypatch, headers):
    _, client, calls, _ = install(tmp_path, monkeypatch)
    session_id = seal_session(client)
    response = search(client, "心电图", session_id, headers=headers)
    assert response.status_code in (401, 403)
    assert calls == []  # a rejected caller never reaches the embedding service
    assert search(client, "心电图", session_id).status_code == 200
