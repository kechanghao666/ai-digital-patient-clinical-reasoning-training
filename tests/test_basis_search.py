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
from app.integration.embedding import BasisSemanticSearch, EmbeddingClient, EmbeddingError
from app.integration.knowledge import BasisEntry
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


# ------------------------------------------------------------ embedding response contract
# The embedding service returns one item per input, each carrying an `index` that points back at
# the input position. Vectors must be restored to input order before they are cached: the array
# order of the response is NOT part of the contract. A response that cannot be aligned to the
# inputs (wrong count, duplicate or out-of-range index) is unusable and must raise instead of
# producing candidates or polluting the vector cache with plausible-looking wrong material.


def _synthetic_entry(basis_id: str, keyword: str) -> BasisEntry:
    return BasisEntry.from_record({
        "basis_id": basis_id, "title": f"条目{keyword}", "text": f"本条目教学要点关键词：{keyword}",
        "tags": [keyword], "stages": ["诊断"], "applies_to_cases": ["STEMI-001"],
        "sources": ["离线测试来源"], "kind": "clinical", "revision": 1,
        "applies_to_versions": {"STEMI-001": [1, 2, 3]},
    })


def _keyword_vector(text: str) -> list[float]:
    return [1.0 if "心电图" in text else 0.0]


def _configured_client(tmp_path) -> EmbeddingClient:
    return EmbeddingClient(settings_for(
        tmp_path, embedding_base_url="http://embed.test/v1",
        embedding_api_key="offline-test-key", embedding_model="offline-model"))


@pytest.mark.parametrize("reverse", [False, True], ids=["in-order", "reversed"])
def test_vectors_follow_the_response_index_regardless_of_array_order(tmp_path, monkeypatch, reverse):
    # Minimal reproduction: materials A and B are embedded together and the query must hit A.
    # When the stand-in returns the two vectors in the opposite array order (indexes 1 then 0),
    # position-based pairing ranked B first; pairing by the response index keeps A first.
    client = _configured_client(tmp_path)
    a = _synthetic_entry("kb_a", "心电图")
    b = _synthetic_entry("kb_b", "再灌注")

    def responder(url, json=None, headers=None, timeout=None):  # noqa: ANN001
        texts = json["input"]
        items = [{"index": i, "embedding": _keyword_vector(t)} for i, t in enumerate(texts)]
        if reverse:
            items.reverse()
        return httpx.Response(200, json={"data": items})

    monkeypatch.setattr(httpx, "post", responder)
    ranked = BasisSemanticSearch(client).search([a, b], "心电图")
    assert [entry.basis_id for _, entry in ranked] == ["kb_a", "kb_b"]


BAD_ITEMS = [
    pytest.param([{"index": 0, "embedding": [1.0]}], id="count_mismatch"),
    pytest.param([{"index": 0, "embedding": [1.0]}, {"index": 0, "embedding": [0.0]}], id="duplicate_index"),
    pytest.param([{"index": 0, "embedding": [1.0]}, {"index": 9, "embedding": [0.0]}], id="index_out_of_range"),
    pytest.param([{"index": 0, "embedding": [1.0]}, {"index": -1, "embedding": [0.0]}], id="negative_index"),
    pytest.param([{"embedding": [1.0]}, {"embedding": [0.0]}], id="missing_index"),
    pytest.param([{"index": "0", "embedding": [1.0]}, {"index": 1, "embedding": [0.0]}], id="non_int_index"),
    pytest.param([{"index": 0, "embedding": [10 ** 400]}, {"index": 1, "embedding": [1.0]}], id="oversized_integer"),
    pytest.param('{"data":[{"index":0,"embedding":[1e400]},{"index":1,"embedding":[1.0]}]}', id="non_finite_value"),
    pytest.param([{"index": 0, "embedding": [1.0, 0.0]}, {"index": 1, "embedding": [1.0]}], id="mismatched_batch_dimensions"),
]


@pytest.mark.parametrize("items", BAD_ITEMS)
def test_unalignable_responses_raise_instead_of_returning_vectors(tmp_path, monkeypatch, items):
    client = _configured_client(tmp_path)
    # A raw body is passed through untouched, so 1e400 reaches the real parser and becomes inf there.
    body = items if isinstance(items, str) else json.dumps({"data": items})
    monkeypatch.setattr(httpx, "post", lambda url, json=None, headers=None, timeout=None: httpx.Response(
        200, content=body.encode(), headers={"content-type": "application/json"}))
    with pytest.raises(EmbeddingError) as raised:
        client.embed(["材料A", "材料B"])
    assert raised.value.reason == "embedding_bad_response"


