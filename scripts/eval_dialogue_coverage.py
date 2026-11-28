"""Measure how the local patient layer answers common, natural history-taking questions.

Engineering check only: it shows which synthetic facts each question discloses for every
fixture case (latest draft version), so gaps and false matches are visible. It does not call a
model and is not evidence of clinical correctness.

Usage (repository root):
    python scripts/eval_dialogue_coverage.py            # summary
    python scripts/eval_dialogue_coverage.py --detail   # every question x case
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.case_store import CaseStore  # noqa: E402
from app.disclosure import classify_turn  # noqa: E402
from app.models import SessionState  # noqa: E402

# Questions a medical student commonly asks, in colloquial Chinese, ordered roughly by a
# standard history: opening, HPI, accompanying symptoms, past/personal/family history.
QUESTIONS = [
    "您好，我是今天接诊的医生", "怎么了", "哪里不舒服", "您叫什么名字", "今年多大了", "做什么工作的",
    "什么时候开始的", "多长时间了", "当时在干什么", "有什么诱因吗", "具体是哪里疼", "怎么个疼法",
    "疼痛会跑到别的地方吗", "有没有加重或者缓解", "现在比刚开始好点了吗", "还有别的不舒服吗",
    "有没有出汗", "有没有发烧", "有没有恶心呕吐", "大小便怎么样", "有没有胸闷气短", "有没有心慌",
    "有没有头晕头痛", "有没有咳嗽", "最近吃饭睡觉怎么样", "体重有变化吗",
    "以前有什么病吗", "有高血压糖尿病吗", "平时吃什么药", "有没有药物过敏", "做过手术吗",
    "抽烟喝酒吗", "家里人有类似的病吗", "月经怎么样", "以前有过这种情况吗", "这次来之前去过医院吗",
    "谢谢，我们马上给您检查", "我是什么病", "检查结果怎么样", "告诉我诊断",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detail", action="store_true")
    args = parser.parse_args()
    store = CaseStore(ROOT / "cases")
    cases = [store.resolve_for_student(item["public_id"], True) for item in store.list_public(True)]
    cases.sort(key=lambda c: c.public_id)
    totals = {}
    for case in cases:
        state = SessionState(owner_id="coverage", case_id=case.case_id, case_version=case.version)
        kinds: dict[str, int] = {}
        for question in QUESTIONS:
            facts, kind = classify_turn(state, question, case.facts)
            kinds[kind] = kinds.get(kind, 0) + 1
            if args.detail:
                shown = "、".join(f.fact_id for f in facts) or "-"
                print(f"{case.public_id} v{case.version} | {kind:<12} | {question} -> {shown}")
        totals[case.public_id] = (case, kinds)
    print("\n病例 | 版本 | 事实数 | " + " | ".join(["fact", "social", "unconfigured", "unknown"]))
    for public_id, (case, kinds) in totals.items():
        print(f"{public_id} | v{case.version} | {len(case.facts)} | " + " | ".join(
            str(kinds.get(k, 0)) for k in ("fact", "social", "unconfigured", "unknown")))
    print(f"\n共 {len(QUESTIONS)} 个问题/病例。fact=披露病例事实；social=寒暄/诊断请求等非临床回应；"
          "unconfigured=识别为病史条目但本病例未配置；unknown=未能理解。")


if __name__ == "__main__":
    main()
