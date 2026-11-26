import hashlib
import json
import runpy
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("root_id", [2, 3])
def test_verifier_checks_the_exported_root_agent(tmp_path, monkeypatch, root_id):
    script = Path(__file__).resolve().parents[1] / "scripts/verify_published_prompt.py"
    main = runpy.run_path(str(script))["main"]
    monkeypatch.setitem(main.__globals__, "CANDIDATE_SHA256", hashlib.sha256(b"candidate").hexdigest())
    export = tmp_path / "export.json"
    export.write_text(json.dumps({
        "agent_id": root_id,
        "agent_info": {
            "2": {"agent_id": 2, "name": "original", "prompt": "original"},
            "3": {"agent_id": 3, "name": "candidate", "prompt": "candidate"},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [str(script), str(export), "--name", "candidate"])
    if root_id == 3:
        assert main() == 0
    else:
        with pytest.raises(SystemExit, match="主智能体名称"):
            main()
