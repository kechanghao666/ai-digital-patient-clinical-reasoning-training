"""Concrete boundary failures from the current-state audit, with temporary data only."""
import pytest

from test_cases_config import Api
from test_teacher_cases import TEACHER, new_case, put


@pytest.mark.parametrize("problem", ["stage_mismatch", "infinite_weight", "overflow_weight", "hidden_phrase", "specialty_leak"])
def test_teacher_cannot_save_misleading_scoring_or_hidden_answer(tmp_path, monkeypatch, problem):
    api = Api(tmp_path, monkeypatch)
    raw = new_case()
    if problem == "stage_mismatch":
        raw["rubric"][0]["stage"] = "治疗"
    elif problem == "infinite_weight":
        raw["rubric"][0]["weight"] = "Infinity"
    elif problem == "overflow_weight":
        for item in raw["rubric"]:
            item["weight"] = 1e308
    elif problem == "hidden_phrase":
        raw["hidden_diagnosis"] = "仅教师可见的内部结论"
        raw["title"] = raw["hidden_diagnosis"]
    else:
        raw["specialty"] = "急性阑尾炎"
    result = put(api, raw)
    assert result.status_code == 422, result.text
    assert not api.store.versions(raw["case_id"])


@pytest.mark.parametrize("candidates", [
    [{"candidate_id": "not-configured"}],
    [{"candidate_id": "gastroenteritis"}, {"candidate_id": "gastroenteritis"}],
])
def test_unknown_or_duplicate_differential_is_rejected_without_writing(tmp_path, monkeypatch, candidates):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-002")
    before = api.repository.get(sid, "demo-cfg").model_dump_json()
    for path, body in (("differential", {"request_id": "bad", "candidates": candidates}),
                       ("action", {"request_id": "bad", "action": "submit_differential", "payload": {"candidates": candidates}})):
        response = api.client.post(f"/api/sessions/{sid}/{path}", headers=api.headers, json=body)
        assert response.status_code in (400, 422), response.text
    assert api.repository.get(sid, "demo-cfg").model_dump_json() == before


def test_boolean_wait_is_not_a_minute_value(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start("CASE-002")
    result = api.client.post(f"/api/sessions/{sid}/action", headers=api.headers,
                            json={"request_id": "boolean", "action": "wait", "payload": {"minutes": True}})
    assert result.status_code == 400
    assert not api.repository.get(sid, "demo-cfg").events
