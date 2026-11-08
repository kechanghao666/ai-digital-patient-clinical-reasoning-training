"""Historical citations stay attributable across knowledge changes and process restarts."""
from dataclasses import replace
import json
import shutil
import sqlite3

import pytest

from app import main
from app.integration.feedback import FeedbackStore
from app.integration.knowledge import KnowledgeStore
from app.integration.runtime import IntegrationRuntime
from test_feedback_contract import sealed
from test_feedback_reviews import payload, read, save
from test_integration import Env, ROOT, feedback_agent, select, _play_and_seal


def library_copy(tmp_path):
    target = tmp_path / "knowledge"
    shutil.copytree(ROOT / "knowledge", target)
    return target


def change_manifest(directory, change):
    path = directory / "teaching_basis/manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("bump_revision", [False, True])
def test_original_citation_does_not_drift_after_restart(sealed, tmp_path, monkeypatch, bump_revision):
    env, state, case, _ = sealed
    directory = library_copy(tmp_path)
    settings = replace(env.settings, knowledge_dir=directory)
    runtime = IntegrationRuntime(settings)
    monkeypatch.setattr(main, "RUNTIME", runtime)
    path = f"/api/sessions/{state.session_id}/feedback"
    before = env.client.post(path, headers=env.headers, json={"request_id": "generate"})
    assert before.status_code == 200, before.text
    original = runtime.feedback_store.get(state.session_id, state.owner_id)
    sealed_before = env.repository.get(state.session_id, state.owner_id).model_dump_json()
    cited = {item["basis_id"] for item in before.json()["teaching_basis"]}

    def revise(data):
        for entry in data["entries"]:
            if entry["basis_id"] in cited:
                entry["title"] = "修订后的教学题名"
                entry["sources"] = ["修订后的项目来源说明"]
                entry["revision"] += int(bump_revision)
                (directory / "teaching_basis" / entry["file"]).write_text("修订后的教学正文。", encoding="utf-8")

    change_manifest(directory, revise)
    restarted = IntegrationRuntime(settings)
    monkeypatch.setattr(main, "RUNTIME", restarted)
    after = env.client.get(path, headers=env.headers)
    assert after.status_code == 200, after.text
    assert after.json()["basis_provenance"] == "snapshot"
    assert after.json()["teaching_basis"] == before.json()["teaching_basis"]
    assert restarted.feedback_store.get(state.session_id, state.owner_id) == original
    assert env.repository.get(state.session_id, state.owner_id).model_dump_json() == sealed_before
    current = restarted.knowledge.snapshot(original["items"], case.case_id, case.version)
    assert current != original["basis_snapshot"]
    assert "entries" not in after.json() and "basis_snapshot" not in after.json()


def test_teacher_revisions_keep_independent_citations_and_retry_identity(sealed):
    env, state, case, items = sealed
    env.runtime.feedback_service.get_or_generate(state, case, "original")
    original = env.runtime.feedback_store.get(state.session_id, state.owner_id)
    body = payload(state, items)
    assert save(env, state, body).json()["revision"] == 1
    first = read(env, state).json()["reviews"][0]
    entry = env.runtime.knowledge.entries["kb_feedback_principles"]
    env.runtime.knowledge.entries[entry.basis_id] = replace(entry, revision=2, title="新版反馈规范", sources=("新版项目规范",))
    assert save(env, state, body).json()["revision"] == 1
    second_body = payload(state, items, request_id="review-2", expected_revision=1, reason="核对新版来源。",
                          basis_hashes={entry.basis_id: env.runtime.knowledge.entries[entry.basis_id].content_hash})
    assert save(env, state, second_body).json()["revision"] == 2
    record = read(env, state).json()
    assert record["reviews"][0] == first
    assert record["reviews"][1]["basis_snapshot"] != first["basis_snapshot"]
    assert record["original"] == original
    shown = env.client.get(f"/api/sessions/{state.session_id}/feedback", headers=env.headers).json()
    assert shown["review_revision"] == 2
    assert shown["teaching_basis"][0]["revision"] == 2


def test_unversioned_feedback_is_not_given_current_sources_and_can_be_reviewed(sealed):
    env, state, _, items = sealed
    env.runtime.feedback_store.save_once(state.session_id, state.owner_id, "local_rules", items, "draft_unreviewed", {})
    original = env.runtime.feedback_store.get(state.session_id, state.owner_id)
    assert original["basis_snapshot"] is None
    shown = env.client.get(f"/api/sessions/{state.session_id}/feedback", headers=env.headers)
    assert shown.status_code == 200
    assert shown.json()["basis_provenance"] == "legacy_unversioned"
    assert all(not entry["sources"] and entry["revision"] is None for entry in shown.json()["teaching_basis"])
    assert save(env, state, payload(state, items)).status_code == 200
    assert env.runtime.feedback_store.get(state.session_id, state.owner_id) == original
    assert env.client.get(f"/api/sessions/{state.session_id}/feedback", headers=env.headers).json()["basis_provenance"] == "snapshot"


