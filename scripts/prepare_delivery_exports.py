#!/usr/bin/env python3
"""Prepare public delivery exports from PROTECTED Nexent platform exports.

The official ``agent.json`` / ``feedback/agent.json`` must come from real platform exports — they
are never hand-written. This tool takes the two protected originals (kept outside the repository),
checks the Nexent export structure, applies the redactions IMPORT.md step 4 documents, writes the
public copies into the delivery directory and produces a sanitisation review artifact listing every
change and every remaining human-review flag (instance-specific URLs, tenant ids, knowledge-base
ids). It re-uses the package verifier as the last gate and never writes anything when a credential
pattern survives redaction.

This is preparation, not provenance: the platform export itself, role bindings and the target-
instance import still require separate human review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from verify_delivery_package import CREDENTIAL, DELIVERABLES, verify  # noqa: E402

# Keys whose values are secrets or instance credentials by name, regardless of content.
SECRET_KEYS = re.compile(
    r"api[_-]?key|apikey|token|secret|password|passwd|cookie|authorization|credential", re.I)
# Instance-specific resources flagged for the human mapping step (never auto-removed).
FLAG_KEYS = re.compile(r"tenant[_-]?id|knowledge[_-]?base|base[_-]?url|callback|webhook", re.I)
FLAG_URL = re.compile(r"https?://(?!nexent\.test)[^\s\"<>]+", re.I)


def _walk(value: Any, path: str, redactions: list[dict], flags: list[dict]) -> Any:
    """Redact secret values in place; record (path, kind) only — never the secret itself."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if SECRET_KEYS.search(str(key)):
                if item not in (None, "", False, True):
                    redactions.append({"path": child_path, "kind": "secret_key"})
                result[key] = "<redacted>"
                continue
            result[key] = _walk(item, child_path, redactions, flags)
            if FLAG_KEYS.search(str(key)) and isinstance(item, str) and item.strip():
                flags.append({"path": child_path, "kind": "instance_specific_value"})
        return result
    if isinstance(value, list):
        return [_walk(item, f"{path}[{index}]", redactions, flags) for index, item in enumerate(value)]
    if isinstance(value, str):
        if CREDENTIAL.search(value):
            redactions.append({"path": path, "kind": "credential_pattern"})
            # Redact only the matched substrings so surrounding content (e.g. a prompt) survives.
            return CREDENTIAL.sub("<redacted>", value)
        if FLAG_URL.search(value):
            flags.append({"path": path, "kind": "internal_url"})
        return value
    return value


def _structural_summary(data: dict) -> dict | None:
    root_id = data.get("agent_id")
    agent = data.get("agent_info", {}).get(str(root_id)) if isinstance(data, dict) else None
    if not isinstance(agent, dict) or str(agent.get("agent_id")) != str(root_id) \
            or not str(agent.get("prompt") or "").strip():
        return None
    return {"agent_id": root_id, "name": agent.get("name"), "display_name": agent.get("display_name"),
            "max_steps": agent.get("max_steps"), "prompt_sha256": hashlib.sha256(
                str(agent.get("prompt")).encode("utf-8")).hexdigest()}


def prepare(source: Path, destination: Path) -> dict:
    """Sanitise one protected export into a public copy; raises ValueError on any refusal."""
    raw = source.read_text(encoding="utf-8-sig")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"{source.name}: 不是有效的 JSON 导出") from exc
    summary = _structural_summary(data) if isinstance(data, dict) else None
    if summary is None:
        raise ValueError(f"{source.name}: 缺少 Nexent 导出结构（agent_id/agent_info/prompt）")

    redactions: list[dict] = []
    flags: list[dict] = []
    cleaned = _walk(data, "$", redactions, flags)
    text = json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n"
    leftover = CREDENTIAL.search(text)
    if leftover:
        raise ValueError(f"{source.name}: 脱敏后仍命中凭据模式，拒绝写出")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError(f"{destination} 已存在；公开副本只生成一次，复查走人工流程")
    destination.write_text(text, encoding="utf-8", newline="\n")
    try:
        destination_note = destination.relative_to(ROOT).as_posix()
    except ValueError:
        destination_note = str(destination)
    return {
        "source_file": source.name,
        "source_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "destination": destination_note,
        "structure": summary,
        "redactions": redactions,
        "human_review_flags": flags,
        "note": "本副本为脱敏生成件；平台导出原件保留在受保护目录，导出真实性、角色绑定与目标实例导入仍须人工审查。",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="从受保护平台导出生成脱敏公开副本（不代写导出）")
    parser.add_argument("--patient", required=True, help="受保护的病人智能体平台导出 JSON 路径")
    parser.add_argument("--feedback", required=True, help="受保护的反馈智能体平台导出 JSON 路径")
    parser.add_argument("--review-out", default=None, help="脱敏审查工件输出路径（JSON；不填则仅打印）")
    args = parser.parse_args()

    targets = [(Path(args.patient), DELIVERABLES / "agent.json"),
               (Path(args.feedback), DELIVERABLES / "feedback" / "agent.json")]
    records = []
    try:
        for source, destination in targets:
            records.append(prepare(source, destination))
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(2) from exc

    package = verify(DELIVERABLES)
    review = {
        "ok": True,
        "exports": records,
        "package_verification": package,
        "pending_human_review": [
            "平台导出真实性与版本授权",
            "工具/知识库/allow_chat_metadata 角色绑定逐项核对",
            "human_review_flags 中实例专属资源的映射",
            "目标实例导入与五病例闭环",
        ],
    }
    text = json.dumps(review, ensure_ascii=False, indent=2)
    if args.review_out:
        review_path = Path(args.review_out)
        if review_path.exists():
            print(json.dumps({"ok": False, "error": f"{review_path} 已存在；审查工件不覆盖"}, ensure_ascii=False))
            raise SystemExit(2)
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    raise SystemExit(0 if package.get("offline_verified") else 2)


if __name__ == "__main__":
    main()
