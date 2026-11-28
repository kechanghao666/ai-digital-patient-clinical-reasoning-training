"""Compare five normal simulator paths across three run_engineering_samples.py output directories."""
import argparse
import hashlib
import json
from pathlib import Path


def fingerprint(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(data.encode()).hexdigest()


def observe(path):
    raw = path.read_bytes()
    sample = json.loads(raw)
    result = sample['result']
    state = result['state']
    if not (sample['complete'] and sample['engineering_pass'] and state['sealed']
            and result['feedback']['source'] == 'local_rules' and sample['scenario'] == 'normal'):
        raise ValueError(f'incomplete simulator run: {path.name}')
    sid = state['session_id']
    seq = {event['event_id']: event['seq'] for event in state['events']}
    inputs = []
    for request in sample['requests']:
        if request['method'] == 'POST':
            body = dict(request['input'] or {})
            body.pop('request_id', None)
            for candidate in body.get('candidates', []):
                candidate['evidence_event_ids'] = [seq[key] for key in candidate['evidence_event_ids']]
            inputs.append([request['url'].replace(sid, '{session}'), body])
    outcomes = {
        'answers': [e['result']['answer'] for e in state['events'] if e['action'] == 'ask'],
        'orders': [[o['exam_name'], o['result']] for o in state['orders']],
        'clinical_state': state['clinical_state'],
        'grading': [[i['stage'], i['item_id'], i['hit'], i['score'], i['max_score']] for i in result['score']['items']],
        'feedback': [[i['stage'], i['comment'], i['improvement'], i['basis_ids'],
                      [seq[key] for key in i['evidence_event_ids']]] for i in result['feedback']['items']],
    }
    return {'session_id': sid, 'sample_sha256': hashlib.sha256(raw).hexdigest(),
            'code_sha': sample['code_sha'], 'public_id': sample['public_id'],
            'case_version': sample['versions']['case_version'], 'case_sha256': sample['versions']['case_hash'],
            'input_sha256': fingerprint(inputs), 'output_sha256': fingerprint(outcomes)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directories', type=Path, nargs=3)
    args = parser.parse_args()
    cases = []
    for n in range(1, 6):
        runs = [observe(directory / f'SIM-{n:02}.json') for directory in args.directories]
        keys = ('code_sha', 'case_sha256', 'input_sha256', 'output_sha256')
        consistent = (len({r['session_id'] for r in runs}) == 3 and
                      all(r['public_id'] == f'CASE-{n:03}' for r in runs) and
                      all(len({r[k] for r in runs}) == 1 for k in keys))
        cases.append({'consistent': consistent, 'runs': runs})
    print(json.dumps({'mode': 'simulator', 'formal_acceptance': False, 'cases': cases}, indent=2))
    return 0 if all(case['consistent'] for case in cases) else 1


if __name__ == '__main__':
    raise SystemExit(main())
