"""Minimal offline reference for the Nexent patient <code> action protocol.

The real three-turn run (docs/evidence/2026-11-27-o10a-real-three-turns.json) recorded, on the
Nexent platform, a step-2 ``malformed_action`` that succeeded after two retries in the hypertension
turn and failed after three consecutive retries (loop error -> HTTP 502) in the diabetes turn. The
raw model text for those steps was NOT exported to this machine, so the exact rejected output is
unknown. This module is a *reference* classifier for the documented <code> format, not the platform
parser: it lets a captured raw output be classified once the platform log is exported, and fixes the
acceptance conditions in one place.

The patient prompt (docs/patient-selection-prompt.txt) requires one action per response:
    <code>
    result = submit_fact_selection(turn_token="TOKEN", fact_ids=[...])
    print(result)
    </code>
and forbids Markdown fences, JSON/function-call style, natural-language substitutions and mixed
tool + <final_answer> in the same response.
"""
from __future__ import annotations

import re

# A complete <code> block. The prompt says the action is one submit_fact_selection call.
_CODE = re.compile(r"<code>(.*?)</code>", re.DOTALL)


def classify(raw: str) -> tuple[str, str]:
    """Classify one captured patient-agent response.

    Returns (verdict, reason) where verdict is "valid" or "malformed". This is a reference for the
    documented protocol only; it does not run the platform's action parser.
    """
    text = (raw or "").strip()
    blocks = _CODE.findall(text)
    if "```" in text and not blocks:
        return "malformed", "Markdown 围栏代替 <code>"
    if "<final_answer>" in text or "</final_answer>" in text:
        return "malformed", "同响应混排工具动作与 <final_answer>"
    if len(blocks) != 1:
        if blocks:
            return "malformed", "一个响应包含多个 <code> 块"
        if re.search(r'"(?:tool|function|arguments)"', text) or text.startswith("{"):
            return "malformed", "JSON/函数调用样式代替代码动作"
        return "malformed", "没有完整的 <code> 动作块"
    body = blocks[0].strip()
    if "submit_fact_selection" not in body:
        return "malformed", "动作未调用 submit_fact_selection"
    if not re.search(r"submit_fact_selection\s*\(", body):
        return "malformed", "submit_fact_selection 调用语法不完整"
    if "turn_token" not in body:
        return "malformed", "动作缺少 turn_token 参数"
    if "fact_ids" not in body:
        return "malformed", "动作缺少 fact_ids 参数"
    return "valid", "单个完整 submit_fact_selection 动作"


def test_reference_classifies_the_valid_action():
    valid = '<code>\nresult = submit_fact_selection(turn_token="T", fact_ids=["f_hx_htn"])\nprint(result)\n</code>'
    verdict, _reason = classify(valid)
    assert verdict == "valid"


def test_reference_rejects_markdown_fence():
    raw = '```python\nresult = submit_fact_selection(turn_token="T", fact_ids=[])\nprint(result)\n```'
    verdict, reason = classify(raw)
    assert verdict == "malformed" and "围栏" in reason


def test_reference_rejects_natural_language():
    raw = "我现在提交事实选择。"
    verdict, reason = classify(raw)
    assert verdict == "malformed" and "代码" not in reason.split("代替")[0]


def test_reference_rejects_json_function_call():
    raw = '{"tool": "submit_fact_selection", "arguments": {"turn_token": "T", "fact_ids": []}}'
    verdict, reason = classify(raw)
    assert verdict == "malformed" and "JSON" in reason


def test_reference_rejects_mixed_action_and_final_answer():
    raw = '<code>\nsubmit_fact_selection(turn_token="T", fact_ids=[])\n</code>\n<final_answer>已提交</final_answer>'
    verdict, reason = classify(raw)
    assert verdict == "malformed" and "混排" in reason


def test_reference_rejects_incomplete_call():
    raw = '<code>\nsubmit_fact_selection(turn_token="T"\n</code>'
    verdict, reason = classify(raw)
    assert verdict == "malformed" and "fact_ids" in reason
