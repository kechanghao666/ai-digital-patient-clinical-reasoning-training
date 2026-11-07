"""Frozen configuration for case versions that predate the data-driven case schema.

STEMI-001 v1 is pinned by stored sessions, so its JSON file must not change. The behaviour that used
to be hard-coded for it (accepted treatments, diagnosis phrasing, delay-rule anchors) lives here and is
merged into the raw case before validation. New cases and new versions carry this configuration in
their own JSON and never use this module.
"""
from __future__ import annotations

import copy
from typing import Any

_STEMI_NAMES = ["stemi", "急性st段抬高型心肌梗死"]
_STEMI_EXTRA_POSITIVE = [
    "{name}已确诊，无药物过敏史",
    "{name}，已排除主动脉夹层",
    "{name}，考虑急诊pci",
    "{name}已确诊，非nstemi",
] + [
    f"{{name}}已确诊{joiner}{already}排除{what}"
    for joiner in ("并", "，")
    for already in ("已", "")
    for what in ("主动脉夹层", "nstemi")
]

LEGACY_PROFILES: dict[tuple[str, int], dict[str, Any]] = {
    ("STEMI-001", 1): {
        "treatments": [
            {"treatment_id": "aspirin", "name": "阿司匹林"},
            {"treatment_id": "reperfusion_pathway", "name": "再灌注路径", "trigger": "reperfusion_started"},
        ],
        "differential_options": [
            {"candidate_id": "aortic_dissection", "label": "主动脉夹层"},
            {"candidate_id": "pulmonary_embolism", "label": "肺栓塞"},
        ],
        "rule_patches": {
            "r_stemi_delay_fixture": {
                "delay_anchor_exam": "ex_ecg",
                "delay_exclusion_treatments": ["reperfusion_pathway"],
            }
        },
        "rubric_patches": {
            "stemi": {
                "diagnosis": {
                    "names": _STEMI_NAMES,
                    "extra_positive": _STEMI_EXTRA_POSITIVE,
                    "leak_terms": ["stemi", "心肌梗死"],
                }
            }
        },
    }
}


def apply_legacy_profile(raw: dict[str, Any]) -> dict[str, Any]:
    """Return ``raw`` with the frozen profile merged in, unless the case already carries its own config."""
    profile = LEGACY_PROFILES.get((raw.get("case_id"), raw.get("version")))
    if profile is None or "treatments" in raw:
        return raw
    merged = copy.deepcopy(raw)
    merged["treatments"] = copy.deepcopy(profile["treatments"])
    merged["differential_options"] = copy.deepcopy(profile["differential_options"])
    for rule in merged.get("rules", []):
        rule.update(copy.deepcopy(profile["rule_patches"].get(rule.get("rule_id"), {})))
    for item in merged.get("rubric", []):
        item.update(copy.deepcopy(profile["rubric_patches"].get(item.get("item_id"), {})))
    return merged