@pytest.mark.parametrize("damage", ["text", "case_version", "missing_entry", "wrong_stage", "status", "invalid_json", "null_json"])
def test_damaged_snapshot_is_blocked_without_current_library_fallback(sealed, damage):
    env, state, case, items = sealed
    snapshot = env.runtime.knowledge.snapshot(items, case.case_id, case.version)
    if damage == "text":
        snapshot["entries"][0]["text"] += "被改写的正文"
    elif damage == "case_version":
        snapshot["case_version"] += 1
    elif damage == "missing_entry":
        snapshot["entries"] = []
    elif damage == "wrong_stage":
        snapshot["entries"][0]["stages"] = ["治疗"]
        from app.integration.knowledge import BasisEntry
        snapshot["entries"][0] = BasisEntry.from_record(snapshot["entries"][0]).record()
    elif damage == "status":
        snapshot["status"] = "different-status"
    env.runtime.feedback_store.save_once(state.session_id, state.owner_id, "local_rules", items,
                                         "draft_unreviewed", {}, snapshot)
    if damage in {"invalid_json", "null_json"}:
        with sqlite3.connect(env.runtime.feedback_store.path) as connection:
            connection.execute("UPDATE feedback_basis SET snapshot_json=? WHERE session_id=?",
                               ("null" if damage == "null_json" else "invalid-json", state.session_id))
    assert env.client.get(f"/api/sessions/{state.session_id}/feedback", headers=env.headers).status_code == 409
    assert read(env, state).json()["original_validation_error"]


@pytest.mark.parametrize("versions", [[1], [1, 2]])
def test_api_and_mcp_enforce_same_case_version(tmp_path, monkeypatch, versions):
    env = Env(tmp_path, monkeypatch, mode="simulator")
    directory = library_copy(tmp_path)
    def bind(data):
        for entry in data["entries"]:
            if entry["basis_id"] == "kb_ecg_first":
                entry["applies_to_versions"] = {"STEMI-001": versions}
    change_manifest(directory, bind)
    library = KnowledgeStore(directory)
    env.service.knowledge = library
    monkeypatch.setattr(env.runtime, "knowledge", library)
    coverage = env.client.get("/api/teacher/cases/STEMI-001/versions/2/teaching-basis",
                              headers={"X-Owner-Id": "teacher-reviewer"}).json()
    assert ("kb_ecg_first" in coverage["clinical_by_stage"]["检查选择"]) == (2 in versions)
    sid = env.new_session()
    env.client.post(f"/api/sessions/{sid}/seal", headers=env.headers, json={"request_id": "seal"})
    state = env.repository.get(sid, "demo-integration")
    case = env.service.cases.get(state.case_id, state.case_version)
    _, token = env.runtime.turns.issue(kind="feedback", owner_id=state.owner_id, session_id=sid,
        case_id=case.case_id, case_version=case.version, request_id="basis", state_version=state.state_version, ttl_s=60)
    found = env.service.search_teaching_basis(token, "心电图 优先", "检查选择")
    assert found["ok"]
    assert ("kb_ecg_first" in {entry["basis_id"] for entry in found["results"]}) == (2 in versions)
    assert all(len(entry["content_hash"]) == 64 and entry["revision"] == 1 for entry in found["results"])


@pytest.mark.parametrize("versions", [None, {}, {"STEMI-001": []}, {"STEMI-001": [True]}, {"STEMI-001": [1, 1]}])
def test_clinical_basis_requires_explicit_valid_case_versions(tmp_path, versions):
    directory = library_copy(tmp_path)
    change_manifest(directory, lambda data: data["entries"][0].update(applies_to_versions=versions))
    with pytest.raises(ValueError):
        KnowledgeStore(directory)


