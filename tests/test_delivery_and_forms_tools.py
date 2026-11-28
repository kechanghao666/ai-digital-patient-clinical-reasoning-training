"""Delivery export preparation and evaluation-form summarisation tools (engineering checks).

Synthetic protected exports and filled forms only. The preparation tool never invents a platform
export; the summariser never invents a verdict — pass/fail wording only appears with an explicitly
confirmed denominator policy and is always labelled "as recorded".
"""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_delivery_package import DELIVERABLES, verify  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare_exports = _load("prepare_delivery_exports")
summarize_forms = _load("summarize_evaluation_forms")


def _protected_export(name: str, *, with_secrets: bool = True) -> dict:
    # auth_note carries a credential-pattern VALUE (not just a secret-named key) to exercise
    # substring redaction inside otherwise meaningful text.
    tools = [{"name": "get_turn_context"}, {"name": "submit_fact_selection"}]
    agent = {
        "agent_id": 7,
        "name": name,
        "display_name": "数字病人（合成导出）",
        "prompt": "你是教学模拟患者。只依据工具返回的事实作答，不泄露诊断。",
        "max_steps": 4,
        "tenant_id": "tenant-synthetic-1234",
        "knowledge_bases": [],
        "tools": tools,
        "allow_chat_metadata": True,
    }
    if with_secrets:
        agent["api_key"] = "sk-synthetic-secret-000000000001"
        agent["auth_note"] = "access_token=zyxwv9876543210abcd"
        agent["callback_url"] = "http://callback.example:9000/hook?key=abc123456789"
    return {"agent_id": 7, "agent_info": {"7": agent}}


# --- delivery export preparation ------------------------------------------------

@pytest.fixture()
def delivery_dir(tmp_path, monkeypatch):
    target = tmp_path / "deliverables"
    monkeypatch.setattr(prepare_exports, "DELIVERABLES", target)
    # Minimal package scaffolding so the verifier can run against the folder.
    (target / "feedback").mkdir(parents=True)
    (target / "mcps").mkdir(parents=True)
    (target / "README.md").write_text("# 合成交付包\n", encoding="utf-8")
    (target / "IMPORT.md").write_text("# 合成导入说明\n", encoding="utf-8")
    (target / "CHECKLIST.md").write_text("# 合成清单\n", encoding="utf-8")
    (target / "mcps" / "mcp-config.example.json").write_text(json.dumps({
        "tools": [{"name": name} for name in sorted({"get_turn_context", "submit_fact_selection",
                                                     "get_sealed_review", "search_teaching_basis", "submit_feedback"})],
        "transport": "streamable-http", "url": "http://<mcp-host>:8765/mcp",
        "headers": {"Authorization": "Bearer <MCP_SERVICE_TOKEN>"}}), encoding="utf-8")
    return target


def test_prepare_writes_sanitised_public_copies_and_review_artifact(delivery_dir, tmp_path):
    patient = tmp_path / "protected-patient.json"
    feedback = tmp_path / "protected-feedback.json"
    patient.write_text(json.dumps(_protected_export("digital_patient_stemi"), ensure_ascii=False), encoding="utf-8")
    feedback.write_text(json.dumps(_protected_export("feedback_reviewer", with_secrets=True), ensure_ascii=False),
                        encoding="utf-8")
    summary_patient = prepare_exports.prepare(patient, delivery_dir / "agent.json")
    summary_feedback = prepare_exports.prepare(feedback, delivery_dir / "feedback" / "agent.json")

    public_patient = json.loads((delivery_dir / "agent.json").read_text(encoding="utf-8"))
    agent = public_patient["agent_info"]["7"]
    assert agent["prompt"].startswith("你是教学模拟患者")  # structure and prompt preserved
    assert agent["api_key"] == "<redacted>"
    assert "[REDACTED-CREDENTIAL]" not in json.dumps(public_patient)
    kinds = {item["kind"] for item in summary_patient["redactions"]}
    assert "secret_key" in kinds and "credential_pattern" in kinds
    flagged = {item["kind"] for item in summary_patient["human_review_flags"]}
    assert "instance_specific_value" in flagged and "internal_url" in flagged
    assert summary_feedback["structure"]["name"] == "feedback_reviewer"

    package = verify(delivery_dir)
    assert package["exports"] == {"agent.json": "structure_checked", "feedback/agent.json": "structure_checked"}
    assert package["problems"] == []

    # The review artifact records provenance without storing any secret value.
    review = {"exports": [summary_patient, summary_feedback], "package_verification": package}
    (tmp_path / "review.json").write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    assert "sk-synthetic" not in (tmp_path / "review.json").read_text(encoding="utf-8")


