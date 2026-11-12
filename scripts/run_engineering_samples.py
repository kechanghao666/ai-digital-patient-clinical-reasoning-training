"""Execute the existing twelve engineering scenarios against temporary data, with full request/response evidence."""
from pathlib import Path
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import traceback

import pytest

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / 'local-only/engineering-samples' / datetime.now().strftime('%Y%m%d-%H%M%S')
OUT.mkdir(parents=True, exist_ok=False)
bootstrap = tempfile.TemporaryDirectory(prefix='patient-samples-')
os.environ['APP_DATA_DIR'] = bootstrap.name
os.environ['INTEGRATION_DB_PATH'] = str(Path(bootstrap.name) / 'integration.db')
os.environ['NEXENT_MODE'] = 'simulator'
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'tests')]
from app import main
from app.integration.config import IntegrationSettings
from app.integration.runtime import IntegrationRuntime
from test_cases_config import Api, item
from test_five_specialties import SPECIALTY_RUNS, ideal_run

assert Path(main.REPOSITORY.database_path).is_relative_to(Path(bootstrap.name))
try:
    SHA = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, stderr=subprocess.DEVNULL).decode().strip()
except subprocess.CalledProcessError:
    SHA = 'source-archive-without-git'
SOURCE_HASHES = {str(p.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
                 for folder in ['backend/app', 'cases', 'knowledge', 'tests', 'scripts']
                 for p in (ROOT / folder).rglob('*') if p.is_file() and p.suffix in {'.py', '.json', '.md'}}
original_db = ROOT / 'backend/data/app.db'
db_hash = hashlib.sha256(original_db.read_bytes()).hexdigest() if original_db.exists() else None


def check(report, name, expected, actual):
    passed = expected == actual
    report['checks'].append(dict(name=name, expected=expected, actual=actual, passed=passed))
    assert passed, name


class SampleApi(Api):
    def __init__(self, directory, monkey, report):
        super().__init__(directory, monkey)
        self.report = report
        self.sid = None
        settings = replace(IntegrationSettings.from_env(), mode='simulator',
                           integration_db_path=directory / 'integration.db', knowledge_dir=ROOT / 'knowledge')
        monkey.setattr(main, 'RUNTIME', IntegrationRuntime(settings))
        request = self.client.request

        def record(method, url, **kwargs):
            expected = kwargs.pop('audit_expected_status', 200)
            begin = time.perf_counter()
            response = request(method, url, **kwargs)
            try:
                body = response.json()
            except ValueError:
                body = response.text
            self.report['requests'].append(dict(method=method, url=str(url),
                input=kwargs.get('json'), expected_status=expected, status=response.status_code,
                elapsed_ms=round((time.perf_counter() - begin) * 1000, 3), output=body))
            assert response.status_code == expected, (method, url, response.status_code, body)
            return response

        self.client.request = record

    def start(self, public_id):
        self.sid = super().start(public_id)
        return self.sid

    def notes(self, sid):
        state = self.state(sid)
        if state['case_notes']:
            return
        observed = [f"问：{r['input']['question']}\n答：{r['output']['answer']}"
                    for r in self.report['requests']
                    if r['url'].endswith('/ask') and r['status'] == 200]
        exams = [f"{o['exam_name']}：{json.dumps(o['result'], ensure_ascii=False)}"
                 for o in state['orders'] if o['result'] is not None]
        notes = ('工程自测病历，非真实患者，未经医学审核。\n病史记录：\n' +
                 ('\n'.join(observed) or '本场景尚未完成问诊，不补造病史。') +
                 '\n已回报检查：\n' + ('\n'.join(exams) or '暂无已回报检查。') +
                 '\n模拟时间：' + str(state['sim_time_minutes']) + '分钟。')
        self.act(sid, 'update_notes', {'notes': notes})
        check(self.report, '病历保存并重新读取一致', notes, self.state(sid)['case_notes'])

    def differential(self, sid, candidates):
        self.notes(sid)
        return super().differential(sid, candidates)

    def seal_and_score(self, sid):
        self.notes(sid)
        return super().seal_and_score(sid)

    def finish(self, sid, score):
        feedback = self.client.post(f'/api/sessions/{sid}/feedback', headers=self.headers,
                                    json={'request_id': self.rid()}).json()
        saved = self.client.get(f'/api/sessions/{sid}/feedback', headers=self.headers).json()
        check(self.report, '反馈生成后可重新读取', feedback['items'], saved['items'])
        check(self.report, '反馈为本地模拟规则而非真实模型', 'local_rules', feedback['source'])
        check(self.report, '五环节反馈完整', 5, len(feedback['items']))
        replay = self.state(sid)
        check(self.report, '封存后仍可读取回放事件', True, replay['sealed'] and bool(replay['events']))
        sequences = [e['seq'] for e in replay['events']]
        check(self.report, '回放事件按序排列', sequences, sorted(set(sequences)))
        self.report['result'] = dict(score=score, feedback=feedback,
                                     replay_event_count=len(replay['events']), state=replay)


reports = []
for number in range(1, 13):
    public_id = f'CASE-{number:03}' if number <= 5 else (f'CASE-{number-5:03}' if number <= 10 else ('CASE-001' if number == 11 else 'CASE-002'))
    group = 'normal' if number <= 5 else ('delay' if number <= 10 else 'boundary')
    report = dict(sample_id=f'SIM-{number:02}', kind='engineering_only', public_id=public_id,
                  scenario=group, code_sha=SHA, platform='FastAPI TestClient / Nexent simulator',
                  model='deterministic local simulator; no LLM', python=platform.python_version(),
                  created_at=datetime.now(timezone.utc).isoformat(), requests=[], checks=[],
                  expected_source='docs/acceptance-plan.md 十二组样本执行清单 and tests/test_five_specialties.py SPECIALTY_RUNS',
                  complete=False, engineering_pass=False, formal_acceptance=False)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="patient-sample-") as tmp, pytest.MonkeyPatch.context() as monkey:
        api = SampleApi(Path(tmp), monkey, report)
        case = api.store.resolve_for_student(public_id, include_unpublished=True)
        case_json = case.model_dump(mode='json')
        report['versions'] = dict(case_id=case.case_id, case_version=case.version,
            case_hash=hashlib.sha256(json.dumps(case_json, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
            case_review=case.review_status.value, rubric=[r.model_dump(mode='json') for r in case.rubric],
            knowledge_manifest_sha256=hashlib.sha256((ROOT / 'knowledge/teaching_basis/manifest.json').read_bytes()).hexdigest(),
            knowledge_status=main.RUNTIME.knowledge.status,
            basis_hashes={k: v.content_hash for k, v in main.RUNTIME.knowledge.entries.items()})
        report['clinical_limitations'] = ['病例、量表与依据未经医学审核；不是正式学生或真实平台样本。']
        coverage = main.RUNTIME.knowledge.coverage(case.case_id, case.version)
        if coverage['missing_clinical_stages']:
            report['clinical_limitations'].append('缺适用专科临床依据：' + '、'.join(coverage['missing_clinical_stages']))
        if public_id == 'CASE-003':
            report['clinical_limitations'].append('v1主诉1小时与问诊1.5小时、09:30起病时轴未统一。')
        if public_id == 'CASE-005':
            report['clinical_limitations'].append('合并补液/胰岛素动作未充分判断安全证据与适用性。')
        try:
            script = SPECIALTY_RUNS[public_id]
            if number <= 5:
                sid, score = ideal_run(api, public_id, script)
                check(report, '现有量表各项均命中', True, all(i['hit'] for i in score['items']))
                check(report, '现有量表总分匹配', score['max_score'], score['total_score'])
                check(report, '五环节评分存在', 5, len({i['stage'] for i in score['items']}))
            elif number <= 10:
                sid = api.start(public_id)
                exam_id, first_wait, delay, treatment = script['delay']
                api.act(sid, 'order_exam', {'exam_id': exam_id})
                api.wait(sid, first_wait)
                check(report, '检查回报后尚未超过延误阈值', 'initial', api.state(sid)['clinical_state'])
                changed = api.act(sid, 'wait', {'minutes': delay})
                check(report, '现有回报后延误规则触发', 'deteriorating', changed['state']['clinical_state'])
                check(report, '演进说明向学生可见', True, bool(changed['response']['clinical_updates']))
                score = api.seal_and_score(sid)
                check(report, '遗漏处置项零分', 0, item(score, treatment)['score'])
                check(report, '遗漏处置有改进建议', True, bool(item(score, treatment)['improvement']))
                report['clinical_limitations'].append('仅覆盖现有回报后延误工程阈值，不能据此声明错误分支100%或临床可安全等待时间。')
            elif number == 11:
                sid = api.start(public_id)
                body = {'request_id': 'same-request', 'question': '疼痛是什么性质'}
                first = api.client.post(f'/api/sessions/{sid}/ask', headers=api.headers, json=body).json()
                before = api.repository.get(sid, 'demo-cfg').model_dump(mode='json')
                replayed = api.client.post(f'/api/sessions/{sid}/ask', headers=api.headers, json=body).json()
                check(report, '相同请求返回原事件', first['event']['event_id'], replayed['event']['event_id'])
                check(report, '重复请求不改会话', before, api.repository.get(sid, 'demo-cfg').model_dump(mode='json'))
                api.client.request('POST', f'/api/sessions/{sid}/ask', headers=api.headers,
                    json={**body, 'question': '换一个问题'}, audit_expected_status=409)
                check(report, '同ID改内容被拒且原会话不变', before, api.repository.get(sid, 'demo-cfg').model_dump(mode='json'))
                score = api.seal_and_score(sid)
            else:
                sid = api.start(public_id)
                api.notes(sid)
                before_seal = api.repository.get(sid, 'demo-cfg').model_dump(mode='json')
                score = api.seal_and_score(sid)
                frozen = api.repository.get(sid, 'demo-cfg').model_dump(mode='json')
                check(report, '封存保留已保存病历', before_seal['case_notes'], frozen['case_notes'])
                for action, payload in [('wait', {'minutes': 1}), ('order_exam', {'exam_id': 'ex_us'}),
                                        ('execute_treatment', {'treatment_id': 'surgical_consult', 'confirm_execution': True})]:
                    api.client.request('POST', f'/api/sessions/{sid}/action', headers=api.headers,
                        json={'request_id': api.rid(), 'action': action, 'payload': payload}, audit_expected_status=400)
                    check(report, '封存拒绝'+action+'且原状态不变', frozen, api.repository.get(sid, 'demo-cfg').model_dump(mode='json'))
                report['seal_before'] = before_seal
            api.finish(sid, score)
            report['complete'] = True
            report['engineering_pass'] = all(c['passed'] for c in report['checks'])
        except Exception:
            report['execution_error'] = traceback.format_exc()
        finally:
            api.client.close()
    report['elapsed_s'] = round(time.perf_counter() - started, 3)
    (OUT / f"{report['sample_id']}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    reports.append(report)
    print(report['sample_id'], report['complete'], report['engineering_pass'], report.get('execution_error', '')[-350:])

summary = dict(code_sha=SHA, samples=len(reports), complete=sum(r['complete'] for r in reports),
               engineering_pass=sum(r['engineering_pass'] for r in reports), formal_acceptance=False,
               rows=[dict(sample_id=r['sample_id'], case=r['public_id'], scenario=r['scenario'],
                          complete=r['complete'], engineering_pass=r['engineering_pass'],
                          requests=len(r['requests']), checks=len(r['checks']),
                          limitations=r['clinical_limitations']) for r in reports])
summary['original_database_unchanged'] = (hashlib.sha256(original_db.read_bytes()).hexdigest() if original_db.exists() else None) == db_hash
assert summary['original_database_unchanged']
summary['source_sha256'] = SOURCE_HASHES
(OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in summary.items() if k not in {'rows', 'source_sha256'}}, ensure_ascii=False))
bootstrap.cleanup()
assert summary['complete'] == summary['engineering_pass'] == 12