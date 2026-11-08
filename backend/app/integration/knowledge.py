from __future__ import annotations

import json
from dataclasses import dataclass
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
        self.status: str = manifest["status"]
        self.audience: str = manifest["audience"]
        self.notice: str = manifest["notice"]
        self.entries: dict[str, BasisEntry] = {}
        for raw in manifest["entries"]:
            entry = BasisEntry(
                basis_id=raw["basis_id"],
                title=raw["title"],
                text=(base / raw["file"]).read_text(encoding="utf-8"),
                tags=tuple(raw["tags"]),
                stages=tuple(raw["stages"]),
                applies_to_cases=tuple(raw["applies_to_cases"]),
                sources=tuple(raw["sources"]),
                kind=raw.get("kind", "clinical"),
            )
            if entry.basis_id in self.entries:
                raise ValueError(f"duplicate basis_id {entry.basis_id}")
            if entry.kind not in {"clinical", "feedback_guidance"}:
                raise ValueError(f"unknown teaching-basis kind: {entry.kind}")
            if not entry.stages or set(entry.stages) - set(STAGES) or not entry.applies_to_cases:
                raise ValueError(f"invalid teaching-basis applicability: {entry.basis_id}")
            self.entries[entry.basis_id] = entry

    def for_case(self, case_id: str) -> list[BasisEntry]:
        return [e for e in self.entries.values() if "*" in e.applies_to_cases or case_id in e.applies_to_cases]

    def coverage(self, case_id: str) -> dict:
        entries = self.for_case(case_id)
        covered = {stage: [entry.basis_id for entry in entries if entry.kind == "clinical" and stage in entry.stages]
                   for stage in STAGES}
        return {"status": self.status, "clinical_by_stage": covered,
                "missing_clinical_stages": [stage for stage, ids in covered.items() if not ids],
                "guidance_ids": [entry.basis_id for entry in entries if entry.kind == "feedback_guidance"],
                "notice": "通用反馈规范不属于专科临床依据；存在条目不代表来源已核对或医学审核通过。"}

    def search(self, query: str, case_id: str, stage: str | None = None, limit: int = 3) -> list[BasisEntry]:
        wanted = _grams(query)
        scored: list[tuple[int, BasisEntry]] = []
        for entry in self.for_case(case_id):
            if stage and stage not in entry.stages:
                continue
            haystack = _grams(entry.title + " " + " ".join(entry.tags) + " " + entry.text)
            score = len(wanted & haystack) + 3 * sum(1 for tag in entry.tags if tag and tag in query)
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda pair: (-pair[0], pair[1].basis_id))
        return [entry for _, entry in scored[: max(1, min(limit, 5))]]

    def get(self, basis_id: str, case_id: str) -> BasisEntry | None:
        entry = self.entries.get(basis_id)
        if entry and ("*" in entry.applies_to_cases or case_id in entry.applies_to_cases):
            return entry
        return None
