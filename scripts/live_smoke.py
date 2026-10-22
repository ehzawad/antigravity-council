#!/usr/bin/env python3
"""Explicitly billable one-model scratch-workspace check against real Antigravity."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'plugins/antigravity-council/scripts'))
import council_common as common
import council_lifecycle as life
import council_models as models
import council_policy as policy


def tree_signature(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--allow-billable', action='store_true', help='authorize one actual agent prompt using account quota')
    p.add_argument('--model', help="exact ID from `agy models`; omit to use agy's own configured default")
    ns = p.parse_args()
    if not ns.allow_billable:
        p.error('requires --allow-billable; no inference was started')
    if ns.model is not None and not common.MODEL_RE.fullmatch(ns.model):
        p.error('model must be an exact execution ID')
    nonce = secrets.token_hex(12)
    # Keep the scratch workspace and private run for inspection on either outcome.
    # Do not remove a directory that an unexpectedly surviving tool may still use.
    scratch = Path(tempfile.mkdtemp(prefix='antigravity-council-live-'))
    (scratch / 'canary.txt').write_text(nonce + '\n')
    before = tree_signature(scratch)
    run = None
    try:
        policy.setup_profiles()
        run = life.prepare(str(scratch), 'smoke-' + nonce, 'smoke-' + nonce,
                           print_timeout='5m', stall_seconds=120, heartbeat_seconds=15)
        label = ns.model or "agy's default model"
        role = {
            'id': 'read-canary', 'label': 'Read-only capability smoke check',
            'instruction': ['Read canary.txt using view_file. Include its exact contents in your response.',
                            'Do not edit, create, or delete files, run commands, invoke subagents, or use external tools.',
                            'Report the tool actually used and any denial.'],
            'access': 'read',
            'selection_reason': 'Operator-requested model (or agy default) for a one-prompt live smoke test.'
        }
        if ns.model:
            role['model'] = ns.model
        roles = [role]
        values = {
            'Verbatim request': f'Read canary.txt with {label} and report its contents without changing files.',
            'Authorized outcome': 'One read-only live model check, using operator-authorized account quota.',
            'Hard constraints and non-goals': 'No writes, shell, external actions, or subagents. No task outside the canary.',
            'Explicit lenses or panel requests': label,
            'Authority boundaries': 'Only local read/search tools in the capability profile. No mutations.',
            'Acceptance evidence': 'Usable final response contains the canary; runtime model/tools match; workspace contents unchanged.'
        }
        context = '# Intent Capsule\n\n' + '\n\n'.join('## '+h+'\n'+values[h] for h in common.HEADINGS)
        context += '\n\n# Current Phase Brief\nThe scratch workspace contains canary.txt. Inspect it now.\n'
        common.write_json(run / 'roles.json', roles)
        common.atomic_write(run / 'context.md', context)
        models.store_discovery(run, scratch)
        life.check(run)
        print(json.dumps({'billable_smoke_start': True, 'model': ns.model, 'run_dir': str(run),
                          'scratch_workspace': str(scratch)}), flush=True)
        code = life.start(run, attached=True)
        # The attached supervisor is this script, so it is still alive after its
        # lifetime lock is released. Read its persisted report directly; do not
        # lie to collect() about supervisor death while this script is executing.
        report = common.read_json(run / 'report.json')
        results = report.get('results', [])
        result = results[0] if results else {}
        verified = bool(code == 0 and result.get('ok') and nonce in result.get('response', '')
                        and not result.get('warnings') and tree_signature(scratch) == before)
        print(json.dumps({'passed': verified, 'model_requested': ns.model,
                          'model_reported': result.get('reported_model'), 'run_dir': str(run),
                          'scratch_workspace': str(scratch), 'workspace_unchanged': tree_signature(scratch) == before,
                          'scope': 'one live read-only canary prompt; not a security or production certification'}, indent=2))
        return 0 if verified else 1
    except (common.CouncilError, OSError, ValueError) as exc:
        print(json.dumps({'passed': False, 'error': str(exc), 'run_dir': str(run) if run else None,
                          'scratch_workspace': str(scratch)}), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
