from ..models import CaseDefinition, ReviewStatus
from .context import require_context


def require_teacher() -> None:
    context = require_context()
    if "teacher" not in context.allowed_actions:
        raise PermissionError("teacher permission required")


def validate_case(case: CaseDefinition) -> dict:
    require_teacher()
    return {"valid": True, "case_id": case.case_id, "version": case.version}


def publish_case(case: CaseDefinition) -> CaseDefinition:
    require_teacher()
    if not case.reviewer or not case.review_date:
        raise ValueError("published case requires real reviewer and review date")
    return case.model_copy(update={"review_status": ReviewStatus.PUBLISHED})
