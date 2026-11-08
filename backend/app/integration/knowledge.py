from __future__ import annotations

import json
import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path

STAGES = ("病史采集", "检查选择", "鉴别诊断", "诊断", "治疗")

@dataclass(frozen=True)
class BasisEntry:
    basis_id: str
    title: str
    text: str
    tags: tuple[str, ...]
    stages: tuple[str, ...]
    applies_to_cases: tuple[str, ...]
    sources: tuple[str, ...]
    kind: str = "clinical"
    revision: int = 1
    applies_to_versions: dict[str, tuple[int, ...]] = field(default_factory=dict)

    @classmethod
    def from_record(cls, raw: dict) -> BasisEntry:
        for key in ("basis_id", "title", "text", "kind"):
            if not isinstance(raw.get(key), str) or not raw[key].strip():
                raise ValueError(f"invalid teaching-basis {key}")
        revision = raw.get("revision")
        if type(revision) is not int or revision < 1:
            raise ValueError("teaching-basis revision must be a positive integer")
        for key in ("tags", "stages", "applies_to_cases", "sources"):
            values = raw.get(key)
            if not isinstance(values, list) or (key != "tags" and not values):
                raise ValueError(f"invalid teaching-basis {key}")
            if not all(isinstance(value, str) and value.strip() for value in values):
                raise ValueError(f"invalid teaching-basis {key}")
        versions = raw.get("applies_to_versions", {})
        if not isinstance(versions, dict):
            raise ValueError("invalid teaching-basis version applicability")
        for case_id, numbers in versions.items():
            if case_id not in raw["applies_to_cases"] or case_id == "*" or not isinstance(numbers, list) or not numbers:
                raise ValueError("invalid teaching-basis version applicability")
            if any(type(number) is not int or number < 1 for number in numbers) or len(set(numbers)) != len(numbers):
                raise ValueError("invalid teaching-basis case versions")
        if raw["kind"] not in {"clinical", "feedback_guidance"} or set(raw["stages"]) - set(STAGES):
            raise ValueError("invalid teaching-basis kind or stages")
        if raw["kind"] == "clinical" and ("*" in raw["applies_to_cases"] or set(versions) != set(raw["applies_to_cases"])):
            raise ValueError("clinical teaching basis requires explicit case versions")
        return cls(**{key: raw[key] for key in ("basis_id", "title", "text", "kind", "revision")},
                   **{key: tuple(raw[key]) for key in ("tags", "stages", "applies_to_cases", "sources")},
                   applies_to_versions={key: tuple(numbers) for key, numbers in versions.items()})

    def applies_to(self, case_id: str, case_version: int) -> bool:
        if case_id not in self.applies_to_cases and "*" not in self.applies_to_cases:
            return False
        return case_id not in self.applies_to_versions or case_version in self.applies_to_versions[case_id]

    @property
    def content_hash(self) -> str:
        canonical = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def record(self) -> dict:
        return {**json.loads(json.dumps(asdict(self), ensure_ascii=False)), "content_hash": self.content_hash}


def _grams(text: str) -> set[str]:
    cleaned = "".join(text.lower().split())
    if len(cleaned) < 2:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


class KnowledgeStore:
    """Read-only teaching-basis library used only by the post-seal feedback chain."""

    def __init__(self, directory: str | Path):
        base = Path(directory) / "teaching_basis"
        manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
        self.version = manifest["version"]
        if type(self.version) is not int or self.version < 1:
            raise ValueError("knowledge version must be a positive integer")
        self.status: str = manifest["status"]
        self.audience: str = manifest["audience"]
        self.notice: str = manifest["notice"]
        self.entries: dict[str, BasisEntry] = {}
        for raw in manifest["entries"]:
            entry = BasisEntry.from_record({**raw, "kind": raw.get("kind", "clinical"),
                                           "text": (base / raw["file"]).read_text(encoding="utf-8")})
            if entry.basis_id in self.entries:
                raise ValueError(f"duplicate basis_id {entry.basis_id}")
            self.entries[entry.basis_id] = entry

    def for_case(self, case_id: str, case_version: int) -> list[BasisEntry]:
        return [e for e in self.entries.values() if e.applies_to(case_id, case_version)]

    def coverage(self, case_id: str, case_version: int) -> dict:
        entries = self.for_case(case_id, case_version)
        covered = {stage: [entry.basis_id for entry in entries if entry.kind == "clinical" and stage in entry.stages]
                   for stage in STAGES}
        return {"status": self.status, "clinical_by_stage": covered,
                "missing_clinical_stages": [stage for stage, ids in covered.items() if not ids],
                "guidance_ids": [entry.basis_id for entry in entries if entry.kind == "feedback_guidance"],
                "notice": "通用反馈规范不属于专科临床依据；存在条目不代表来源已核对或医学审核通过。"}

    def search(self, query: str, case_id: str, stage: str | None = None, limit: int = 3, *, case_version: int) -> list[BasisEntry]:
        wanted = _grams(query)
        scored: list[tuple[int, BasisEntry]] = []
        for entry in self.for_case(case_id, case_version):
            if stage and stage not in entry.stages:
                continue
            haystack = _grams(entry.title + " " + " ".join(entry.tags) + " " + entry.text)
            score = len(wanted & haystack) + 3 * sum(1 for tag in entry.tags if tag and tag in query)
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda pair: (-pair[0], pair[1].basis_id))
        return [entry for _, entry in scored[: max(1, min(limit, 5))]]

    def get(self, basis_id: str, case_id: str, case_version: int) -> BasisEntry | None:
        entry = self.entries.get(basis_id)
        if entry and entry.applies_to(case_id, case_version):
            return entry
        return None

    def snapshot(self, items: list[dict], case_id: str, case_version: int) -> dict:
        ids = sorted({basis_id for item in items for basis_id in item["basis_ids"]})
        entries = [self.get(basis_id, case_id, case_version) for basis_id in ids]
        if not ids or any(entry is None for entry in entries):
            raise ValueError("teaching basis is not applicable to the case version")
        return {"schema_version": 1, "library_version": self.version, "status": self.status,
                "case_id": case_id, "case_version": case_version,
                "entries": [entry.record() for entry in entries]}

    @staticmethod
    def saved_entries(snapshot: dict, items: list[dict], case_id: str, case_version: int) -> dict[str, BasisEntry]:
        if (not isinstance(snapshot, dict) or type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != 1
                or type(snapshot.get("case_version")) is not int
                or snapshot.get("case_id") != case_id or snapshot.get("case_version") != case_version
                or type(snapshot.get("library_version")) is not int or snapshot["library_version"] < 1
                or not isinstance(snapshot.get("status"), str) or not isinstance(snapshot.get("entries"), list)):
            raise ValueError("invalid teaching-basis snapshot")
        entries = {}
        for raw in snapshot["entries"]:
            if not isinstance(raw, dict):
                raise ValueError("invalid teaching-basis snapshot entry")
            entry = BasisEntry.from_record(raw)
            if entry.content_hash != raw.get("content_hash") or entry.basis_id in entries or not entry.applies_to(case_id, case_version):
                raise ValueError("teaching-basis snapshot integrity or applicability mismatch")
            entries[entry.basis_id] = entry
        ids = {basis_id for item in items for basis_id in item["basis_ids"]}
        if ids != set(entries) or any(item["stage"] not in entries[basis_id].stages for item in items for basis_id in item["basis_ids"]):
            raise ValueError("teaching-basis snapshot does not match feedback")
        return entries
