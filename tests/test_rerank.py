"""Teacher-side rerank: read-only, scope-narrowed, and honest about service failures.

Offline only: the rerank service is a stub wired through the settings; nothing here calls a
real vendor. Failure modes (missing score, duplicate index, non-200, timeout) must surface as
"rerank failed, original order kept" — never as a successful model output — and must not touch
the teacher's draft or the sealed score.
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.integration.runtime import IntegrationRuntime
from app.state_machine import StateMachine
from app.storage import SessionRepository
from test_basis_search import OWNER, STUDENT, TEACHER, seal_session
from test_integration import settings_for

RERANK_PATH = "/api/teacher/feedback/basis/rerank"
QUERY = "怀疑急性冠脉综合征的病人要优先做心电图"
# Pre-registered fixed submission order: the planted hit sits in second place, so a correct
# rerank must move it to first. Response array order never decides the ranking.
PREORDER = ["kb_troponin_role", "kb_ecg_first"]


def rerank(client, session_id, *, query=QUERY, stage="检查选择", order=None, owner=OWNER,
           headers=TEACHER):
    payload = {"session_id": session_id, "owner_id": owner, "query": query, "stage": stage}
    if order is not None:
        payload["order"] = order
    return client.post(RERANK_PATH, headers=headers, json=payload)


def make_runtime(tmp_path, configured=True) -> IntegrationRuntime:
    overrides = (
        {"rerank_base_url": "http://rerank.test/v2/rerank", "rerank_api_key": "offline-test-key",
         "rerank_model": "offline-reranker"}
        if configured else {}
    )
    return IntegrationRuntime(settings_for(tmp_path, **overrides),
                              httpx.MockTransport(lambda request: httpx.Response(500)))


def install_rerank(tmp_path, monkeypatch, configured=True):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("APP_AUTH_MODE", raising=False)
    repository = SessionRepository(tmp_path / "app.db")
    monkeypatch.setattr(main, "REPOSITORY", repository)
    monkeypatch.setattr(main, "MACHINE", StateMachine(main.CASE_STORE, repository))
    runtime = make_runtime(tmp_path, configured)
    monkeypatch.setattr(main, "RUNTIME", runtime)
    calls: list[dict] = []

    def post(url, *, json, **kwargs):
        assert url == runtime.settings.rerank_base_url
        calls.append(json)
        scores = [0.61 if "肌钙蛋白" in text else 0.92 for text in json["documents"]]
        return httpx.Response(200, json={"results": [
            {"index": i, "relevance_score": scores[i]} for i in reversed(range(len(scores)))
        ]})

    monkeypatch.setattr(httpx, "post", post)
    return runtime, TestClient(main.app), calls, repository


def test_rerank_moves_the_planted_hit_to_first_and_keeps_readonly_guarantees(tmp_path, monkeypatch):
    runtime, client, calls, _ = install_rerank(tmp_path, monkeypatch)
    session_id = seal_session(client)
    before_scores = client.get(f"/api/teacher/scores/{session_id}",
                               params={"owner_id": OWNER}, headers=TEACHER).json()
    response = rerank(client, session_id, order=PREORDER)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True and body["reranked"] is True
    assert body["original_order"] == PREORDER
    assert [item["basis_id"] for item in body["results"]] == ["kb_ecg_first", "kb_troponin_role"]
    assert body["results"][0]["relevance_score"] > body["results"][1]["relevance_score"]
    # Scope came from the case/stage filter alone: the embedding client is never touched.
    assert len(calls) == 1
    by_id = {entry.basis_id: entry for entry in runtime.knowledge.for_case("STEMI-001", 3)}
    assert calls[0]["documents"] == [by_id[basis_id].text for basis_id in PREORDER]
    assert calls[0]["query"] == QUERY
    after_scores = client.get(f"/api/teacher/scores/{session_id}",
                              params={"owner_id": OWNER}, headers=TEACHER).json()
    assert before_scores == after_scores


def test_rerank_without_explicit_order_uses_the_library_order(tmp_path, monkeypatch):
    runtime, client, calls, _ = install_rerank(tmp_path, monkeypatch)
    session_id = seal_session(client)
    body = rerank(client, session_id).json()
    assert body["reranked"] is True
    assert body["original_order"] == [entry for entry in
                                      [item["basis_id"] for item in body["results"]]]
    assert len(calls) == 1


def test_unconfigured_rerank_reports_honestly(tmp_path, monkeypatch):
    runtime, client, _, _ = install_rerank(tmp_path, monkeypatch, configured=False)
    session_id = seal_session(client)
    body = rerank(client, session_id, order=PREORDER).json()
    assert body["available"] is False and body["reason"] == "rerank_unconfigured"
    assert body["reranked"] is False
    assert [item["basis_id"] for item in body["results"]] == PREORDER
    assert "未医学审核" in body["notice"]
    assert rerank(client, session_id, order=["unknown"]).status_code == 422


@pytest.mark.parametrize("payload", [
    [],                                  # missing scores
    [{"index": 0}, {"index": 1, "relevance_score": 0.1}],
    *[[{"index": 0, "relevance_score": value}, {"index": 1, "relevance_score": 0.1}]
      for value in (float("nan"), float("inf"), float("-inf"), 10 ** 400)],
    [{"index": 0, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}],  # duplicate index
    [{"index": 0, "relevance_score": 0.9}, {"index": 3, "relevance_score": 0.1}],  # out of range
    [{"index": 0, "relevance_score": 0.9}],                                        # count mismatch
])
def test_bad_rerank_responses_show_the_original_order(tmp_path, monkeypatch, payload):
    runtime, client, _, _ = install_rerank(tmp_path, monkeypatch)
    session_id = seal_session(client)

    monkeypatch.setattr(httpx, "post", lambda *a, **kw: httpx.Response(
        200, content=json.dumps({"results": payload}).encode()))
    body = rerank(client, session_id, order=PREORDER).json()
    assert body["available"] is False and body["reranked"] is False
    assert body["reason"] == "rerank_bad_response"
    assert body["notice"] == "重排失败，显示原顺序；草稿，未医学审核"
    assert [item["basis_id"] for item in body["results"]] == PREORDER
    assert all("relevance_score" not in item for item in body["results"])


def test_http_and_network_failures_keep_the_original_order(tmp_path, monkeypatch):
    runtime, client, _, _ = install_rerank(tmp_path, monkeypatch)
    session_id = seal_session(client)
    for reason in ("rerank_http_error", "rerank_network_error", "rerank_timeout"):
        def boom(*args, **kwargs):
            if reason == "rerank_http_error":
                return httpx.Response(503)
            if reason == "rerank_timeout":
                raise httpx.ReadTimeout("offline")
            raise httpx.ConnectError("offline")

        monkeypatch.setattr(httpx, "post", boom)
        body = rerank(client, session_id, order=PREORDER).json()
        assert body["available"] is False and body["reason"] == reason
        assert body["notice"] == "重排失败，显示原顺序；草稿，未医学审核"
        assert [item["basis_id"] for item in body["results"]] == PREORDER


def test_rerank_requires_teacher(tmp_path, monkeypatch):
    runtime, client, _, _ = install_rerank(tmp_path, monkeypatch)
    session_id = seal_session(client)
    assert rerank(client, session_id, headers=STUDENT).status_code == 403
    assert rerank(client, session_id, headers={}).status_code == 401


def test_rerank_rejects_a_non_permutation_order(tmp_path, monkeypatch):
    runtime, client, _, _ = install_rerank(tmp_path, monkeypatch)
    session_id = seal_session(client)
    response = rerank(client, session_id, order=["kb_ecg_first", "kb_ecg_first"])
    assert response.status_code == 422
    response = rerank(client, session_id, order=["kb_ddx_pulmonary_embolism", "kb_ecg_first"])
    assert response.status_code == 422
