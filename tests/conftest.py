import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))


def pytest_configure(config):
    config._patient_data = tempfile.TemporaryDirectory(prefix="patient-tests-")
    config._patient_env = pytest.MonkeyPatch()
    config._patient_env.setenv("APP_DATA_DIR", config._patient_data.name)
    config._patient_env.setenv("INTEGRATION_DB_PATH", str(Path(config._patient_data.name) / "integration.db"))


def pytest_unconfigure(config):
    config._patient_env.undo()
    config._patient_data.cleanup()
