import json
import runpy
from pathlib import Path


def test_replay_requires_explicit_replay_flag_even_for_identical_answer_and_event():
    root = Path(__file__).parents[1]
    replay_passes = runpy.run_path(str(root / "scripts/audit_compound_scores.py"))["replay_passes"]
    archive = root / "evaluation/reports/o3_compound_post_d05450d.json"
    row = json.loads(archive.read_bytes())["runs"][0]
    assert replay_passes(row)
    row["replay_after_seal"]["replayed_flag"] = False
    assert not replay_passes(row)
