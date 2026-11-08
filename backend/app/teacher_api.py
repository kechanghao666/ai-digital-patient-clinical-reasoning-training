from __future__ import annotations

from datetime import date
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError

from .case_authoring import Issue, check_case, has_errors, schema_errors
from .case_store import CaseStore, CaseStoreError
from .integration.knowledge import KnowledgeStore
from .models import CaseDefinition, ReviewStatus


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer: str
    review_date: str
    confirm_medical_review: bool


def _parse_case(raw: Any) -> CaseDefinition:
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail={"issues": [{"level": "error", "code": "schema", "message": "病例必须是JSON对象"}]})
    try:
        from .legacy import apply_legacy_profile

        return CaseDefinition.model_validate(apply_legacy_profile(raw))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail={"issues": schema_errors(exc)}) from exc


def _issues(issues: list[Issue]) -> list[dict[str, str]]:
    return [issue.as_dict() for issue in issues]


def _summary(case: CaseDefinition) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "version": case.version,
        "public_id": case.public_id,
        "review_status": case.review_status.value,
        "authored_by": case.authored_by,
    }


def build_router(get_store: Callable[[], CaseStore], current_teacher: Callable[..., str],
                 get_knowledge: Callable[[], KnowledgeStore]) -> APIRouter:
    router = APIRouter(prefix="/api/teacher", tags=["teacher"])

    def checked_issues(case: CaseDefinition, for_publish: bool = False) -> list[Issue]:
        issues = check_case(case, for_publish=for_publish)
        missing = get_knowledge().coverage(case.case_id)["missing_clinical_stages"]
        if missing:
            issues.append(Issue("warning", "missing_clinical_basis",
                                "缺少适用于当前病例的专科临床依据：" + "、".join(missing) + "；通用反馈规范不能替代。"))
        return issues

    @router.get("/cases/{case_id}/versions/{version}/teaching-basis")
    def basis_coverage(case_id: str, version: int, _: str = Depends(current_teacher)) -> dict[str, Any]:
        try:
            case = get_store().get(case_id, version)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="病例版本不存在") from exc
        return get_knowledge().coverage(case.case_id)

    @router.get("/cases")
    def list_cases(_: str = Depends(current_teacher)) -> list[dict[str, Any]]:
        return get_store().list_all()

    @router.get("/cases/{case_id}/versions/{version}")
    def read_case(case_id: str, version: int, _: str = Depends(current_teacher)) -> dict[str, Any]:
        try:
            return get_store().get(case_id, version).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="病例版本不存在") from exc

    @router.post("/cases/validate")
    def validate(raw: dict[str, Any], _: str = Depends(current_teacher)) -> dict[str, Any]:
        case = _parse_case(raw)
        issues = checked_issues(case, for_publish=True)
        return {"valid": not has_errors(issues), "issues": _issues(issues)}

    @router.put("/cases/{case_id}")
    def save(case_id: str, raw: dict[str, Any], teacher_id: str = Depends(current_teacher)) -> dict[str, Any]:
        store = get_store()
        if raw.get("case_id") != case_id:
            raise HTTPException(status_code=422, detail={"issues": [{"level": "error", "code": "case_id", "message": "路径与病例中的 case_id 不一致"}]})
        raw = dict(raw)
        raw.setdefault("public_id", store.next_public_id())
        raw.setdefault("version", 1)
        raw.setdefault("review_status", "draft")
        case = _parse_case(raw)
        issues = checked_issues(case)
        if has_errors(issues):
            raise HTTPException(status_code=422, detail={"issues": _issues(issues)})
        try:
            stored = store.save_draft(case, teacher_id)
        except CaseStoreError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {**_summary(stored), "issues": _issues(issues)}

    def _transition(case_id: str, version: int, target: ReviewStatus, **extra: Any) -> dict[str, Any]:
        store = get_store()
        try:
            current = store.get(case_id, version)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="病例版本不存在") from exc
        issues: list[Issue] = []
        if target in {ReviewStatus.VALIDATED, ReviewStatus.PUBLISHED}:
            issues = checked_issues(current, for_publish=True)
            if has_errors(issues):
                raise HTTPException(status_code=422, detail={"issues": _issues(issues)})
        try:
            updated = store.transition(case_id, version, target, **extra)
        except CaseStoreError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {**_summary(updated), "issues": _issues(issues)}

    @router.post("/cases/{case_id}/versions/{version}/validate")
    def mark_validated(case_id: str, version: int, _: str = Depends(current_teacher)) -> dict[str, Any]:
        return _transition(case_id, version, ReviewStatus.VALIDATED)

    @router.post("/cases/{case_id}/versions/{version}/submit-review")
    def submit_review(case_id: str, version: int, _: str = Depends(current_teacher)) -> dict[str, Any]:
        return _transition(case_id, version, ReviewStatus.UNDER_REVIEW)

    @router.post("/cases/{case_id}/versions/{version}/withdraw")
    def withdraw(case_id: str, version: int, _: str = Depends(current_teacher)) -> dict[str, Any]:
        return _transition(case_id, version, ReviewStatus.DRAFT)

    @router.post("/cases/{case_id}/versions/{version}/publish")
    def publish(case_id: str, version: int, request: PublishRequest, _: str = Depends(current_teacher)) -> dict[str, Any]:
        if not request.confirm_medical_review:
            raise HTTPException(status_code=422, detail="发布须由负责人确认已完成真实的医学审核")
        try:
            reviewed_on = date.fromisoformat(request.review_date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="review_date 须为 YYYY-MM-DD") from exc
        if reviewed_on > date.today():
            raise HTTPException(status_code=422, detail="审核日期不能晚于今天")
        return _transition(
            case_id, version, ReviewStatus.PUBLISHED, reviewer=request.reviewer, review_date=request.review_date
        )

    return router
