#!/usr/bin/env python3
"""Check public delivery files offline. The report goes to stdout, outside the package."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DELIVERABLES = ROOT / 'deliverables/AgentsHub/行业智能体/医疗/AI数字病人临床思维训练'
REQUIRED = ('README.md', 'IMPORT.md', 'CHECKLIST.md', 'mcps/mcp-config.example.json')
EXPORTS = ('agent.json', 'feedback/agent.json')
CREDENTIAL = re.compile(
    r'sk-[a-z0-9_-]{20,}|Bearer\s+[a-z0-9._~+/=-]{16,}|https?://[^/\s"<>]+:[^/\s"<>]+@|'
    r'''(?:api[_-]?key|mcp_service_token|access_token|password)["']?\s*[:=]\s*["']?[a-z0-9_./+=-]{8,}''', re.I)


def verify(directory: Path) -> dict:
    problems = []
    exports = {}
    for name in REQUIRED:
        if not (directory / name).is_file():
            problems.append(f'missing: {name}')
    for path in sorted(directory.rglob('*')):
        name = path.relative_to(directory).as_posix()
        if path.is_symlink() or (not path.is_dir() and path.suffix.lower() not in {'.json', '.md'}):
            problems.append(f'unreviewed file type or link: {name}')
            continue
        if path.is_dir():
            continue
        try:
            text = path.read_text(encoding='utf-8-sig')
            if CREDENTIAL.search(text):
                problems.append(f'possible credential: {name}')
            if path.suffix.lower() != '.json':
                continue
            data = json.loads(text)
            if name == 'mcps/mcp-config.example.json':
                tools = data.get('tools') if isinstance(data, dict) else None
                expected = {'get_turn_context', 'submit_fact_selection', 'get_sealed_review',
                            'search_teaching_basis', 'submit_feedback'}
                if (not isinstance(tools, list) or len(tools) != len(expected) or
                        {item.get('name') for item in tools if isinstance(item, dict) and isinstance(item.get('name'), str)} != expected or
                        data.get('transport') != 'streamable-http' or
                        data.get('url') != 'http://<mcp-host>:8765/mcp' or
                        data.get('headers') != {'Authorization': 'Bearer <MCP_SERVICE_TOKEN>'}):
                    problems.append(f'invalid MCP registration example: {name}')
            if name in EXPORTS:
                info = data.get('agent_info') if isinstance(data, dict) else None
                root_id = data.get('agent_id') if isinstance(data, dict) else None
                agent = info.get(str(root_id)) if isinstance(info, dict) else None
                valid = (root_id is not None and isinstance(agent, dict) and
                         str(agent.get('agent_id')) == str(root_id) and
                         isinstance(agent.get('prompt'), str) and bool(agent['prompt'].strip()))
                exports[name] = 'structure_checked' if valid else 'invalid'
                if not valid:
                    problems.append(f'invalid Nexent agent structure: {name}')
        except (ValueError, OSError):
            problems.append(f'unreadable or invalid content: {name}')
    for name in EXPORTS:
        exports.setdefault(name, 'invalid' if (directory / name).exists() else 'missing')
    return {'problems': problems, 'exports': exports,
            'offline_verified': not problems and all(v == 'structure_checked' for v in exports.values()),
            'scope': 'File checks only. Export provenance, role bindings, disclosure and target import require separate review.'}


if __name__ == '__main__':
    result = verify(DELIVERABLES)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    raise SystemExit(0 if result['offline_verified'] else 2)
