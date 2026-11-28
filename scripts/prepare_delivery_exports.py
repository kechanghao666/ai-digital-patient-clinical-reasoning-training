#!/usr/bin/env python3
"""Prepare public delivery exports from PROTECTED Nexent platform exports.

The official ``agent.json`` / ``feedback/agent.json`` must come from real platform exports — they
are never hand-written. This tool takes the two protected originals (kept outside the repository),
checks the Nexent export structure, applies the redactions IMPORT.md step 4 documents, writes the
public copies into the delivery directory and produces a sanitisation review artifact listing every
change and every remaining human-review flag. It re-uses the package verifier as the last gate.

Redaction deliberately distinguishes three things:
- credential VALUES (pattern-matched strings) are redacted everywhere, including inside prompts;
- secret-named KEYS (an exact name set: ``api_key``, ``password``, ``mcp_service_token``, ...)
  have their values redacted *outside* tool-parameter definitions;
- parameter NAMES and DEFINITIONS (``parameters``/``properties``/``required`` subtrees, including
  ``turn_token`` and ``max_tokens``) are preserved untouched — redacting them would break the
  importable tool wiring, so a secret-named parameter inside a definition is kept and only flagged
  for human review.

Writes are transactional: both sources are parsed and sanitised in memory, every destination is
exclusively claimed before anything is written, copies land via temp+rename, and any later failure
(verifier, review artifact) rolls the newly written files back, so a failed run never leaves a
half-generated package that a retry would refuse.

This is preparation, not provenance: the platform export itself, role bindings and the target-
instance import still require separate human review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from verify_delivery_package import CREDENTIAL, DELIVERABLES, verify  # noqa: E402

# Exact key names (case-insensitive) whose scalar values are credentials by name. Deliberately
# exact, not substring: ``max_tokens`` and ``turn_token`` are configuration/parameter names that
# must survive sanitisation.
SECRET_KEY_NAMES = frozenset({
    "api_key", "apikey", "secret", "secret_key", "password", "passwd", "cookie", "cookies",
    "authorization", "credentials", "private_key", "mcp_service_token", "access_token",
    "refresh_token", "session_token", "id_token", "auth_token", "client_secret",
})
# Subtrees that describe tool/parameter wiring; their keys and structure are import-critical.
SCHEMA_SUBTREE_KEYS = frozenset({"parameters", "properties", "required", "items", "schema"})
# Instance-specific resources flagged for the human mapping step (never auto-removed).
FLAG_KEYS = re.compile(r"tenant[_-]?id|knowledge[_-]?base|base[_-]?url|callback|webhook", re.I)
FLAG_URL = re.compile(r"https?://(?!nexent\.test)[^\s\"<>]+", re.I)


def _walk(value: Any, path: str, in_schema: bool, redactions: list[dict], flags: list[dict]) -> Any:
    """Redact in place; record (path, kind) only — never the secret itself."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            child_path = f"{path}.{key}"
            child_schema = in_schema or str(key) in SCHEMA_SUBTREE_KEYS
            if not child_schema and str(key).lower() in SECRET_KEY_NAMES:
                if item not in (None, "", False, True):
                    redactions.append({"path": child_path, "kind": "secret_key"})
                result[key] = "<redacted>"
                continue
            if child_schema and str(key).lower() in SECRET_KEY_NAMES:
                flags.append({"path": child_path, "kind": "secret_named_parameter_preserved"})
            if FLAG_KEYS.search(str(key)) and isinstance(item, str) and item.strip():
                flags.append({"path": child_path, "kind": "instance_specific_value"})
            result[key] = _walk(item, child_path, child_schema, redactions, flags)
        return result
    if isinstance(value, list):
        return [_walk(item, f"{path}[{index}]", in_schema, redactions, flags)
                for index, item in enumerate(value)]
    if isinstance(value, str):
        if CREDENTIAL.search(value):
            redactions.append({"path": path, "kind": "credential_pattern"})
            # Redact only the matched substrings so surrounding content (e.g. a prompt) survives.
            return CREDENTIAL.sub("<redacted>", value)
        if not in_schema and FLAG_URL.search(value):
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


def _provenance(source: Path, raw: bytes) -> dict:
    return {
        "source_file": source.name,
        "source_sha256_of_original_bytes": hashlib.sha256(raw).hexdigest(),
        "bom_present": raw.startswith(b"\xef\xbb\xbf"),
        "newline_style": "crlf" if b"\r\n" in raw else "lf",
    }


def _claim_and_write(destination: Path, text: str) -> None:
    """Exclusively claim the destination, then publish the content atomically.

    The O_CREAT|O_EXCL claim makes concurrent preparation of the same package refuse cleanly; a
    failure after the claim is rolled back by the caller via :func:`_discard_claim`.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(handle)
    descriptor, temp = tempfile.mkstemp(dir=destination.parent, prefix=f".{destination.name}.")
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    os.replace(temp, destination)


def _discard_claim(destination: Path) -> None:
    try:
        destination.unlink()
    except OSError:
        pass


def prepare(source: Path, destination: Path) -> dict:
    """Sanitise one protected export into a public copy; raises ValueError on any refusal."""
    raw = source.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"{source.name}: 不是有效的 JSON 导出") from exc
    summary = _structural_summary(data) if isinstance(data, dict) else None
    if summary is None:
        raise ValueError(f"{source.name}: 缺少 Nexent 导出结构（agent_id/agent_info/prompt）")

    redactions: list[dict] = []
    flags: list[dict] = []
    cleaned = _walk(data, "$", False, redactions, flags)
    text = json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n"
    leftover = CREDENTIAL.search(text)
    if leftover:
        raise ValueError(f"{source.name}: 脱敏后仍命中凭据模式，拒绝写出")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError(f"{destination} 已存在；公开副本只生成一次，复查走人工流程")
    _claim_and_write(destination, text)
    return {
        "source_file": source.name,
        **_provenance(source, raw),
        "destination": destination.relative_to(ROOT).as_posix()
        if destination.is_relative_to(ROOT) else str(destination),
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
    written: list[Path] = []
    try:
        records = []
        for source, destination in targets:
            records.append(prepare(source, destination))
            written.append(destination)
        package = verify(DELIVERABLES)
        if not package.get("offline_verified"):
            raise ValueError("包验证未通过；已回滚本次写入")
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
        text = json.dumps(review, ensure_ascii=False, indent=2) + "\n"
        if args.review_out:
            review_path = Path(args.review_out)
            if review_path.exists():
                raise ValueError(f"{review_path} 已存在；审查工件不覆盖")
            _claim_and_write(review_path, text)
            written.append(review_path)
        print(text)
        raise SystemExit(0)
    except ValueError as exc:
        for path in written:
            _discard_claim(path)
        print(json.dumps({"ok": False, "error": str(exc),
                          "rolled_back": [str(path) for path in written]}, ensure_ascii=False, indent=2))
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
