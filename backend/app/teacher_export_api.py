"""Teacher de-identified export: scope-verified, whitelist-only, audited.

A teacher exports teaching data for their own classes only. The server re-verifies the whole scope
from the roster (never trusting a frontend selector), builds a whitelist-only de-identified record
set, and appends an audit entry for success / rejection / failure. It never modifies trainings
or scores; the restricted audit is stored alongside accounts for backup consistency.
"""
from __future__ import annotations

import logging
import json
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .export import EXPORT_NOTICE, build_deidentified_records

logger = logging.getLogger(__name__)


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    class_id: str | None = Field(default=None, min_length=1, max_length=128)


def _authorized_member_ids(roster, teacher_id: str, class_id: str | None) -> tuple[set[str], dict]:
    """Return (member_ids, scope) after verifying ``class_id`` belongs to the teacher.

    ``class_id=None`` means every class the teacher owns. Raises KeyError when the requested class
    is not owned, and lets other errors propagate so the caller reports failure (not "no data").
    """
    if class_id is not None:
        roster.owned_class(class_id, teacher_id)  # raises KeyError if not owned
        return set(roster.member_ids(class_id)), {"class_id": class_id}
    members: set[str] = set()
    class_ids: list[str] = []
    for cls in roster.classes(teacher_id):
        class_ids.append(cls["id"])
        members.update(roster.member_ids(cls["id"]))
    return members, {"class_ids": sorted(class_ids)}


def build_router(get_repository: Callable, get_cases: Callable, get_roster: Callable | None,
                 current_teacher: Callable, get_audit: Callable | None):
    router = APIRouter(prefix="/api/teacher")

    def record_audit(audit, teacher_id, scope, count, result, error=""):
        try:
            return audit.append(teacher_id, scope, count, result, error)
        except Exception as exc:
            logger.warning("export audit unavailable: %s", type(exc).__name__)
            raise HTTPException(503, "导出审计暂不可用，未提供导出文件") from exc

    @router.post("/export")
    def export(body: ExportRequest, teacher_id: str = Depends(current_teacher)) -> dict:
        if get_roster is None or get_audit is None:
            raise HTTPException(404, "导出功能未启用")
        audit = get_audit()
        try:
            roster = get_roster()
            member_ids, scope = _authorized_member_ids(roster, teacher_id, body.class_id)
        except KeyError:
            record_audit(audit, teacher_id, {"requested_class": True}, 0, "rejected", "class_not_owned")
            raise HTTPException(404, "班级不存在或不属于当前教师")
        except Exception as exc:
            logger.warning("export scope lookup failed for teacher=%s: %s", teacher_id, type(exc).__name__)
            record_audit(audit, teacher_id, {}, 0, "failed", "scope_error")
            raise HTTPException(503, "导出暂不可用") from exc
        try:
            states = [s for s in get_repository().list_all() if s.owner_id in member_ids]
            records = build_deidentified_records(states, get_cases().get)
            json.dumps(records, ensure_ascii=False, allow_nan=False)
        except Exception as exc:
            logger.warning("export build failed for teacher=%s: %s", teacher_id, type(exc).__name__)
            record_audit(audit, teacher_id, scope, 0, "failed", "build_error")
            raise HTTPException(503, "导出暂不可用") from exc
        audit_event = record_audit(audit, teacher_id, scope, len(records), "success")
        return {
            "ok": True,
            "scope": "当前班级" if body.class_id is not None else "全部自有班级",
            "record_count": len(records),
            "records": records,
            "notice": EXPORT_NOTICE,
            "audit": {"occurred_at": audit_event["occurred_at"], "result": audit_event["result"]},
        }

    return router