def test_bad_batch_response_shows_no_candidates_and_the_next_query_recovers(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("APP_AUTH_MODE", raising=False)
    repository = SessionRepository(tmp_path / "app.db")
    monkeypatch.setattr(main, "REPOSITORY", repository)
    monkeypatch.setattr(main, "MACHINE", StateMachine(main.CASE_STORE, repository))
    runtime = make_runtime(tmp_path, True)
    monkeypatch.setattr(main, "RUNTIME", runtime)
    calls: list[list[str]] = []

    def responder(url, json=None, headers=None, timeout=None):  # noqa: ANN001
        texts = json["input"]
        calls.append(list(texts))
        if len(calls) == 1:
            # First batch returns fewer vectors than requested: unalignable and unusable.
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})
        items = [{"index": i, "embedding": _keyword_vector(t)} for i, t in enumerate(texts)]
        return httpx.Response(200, json={"data": items})

    monkeypatch.setattr(httpx, "post", responder)
    client = TestClient(main.app)
    session_id = seal_session(client)

    first = search(client, "心电图", session_id)
    assert first.status_code == 200
    body = first.json()
    assert body["available"] is False and body["results"] == []
    assert runtime.basis_search._cache == {}  # the bad response never pollutes the vector cache

    second = search(client, "心电图", session_id)
    body2 = second.json()
    assert body2["available"] is True
    assert body2["results"]  # the next normal query recovers with real candidates
    assert all("心电图" in item["text"] for item in body2["results"])
    assert len(runtime.basis_search._cache) == body2["scope"]["clinical_candidates"]

    assert search(client, "心电图", session_id).json()["available"] is True
    assert len(calls) == 4  # bad batch, good batch + query, query: cached vectors are reused after recovery


# ------------------------------------------------- vector dimensions and finite values
# A vector is only comparable with vectors of the same dimension: zip() silently truncates to the
# shorter one, so a one-element material scored 1.0 against a two-element query. Non-finite numbers
# (NaN, or 1e400, which the JSON parser turns into inf) poison every score they touch. Neither may
# produce candidates or enter the vector cache.


def _one_dimensional(text: str) -> list[float]:
    return [1.0 if "心电图" in text else 0.0]


# Written by hand: json.dumps(float("inf")) would emit `Infinity`, not the `1e400` a provider sends.
# Every requested material gets a valid one-dimensional vector except the last, which is 1e400.
def _infinite_batch_body(count: int) -> str:
    valid = json.dumps([{"index": i, "embedding": [1.0]} for i in range(count - 1)])[:-1]
    return '{"data":' + valid + ',{"index":%d,"embedding":[1e400]}]}' % (count - 1)


INFINITE_QUERY_BODY = '{"data":[{"index":0,"embedding":[1e400]}]}'


def _raw(body: str) -> httpx.Response:
    return httpx.Response(200, content=body.encode(), headers={"content-type": "application/json"})


def _install_responder(tmp_path, monkeypatch, responder):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("APP_AUTH_MODE", raising=False)
    repository = SessionRepository(tmp_path / "app.db")
    monkeypatch.setattr(main, "REPOSITORY", repository)
    monkeypatch.setattr(main, "MACHINE", StateMachine(main.CASE_STORE, repository))
    runtime = make_runtime(tmp_path, True)
    monkeypatch.setattr(main, "RUNTIME", runtime)
    calls: list[list[str]] = []

    def record(url, json=None, headers=None, timeout=None):  # noqa: ANN001
        calls.append(list(json["input"]))
        return responder(len(calls), json["input"])

    monkeypatch.setattr(httpx, "post", record)
    return runtime, TestClient(main.app), calls


def _good(call, texts):
    items = [{"index": i, "embedding": _one_dimensional(t)} for i, t in enumerate(texts)]
    return httpx.Response(200, json={"data": items})


def test_a_shorter_material_vector_does_not_score_as_a_perfect_match(tmp_path, monkeypatch):
    def responder(call, texts):
        if call == 2:  # the query answers in two dimensions while the materials are one-dimensional
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})
        return _good(call, texts)

    runtime, client, _ = _install_responder(tmp_path, monkeypatch, responder)
    session_id = seal_session(client)
    body = search(client, "心电图", session_id).json()
    assert body["available"] is False
    assert body["reason"] == "embedding_bad_response"
    assert body["results"] == []
    assert all(len(vector) == 1 for vector in runtime.basis_search._cache.values())  # sound materials stay


def test_infinite_number_in_the_raw_response_rejects_the_whole_batch(tmp_path, monkeypatch):
    # The first materials of the batch are fine; the last is 1e400. None may be kept.
    monkeypatch.setattr(httpx, "post", lambda url, json=None, headers=None, timeout=None: _raw(_infinite_batch_body(3)))
    search_engine = BasisSemanticSearch(_configured_client(tmp_path))
    with pytest.raises(EmbeddingError) as raised:
        search_engine.search([_synthetic_entry(f"kb_{i}", "心电图") for i in range(3)], "心电图")
    assert raised.value.reason == "embedding_bad_response"
    assert search_engine._cache == {}


def test_infinite_batch_caches_nothing_and_the_next_query_recovers(tmp_path, monkeypatch):
    def responder(call, texts):
        return _raw(_infinite_batch_body(len(texts))) if call == 1 else _good(call, texts)

    runtime, client, calls = _install_responder(tmp_path, monkeypatch, responder)
    session_id = seal_session(client)
    body = search(client, "心电图", session_id).json()
    assert body["available"] is False and body["reason"] == "embedding_bad_response"
    assert body["results"] == []
    assert runtime.basis_search._cache == {}  # the valid first two vectors are not kept either

    body2 = search(client, "心电图", session_id).json()
    assert body2["available"] is True and body2["results"]
    assert len(runtime.basis_search._cache) == body2["scope"]["clinical_candidates"]


