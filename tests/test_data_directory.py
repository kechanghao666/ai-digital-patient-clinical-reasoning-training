"""The API and trusted tools must share the configured, isolated persistent data."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).parents[1]


def test_api_and_mcp_share_data_directory_across_working_directories(tmp_path):
    target = tmp_path / "isolated-data"
    env = {**os.environ, "APP_ENV": "development", "APP_DATA_DIR": str(target),
           "NEXENT_MODE": "simulator", "PYTHONPATH": str(ROOT / "backend")}
    env.pop("INTEGRATION_DB_PATH", None)
    script = '''
from pathlib import Path
import sys
from fastapi.testclient import TestClient
from app import main
from app.integration.config import IntegrationSettings
from app.mcp_server import build_service
target = Path(sys.argv[1]).resolve()
assert Path(main.REPOSITORY.database_path) == target / "app.db"
assert main.CASE_STORE.teacher_dir == target / "teacher_cases"
settings = IntegrationSettings.from_env()
assert settings.integration_db_path == target / "integration.db"
service = build_service(settings)
client = TestClient(main.app)
headers = {"X-Owner-Id": "demo-isolated"}
session = client.post("/api/sessions?case_id=CASE-001", headers=headers)
assert session.status_code == 200
sid = session.json()["session_id"]
assert service.repository.get(sid, "demo-isolated").session_id == sid
assert service.cases.teacher_dir == target / "teacher_cases"
'''
    result = subprocess.run([sys.executable, "-c", script, str(target)], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert (target / "app.db").exists()


def test_relative_data_directory_resolves_from_project_root(tmp_path, monkeypatch):
    from app.integration import config

    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setenv("APP_DATA_DIR", "local-only/rehearsal")
    monkeypatch.delenv("INTEGRATION_DB_PATH", raising=False)
    monkeypatch.chdir(tmp_path.parent)
    assert config.data_directory() == tmp_path / "local-only/rehearsal"
    assert config.IntegrationSettings.from_env().integration_db_path == tmp_path / "local-only/rehearsal/integration.db"


def test_explicit_integration_path_remains_independent(tmp_path, monkeypatch):
    from app.integration.config import IntegrationSettings

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "business"))
    monkeypatch.setenv("INTEGRATION_DB_PATH", str(tmp_path / "audit" / "turns.db"))
    assert IntegrationSettings.from_env().integration_db_path == tmp_path / "audit" / "turns.db"
