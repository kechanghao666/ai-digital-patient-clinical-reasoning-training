import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Callable, Iterable

from .legacy import apply_legacy_profile
from .models import CaseDefinition, ReviewStatus


def _legacy_classification(specialty: str) -> tuple[str | None, str | None]:
    from .department_catalog import map_legacy_specialty

    return map_legacy_specialty(specialty)


def _classification(case: CaseDefinition) -> tuple[str | None, str | None]:
    if case.department_code or case.subspecialty_code:
        return case.department_code, case.subspecialty_code
    return _legacy_classification(case.specialty)


CASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,39}$")


class CaseStoreError(ValueError):
    """Rejected case operation; messages are safe to show to teachers."""


def load_case_file(path: Path) -> CaseDefinition:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return CaseDefinition.model_validate(apply_legacy_profile(raw))


def _check_identifiers(case: CaseDefinition, existing: Iterable[CaseDefinition]) -> None:
    identifiers = {case.case_id, case.public_id}
    for other in existing:
        if other.case_id != case.case_id and identifiers.intersection((other.case_id, other.public_id)):
            raise CaseStoreError("病例内部编号或公开编号与其他病例冲突，请使用不同编号")


class CaseStore:
    """Read-mostly case library.

    Fixture cases live in ``cases_dir`` and are never written. Teacher cases live in ``teacher_dir``
    as one immutable-content file per version; only status, reviewer and review date change in place.
    """

    def __init__(self, cases_dir: str | Path, teacher_dir: str | Path | None = None):
        self.cases_dir = Path(cases_dir)
        self.teacher_dir = Path(teacher_dir) if teacher_dir else None
        self._cases: dict[tuple[str, int], CaseDefinition] = {}
        self._teacher_keys: set[tuple[str, int]] = set()
        self._lock = RLock()
        self.reload()

    def reload(self) -> None:
        with self._lock:
            cases: dict[tuple[str, int], CaseDefinition] = {}
            teacher_keys: set[tuple[str, int]] = set()
            for path in sorted(self.cases_dir.glob("*.json")):
                case = load_case_file(path)
                key = (case.case_id, case.version)
                if key in cases:
                    raise CaseStoreError(f"duplicate fixture case version: {key}")
                _check_identifiers(case, cases.values())
                cases[key] = case
            if self.teacher_dir and self.teacher_dir.exists():
                for path in sorted(self.teacher_dir.glob("*.json")):
                    case = load_case_file(path)
                    key = (case.case_id, case.version)
                    if key in cases:
                        raise CaseStoreError(f"duplicate teacher case version: {key}")
                    _check_identifiers(case, cases.values())
                    cases[key] = case
                    teacher_keys.add(key)
            self._cases = cases
            self._teacher_keys = teacher_keys

    # ------------------------------------------------------------------ lookup
    def get(self, case_id: str, version: int | None = None) -> CaseDefinition:
        candidates = [case for (cid, _), case in self._cases.items() if cid == case_id]
        if not candidates:
            raise KeyError(case_id)
        if version is None:
            version = max(case.version for case in candidates)
        return self._cases[(case_id, version)]

    def versions(self, case_id: str) -> list[CaseDefinition]:
        return sorted((c for (cid, _), c in self._cases.items() if cid == case_id), key=lambda c: c.version)

    def is_teacher_case(self, case_id: str, version: int) -> bool:
        return (case_id, version) in self._teacher_keys

    def get_by_public_id(self, public_id: str) -> CaseDefinition:
        matches = [case for case in self._cases.values() if case.public_id == public_id]
        if not matches:
            raise KeyError(public_id)
        return max(matches, key=lambda case: case.version)

    def resolve_for_student(self, identifier: str, include_unpublished: bool) -> CaseDefinition:
        """Pick the version a new student session should use: latest published, or latest draft in dev."""
        try:
            versions = self.versions(self.get(identifier).case_id)
        except KeyError:
            versions = self.versions(self.get_by_public_id(identifier).case_id)
        allowed = {ReviewStatus.PUBLISHED} | ({ReviewStatus.DRAFT} if include_unpublished else set())
        eligible = [c for c in versions if c.review_status in allowed]
        if not eligible:
            raise KeyError(identifier)
        return eligible[-1]

    def list_public(self, include_unpublished: bool = True,
                    available: Callable[[CaseDefinition], bool] | None = None) -> list[dict]:
        allowed = {ReviewStatus.PUBLISHED} | ({ReviewStatus.DRAFT} if include_unpublished else set())
        latest: dict[str, CaseDefinition] = {}
        for case in self._cases.values():
            if case.review_status in allowed and (
                case.case_id not in latest or case.version > latest[case.case_id].version
            ):
                latest[case.case_id] = case
        # Availability is checked after picking the newest allowed version. Filtering earlier
        # would silently fall back to an older published version when the newest one is stopped.
        return [
            {
                "public_id": case.public_id,
                "version": case.version,
                "title": case.title,
                "specialty": case.specialty,
                "department_code": _classification(case)[0],
                "subspecialty_code": _classification(case)[1],
                "chief_complaint": case.chief_complaint,
                "review_status": case.review_status.value,
            }
            for case in latest.values()
            if available is None or available(case)
        ]

    def list_all(self) -> list[dict]:
        return [
            {
                "case_id": case.case_id,
                "public_id": case.public_id,
                "version": case.version,
                "title": case.title,
                "specialty": case.specialty,
                "department_code": _classification(case)[0],
                "subspecialty_code": _classification(case)[1],
                "review_status": case.review_status.value,
                "source": "teacher" if (case.case_id, case.version) in self._teacher_keys else "fixture",
                "authored_by": case.authored_by,
                "reviewer": case.reviewer,
                "review_date": case.review_date,
                "source_case_id": case.source_case_id,
                "source_version": case.source_version,
            }
            for case in sorted(self._cases.values(), key=lambda c: (c.case_id, c.version))
        ]

    def check_source(self, case: CaseDefinition) -> None:
        """Validate a declared source and prevent lineage from changing across revisions."""
        with self._lock:
            existing = self.versions(case.case_id)
            requested = (case.source_case_id, case.source_version)
            if existing:
                recorded = (existing[-1].source_case_id, existing[-1].source_version)
                if requested != (None, None) and requested != recorded:
                    raise CaseStoreError("病例来源在创建后不可更改")
                return
            if requested == (None, None):
                return
            if requested[0] == case.case_id or (requested[0], requested[1]) not in self._cases:
                raise CaseStoreError("来源病例版本不存在或不能引用自身")

    def audit_events(self, case_id: str, version: int) -> list[dict]:
        if self.teacher_dir is None:
            return []
        path = self.teacher_dir / "case-audit.jsonl"
        with self._lock:
            if not path.exists():
                return []
            events = []
            try:
                with path.open("r", encoding="utf-8") as stream:
                    for line in stream:
                        if not line.strip():
                            continue
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise CaseStoreError("病例审计记录格式无效")
                        if event.get("case_id") == case_id and event.get("case_version") == version:
                            events.append(event)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CaseStoreError("病例审计记录不可读取") from exc
            return events

    # --------------------------------------------------------------- teacher writes
    def check_identifiers(self, case: CaseDefinition) -> None:
        with self._lock:
            _check_identifiers(case, self._cases.values())

    def _teacher_path(self, case_id: str, version: int) -> Path:
        if self.teacher_dir is None:
            raise CaseStoreError("teacher case storage is not configured")
        return self.teacher_dir / f"{case_id}__v{version}.json"

    def _write(self, case: CaseDefinition) -> None:
        path = self._teacher_path(case.case_id, case.version)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = case.model_dump_json(indent=2, exclude_none=False)
        handle, temp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
        self._cases[(case.case_id, case.version)] = case
        self._teacher_keys.add((case.case_id, case.version))

    def _append_audit(
        self,
        case: CaseDefinition,
        action: str,
        actor_id: str,
        status_from: ReviewStatus | None = None,
        reviewer: str | None = None,
        review_date: str | None = None,
    ) -> None:
        if self.teacher_dir is None:
            raise CaseStoreError("teacher case storage is not configured")
        event = {
            "case_id": case.case_id,
            "case_version": case.version,
            "public_id": case.public_id,
            "action": action,
            "actor_id": actor_id,
            "occurred_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "status_from": status_from.value if status_from else None,
            "status_to": case.review_status.value,
            "department_code": case.department_code,
            "subspecialty_code": case.subspecialty_code,
            "source_case_id": case.source_case_id,
            "source_version": case.source_version,
        }
        if reviewer is not None:
            event["reviewer_label"] = reviewer
        if review_date is not None:
            event["review_date"] = review_date
        path = self.teacher_dir / "case-audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _write_with_audit(
        self,
        case: CaseDefinition,
        action: str,
        actor_id: str,
        status_from: ReviewStatus | None = None,
        reviewer: str | None = None,
        review_date: str | None = None,
    ) -> None:
        key = (case.case_id, case.version)
        previous = self._cases.get(key)
        if self.teacher_dir is None:
            raise CaseStoreError("teacher case storage is not configured")
        audit_path = self.teacher_dir / "case-audit.jsonl"
        try:
            audit_existed = audit_path.exists()
            audit_size = audit_path.stat().st_size if audit_existed else 0
        except OSError as exc:
            raise CaseStoreError("无法读取病例审计记录状态，未保存病例变更") from exc
        self._write(case)
        try:
            self._append_audit(case, action, actor_id, status_from, reviewer, review_date)
        except (OSError, UnicodeError) as exc:
            rollback_errors = []
            try:
                if audit_existed:
                    with audit_path.open("r+b") as stream:
                        stream.truncate(audit_size)
                        stream.flush()
                        os.fsync(stream.fileno())
                else:
                    audit_path.unlink(missing_ok=True)
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
            try:
                if previous is None:
                    self._teacher_path(*key).unlink(missing_ok=True)
                    self._cases.pop(key, None)
                    self._teacher_keys.discard(key)
                else:
                    self._write(previous)
            except (OSError, UnicodeError) as rollback_error:
                rollback_errors.append(rollback_error)
            if rollback_errors:
                raise CaseStoreError("病例审计写入失败，且病例或审计记录回滚失败；请停止操作并恢复备份") from rollback_errors[0]
            raise CaseStoreError("病例审计写入失败，病例变更已回滚") from exc

    def next_public_id(self) -> str:
        used = {identifier for c in self._cases.values() for identifier in (c.case_id, c.public_id)}
        number = 100
        while f"CASE-T{number}" in used:
            number += 1
        return f"CASE-T{number}"

    def save_draft(self, case: CaseDefinition, teacher_id: str) -> CaseDefinition:
        """Store a teacher-authored case as a draft; never touches fixtures or non-draft versions."""
        if not CASE_ID_PATTERN.match(case.case_id):
            raise CaseStoreError("case_id must be 3-40 characters of letters, digits, '-' or '_'")
        with self._lock:
            existing = self.versions(case.case_id)
            if existing and not any((case.case_id, c.version) in self._teacher_keys for c in existing):
                raise CaseStoreError("this case_id belongs to a built-in fixture case; use a new case_id")
            self.check_identifiers(case)
            self.check_source(case)
            latest = existing[-1] if existing else None
            version = (latest.version + 1) if latest else 1
            source_case_id = latest.source_case_id if latest else case.source_case_id
            source_version = latest.source_version if latest else case.source_version
            stored = case.model_copy(
                update={
                    "version": version,
                    "review_status": ReviewStatus.DRAFT,
                    "reviewer": None,
                    "review_date": None,
                    "authored_by": teacher_id,
                    "source_case_id": source_case_id,
                    "source_version": source_version,
                }
            )
            CaseDefinition.model_validate(stored.model_dump())
            self._write_with_audit(stored, "draft_saved", teacher_id)
            return stored

    def transition(
        self,
        case_id: str,
        version: int,
        to_status: ReviewStatus,
        actor_id: str,
        reviewer: str | None = None,
        review_date: str | None = None,
    ) -> CaseDefinition:
        with self._lock:
            if not self.is_teacher_case(case_id, version):
                raise CaseStoreError("only teacher cases can change status")
            case = self.get(case_id, version)
            allowed = {
                ReviewStatus.DRAFT: {ReviewStatus.VALIDATED},
                ReviewStatus.VALIDATED: {ReviewStatus.UNDER_REVIEW, ReviewStatus.DRAFT},
                ReviewStatus.UNDER_REVIEW: {ReviewStatus.PUBLISHED, ReviewStatus.DRAFT},
                ReviewStatus.PUBLISHED: set(),
            }
            if to_status not in allowed[case.review_status]:
                raise CaseStoreError(f"cannot change status from {case.review_status.value} to {to_status.value}")
            update: dict = {"review_status": to_status}
            if to_status == ReviewStatus.PUBLISHED:
                if not reviewer or not reviewer.strip() or not review_date or not review_date.strip():
                    raise CaseStoreError("publishing requires a real reviewer name and review date")
                update.update(reviewer=reviewer.strip(), review_date=review_date.strip())
            if to_status == ReviewStatus.DRAFT:
                update.update(reviewer=None, review_date=None)
            updated = CaseDefinition.model_validate(case.model_copy(update=update).model_dump())
            action = {
                ReviewStatus.VALIDATED: "case_validated",
                ReviewStatus.UNDER_REVIEW: "case_submitted_for_review",
                ReviewStatus.PUBLISHED: "case_published",
                ReviewStatus.DRAFT: "case_withdrawn",
            }[to_status]
            self._write_with_audit(
                updated, action, actor_id, case.review_status,
                reviewer if to_status == ReviewStatus.PUBLISHED else None,
                review_date if to_status == ReviewStatus.PUBLISHED else None,
            )
            return updated
