import json
import os
import re
import tempfile
from pathlib import Path
from threading import RLock

from .legacy import apply_legacy_profile
from .models import CaseDefinition, ReviewStatus

CASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,39}$")


class CaseStoreError(ValueError):
    """Rejected case operation; messages are safe to show to teachers."""


def load_case_file(path: Path) -> CaseDefinition:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return CaseDefinition.model_validate(apply_legacy_profile(raw))


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
            self._cases.clear()
            self._teacher_keys.clear()
            for path in sorted(self.cases_dir.glob("*.json")):
                case = load_case_file(path)
                self._cases[(case.case_id, case.version)] = case
            if self.teacher_dir and self.teacher_dir.exists():
                for path in sorted(self.teacher_dir.glob("*.json")):
                    case = load_case_file(path)
                    key = (case.case_id, case.version)
                    if key in self._cases:
                        raise CaseStoreError(f"teacher case {key} collides with a fixture case")
                    self._cases[key] = case
                    self._teacher_keys.add(key)

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

    def list_public(self, include_unpublished: bool = True) -> list[dict]:
        allowed = {ReviewStatus.PUBLISHED} | ({ReviewStatus.DRAFT} if include_unpublished else set())
        latest: dict[str, CaseDefinition] = {}
        for case in self._cases.values():
            if case.review_status in allowed and (
                case.case_id not in latest or case.version > latest[case.case_id].version
            ):
                latest[case.case_id] = case
        return [
            {
                "public_id": case.public_id,
                "version": case.version,
                "title": case.title,
                "specialty": case.specialty,
                "chief_complaint": case.chief_complaint,
                "review_status": case.review_status.value,
            }
            for case in latest.values()
        ]

    def list_all(self) -> list[dict]:
        return [
            {
                "case_id": case.case_id,
                "public_id": case.public_id,
                "version": case.version,
                "title": case.title,
                "specialty": case.specialty,
                "review_status": case.review_status.value,
                "source": "teacher" if (case.case_id, case.version) in self._teacher_keys else "fixture",
                "authored_by": case.authored_by,
                "reviewer": case.reviewer,
                "review_date": case.review_date,
            }
            for case in sorted(self._cases.values(), key=lambda c: (c.case_id, c.version))
        ]

    # --------------------------------------------------------------- teacher writes
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

    def next_public_id(self) -> str:
        used = {c.public_id for c in self._cases.values()}
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
            for other in self._cases.values():
                if other.public_id == case.public_id and other.case_id != case.case_id:
                    raise CaseStoreError("public_id is already used by another case")
            latest = existing[-1] if existing else None
            version = (latest.version + 1) if latest else 1
            stored = case.model_copy(
                update={
                    "version": version,
                    "review_status": ReviewStatus.DRAFT,
                    "reviewer": None,
                    "review_date": None,
                    "authored_by": teacher_id,
                }
            )
            CaseDefinition.model_validate(stored.model_dump())
            self._write(stored)
            return stored

    def transition(
        self,
        case_id: str,
        version: int,
        to_status: ReviewStatus,
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
            self._write(updated)
            return updated
