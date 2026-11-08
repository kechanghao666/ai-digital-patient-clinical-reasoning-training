from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .case_store import CaseStore
from .integration.runtime import IntegrationRuntime
from .integration.tools import _event_digest, validate_feedback_items
from .models import CaseDefinition, SessionState
from .storage import ConcurrentUpdateError, SessionRepository


class FeedbackReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    owner_id: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=1000)
    items: list[dict[str, Any]] = Field(min_length=5, max_length=5)


def build_router(
    get_repository: Callable[[], SessionRepository], get_cases: Callable[[], CaseStore],
    get_runtime: Callable[[], IntegrationRuntime], current_teacher: Callable[..., str],
) -> APIRouter:
    router = APIRouter(prefix="/api/teacher/feedback", tags=["teacher"])

    def context(session_id: str, owner_id: str) -> tuple[SessionState, CaseDefinition]:
        try:
            state = get_repository().get(session_id, owner_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="训练不存在或学生账号不匹配") from exc
        if not state.sealed or state.score_snapshot is None:
            raise HTTPException(status_code=409, detail="只有已封存且含评分快照的训练可复核")
        try:
            case = get_cases().get(state.case_id, state.case_version)
        except KeyError as exc:
            raise HTTPException(status_code=409, detail="训练引用的病例版本不可用，请先恢复该版本") from exc
        return state, case

    @router.get("/{session_id}")
    def read(session_id: str, owner_id: str = Query(min_length=1, max_length=200),
             _: str = Depends(current_teacher)) -> dict[str, Any]:
        state, case = context(session_id, owner_id)
        runtime = get_runtime()
        original = runtime.feedback_store.get(session_id, owner_id)
        reviews = runtime.feedback_store.reviews(session_id, owner_id)
        effective = runtime.feedback_store.effective(session_id, owner_id)
        checked = validate_feedback_items(effective["items"], state, case, runtime.knowledge) if effective else None
        original_check = validate_feedback_items(original["items"], state, case, runtime.knowledge) if original else None
        return {
            "session_id": session_id, "owner_id": owner_id,
            "case": {"title": case.title, "public_id": case.public_id, "version": case.version},
            "score_snapshot": state.score_snapshot.model_dump(mode="json"),
            "events": [_event_digest(event, case) for event in state.events[:state.seal_seq]],
            "submissions": {"diagnosis": state.diagnosis_submission,
                            "differential": state.differential_submission,
                            "treatment": state.treatment_submission},
            "case_notes": state.case_notes,
            "original": original, "reviews": reviews,
            "current_revision": reviews[-1]["revision"] if reviews else 0,
            "validation_error": checked if isinstance(checked, str) else None,
            "original_validation_error": original_check if isinstance(original_check, str) else None,
            "editable_items": effective["items"] if effective else [],
            "teaching_basis": [{"basis_id": e.basis_id, "title": e.title, "stages": e.stages,
                                "text": e.text, "sources": e.sources}
                               for e in runtime.knowledge.for_case(case.case_id)],
            "notice": "仅复核反馈文字及引用，不改变封存分数或待评分复核状态，也不代表医学审核。",
        }

    @router.post("/{session_id}")
    def review(session_id: str, request: FeedbackReviewRequest,
               teacher_id: str = Depends(current_teacher)) -> dict[str, Any]:
        state, case = context(session_id, request.owner_id)
        runtime = get_runtime()
        checked = validate_feedback_items(request.items, state, case, runtime.knowledge)
        if isinstance(checked, str):
            raise HTTPException(status_code=422, detail="复核反馈未通过校验：" + checked)
        try:
            revision = runtime.feedback_store.append_review(
                session_id, request.owner_id, teacher_id, request.request_id, request.expected_revision,
                request.reason, checked, runtime.knowledge.status,
            )
        except ConcurrentUpdateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"revision": revision, "score_status": state.score_snapshot.status,
                "notice": "已保存反馈复核版本，原始反馈、轨迹和封存分数保持不变。"}

    return router
