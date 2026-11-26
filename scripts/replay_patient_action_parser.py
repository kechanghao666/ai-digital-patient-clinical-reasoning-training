"""Read the archived structural examples on stdin inside the measured Nexent runtime."""
import hashlib
import json
import sys
from pathlib import Path

from nexent.core.agents import output_protocol


def classify(text):
    try:
        result = output_protocol.classify_model_output(text, protocol="code_action", finish_reason="stop")
    except output_protocol.ModelOutputProtocolError as exc:
        return exc.reason.value
    return type(result).__name__


archive = json.load(sys.stdin)
parser_hash = hashlib.sha256(Path(output_protocol.__file__).read_bytes()).hexdigest()
if parser_hash != archive["parser_sha256"]:
    raise SystemExit("The installed parser differs from the measured version")
for sample in archive["samples"]:
    action = sample["action_example"]
    text = "\n".join([action, *sample["following_tags"]])
    if classify(text) != sample["expected"] or classify(action) != "ExecutableAction":
        raise SystemExit(f"Unexpected verdict: {sample['turn']} step {sample['step']} attempt {sample['attempt']}")
print(f"Matched {len(archive['samples'])} structural examples and their action-only controls")