@pytest.mark.parametrize("write", ["original", "review"])
def test_feedback_and_basis_snapshot_commit_together(sealed, monkeypatch, write):
    env, state, case, items = sealed
    store = env.runtime.feedback_store
    snapshot = env.runtime.knowledge.snapshot(items, case.case_id, case.version)
    def fail(*args):
        raise sqlite3.OperationalError("injected snapshot failure")
    with monkeypatch.context() as patch:
        patch.setattr(store, "_save_basis", fail)
        with pytest.raises(sqlite3.OperationalError):
            if write == "original":
                store.save_once(state.session_id, state.owner_id, "local_rules", items, "draft_unreviewed", {}, snapshot)
            else:
                store.append_review(state.session_id, state.owner_id, "teacher-reviewer", "r1", 0,
                                    "核对来源", items, "draft_unreviewed", snapshot)
    assert store.get(state.session_id, state.owner_id) is None
    assert store.reviews(state.session_id, state.owner_id) == []
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM feedback_basis").fetchone()[0] == 0


def test_mcp_and_api_library_mismatch_stores_nothing_and_can_retry(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch)
    env.agent = lambda r, b, s: select("f_pain_character")(r, b, s) if b["agent_name"] == "digital_patient" else feedback_agent()(r, b, s)
    sid = _play_and_seal(env)
    entry = env.service.knowledge.entries["kb_feedback_principles"]
    env.service.knowledge.entries[entry.basis_id] = replace(entry, revision=2)
    path = f"/api/sessions/{sid}/feedback"
    assert env.client.post(path, headers=env.headers, json={"request_id": "feedback"}).status_code == 502
    assert env.runtime.feedback_store.get(sid, "demo-integration") is None
    env.service.knowledge.entries[entry.basis_id] = entry
    assert env.client.post(path, headers=env.headers, json={"request_id": "feedback"}).status_code == 200


def test_old_database_adds_snapshot_table_without_rewriting_original(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE feedback (session_id TEXT PRIMARY KEY, owner_id TEXT, source TEXT, "
                           "items_json TEXT, basis_status TEXT, trace_json TEXT, created_at REAL)")
        connection.execute("INSERT INTO feedback VALUES (?,?,?,?,?,?,?)", ("sid", "owner", "local_rules", "[]", "draft_unreviewed", "{}", 123))
        original = connection.execute("SELECT * FROM feedback").fetchall()
    store = FeedbackStore(path)
    assert store.get("sid", "owner")["basis_snapshot"] is None
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM feedback").fetchall() == original
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_teacher_stale_basis_requires_reload_but_committed_retries_still_work(sealed):
    env, state, _, items = sealed
    body = payload(state, items)
    entry = env.runtime.knowledge.entries["kb_feedback_principles"]
    env.runtime.knowledge.entries[entry.basis_id] = replace(entry, revision=2, text="修订后的反馈规范。")
    assert save(env, state, body).status_code == 409
    assert env.runtime.feedback_store.reviews(state.session_id, state.owner_id) == []
    body["basis_hashes"] = {entry.basis_id: env.runtime.knowledge.entries[entry.basis_id].content_hash}
    assert save(env, state, body).json()["revision"] == 1
    env.runtime.knowledge.entries.pop(entry.basis_id)
    assert save(env, state, body).json()["revision"] == 1
    assert save(env, state, {**body, "reason": "更改后的请求"}).status_code == 409
    assert env.client.get(f"/api/sessions/{state.session_id}/feedback", headers=env.headers).status_code == 409


def test_new_case_version_does_not_inherit_previous_clinical_coverage(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, mode="simulator")
    from app.case_store import CaseStore
    cases = CaseStore(ROOT / "cases", tmp_path / "teacher_cases")
    monkeypatch.setattr(main, "CASE_STORE", cases)
    case = cases.get("STEMI-001", 2).model_dump(mode="json")
    case.update(case_id="TEACH-PIN", public_id="CASE-T-PIN", version=1)
    directory = library_copy(tmp_path)
    def bind(data):
        for entry in data["entries"]:
            if entry.get("kind", "clinical") == "clinical":
                entry.update(applies_to_cases=["TEACH-PIN"], applies_to_versions={"TEACH-PIN": [1]})
    change_manifest(directory, bind)
    monkeypatch.setattr(env.runtime, "knowledge", KnowledgeStore(directory))
    first = env.client.put("/api/teacher/cases/TEACH-PIN", headers={"X-Owner-Id": "teacher-reviewer"}, json=case)
    assert first.status_code == 200, first.text
    assert not any(issue["code"] == "missing_clinical_basis" for issue in first.json()["issues"])
    saved = env.client.put("/api/teacher/cases/TEACH-PIN", headers={"X-Owner-Id": "teacher-reviewer"}, json=case)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 2
    assert any(issue["code"] == "missing_clinical_basis" for issue in saved.json()["issues"])
