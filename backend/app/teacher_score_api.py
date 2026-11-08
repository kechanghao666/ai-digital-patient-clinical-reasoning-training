from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .case_store import CaseStore
from .integration.runtime import IntegrationRuntime
from .integration.tools import _event_digest
from .models import ScoreSnapshot
from .scoring import student_score_snapshot
from .storage import ConcurrentUpdateError, SessionRepository


class ScoreDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    item_id: str = Field(min_length=1, max_length=200)
    score: float = Field(ge=0, strict=True, allow_inf_nan=False)
    reason: str = Field(min_length=1, max_length=600)
    evidence_event_ids: list[str] = Field(max_length=100)


class ScoreReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    owner_id: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=1000)
    confirmed: bool = Field(strict=True)
    items: list[ScoreDecision] = Field(min_length=1, max_length=1000)


def reviewed_snapshot(original: ScoreSnapshot, request: ScoreReviewRequest, event_ids: set[str]) -> ScoreSnapshot:
    decisions = {item.item_id: item for item in request.items}
    if len(decisions) != len(request.items) or set(decisions) != {item.item_id for item in original.items}:
        raise ValueError("须逐项裁定原始快照中的全部评分项，不得增加、重复或遗漏")
    items = []
    for item in original.items:
        decision = decisions[item.item_id]
        evidence = list(dict.fromkeys(decision.evidence_event_ids))
        if decision.score > item.max_score:
            raise ValueError("裁定分数不得超过原始评分项上限")
        if set(evidence) - event_ids or decision.score > 0 and not evidence:
            raise ValueError("得分项须引用封存前真实事件，所有引用须在封存范围内")
        items.append(item.model_copy(update={"score": decision.score, "hit": decision.score == item.max_score,
                                            "reason": decision.reason, "evidence_event_ids": evidence,
                                            "improvement": "" if decision.score == item.max_score else "请结合教师裁定说明复盘本项训练。"}))
    # Keep the original timestamp in the payload so a retried request hashes identically.
    return original.model_copy(update={"items": items, "total_score": sum(item.score for item in items), "status": "complete"})


def build_router(get_repository: Callable[[], SessionRepository], get_cases: Callable[[], CaseStore],
                 get_runtime: Callable[[], IntegrationRuntime], current_teacher: Callable[..., str]) -> APIRouter:
    router = APIRouter(prefix="/api/teacher/scores", tags=["teacher"])

    def context(session_id: str, owner_id: str):
        try:
            state = get_repository().get(session_id, owner_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="训练不存在或学生账号不匹配") from exc
        if not state.sealed or state.score_snapshot is None or state.score_snapshot.status != "needs_review":
            raise HTTPException(status_code=409, detail="当前入口仅处理已封存且规则评分为待复核的训练")
        try:
            case = get_cases().get(state.case_id, state.case_version)
        except KeyError as exc:
            raise HTTPException(status_code=409, detail="原病例版本不可用，请先恢复该版本") from exc
        return state, case

    @router.get("/{session_id}")
    def read(session_id: str, owner_id: str = Query(min_length=1, max_length=200),
             _: str = Depends(current_teacher)) -> dict:
        state, case = context(session_id, owner_id)
        reviews = get_runtime().score_reviews.list(session_id, owner_id)
        return {"session_id": session_id, "owner_id": owner_id,
                "case": {"title": case.title, "public_id": case.public_id, "version": case.version},
                "original_score": state.score_snapshot.model_dump(mode="json"), "reviews": reviews,
                "current_revision": reviews[-1]["revision"] if reviews else 0,
                "events": [_event_digest(event, case) for event in state.events[:state.seal_seq]],
                "rubric": [item.model_dump(mode="json") for item in case.rubric],
                "submissions": {"diagnosis": state.diagnosis_submission, "differential": state.differential_submission,
                                "treatment": state.treatment_submission}, "case_notes": state.case_notes,
                "basis_coverage": get_runtime().knowledge.coverage(case.case_id, case.version)}

    @router.post("/{session_id}")
    def save(session_id: str, request: ScoreReviewRequest, teacher_id: str = Depends(current_teacher)) -> dict:
        state, case = context(session_id, request.owner_id)
        if not request.confirmed:
            raise HTTPException(status_code=422, detail="须明确确认已逐项核对分数、事件和量表")
        try:
            snapshot = reviewed_snapshot(state.score_snapshot, request, {event.event_id for event in state.events[:state.seal_seq]})
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        safe = student_score_snapshot(snapshot, case)
        if any(item.reason != checked.reason for item, checked in zip(snapshot.items, safe.items)):
            raise HTTPException(status_code=422, detail="分项裁定说明含参考答案或受保护内容，请改写为可公开的判断依据")
        try:
            revision = get_runtime().score_reviews.append(session_id, request.owner_id, teacher_id, request.request_id,
                request.expected_revision, request.reason, snapshot.model_dump(mode="json"))
        except ConcurrentUpdateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"revision": revision, "notice": "裁定已独立保存；原始规则评分、反馈和训练轨迹保留。"}

    return router
