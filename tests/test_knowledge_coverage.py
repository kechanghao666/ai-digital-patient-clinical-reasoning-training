"""Clinical teaching coverage is distinct from general feedback-writing guidance."""
import json

import pytest

from app.integration.knowledge import KnowledgeStore, STAGES
from test_integration import Env, ROOT
from test_teacher_cases import TEACHER, new_case


def test_stemi_coverage_counts_clinical_entries_only():
    store = KnowledgeStore(ROOT / "knowledge")
    coverage = store.coverage("STEMI-001")
    assert not coverage["missing_clinical_stages"]
    assert coverage["guidance_ids"] == ["kb_feedback_principles"]
    assert all(ids and "kb_feedback_principles" not in ids for ids in coverage["clinical_by_stage"].values())
    assert coverage["status"] == "draft_unreviewed"


@pytest.mark.parametrize("case_id", ["APPY-001", "STROKE-001", "CAP-001", "DKA-001", "TEACH-COPY"])
def test_general_feedback_rules_do_not_fill_missing_clinical_coverage(case_id):
    coverage = KnowledgeStore(ROOT / "knowledge").coverage(case_id)
    assert coverage["missing_clinical_stages"] == list(STAGES)
    assert not any(coverage["clinical_by_stage"].values())
    assert coverage["guidance_ids"] == ["kb_feedback_principles"]


@pytest.mark.parametrize("change", [
    {"stages": []}, {"stages": ["unknown"]}, {"kind": "medical_reviewed"}, {"applies_to_cases": []},
])
def test_invalid_knowledge_applicability_is_rejected(tmp_path, change):
    base = tmp_path / "teaching_basis"
    base.mkdir()
    manifest = json.loads((ROOT / "knowledge/teaching_basis/manifest.json").read_text(encoding="utf-8"))
    entry = {**manifest["entries"][0], **change}
    manifest["entries"] = [entry]
    (base / entry["file"]).write_text("本地结构校验样本", encoding="utf-8")
    (base / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        KnowledgeStore(tmp_path)


def test_teacher_copy_is_warned_and_student_gets_no_clinical_id_inventory(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, mode="simulator")
    result = env.client.post("/api/teacher/cases/validate", headers=TEACHER,
                             json=new_case(public_id="CASE-T-COPY"))
    assert result.status_code == 200
    assert any(issue["code"] == "missing_clinical_basis" for issue in result.json()["issues"])
    path = "/api/teacher/cases/STEMI-001/versions/2/teaching-basis"
    assert env.client.get(path, headers=env.headers).status_code == 403
    assert env.client.get(path, headers=TEACHER).json()["missing_clinical_stages"] == []
    sid = env.new_session()
    env.client.post(f"/api/sessions/{sid}/seal", headers=env.headers, json={"request_id": "seal"})
    shown = env.client.post(f"/api/sessions/{sid}/feedback", headers=env.headers, json={"request_id": "feedback"})
    assert shown.status_code == 200, shown.text
    assert set(shown.json()["basis_coverage"]) == {"status", "missing_clinical_stages", "notice"}
