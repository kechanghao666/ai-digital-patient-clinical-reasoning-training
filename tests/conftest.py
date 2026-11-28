import json
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
    # Explicit synthetic institution configuration for tests; production has no implicit defaults.
    department_config = {
        "schema_version": 1,
        "enabled_departments": ["03", "04"],
        "enabled_subspecialties": ["03.01", "03.02", "03.03", "03.04", "03.05", "03.07", "04.01"],
        "updated_by": "pytest-fixture-not-a-real-administrator",
        "updated_at": "test-fixture",
    }
    (Path(config._patient_data.name) / "department-catalog.json").write_text(
        json.dumps(department_config), encoding="utf-8"
    )


def pytest_unconfigure(config):
    config._patient_env.undo()
    config._patient_data.cleanup()
