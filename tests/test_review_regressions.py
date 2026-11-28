from test_cases_config import Api, item
from test_teacher_cases import new_case, put


def test_mcp_reads_teacher_case_created_after_service_start(tmp_path, monkeypatch):
    from dataclasses import replace
    import shutil
    from app import mcp_server
    from app.case_store import CaseStore
    from app.integration.config import IntegrationSettings, ROOT
    from app.models import CaseDefinition, SessionState

    (tmp_path / 'cases').mkdir()
    shutil.copy2(ROOT / 'cases/appendicitis_case_v1.json', tmp_path / 'cases')
    monkeypatch.setattr(mcp_server, 'ROOT', tmp_path)
    monkeypatch.setenv('APP_DATA_DIR', str(tmp_path / 'backend/data'))
    settings = replace(IntegrationSettings.from_env(), integration_db_path=tmp_path / 'integration.db')
    service = mcp_server.build_service(settings)
    store = CaseStore(tmp_path / 'cases', tmp_path / 'backend/data/teacher_cases')
    case = store.save_draft(CaseDefinition.model_validate(new_case(public_id='CASE-T100')), 'teacher-test')
    state = SessionState(owner_id='demo-test', case_id=case.case_id, case_version=case.version)
    service.repository.create(state)
    _, token = service.turns.issue(kind='patient', owner_id=state.owner_id,
        session_id=state.session_id, case_id=case.case_id, case_version=case.version,
        request_id='r1', state_version=0, candidate_fact_ids=[case.facts[0].fact_id])
    assert service.get_turn_context(token)['ok']


def test_mixed_event_does_not_turn_opposing_fact_into_support(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    sid = api.start('CASE-004')
    event = api.ask(sid, '发热多少度，有没有气短和呼吸困难')
    case = api.store.get_by_public_id('CASE-004')
    fever = next(f.content for f in case.facts if f.fact_id == 'f_fever_detail')
    api.differential(sid, [{'candidate_id': 'pulmonary_embolism',
        'supporting_evidence': [fever], 'opposing_evidence': [fever],
        'evidence_event_ids': [event]}])
    result = item(api.seal_and_score(sid), 'pulmonary_embolism')
    assert not result['hit']
    assert result['score'] < result['max_score']


def test_editing_draft_preserves_existing_training(tmp_path, monkeypatch):
    api = Api(tmp_path, monkeypatch)
    first = put(api, new_case()).json()
    sid = api.start(first['public_id'])
    revised = new_case(public_id=first['public_id'], title='修订标题')
    assert put(api, revised).json()['version'] == 2
    detail = api.client.get(f'/api/sessions/{sid}', headers=api.headers).json()
    assert detail['case']['title'] == '教师新增的腹痛模拟病例'
