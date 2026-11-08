"""Executable case rules; synthetic fixtures are not medical approval."""
import pytest
from pydantic import ValidationError

from app.models import CaseDefinition
from test_cases_config import Api
from test_teacher_cases import TEACHER, new_case, put


@pytest.mark.parametrize("delayed", [False, True])
@pytest.mark.parametrize("selector,prior,cancelled", [
    ("id", None, False), ("id", "plan_treatment", False), ("id", "execute_treatment", True),
    ("trigger", "plan_treatment", False), ("trigger", "execute_treatment", True),
    ("plan_treatment", "plan_treatment", True), ("plan_treatment", "execute_treatment", False),
])
def test_exclusions_respect_execution_and_explicit_event_kind(tmp_path, monkeypatch, delayed, selector, prior, cancelled):
    api = Api(tmp_path, monkeypatch)
    raw = new_case()
    primary, earlier = raw['treatments'][:2]
    primary['trigger'], earlier['trigger'] = 'primary_executed', 'earlier_executed'
    rule = raw['rules'][0]
    rule['exclusions'] = [{'id': earlier['treatment_id'], 'trigger': earlier['trigger']}.get(selector, selector)]
    rule['delay_exclusion_treatments'] = []
    if delayed:
        rule['delay_minutes'] = 10
    else:
        rule.update(delay_anchor_exam=None, delay_minutes=0, anchor_event=primary['trigger'])
    response = put(api, raw)
    assert response.status_code == 200, response.text
    sid = api.start(response.json()['public_id'])
    if prior:
        api.act(sid, prior, {'treatment_id': earlier['treatment_id'], 'confirm_execution': prior == 'execute_treatment'})
    if delayed:
        exam = next(e for e in raw['exams'] if e['exam_id'] == rule['delay_anchor_exam'])
        api.act(sid, 'order_exam', {'exam_id': exam['exam_id']})
        api.wait(sid, exam['return_after_minutes'] + 10)
    else:
        api.act(sid, 'execute_treatment', {'treatment_id': primary['treatment_id'], 'confirm_execution': True})
    state = api.repository.get(sid, 'demo-cfg')
    assert state.clinical_state.value == ('initial' if cancelled else 'deteriorating')
    event = state.events[-1]
    assert bool(event.rule_id) == (not cancelled)
    replay = api.client.post(f'/api/sessions/{sid}/action', headers=api.headers,
                            json={'request_id': event.request_id, 'action': event.action.value, 'payload': event.payload})
    assert replay.status_code == 200
    assert api.repository.get(sid, 'demo-cfg').model_dump_json() == state.model_dump_json()
    api.seal_and_score(sid)
    api.act(sid, 'wait', {'minutes': 1}, expect=400)


@pytest.mark.parametrize('change', [
    lambda raw: raw['rules'][0].update(delay_anchor_exam=None, delay_minutes=0, delay_exclusion_treatments=[], anchor_event='unknown'),
    lambda raw: raw['rules'][0].update(delay_anchor_exam=None, delay_minutes=0, anchor_event='treatment_executed'),
    lambda raw: raw['rules'][0].update(exclusions=['unknown']),
    lambda raw: next(e for e in raw['exams'] if e['exam_id'] == raw['rules'][0]['delay_anchor_exam']).update(configured=False),
    lambda raw: raw['treatments'][0].update(trigger='  '),
    lambda raw: raw['treatments'][0].update(trigger='plan_treatment'),
    lambda raw: raw['rules'][0].update(rule_id=raw['treatments'][0]['treatment_id']),
    lambda raw: raw['treatments'][1].update(trigger=raw['treatments'][0]['treatment_id']),
])
def test_unreachable_or_ignored_rule_settings_fail_shared_boundary(tmp_path, monkeypatch, change):
    api = Api(tmp_path, monkeypatch)
    raw = new_case(public_id='CASE-T100')
    change(raw)
    with pytest.raises(ValidationError):
        CaseDefinition.model_validate(raw)
    assert put(api, raw).status_code == 422
    result = api.client.post('/api/teacher/cases/validate', headers=TEACHER, json=raw)
    assert result.status_code == 422
    assert not api.store.versions(raw['case_id'])
