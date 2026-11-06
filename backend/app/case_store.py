import json
from pathlib import Path

from .models import CaseDefinition, ReviewStatus


class CaseStore:
    def __init__(self, cases_dir: str | Path):
        self.cases_dir = Path(cases_dir)
        self._cases: dict[tuple[str, int], CaseDefinition] = {}
        self.reload()

    def reload(self) -> None:
        self._cases.clear()
        for path in sorted(self.cases_dir.glob("*.json")):
            case = CaseDefinition.model_validate_json(path.read_text(encoding="utf-8"))
            self._cases[(case.case_id, case.version)] = case

    def get(self, case_id: str, version: int | None = None) -> CaseDefinition:
        candidates = [case for (cid, _), case in self._cases.items() if cid == case_id]
        if not candidates:
            raise KeyError(case_id)
        if version is None:
            version = max(case.version for case in candidates)
        return self._cases[(case_id, version)]

    def get_by_public_id(self, public_id: str) -> CaseDefinition:
        for case in self._cases.values():
            if case.public_id == public_id:
                return case
        raise KeyError(public_id)

    def list_public(self) -> list[dict]:
        return [
            {
                "public_id": case.public_id,
                "version": case.version,
                "title": case.title,
                "specialty": case.specialty,
                "chief_complaint": case.chief_complaint,
                "review_status": case.review_status.value,
            }
            for case in self._cases.values()
            if case.review_status in {ReviewStatus.DRAFT, ReviewStatus.PUBLISHED}
        ]
