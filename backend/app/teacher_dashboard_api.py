"""Read-only class overview for teachers: overview -> per case -> per training.

Shows original rule scores only (teacher score reviews are stored separately and are not merged).
Scores are engineering rule results on unreviewed draft cases, not formal grades.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException

from .history_coverage import history_coverage
from .integration.turns import empty_stats
from .teacher_scope import teacher_member_ids

logger = logging.getLogger(__name__)

STAGES = ("病史采集", "检查选择", "鉴别诊断", "诊断", "治疗")


def _ratio(score: float, maximum: float) -> float | None:
    return round(score / maximum, 4) if maximum else None


def build_dashboard(states, case_lookup, people) -> dict:
    trainings = []
    by_case: dict[str, dict] = {}
    for state in states:
        try:
            case = case_lookup(state.case_id, state.case_version)
        except KeyError:
            continue
        snapshot = state.score_snapshot
        coverage = history_coverage(state.events)
        person = people.get(state.owner_id) or {}
        stage_scores: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        if snapshot:
            for item in snapshot.items:
                stage_scores[item.stage][0] += item.score
                stage_scores[item.stage][1] += item.max_score
        trainings.append({
            "session_id": state.session_id,
            "summary_id": state.session_id[:8],
            "owner_id": state.owner_id,
            "username": person.get("username", state.owner_id),
            "display_name": person.get("display_name", ""),
            "case_id": case.case_id,
            "public_id": case.public_id,
            "case_version": case.version,
            "title": case.title,
            "sealed": state.sealed,
            "question_count": coverage["question_count"],
            "history_domains": coverage["covered"],
            "history_domains_total": coverage["total"],
            "score_status": snapshot.status if snapshot else None,
            "score_ratio": _ratio(snapshot.total_score, snapshot.max_score) if snapshot else None,
            "stage_ratios": {stage: _ratio(*stage_scores[stage]) for stage in STAGES if stage in stage_scores},
        })
        bucket = by_case.setdefault(case.case_id, {
            "case_id": case.case_id, "public_id": case.public_id, "title": case.title,
            "specialty": case.specialty, "trainings": 0, "sealed": 0, "needs_review": 0,
            "_ratios": [], "_stages": defaultdict(lambda: [0.0, 0.0]), "_items": {},
        })
        bucket["trainings"] += 1
        if not snapshot:
            continue
        bucket["sealed"] += 1
        bucket["needs_review"] += snapshot.status == "needs_review"
        if snapshot.max_score:
            bucket["_ratios"].append(snapshot.total_score / snapshot.max_score)
        descriptions = {item.item_id: item.description for item in case.rubric}
        descriptions["exam_stewardship"] = "检查合理性（避免无关检查）"
        for item in snapshot.items:
            bucket["_stages"][item.stage][0] += item.score
            bucket["_stages"][item.stage][1] += item.max_score
            entry = bucket["_items"].setdefault(item.item_id, {
                "item_id": item.item_id, "stage": item.stage,
                "description": descriptions.get(item.item_id, item.item_id), "attempts": 0, "lost": 0})
            entry["attempts"] += 1
            entry["lost"] += item.score < item.max_score
    cases = []
    for bucket in by_case.values():
        ratios = bucket.pop("_ratios")
        stages = bucket.pop("_stages")
        items = bucket.pop("_items")
        missed = sorted((dict(entry, loss_rate=round(entry["lost"] / entry["attempts"], 4))
                         for entry in items.values() if entry["lost"]),
                        key=lambda entry: (-entry["loss_rate"], -entry["lost"], entry["item_id"]))
        cases.append(dict(bucket,
                          average_ratio=round(sum(ratios) / len(ratios), 4) if ratios else None,
                          stage_ratios={stage: _ratio(*stages[stage]) for stage in STAGES if stage in stages},
                          frequently_lost=missed[:5]))
    cases.sort(key=lambda case: case["public_id"])
    sealed = [t for t in trainings if t["score_ratio"] is not None]
    return {
        "overview": {
            "trainings": len(trainings),
            "students": len({t["owner_id"] for t in trainings}),
            "sealed": sum(t["sealed"] for t in trainings),
            "needs_review": sum(t["score_status"] == "needs_review" for t in trainings),
            "average_ratio": round(sum(t["score_ratio"] for t in sealed) / len(sealed), 4) if sealed else None,
        },
        "cases": cases,
        "trainings": trainings,
        "notice": "原规则评分，病例未经医学审核，未合并教师裁定；仅用于教学复盘，不是正式成绩。",
    }


def build_router(get_repository: Callable, get_cases: Callable, get_accounts: Callable, current_teacher: Callable,
                 get_roster: Callable | None = None, get_turn_stats: Callable | None = None,
                 get_turn_budget: Callable | None = None):
    router = APIRouter(prefix="/api/teacher")

    @router.get("/dashboard")
    def dashboard(class_id: str | None = None, teacher_id: str = Depends(current_teacher)) -> dict:
        members = set(teacher_member_ids(get_roster, teacher_id, class_id))
        states = [state for state in get_repository().list_all() if state.owner_id in members]
        people = get_accounts().directory(state.owner_id for state in states)
        return build_dashboard(states, get_cases().get, people)

    @router.get("/observability")
    def observability(teacher_id: str = Depends(current_teacher), case_id: str | None = None,
                      date_from: str | None = None, date_to: str | None = None) -> dict:
        # Read-only, teacher-only observability over the trusted turn/call store. A teacher sees
        # only the trusted turns of the students enrolled in their own classes (the existing
        # class-ownership relationship); tool-call stats follow the same scoped turns. Optional
        # case/date filters narrow the same scoped rows — they never widen the scope. Distinguish
        # "no data yet" (store absent / no turns table) from "statistics unavailable" (corrupted
        # or incomplete store): the former is a legitimate empty state, the latter is an error.
        if get_turn_stats is None:
            raise HTTPException(404, "可观测统计未启用")
        try:
            owner_ids = _teacher_member_ids(get_roster, teacher_id)
        except Exception as exc:
            # A roster/query failure is not "no class": refuse to serve statistics and keep the
            # diagnostic server-side. Never fall back to the full library here.
            logger.warning("observability roster lookup failed for teacher=%s: %s", teacher_id, type(exc).__name__)
            raise HTTPException(503, "统计暂不可用") from exc
        case_ids = [case_id] if case_id else None
        try:
            result = get_turn_stats(owner_ids, case_ids, date_from, date_to)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            logger.warning("observability stats failed for teacher=%s: %s", teacher_id, type(exc).__name__)
            raise HTTPException(503, "统计暂不可用") from exc
        if not result.get("ok") and result.get("reason") not in ("not_found", "not_initialized"):
            raise HTTPException(503, result.get("error", "统计暂不可用"))
        stats = result["stats"] if result.get("ok") else empty_stats()
        body: dict = {"ok": True, "stats": stats}
        if not result.get("ok"):
            body["store_note"] = result.get("error")
        budget, budget_note = _budget(get_turn_budget, stats.get("turns_today"))
        if budget is not None:
            budget["scope"] = "teacher-owned class members; independent of case/date filters"
            if case_ids is not None or date_from is not None or date_to is not None:
                from .turn_budget import budget_status

                try:
                    daily = get_turn_stats(owner_ids, None, None, None)
                    if not daily.get("ok"):
                        raise ValueError("daily statistics unavailable")
                    budget.update(budget_status(budget, daily["stats"]["turns_today"]))
                except Exception as exc:
                    logger.warning("daily budget statistics failed: %s", type(exc).__name__)
                    budget.update(budget_status(budget, None))
                    budget_note = "今日回合统计不可用，预算状态未知"
        if budget_note:
            body["budget_note"] = budget_note
        if budget is not None or get_turn_budget is not None:
            body["budget"] = budget
        return body

    return router


def _budget(get_turn_budget: Callable | None, turns_today: int | None) -> tuple[dict | None, str | None]:
    """Load the configured budget and merge today's count; failures degrade to a note, never a claim."""
    from .turn_budget import budget_status

    if get_turn_budget is None:
        return None, None
    try:
        config = get_turn_budget()
    except Exception as exc:
        logger.warning("turn budget lookup failed: %s", type(exc).__name__)
        return None, "预算配置不可用"
    if config is None:
        return None, None
    return budget_status(config, turns_today), None


def _teacher_member_ids(get_roster: Callable | None, teacher_id: str) -> list[str]:
    """The student account ids across all classes owned by ``teacher_id``.

    Returns an empty list only for a genuinely empty roster; a lookup/query failure raises so the
    caller can report "statistics unavailable" rather than silently treating it as "no data".
    """
    return teacher_member_ids(get_roster, teacher_id)
