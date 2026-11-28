"""Negative controls for partial fact scoring in the offline O3 evaluator."""
from pathlib import Path

from app.adapters import FALLBACK_ANSWER
from app.case_store import CaseStore
from app.patient_answers import fact_fragments, render_fragments
from scripts.run_o3_spotcheck import grade_disclosure


def test_partial_fact_negative_controls():
    case = CaseStore(Path(__file__).resolve().parents[1] / 'cases').get('APPY-001', 2)
    first, sibling, *_ = fact_fragments(next(f for f in case.facts if f.fact_id == 'f_hx'))
    other = next(a for f in case.facts for a in fact_fragments(f) if a.fact_id != 'f_hx')
    base = dict(required_fact_ids=['f_hx'], allowed_fact_ids=['f_hx'], prohibited_fact_ids=[other.fact_id])

    def flags(ids, answer, **overrides):
        row = dict(actual_fact_ids=[], actual_answer_atom_ids=ids, answer=answer, flags=[])
        grade_disclosure(row, base | overrides, case)
        return set(row['flags'])

    text = render_fragments([first])
    assert not flags([first.atom_id], text)
    assert 'over_answer' in flags([first.atom_id], text + '额外内容')
    assert 'over_answer' in flags([sibling.atom_id], render_fragments([sibling]), required_answer=text)
    assert {'over_answer', 'unauthorized_disclosure'} <= flags([other.atom_id], render_fragments([other]))
    assert 'interface_failure' in flags(['f_hx#invalid'], text)
    assert {'omission', 'inappropriate_refusal'} <= flags([], FALLBACK_ANSWER)
    assert not flags([], FALLBACK_ANSWER, acceptable_no_fact_answers=[FALLBACK_ANSWER])
