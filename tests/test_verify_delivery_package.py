import json
from shutil import copyfile

import pytest

from scripts.verify_delivery_package import DELIVERABLES, EXPORTS, REQUIRED, verify


@pytest.fixture
def package(tmp_path):
    for name in REQUIRED:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        copyfile(DELIVERABLES / name, path)
    for name in EXPORTS:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'agent_id': 1, 'agent_info': {'1': {'agent_id': 1, 'prompt': 'fixture'}}}), encoding='utf-8')
    return tmp_path


@pytest.mark.parametrize('body', [None, '{', 'null', '[]', '{}', '{"agent_id":1,"agent_info":{"2":{}}}'])
def test_each_export_must_be_readable_and_identify_its_root(package, body):
    assert verify(package)['offline_verified']
    path = package / EXPORTS[1]
    if body is None:
        path.unlink()
    else:
        path.write_text(body, encoding='utf-8')
    assert not verify(package)['offline_verified']


def test_nested_findings_never_echo_values_or_write_reports(package):
    marker = 'synthetic-test-value-000000'
    path = package / 'mcps/config.json'
    path.write_text(json.dumps({'Authorization': 'Bearer ' + marker}), encoding='utf-8')
    result = verify(package)
    assert not result['offline_verified'] and 'mcps/config.json' in str(result['problems'])
    assert marker not in str(result) and not (package / 'verify-report.json').exists()


def test_mcp_example_cannot_be_empty(package):
    (package / 'mcps/mcp-config.example.json').write_text('{}', encoding='utf-8')
    assert not verify(package)['offline_verified']