def test_prepare_refuses_bad_structure_and_existing_targets(delivery_dir, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"note": "not an export"}), encoding="utf-8")
    with pytest.raises(ValueError, match="Nexent 导出结构"):
        prepare_exports.prepare(bad, delivery_dir / "agent.json")
    assert not (delivery_dir / "agent.json").exists()

    good = tmp_path / "good.json"
    good.write_text(json.dumps(_protected_export("digital_patient_stemi"), ensure_ascii=False), encoding="utf-8")
    prepare_exports.prepare(good, delivery_dir / "agent.json")
    with pytest.raises(ValueError, match="只生成一次"):
        prepare_exports.prepare(good, delivery_dir / "agent.json")

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="不是有效的 JSON"):
        prepare_exports.prepare(invalid, delivery_dir / "feedback" / "agent.json")


def test_prepare_survives_credentials_inside_prompt_text(delivery_dir, tmp_path):
    export = _protected_export("digital_patient_stemi")
    export["agent_info"]["7"]["prompt"] += "\n备用接入：http://user:secret-pw@example.internal/hook"
    source = tmp_path / "protected.json"
    source.write_text(json.dumps(export, ensure_ascii=False), encoding="utf-8")
    summary = prepare_exports.prepare(source, delivery_dir / "agent.json")
    public = json.loads((delivery_dir / "agent.json").read_text(encoding="utf-8"))
    assert "secret-pw" not in public["agent_info"]["7"]["prompt"]
    assert public["agent_info"]["7"]["prompt"].startswith("你是教学模拟患者")  # only the credential substring went
    kinds = {item["kind"] for item in summary["redactions"]}
    assert "credential_pattern" in kinds


# --- evaluation form summarisation ----------------------------------------------

def _write_forms(directory: Path, qa_rows, student_rows, error_rows):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps({"cases": [
        {"case_id": "APPY-001", "version": 2}, {"case_id": "STEMI-001", "version": 3},
        {"case_id": "CAP-001", "version": 2}, {"case_id": "DKA-001", "version": 2},
        {"case_id": "STROKE-001", "version": 2}]}, ensure_ascii=False), encoding="utf-8")
    qa_header = ["计划行号", "病例ID", "版本", "会话匿名ID", "请求ID", "提问", "完整回答", "允许事实", "禁止事实",
                 "是否一致", "失败或超时", "评审人", "理由", "日期"]
    student_header = ["计划学生槽位", "实际匿名编号", "病例ID及版本", "轨迹位置", "评分环节", "专家独立原分",
                      "系统原分", "事先确认容差", "是否一致", "差异说明", "评审人", "日期"]
    error_header = ["计划场景ID", "病例ID", "版本", "路径类型", "当时可见证据", "决策输入", "时间锚点",
                    "预期后果", "例外", "是否纳入错误触发分母", "实际演进", "是否触发", "反馈是否准确",
                    "审核人", "结论"]
    for name, header, rows in (("问答抽检_20轮_待填写.csv", qa_header, qa_rows),
                               ("学生专家评分_至少3人_待填写.csv", student_header, student_rows),
                               ("错误决策演进_待临床定义.csv", error_header, error_rows)):
        with (directory / name).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)


def test_summarizer_computes_rates_without_fabricating_verdicts(tmp_path, capsys):
    qa_rows = [
        ["QA-01", "APPY-001", "2", "s1", "r1", "有高血压吗", "有10年", "", "", "是", "否", "评审甲", "", "2026-12-01"],
        ["QA-02", "APPY-001", "2", "s1", "r2", "有糖尿病吗", "没有", "", "", "否", "否", "评审甲", "", "2026-12-01"],
        ["QA-03", "APPY-001", "2", "s1", "r3", "有外伤吗", "", "", "", "未定", "是", "评审甲", "超时", "2026-12-01"],
        ["QA-04", "APPY-999", "9", "", "", "", "", "", "", "", "", "", "", ""],  # blank + unfrozen case
    ]
    student_rows = [
        [f"待招募-1", "S-A", "APPY-001@2", "第3轮", stage, "4", "4", "", "是", "", "评审乙", ""]
        for stage in ("病史采集", "检查选择", "鉴别诊断", "诊断", "治疗")
    ]
    error_rows = [
        ["APPY-001-1", "APPY-001", "2", "正常对照", "", "", "", "", "", "否", "", "", "", "", ""],
        ["APPY-001-2", "APPY-001", "2", "漏检查", "", "", "", "", "", "是", "恶化", "是", "是", "审核人", ""],
        ["APPY-001-3", "APPY-001", "2", "检查后延误", "", "", "", "", "", "是", "恶化", "是", "否", "审核人", ""],
    ]
    _write_forms(tmp_path, qa_rows, student_rows, error_rows)

    summarize_forms.main.__globals__["sys"].argv = ["summarize_evaluation_forms.py", "--forms-dir", str(tmp_path)]
    with pytest.raises(SystemExit) as exit_info:
        summarize_forms.main()
    assert exit_info.value.code == 0
    report = json.loads(capsys.readouterr().out)

    qa = report["summaries"]["qa_20rounds"]
    assert qa["filled_rows"] == 3 and qa["consistent"] == 1 and qa["inconsistent"] == 1
    assert qa["failed_or_timeout"] == 1 and qa["judgement_missing"] == 1
    assert qa["agreement_rate_excluding_failures"] == 1.0  # 1 consistent / (2 judged - 1 failed)
    assert qa["unfrozen_case_references"] == ["APPY-999@9"]
    student = report["summaries"]["student_expert"]
    assert student["complete_students"] == 1
    assert student["students"][0]["agreement_rate_as_recorded"] == 1.0
    error = report["summaries"]["error_evolution"]
    assert error["in_denominator"] == 2 and error["triggered"] == 2
    assert error["trigger_rate_as_recorded"] == 1.0
    assert error["feedback_accuracy_among_triggered"] == 0.5
    assert report["verdicts_as_recorded"] is None  # no policy confirmation -> no pass/fail wording


