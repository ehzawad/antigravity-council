#!/usr/bin/env python3
"""Offline release structure checks. Does not replace `claude plugin validate`."""
from __future__ import annotations
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/antigravity-council'
sys.dont_write_bytecode = True
sys.path.insert(0, str(PLUGIN / 'scripts'))
import council_common as common
import council_policy as policy


def verify() -> dict:
    compiled = []
    for path in ROOT.rglob('*.py'):
        compile(path.read_text(encoding='utf-8'), str(path), 'exec')
        compiled.append(str(path.relative_to(ROOT)))
    manifest = common.strict_json((PLUGIN / '.claude-plugin/plugin.json').read_text())
    marketplace = common.strict_json((ROOT / '.claude-plugin/marketplace.json').read_text())
    assert manifest['name'] == 'antigravity-council'
    assert manifest['version'] == common.VERSION
    assert marketplace['name'] == manifest['name']
    assert marketplace['plugins'][0]['source'] == './plugins/antigravity-council'
    assert len(marketplace['plugins']) == 1
    for field in ('name', 'version', 'description', 'license'):
        assert isinstance(manifest[field], str) and manifest[field]
    skills = list((PLUGIN / 'skills').glob('*/SKILL.md'))
    assert len(skills) == 1
    skill = skills[0].read_text()
    assert skill.startswith('---\nname: antigravity-council\n')
    assert '${CLAUDE_PLUGIN_ROOT}/scripts/antigravity_council.py' in skill
    assert '--skill-contract 1' in skill
    assert 'allowed-tools:\n  - Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/antigravity_council.py" *)' in skill
    assert not (PLUGIN / '.claude-plugin/skills').exists()
    assert marketplace['metadata']['version'] == common.VERSION
    for key in ('homepage', 'repository'):
        assert manifest[key] == 'https://github.com/ehzawad/antigravity-council', key
    workflows = sorted((PLUGIN / 'workflows').glob('*.js'))
    assert [w.name for w in workflows] == ['council.js'], workflows
    flow = workflows[0].read_text()
    # Claude Code requires a pure-literal meta as the first statement.
    assert flow.startswith("export const meta = {\n  name: 'council',\n"), 'workflow meta must lead the file'
    assert 'import(' not in flow and 'Date.now(' not in flow and 'Math.random(' not in flow
    assert "access !== 'read'" in flow, 'workflow must refuse non-read roles'
    # Model-agnostic: catalogs change monthly, so shipped files name no vendor models.
    named = re.compile(r'gemini-\d|claude-(?:opus|sonnet|haiku)|\bargon\b|\bopus\b', re.I)
    shipped = [path for path in PLUGIN.rglob('*') if path.is_file() and path.suffix in {'.py', '.md', '.json', '.js'}]
    offenders = [str(path.relative_to(ROOT)) for path in shipped if named.search(path.read_text())]
    assert not offenders, 'hardcoded model names in shipped plugin files: ' + ', '.join(offenders)
    expected = {policy.profile_name(a) + '.md': policy.profile_text(a) for a in policy.TOOLS}
    actual = {p.name: p.read_text() for p in (PLUGIN / 'worker-profiles').glob('*.md')}
    assert actual == expected, 'worker profile mirrors are stale'
    for name in ('LICENSE', 'NOTICE', 'README.md', 'SECURITY.md', 'DESIGN.md', 'docs/SOURCES.md'):
        assert (ROOT / name).is_file(), name
    for path in (ROOT / 'examples').glob('*roles.json'):
        common.parse_roles(common.read_json(path))
    common.parse_roles(common.read_json(ROOT / 'examples/writer-role.json'))
    common.intent_capsule((ROOT / 'examples/context.md').read_text())
    broken = []
    for path in ROOT.rglob('*.md'):
        for dest in re.findall(r'\]\(([^)]+)\)', path.read_text()):
            if '://' in dest or dest.startswith(('#', 'mailto:')):
                continue
            target = dest.split('#')[0]
            if target and not (path.parent / target).exists():
                broken.append(f'{path.relative_to(ROOT)} -> {dest}')
    assert not broken, 'broken local document links: ' + ', '.join(broken)
    return {'ok': True, 'version': common.VERSION, 'python_files_compiled': len(compiled),
            'capability_profiles': len(expected), 'marketplace_entries': 1, 'workflows': 1,
            'external_python_dependencies': 0,
            'claude_vendor_validation': 'not run by this script',
            'live_model_execution': 'not run by this script'}


if __name__ == '__main__':
    try:
        print(json.dumps(verify(), indent=2))
    except (AssertionError, OSError, ValueError, common.CouncilError, SyntaxError) as exc:
        print(f'verification failed: {exc}', file=sys.stderr)
        raise SystemExit(1)
