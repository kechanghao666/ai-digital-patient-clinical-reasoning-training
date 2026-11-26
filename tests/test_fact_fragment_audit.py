"""Audit snapshot for multi-fragment case facts.

Read-only engineering bookkeeping, not clinical validation. The counts are the 2026-11-26
inventory (11 versions / 5 cases / 144 facts / 121 multi-fragment / 323 fragments). If case
content changes, update the audit document and this snapshot together instead of silently
re-baselining. Historical frozen question sets and failure reports are not touched here.
"""
from __future__ import annotations

from pathlib import Path

from app.case_store import CaseStore
from app.patient_answers import fact_fragments

ROOT = Path(__file__).parents[1]


def test_fragment_inventory_scope_matches_the_audit():
    store = CaseStore(ROOT / "cases")
    cases = [store.get(row["case_id"], row["version"]) for row in store.list_all()]
    fragments = {
        (case.case_id, case.version, fact.fact_id): fact_fragments(fact)
        for case in cases
        for fact in case.facts
    }
    assert len(cases) == 11
    assert len({case.case_id for case in cases}) == 5
    assert len(fragments) == 144
    assert sum(1 for value in fragments.values() if len(value) > 1) == 121
    assert sum(len(value) for value in fragments.values()) == 323


def test_allergy_compound_facts_keep_their_two_topic_atoms():
    store = CaseStore(ROOT / "cases")
    for case_id, version in (("STEMI-001", 3), ("DKA-001", 2)):
        case = store.get(case_id, version)
        fact = next(item for item in case.facts if item.fact_id == "f_allergy")
        assert [fragment.content for fragment in fact_fragments(fact)] == [
            "没有药物过敏", "没做过手术",
        ]
