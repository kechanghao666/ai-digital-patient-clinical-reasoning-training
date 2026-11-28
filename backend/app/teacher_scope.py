"""Fail-closed teacher access through the existing class membership relationship."""
from __future__ import annotations

import logging
from typing import Callable

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def teacher_member_ids(get_roster: Callable | None, teacher_id: str,
                       class_id: str | None = None) -> list[str]:
    try:
        if get_roster is None:
            raise RuntimeError("roster is not configured")
        roster = get_roster()
        if class_id is not None:
            try:
                roster.owned_class(class_id, teacher_id)
            except KeyError as exc:
                raise HTTPException(404, "班级不存在或不属于当前教师") from exc
            return list(dict.fromkeys(roster.member_ids(class_id)))
        return list(dict.fromkeys(member for cls in roster.classes(teacher_id)
                                  for member in roster.member_ids(cls["id"])))
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("teacher roster lookup failed: %s", type(exc).__name__)
        raise HTTPException(503, "班级授权暂不可用") from exc


def require_teacher_student(get_roster: Callable | None, teacher_id: str, owner_id: str) -> None:
    try:
        if get_roster is None:
            raise RuntimeError("roster is not configured")
        allowed = get_roster().teaches(teacher_id, owner_id)
    except Exception as exc:
        logger.warning("teacher membership lookup failed: %s", type(exc).__name__)
        raise HTTPException(503, "班级授权暂不可用") from exc
    if not allowed:
        raise HTTPException(404, "训练不存在或不属于当前教师的学生")