def test_summarizer_emits_as_recorded_verdicts_only_with_confirmed_policy(tmp_path, capsys):
    qa_rows = [[f"QA-{i:02d}", "APPY-001", "2", "s1", f"r{i}", "问", "答", "", "", "是", "否", "评审甲", "", ""]
               for i in range(1, 21)]  # 20/20 consistent
    student_rows = [[f"待招募-{n}", f"S-{n}", "APPY-001@2", "", stage, "4", "4", "", "是", "", "评审乙", ""]
                    for n in (1, 2, 3) for stage in ("病史采集", "检查选择", "鉴别诊断", "诊断", "治疗")]
    error_rows = [[f"APPY-001-{i}", "APPY-001", "2", "漏检查", "", "", "", "", "", "是", "恶化", "是", "是",
                   "审核人", ""] for i in range(1, 11)]
    _write_forms(tmp_path, qa_rows, student_rows, error_rows)

    summarize_forms.main.__globals__["sys"].argv = [
        "summarize_evaluation_forms.py", "--forms-dir", str(tmp_path), "--policy-confirmed-by", "评审组长"]
    with pytest.raises(SystemExit) as exit_info:
        summarize_forms.main()
    assert exit_info.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["verdicts_as_recorded"]["qa_20rounds_agreement_excluding_failures"] == "meets_target_as_recorded"
    assert report["verdicts_as_recorded"]["student_expert_overall"] == "meets_target_as_recorded"
    assert report["verdicts_as_recorded"]["error_evolution_trigger"] == "meets_target_as_recorded"
    assert "not a certification" in report["verdicts_as_recorded"]["scope"]

    # Below target: two inconsistent QA rows flip the verdict (18/20 = 0.9 < 0.95), still "as recorded".
    qa_rows[0][9] = "否"
    qa_rows[1][9] = "否"
    _write_forms(tmp_path, qa_rows, student_rows, error_rows)
    summarize_forms.main.__globals__["sys"].argv = [
        "summarize_evaluation_forms.py", "--forms-dir", str(tmp_path), "--policy-confirmed-by", "评审组长"]
    with pytest.raises(SystemExit) as exit_info:
        summarize_forms.main()
    assert exit_info.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["verdicts_as_recorded"]["qa_20rounds_agreement_excluding_failures"] == "below_target_as_recorded"


def test_summarizer_reports_missing_forms_and_writes_report_once(tmp_path, capsys):
    _write_forms(tmp_path, [], [], [])
    (tmp_path / "学生专家评分_至少3人_待填写.csv").unlink()
    summarize_forms.main.__globals__["sys"].argv = ["summarize_evaluation_forms.py", "--forms-dir", str(tmp_path)]
    with pytest.raises(SystemExit) as excinfo:
        summarize_forms.main()
    assert excinfo.value.code == 2
    assert "missing" in capsys.readouterr().out

    _write_forms(tmp_path, [], [], [])
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    summarize_forms.main.__globals__["sys"].argv = [
        "summarize_evaluation_forms.py", "--forms-dir", str(tmp_path), "--report", str(tmp_path / "report.json")]
    with pytest.raises(SystemExit) as excinfo:
        summarize_forms.main()
    assert excinfo.value.code == 2
    assert "不覆盖" in capsys.readouterr().out