def test_short_material_vector_caches_nothing(tmp_path, monkeypatch):
    def responder(call, texts):
        if call == 1:  # every material one-dimensional except the last, which is two-dimensional
            items = [{"index": i, "embedding": [1.0]} for i in range(len(texts) - 1)]
            return httpx.Response(200, json={"data": items + [{"index": len(texts) - 1, "embedding": [1.0, 0.0]}]})
        return _good(call, texts)

    runtime, client, _ = _install_responder(tmp_path, monkeypatch, responder)
    session_id = seal_session(client)
    body = search(client, "心电图", session_id).json()
    assert body["available"] is False and body["reason"] == "embedding_bad_response"
    assert body["results"] == []
    assert runtime.basis_search._cache == {}


BAD_QUERIES = [
    pytest.param(lambda: _raw(INFINITE_QUERY_BODY), id="infinite_query"),
    pytest.param(lambda: httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]}),
                 id="query_of_another_dimension"),
]


@pytest.mark.parametrize("bad_query", BAD_QUERIES)
def test_bad_query_leaves_cached_vectors_intact_and_the_next_query_hits_the_same_material(
    tmp_path, monkeypatch, bad_query
):
    def responder(call, texts):
        # 1: material batch and 2: query of the good search; 3: the bad query; 4: the recovery query.
        return bad_query() if call == 3 else _good(call, texts)

    runtime, client, calls = _install_responder(tmp_path, monkeypatch, responder)
    session_id = seal_session(client)
    first = search(client, "心电图", session_id).json()
    assert first["available"] is True and first["results"]
    cached = dict(runtime.basis_search._cache)
    assert len(cached) == first["scope"]["clinical_candidates"]

    failed = search(client, "心电图", session_id).json()
    assert failed["available"] is False and failed["reason"] == "embedding_bad_response"
    assert failed["results"] == []
    assert runtime.basis_search._cache == cached  # neither cleared nor rewritten

    recovered = search(client, "心电图", session_id).json()
    assert recovered["available"] is True
    assert [(item["basis_id"], item["score"]) for item in recovered["results"]] == [
        (item["basis_id"], item["score"]) for item in first["results"]
    ]
    assert runtime.basis_search._cache == cached
    assert len(calls) == 4 and all(len(texts) == 1 for texts in calls[2:])  # only queries after the first batch


def test_new_material_of_another_dimension_is_not_cached_beside_cached_material(tmp_path, monkeypatch):
    # Every material of the case is cached as a two-dimensional vector (A). B is then the only
    # material not in the cache, the state an edited material leaves behind, and it answers in one
    # dimension. B must neither be stored nor make the cached vectors unusable from then on.
    narrow = {"title": ""}

    def two_dimensional(text):
        return [1.0 if "心电图" in text else 0.0, 0.5]

    def responder(call, texts):
        vectors = [[1.0] if narrow["title"] and narrow["title"] in text else two_dimensional(text) for text in texts]
        return httpx.Response(200, json={"data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)]})

    runtime, client, calls = _install_responder(tmp_path, monkeypatch, responder)
    session_id = seal_session(client)
    clinical = [e for e in runtime.knowledge.for_case("STEMI-001", 3) if e.kind == "clinical"]
    wide = {e.content_hash for e in clinical}
    assert len(wide) == 8

    def cache():
        return {h: list(v) for h, v in runtime.basis_search._cache.items()}

    assert search(client, "心电图", session_id).json()["available"] is True
    cached = cache()
    assert set(cached) == wide and all(len(v) == 2 for v in cached.values())

    b = runtime.knowledge.entries["kb_aspirin_consideration"]
    del runtime.basis_search._cache[b.content_hash]  # B's text changed, so its hash is no longer cached
    cached = cache()
    assert set(cached) == wide - {b.content_hash}
    narrow["title"] = b.title

    failed = search(client, "心电图", session_id).json()
    assert failed["available"] is False and failed["reason"] == "embedding_bad_response"
    assert failed["results"] == []
    assert cache() == cached  # A is kept and B is not written
    assert calls[2:] == [[runtime.basis_search._text(b)]]  # only B was requested; no query followed

    narrow["title"] = ""
    recovered = search(client, "心电图", session_id).json()
    assert recovered["available"] is True and recovered["results"]
    assert "心电图" in recovered["results"][0]["text"]
    assert set(cache()) == wide and all(len(v) == 2 for v in cache().values())
    assert calls[3] == calls[2]  # only the still-missing B is fetched again, then the query
    assert calls[4] == ["心电图"]

    again = search(client, "心电图", session_id).json()
    assert again["available"] is True
    assert [(i["basis_id"], i["score"]) for i in again["results"]] == [
        (i["basis_id"], i["score"]) for i in recovered["results"]
    ]
    assert len(calls) == 6 and calls[5] == ["心电图"]  # A and B are both reused
