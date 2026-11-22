"""Run the dialogue evaluation set through the real API (local simulator) and write a report.

Each item uses a fresh training so earlier questions cannot influence the answer. Every item is
run three times to check that the same input gives the same disclosed facts and the same answer.
Data goes to a temporary directory; the project databases are not touched.

Usage (repository root):
    python scripts/run_dialogue_eval.py                    # -> evaluation/reports/<timestamp>/
    python scripts/run_dialogue_eval.py <new-output-dir>
    python scripts/run_dialogue_eval.py --set evaluation/dialogue_holdout_set.json

The labels are engineering annotations by the developer, not an expert evaluation. Passing here
does not satisfy the official ">=95% on 20 audited turns" criterion by itself.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = 3
_data = tempfile.TemporaryDirectory(prefix="dialogue-eval-")
os.environ.update(APP_DATA_DIR=_data.name, INTEGRATION_DB_PATH=str(Path(_data.name) / "integration.db"),
                  NEXENT_MODE="simulator", APP_ENV="development", APP_AUTH_MODE="demo")
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.adapters import diagnosis_leak_terms  # noqa: E402

HEADERS = {"X-Owner-Id": "demo-dialogue-eval"}


def judge(item, fact_ids, answer, case):
    expected, optional = set(item.get("expected", [])), set(item.get("optional", []))
    got = set(fact_ids)
    leaked_terms = sorted(term for term in diagnosis_leak_terms(case) if term and term in answer.lower())
    hidden = sorted(got & {"f_deterioration"})
    missing, extra = sorted(expected - got), sorted(got - expected - optional)
    return {
        "missing": missing, "extra": extra, "leaked_terms": leaked_terms, "hidden_disclosed": hidden,
        "passed": not missing and not extra and not leaked_terms and not hidden,
    }


def main_run(out: Path, spec_path: Path = ROOT / "evaluation/dialogue_eval_set.json") -> dict:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    client = TestClient(main.app)
    rows = []
    for index, item in enumerate(spec["items"]):
        runs = []
        for attempt in range(RUNS):
            sid = client.post(f"/api/sessions?case_id={item['case']}", headers=HEADERS).json()["session_id"]
            started = time.perf_counter()
            response = client.post(f"/api/sessions/{sid}/ask", headers=HEADERS,
                                   json={"request_id": f"eval-{index}-{attempt}", "question": item["question"]})
            elapsed_ms = (time.perf_counter() - started) * 1000
            body = response.json()
            state = main.REPOSITORY.get(sid, HEADERS["X-Owner-Id"])
            event = state.events[-1]
            runs.append({"status": response.status_code, "answer": body.get("answer", ""),
                         "fact_ids": event.result.get("fact_ids", []), "turn_kind": event.result.get("turn_kind"),
                         "elapsed_ms": round(elapsed_ms, 2), "case_version": state.case_version})
        case = main.CASE_STORE.get(main.CASE_STORE.get_by_public_id(item["case"]).case_id, runs[0]["case_version"])
        verdict = judge(item, runs[0]["fact_ids"], runs[0]["answer"], case)
        consistent = all(r["answer"] == runs[0]["answer"] and r["fact_ids"] == runs[0]["fact_ids"] for r in runs)
        rows.append({**item, "case_version": runs[0]["case_version"], "answer": runs[0]["answer"],
                     "fact_ids": runs[0]["fact_ids"], "turn_kind": runs[0]["turn_kind"], **verdict,
                     "consistent_3_runs": consistent, "latency_ms": [r["elapsed_ms"] for r in runs],
                     "http_ok": all(r["status"] == 200 for r in runs)})
    content = [r for r in rows if not r.get("probe")]
    probes = [r for r in rows if r.get("probe")]
    expected_total = sum(len(r.get("expected", [])) for r in content)
    hit_total = sum(len(set(r.get("expected", [])) - set(r["missing"])) for r in content)
    returned_total = sum(len(r["fact_ids"]) for r in content)
    latencies = [ms for r in rows for ms in r["latency_ms"]]
    summary = {
        "items": len(rows), "content_items": len(content), "probe_items": len(probes),
        "pass_rate": round(sum(r["passed"] for r in rows) / len(rows), 4),
        "content_pass_rate": round(sum(r["passed"] for r in content) / len(content), 4),
        "fact_recall": round(hit_total / expected_total, 4) if expected_total else None,
        "fact_precision": round((returned_total - sum(len(r["extra"]) for r in content)) / returned_total, 4)
        if returned_total else None,
        "leak_count": sum(bool(r["leaked_terms"] or r["hidden_disclosed"]) for r in rows),
        "probe_pass_rate": round(sum(r["passed"] for r in probes) / len(probes), 4) if probes else None,
        "consistency_3_runs": round(sum(r["consistent_3_runs"] for r in rows) / len(rows), 4),
        "latency_ms_median": round(statistics.median(latencies), 2),
        "latency_ms_max": round(max(latencies), 2),
        "patient_adapter": "simulator",
        "annotator": spec["annotator"],
    }
    by_case = {}
    for r in rows:
        bucket = by_case.setdefault(r["case"], {"items": 0, "passed": 0, "version": r["case_version"]})
        bucket["items"] += 1
        bucket["passed"] += r["passed"]
    out.mkdir(parents=True, exist_ok=False)
    (out / "results.json").write_text(json.dumps({"summary": summary, "by_case": by_case, "rows": rows},
                                                 ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# 问诊一致性评测报告：{spec['name']}", "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}；患者适配器：本地规则模拟（未调用大模型）；"
        f"标注：{spec['annotator']}。每条问句新建训练、重复 {RUNS} 次。", "",
        "| 指标 | 结果 |", "|---|---|",
        f"| 总条目（含泄露探针） | {summary['items']}（探针 {summary['probe_items']}） |",
        f"| 逐条通过率 | {summary['pass_rate']:.1%} |",
        f"| 内容问句通过率 | {summary['content_pass_rate']:.1%} |",
        f"| 期望事实召回率 | {summary['fact_recall']:.1%} |",
        f"| 返回事实精确率（非多答比例） | {summary['fact_precision']:.1%} |",
        f"| 泄露（诊断词或未发生的危急变化） | {summary['leak_count']} 条 |",
        f"| 泄露探针通过率 | {summary['probe_pass_rate']:.1%} |",
        f"| 三次重复一致 | {summary['consistency_3_runs']:.1%} |",
        f"| 接口耗时中位数 / 最大 | {summary['latency_ms_median']} / {summary['latency_ms_max']} 毫秒 |", "",
        "| 病例 | 版本 | 条目 | 通过 |", "|---|---|---|---|",
        *[f"| {case} | v{b['version']} | {b['items']} | {b['passed']} |" for case, b in sorted(by_case.items())], "",
        "## 未通过条目", "",
    ]
    failed = [r for r in rows if not r["passed"]]
    if failed:
        lines += ["| 病例 | 问句 | 期望 | 实际 | 漏答 | 多答 | 回答 |", "|---|---|---|---|---|---|---|"]
        lines += [f"| {r['case']} | {r['question']} | {'、'.join(r.get('expected', [])) or '-'} | "
                  f"{'、'.join(r['fact_ids']) or '-'} | {'、'.join(r['missing']) or '-'} | "
                  f"{'、'.join(r['extra']) or '-'} | {r['answer']} |" for r in failed]
    else:
        lines.append("无。")
    lines += ["", "## 说明", "",
              "本报告只验证本地规则模拟层。标注为开发者工程标注，未经医学专家复核；通过率不能替代官方“20轮抽检≥95%”的正式判定，"
              "也不代表真实大模型的表现。接口耗时为进程内调用，不含网络和浏览器绘制。"]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("out", nargs="?")
    parser.add_argument("--set", default=str(ROOT / "evaluation/dialogue_eval_set.json"))
    args = parser.parse_args()
    spec_path = Path(args.set).resolve()
    target = Path(args.out).resolve() if args.out else \
        ROOT / "evaluation/reports" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + spec_path.stem)
    print(json.dumps(main_run(target, spec_path), ensure_ascii=False, indent=2))
    print(f"报告：{target / 'report.md'}")
