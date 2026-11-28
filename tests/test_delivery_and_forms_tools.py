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


def _protected_export(name: str, *, with_secrets: bool = True, role: str = "patient") -> dict:
    # Shape mirrors a real Nexent export closely enough to catch sanitisation that breaks
    # importability: tool parameter definitions (turn_token / max_tokens) must survive.
    if role == "patient":
        tools = [
            {"name": "get_turn_context", "description": "读取本轮可信上下文",
             "parameters": {"type": "object", "properties": {"turn_token": {"type": "string"}},
                            "required": ["turn_token"]}},
            {"name": "submit_fact_selection", "description": "提交事实选择",
             "parameters": {"type": "object", "properties": {
                 "turn_token": {"type": "string"},
                 "fact_ids": {"type": "array", "items": {"type": "string"}}},
                 "required": ["turn_token", "fact_ids"]}},
        ]
    else:
        tools = [
            {"name": "get_sealed_review", "parameters": {"type": "object",
             "properties": {"review_token": {"type": "string"}}, "required": ["review_token"]}},
            {"name": "search_teaching_basis", "parameters": {"type": "object",
             "properties": {"query": {"type": "string"}}, "required": ["query"]}},
            {"name": "submit_feedback", "parameters": {"type": "object",
             "properties": {"review_token": {"type": "string"}, "items": {"type": "array"}},
             "required": ["review_token", "items"]}},
        ]
    agent = {
        "agent_id": 7,
        "name": name,
        "display_name": "数字病人（合成导出）",
        "description": "合成导出；病例和依据未经医学审核",
        "prompt": "你是教学模拟患者。只依据工具返回的事实作答，不泄露诊断。",
        "max_steps": 4,
        "max_tokens": 512,
        "tenant_id": "tenant-synthetic-1234",
        "knowledge_bases": ["teaching_basis" if role != "patient" else ""],
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
    feedback.write_text(json.dumps(_protected_export("feedback_reviewer", role="feedback"), ensure_ascii=False),
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


def test_tool_definitions_survive_sanitisation(delivery_dir, tmp_path):
    """``max_tokens`` and the ``turn_token`` parameter are configuration, not credentials: the
    exported tool wiring must remain importable after sanitisation."""
    source = tmp_path / "protected-patient.json"
    source.write_text(json.dumps(_protected_export("digital_patient_stemi"), ensure_ascii=False), encoding="utf-8")
    summary = prepare_exports.prepare(source, delivery_dir / "agent.json")
    public = json.loads((delivery_dir / "agent.json").read_text(encoding="utf-8"))
    agent = public["agent_info"]["7"]

    assert agent["max_tokens"] == 512  # not redacted as a secret
    get_context = next(tool for tool in agent["tools"] if tool["name"] == "get_turn_context")
    assert get_context["parameters"]["properties"]["turn_token"] == {"type": "string"}
    assert get_context["parameters"]["required"] == ["turn_token"]
    submit = next(tool for tool in agent["tools"] if tool["name"] == "submit_fact_selection")
    assert submit["parameters"]["required"] == ["turn_token", "fact_ids"]
    assert submit["parameters"]["properties"]["fact_ids"]["items"] == {"type": "string"}

    # A credential-shaped VALUE inside the definition subtree would still be redacted; here none
    # exists, so no tool wiring changed at all.


def test_secret_named_tool_parameter_is_preserved_and_flagged(delivery_dir, tmp_path):
    """A tool parameter literally named ``password`` must keep its definition (import wiring) and
    be flagged for human review instead of being silently redacted into a broken schema."""
    export = _protected_export("digital_patient_stemi")
    export["agent_info"]["7"]["tools"][1]["parameters"]["properties"]["password"] = {
        "type": "string", "description": "合成参数定义；实际导出中如出现须人工确认"}
    source = tmp_path / "protected.json"
    source.write_text(json.dumps(export, ensure_ascii=False), encoding="utf-8")
    summary = prepare_exports.prepare(source, delivery_dir / "agent.json")
    public = json.loads((delivery_dir / "agent.json").read_text(encoding="utf-8"))
    submit = next(tool for tool in public["agent_info"]["7"]["tools"] if tool["name"] == "submit_fact_selection")
    assert submit["parameters"]["properties"]["password"]["type"] == "string"
    assert {"path": "$.agent_info.7.tools[1].parameters.properties.password",
            "kind": "secret_named_parameter_preserved"} in summary["human_review_flags"]


def test_provenance_hashes_original_bytes_and_records_bom_and_newlines(delivery_dir, tmp_path):
    source = tmp_path / "protected-bom.json"
    export = json.dumps(_protected_export("digital_patient_stemi"), ensure_ascii=False, indent=2)
    source.write_bytes(b"\xef\xbb\xbf" + export.replace("\n", "\r\n").encode("utf-8"))
    import hashlib
    raw_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    summary = prepare_exports.prepare(source, delivery_dir / "agent.json")
    assert summary["source_sha256_of_original_bytes"] == raw_sha
    assert summary["bom_present"] is True and summary["newline_style"] == "crlf"
    # The public copy is normalised UTF-8 without BOM, parseable as-is.
    assert not (delivery_dir / "agent.json").read_bytes().startswith(b"\xef\xbb\xbf")


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


def test_second_source_failure_rolls_back_the_first_copy(delivery_dir, tmp_path, monkeypatch, capsys):
    """A corrupted second input must leave NO public copy behind — a failed run never produces a
    half-generated package that a retry would then refuse. The rollback is the CLI transaction's
    responsibility (single-step prepare() claims its one file by design)."""
    good = tmp_path / "good-patient.json"
    good.write_text(json.dumps(_protected_export("digital_patient_stemi"), ensure_ascii=False), encoding="utf-8")
    corrupt = tmp_path / "corrupt-feedback.json"
    corrupt.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["prepare_delivery_exports.py", "--patient", str(good),
                                      "--feedback", str(corrupt)])
    with pytest.raises(SystemExit) as exit_info:
        prepare_exports.main()
    assert exit_info.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["rolled_back"] == [str(delivery_dir / "agent.json")]
    assert not (delivery_dir / "agent.json").exists()
    assert not (delivery_dir / "feedback" / "agent.json").exists()


def test_review_artifact_failure_rolls_back_written_copies(delivery_dir, tmp_path, monkeypatch):
    patient = tmp_path / "good-patient.json"
    feedback = tmp_path / "good-feedback.json"
    patient.write_text(json.dumps(_protected_export("digital_patient_stemi"), ensure_ascii=False), encoding="utf-8")
    feedback.write_text(json.dumps(_protected_export("feedback_reviewer", role="feedback"), ensure_ascii=False),
                        encoding="utf-8")

    # Simulate "review artifact cannot be written": the destination already exists.
    review_out = tmp_path / "review.json"
    review_out.write_text("{}", encoding="utf-8")
    argv = ["prepare_delivery_exports.py", "--patient", str(patient), "--feedback", str(feedback),
            "--review-out", str(review_out)]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exit_info:
        prepare_exports.main()
    assert exit_info.value.code == 2
    assert not (delivery_dir / "agent.json").exists()
    assert not (delivery_dir / "feedback" / "agent.json").exists()
    assert review_out.read_text(encoding="utf-8") == "{}"  # untouched


def test_package_verification_failure_rolls_back_written_copies(delivery_dir, tmp_path, monkeypatch, capsys):
    patient = tmp_path / "good-patient.json"
    feedback = tmp_path / "good-feedback.json"
    patient.write_text(json.dumps(_protected_export("digital_patient_stemi"), ensure_ascii=False), encoding="utf-8")
    feedback.write_text(json.dumps(_protected_export("feedback_reviewer", role="feedback"), ensure_ascii=False),
                        encoding="utf-8")
    monkeypatch.setattr(prepare_exports, "verify",
                        lambda directory: {"offline_verified": False, "problems": ["simulated"],
                                           "exports": {}, "scope": "x"})
    argv = ["prepare_delivery_exports.py", "--patient", str(patient), "--feedback", str(feedback)]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exit_info:
        prepare_exports.main()
    assert exit_info.value.code == 2
    assert not (delivery_dir / "agent.json").exists()
    assert not (delivery_dir / "feedback" / "agent.json").exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["rolled_back"] == [str(delivery_dir / "agent.json"), str(delivery_dir / "feedback" / "agent.json")]


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


def _run_summarizer(tmp_path, capsys, *extra):
    summarize_forms.main.__globals__["sys"].argv = [
        "summarize_evaluation_forms.py", "--forms-dir", str(tmp_path), *extra]
    with pytest.raises(SystemExit) as exit_info:
        summarize_forms.main()
    assert exit_info.value.code == 0
    return json.loads(capsys.readouterr().out)


def test_summarizer_computes_rates_without_fabricating_verdicts(tmp_path, capsys):
    qa_rows = [
        ["QA-01", "APPY-001", "2", "s1", "r1", "有高血压吗", "有10年", "", "", "是", "否", "评审甲", "", "2026-12-01"],
        ["QA-02", "APPY-001", "2", "s1", "r2", "有糖尿病吗", "没有", "", "", "否", "否", "评审甲", "", "2026-12-01"],
        ["QA-03", "APPY-001", "2", "s1", "r3", "有外伤吗", "", "", "", "", "是", "评审甲", "超时", "2026-12-01"],
        ["QA-04", "APPY-001", "2", "s1", "r4", "以前疼过吗", "没有", "", "", "是", "是", "评审甲", "", "2026-12-01"],
        ["QA-05", "APPY-001", "2", "s1", "r5", "有呕吐吗", "有", "", "", "可能", "否", "评审甲", "", ""],
        ["QA-06", "APPY-999", "9", "s1", "r6", "有腹泻吗", "没有", "", "", "是", "否", "评审甲", "", ""],
    ]
    student_rows = [
        ["待招募-1", "S-A", "APPY-001@2", "第3轮", "病史采集", "4", "4", "±0.5", "是", "", "评审乙", ""],
        ["待招募-1", "S-A", "APPY-001@2", "第3轮", "病史采集", "4", "4", "±0.5", "是", "", "评审乙", ""],
        ["待招募-1", "S-A", "APPY-001@2", "", "检查选择", "4", "4", "±0.5", "", "", "评审乙", ""],
        ["待招募-1", "S-A", "APPY-001@2", "", "鉴别诊断", "4", "", "±0.5", "是", "", "评审乙", ""],
        ["待招募-1", "S-A", "APPY-001@2", "", "诊断", "4", "4", "", "是", "", "评审乙", ""],
        ["待招募-1", "S-A", "APPY-001@9", "", "治疗", "4", "4", "±0.5", "是", "", "评审乙", ""],
        ["待招募-1", "S-A", "APPY-001", "", "治疗", "4", "4", "±0.5", "是", "", "评审乙", ""],
    ]
    error_rows = [
        ["APPY-001-1", "APPY-001", "2", "正常对照", "", "", "", "", "", "否", "", "", "", "", ""],
        ["APPY-001-2", "APPY-001", "2", "漏检查", "", "", "", "", "", "是", "恶化", "是", "是", "审核人", ""],
        ["APPY-001-3", "APPY-001", "2", "检查后延误", "", "", "", "", "", "是", "恶化", "是", "否", "审核人", ""],
        ["APPY-001-4", "APPY-001", "2", "不当检查", "", "", "", "", "", "是", "恶化", "", "", "审核人", ""],
        ["APPY-001-5", "APPY-999", "9", "其他", "", "", "", "", "", "", "", "", "", "审核人", ""],
    ]
    _write_forms(tmp_path, qa_rows, student_rows, error_rows)

    report = _run_summarizer(tmp_path, capsys)
    qa = report["summaries"]["qa_20rounds"]
    assert qa["filled_rows"] == 6 and qa["consistent"] == 2 and qa["inconsistent"] == 1
    assert qa["failed_or_timeout"] == 1 and qa["contradictory"] == 1
    assert qa["invalid_marker_rows"] == 1 and qa["judgement_missing"] == 0
    excluding = qa["agreement_rate_excluding_failures"]
    assert (excluding["numerator"], excluding["denominator"]) == (2, 3) and excluding["value"] == 0.6667
    as_miss = qa["agreement_rate_failures_as_miss"]
    assert (as_miss["numerator"], as_miss["denominator"]) == (2, 6) and as_miss["value"] == 0.3333
    assert "评审确认前不作为正式一致率" in excluding["policy"]
    assert qa["unfrozen_or_invalid_case_references"] == ["非冻结版本:APPY-999@9"]

    student = report["summaries"]["student_expert"]
    assert student["students_recorded"] == 1 and student["complete_students"] == 0
    assert student["overall_complete_students_rate_as_recorded"]["display"] == "未知（分母为 0）"
    reasons = {row["reason"] for row in student["invalid_rows"]}
    assert "重复行（同一学生/病例/轨迹/环节）" in reasons
    assert any(r.startswith("一致性判断为空或非法") for r in reasons)
    assert "缺少专家或系统原分" in reasons and "缺少事先确认容差" in reasons
    assert "非冻结版本:APPY-001@9" in reasons and "格式错误:APPY-001" in reasons

    error = report["summaries"]["error_evolution"]
    assert error["in_denominator"] == 3 and error["triggered"] == 2 and error["not_triggered"] == 0
    assert error["trigger_undecided"] == 1
    assert error["trigger_rate_as_recorded"]["display"] == "2/3"
    assert error["feedback_accuracy_detail"] == {"accurate": 1, "inaccurate": 1, "undecided": 0}
    assert error["accuracy_marked_on_not_triggered_rows"] == 0
    assert {"row": 6, "reason": "非冻结版本:APPY-999@9"} in error["invalid_rows"]
    assert report["verdicts_as_recorded"] is None  # no policy confirmation -> no pass/fail wording


def test_qa_repro_nineteen_consistent_one_failed_is_19_over_19_and_19_over_20(tmp_path, capsys):
    """The handoff repro: 19 consistent, 1 failed/timeout with judgment left blank.

    Excluding failures must read 19/19; counting the failure as a miss must read 19/20. Neither
    rate may be presented without its numerator, denominator and policy.
    """
    qa_rows = [[f"QA-{i:02d}", "APPY-001", "2", "s1", f"r{i}", "问", "答", "", "", "是", "否", "评审甲", "", ""]
               for i in range(1, 20)]
    qa_rows.append(["QA-20", "APPY-001", "2", "s1", "r20", "问", "", "", "", "", "是", "评审甲", "超时", ""])
    _write_forms(tmp_path, qa_rows, [], [])

    report = _run_summarizer(tmp_path, capsys)
    qa = report["summaries"]["qa_20rounds"]
    assert qa["filled_rows"] == 20 and qa["consistent"] == 19 and qa["failed_or_timeout"] == 1
    excluding = qa["agreement_rate_excluding_failures"]
    assert (excluding["numerator"], excluding["denominator"], excluding["value"]) == (19, 19, 1.0)
    as_miss = qa["agreement_rate_failures_as_miss"]
    assert (as_miss["numerator"], as_miss["denominator"], as_miss["value"]) == (19, 20, 0.95)


def test_policy_confirmation_cannot_bless_insufficient_material(tmp_path, capsys):
    qa_rows = [["QA-01", "APPY-001", "2", "s1", "r1", "问", "答", "", "", "是", "否", "评审甲", "", ""]]
    student_rows = [["待招募-1", "S-A", "APPY-001@2", "", stage, "4", "4", "±0.5", "是", "", "评审乙", ""]
                    for stage in ("病史采集", "检查选择", "鉴别诊断", "诊断", "治疗")]
    error_rows = [["APPY-001-1", "APPY-001", "2", "漏检查", "", "", "", "", "", "是", "恶化", "是", "是",
                   "审核人", ""]]
    _write_forms(tmp_path, qa_rows, student_rows, error_rows)

    report = _run_summarizer(tmp_path, capsys, "--policy-confirmed-by", "评审组长",
                             "--failure-policy", "excluding")
    verdict = report["verdicts_as_recorded"]
    assert verdict["verdict"] == "verdict_withheld"
    assert any("问答已尝试轮次 1 < 20" in reason for reason in verdict["reasons"])
    assert any("完整五环节学生 1 < 3" in reason for reason in verdict["reasons"])
    assert verdict["scope"].startswith("as_recorded only")


def test_policy_with_sufficient_material_and_recorded_failure_policy(tmp_path, capsys):
    qa_rows = [[f"QA-{i:02d}", "APPY-001", "2", "s1", f"r{i}", "问", "答", "", "", "是", "否", "评审甲", "", ""]
               for i in range(1, 21)]
    student_rows = [[f"待招募-{n}", f"S-{n}", "APPY-001@2", "", stage, "4", "4", "±0.5", "是", "", "评审乙", ""]
                    for n in (1, 2, 3) for stage in ("病史采集", "检查选择", "鉴别诊断", "诊断", "治疗")]
    error_rows = [[f"APPY-001-{i}", "APPY-001", "2", "漏检查", "", "", "", "", "", "是", "恶化", "是", "是",
                   "审核人", ""] for i in range(1, 11)]
    _write_forms(tmp_path, qa_rows, student_rows, error_rows)

    report = _run_summarizer(tmp_path, capsys, "--policy-confirmed-by", "评审组长",
                             "--failure-policy", "as_miss")
    verdict = report["verdicts_as_recorded"]
    assert verdict["verdict"] == "meets_target_as_recorded"
    assert verdict["failure_policy"] == "as_miss" and verdict["confirmed_by"] == "评审组长"
    assert verdict["applied_rates"]["qa"]["policy"].startswith("失败或超时的轮次按未命中计入分母")

    # The same 20 rounds with one failure read 19/20 under as_miss and 19/19 under excluding:
    # the recorded policy decides which rate applies, never the tool.
    qa_rows[-1][10] = "是"
    qa_rows[-1][9] = ""
    _write_forms(tmp_path, qa_rows, student_rows, error_rows)
    report = _run_summarizer(tmp_path, capsys, "--policy-confirmed-by", "评审组长",
                             "--failure-policy", "as_miss")
    applied = report["verdicts_as_recorded"]["applied_rates"]["qa"]
    assert (applied["numerator"], applied["denominator"]) == (19, 20)
    report = _run_summarizer(tmp_path, capsys, "--policy-confirmed-by", "评审组长",
                             "--failure-policy", "excluding")
    applied = report["verdicts_as_recorded"]["applied_rates"]["qa"]
    assert (applied["numerator"], applied["denominator"]) == (19, 19)


def test_all_failed_and_zero_denominator_show_unknown(tmp_path, capsys):
    qa_rows = [[f"QA-{i:02d}", "APPY-001", "2", "s1", f"r{i}", "问", "", "", "", "", "是", "评审甲", "超时", ""]
               for i in range(1, 21)]
    _write_forms(tmp_path, qa_rows, [], [])
    report = _run_summarizer(tmp_path, capsys)
    qa = report["summaries"]["qa_20rounds"]
    assert qa["consistent"] == 0 and qa["failed_or_timeout"] == 20
    excluding = qa["agreement_rate_excluding_failures"]
    assert excluding["denominator"] == 0 and excluding["value"] is None
    assert excluding["display"] == "未知（分母为 0）"
    as_miss = qa["agreement_rate_failures_as_miss"]
    assert (as_miss["numerator"], as_miss["denominator"]) == (0, 20) and as_miss["value"] == 0.0


def test_structural_header_errors_are_actionable(tmp_path, capsys):
    _write_forms(tmp_path, [], [], [])
    path = tmp_path / "问答抽检_20轮_待填写.csv"
    text = path.read_text(encoding="utf-8-sig")
    path.write_text(text.replace("是否一致", "一致与否"), encoding="utf-8-sig")
    summarize_forms.main.__globals__["sys"].argv = ["summarize_evaluation_forms.py", "--forms-dir", str(tmp_path)]
    with pytest.raises(SystemExit) as exit_info:
        summarize_forms.main()
    assert exit_info.value.code == 2
    problems = json.loads(capsys.readouterr().out)["problems"]
    assert any("缺少列 ['是否一致']" in item and "review-forms-20261127" in item for item in problems)


def test_summarizer_reports_missing_forms_and_writes_report_once(tmp_path, capsys):
    _write_forms(tmp_path, [], [], [])
    (tmp_path / "学生专家评分_至少3人_待填写.csv").unlink()
    summarize_forms.main.__globals__["sys"].argv = ["summarize_evaluation_forms.py", "--forms-dir", str(tmp_path)]
    with pytest.raises(SystemExit) as excinfo:
        summarize_forms.main()
    assert excinfo.value.code == 2
    assert "缺少 学生专家评分_至少3人_待填写.csv" in capsys.readouterr().out

    _write_forms(tmp_path, [], [], [])
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    summarize_forms.main.__globals__["sys"].argv = [
        "summarize_evaluation_forms.py", "--forms-dir", str(tmp_path), "--report", str(tmp_path / "report.json")]
    with pytest.raises(SystemExit) as excinfo:
        summarize_forms.main()
    assert excinfo.value.code == 2
    assert "不覆盖" in capsys.readouterr().out
