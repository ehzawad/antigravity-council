#!/usr/bin/env python3
"""Explicitly billable one-model scratch-workspace check against real Antigravity.

Default: one full-access worker (the plugin default) must read a canary outside its
workspace, run a networked shell command that writes a marker file, search the web,
fetch a URL, create then edit a file with its file tools, and delegate to an agy subagent. The shell step runs
without cd, so its marker also proves commands start in the workspace. Every check is verified from the CLI stream log and the filesystem,
not from the model's prose alone. `--restricted` runs the older read-only canary.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'plugins/antigravity-council/scripts'))
import council_common as common
import council_lifecycle as life
import council_models as models
import council_policy as policy

NETWORK_URL = 'https://example.com'


def tree_signature(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def capsule(values: dict) -> str:
    return '# Intent Capsule\n\n' + '\n\n'.join('## ' + h + '\n' + values[h] for h in common.HEADINGS)


def all_steps(log: Path) -> list[dict]:
    steps = []
    if not log.exists():
        return steps
    for line in log.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        step = event.get('step_update') if isinstance(event, dict) else None
        if isinstance(step, dict):
            steps.append(step)
    return steps


def restricted_inputs(workspace: Path, nonce: str, label: str) -> tuple[dict, str]:
    (workspace / 'canary.txt').write_text(nonce + '\n')
    role = {
        'id': 'read-canary', 'label': 'Restricted read-only capability smoke check',
        'instruction': ['Read canary.txt using view_file. Include its exact contents in your response.',
                        'Do not edit, create, or delete files, run commands, invoke subagents, or use external tools.',
                        'Report the tool actually used and any denial.'],
        'access': 'read',
        'selection_reason': 'Operator-requested model (or agy default) for a one-prompt live smoke test.'
    }
    values = {
        'Verbatim request': f'Read canary.txt with {label} and report its contents without changing files.',
        'Authorized outcome': 'One read-only live model check, using operator-authorized account quota.',
        'Hard constraints and non-goals': 'No writes, shell, external actions, or subagents. No task outside the canary.',
        'Explicit lenses or panel requests': label,
        'Authority boundaries': 'Only local read/search tools in the restricted read profile. No mutations.',
        'Acceptance evidence': 'Usable final response contains the canary; runtime model/tools match; workspace contents unchanged.'
    }
    brief = '\n\n# Current Phase Brief\nThe scratch workspace contains canary.txt. Inspect it now.\n'
    return role, capsule(values) + brief


def full_inputs(outside: Path, nonce: str, marker: str, label: str) -> tuple[dict, str]:
    (outside / 'canary.txt').write_text(nonce + '\n')
    command = (f"printf %s {marker} > shell-marker.txt && "
               f"curl -sS -o /dev/null -w 'HTTP_STATUS=%{{http_code}}' {NETWORK_URL}")
    role = {
        'id': 'full-access-check', 'label': 'Full-access capability smoke check',
        'instruction': [
            f'1. Read the file {outside / "canary.txt"} (it is outside your workspace) and include its exact contents.',
            f'2. Run exactly this shell command with your terminal tool, without cd or any other change, and report its output: {command}',
            '3. Use your web search tool once for: IANA example domain. Report one result URL.',
            f'4. Fetch {NETWORK_URL} and report its page title. Use a URL-reading tool if you have one; otherwise use curl in the shell.',
            '5. Use your file-writing tool (not the shell) to create edit-marker.txt in your workspace containing exactly: draft',
            f'6. Use your file-editing tool (not the shell) to replace draft with {marker} in edit-marker.txt.',
            '7. Use your subagent tool (invoke_subagent) to have one subagent independently read the canary file '
            'from step 1 and report its contents; include what the subagent reported.',
            'Report which tool you used for each step and any denial. Do nothing else.'],
        'access': 'full',
        'selection_reason': 'Operator-requested model (or agy default) for a one-prompt live smoke test.'
    }
    values = {
        'Verbatim request': f'Verify full access with {label}: outside read, shell, network, web search, URL fetch, file write and edit, agy subagent.',
        'Authorized outcome': 'One live full-access check in a scratch workspace, using operator-authorized quota.',
        'Hard constraints and non-goals': 'Only the seven listed steps. Write only shell-marker.txt and edit-marker.txt.',
        'Explicit lenses or panel requests': label,
        'Authority boundaries': 'Full access (the plugin default). No commits, pushes, or external mutations.',
        'Acceptance evidence': 'Canary in the reply; marker files written by the shell and the edit tool; network status 200; '
                               'web search step and fetched page observed; permission_mode always-proceed.'
    }
    brief = '\n\n# Current Phase Brief\nThe workspace is empty scratch space. Perform the seven steps now.\n'
    return role, capsule(values) + brief


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--allow-billable', action='store_true', help='authorize one actual agent prompt using account quota')
    p.add_argument('--model', help="exact ID from `agy models`; omit to use agy's own configured default")
    p.add_argument('--effort', choices=common.EFFORTS, help='optional agy --effort; omit to keep the model default')
    p.add_argument('--restricted', action='store_true', help='run the restricted read-only canary instead of full access')
    ns = p.parse_args()
    if not ns.allow_billable:
        p.error('requires --allow-billable; no inference was started')
    if ns.model is not None and not common.MODEL_RE.fullmatch(ns.model):
        p.error('model must be an exact execution ID')
    nonce, marker = secrets.token_hex(12), secrets.token_hex(12)
    # Keep the scratch tree and private run for inspection on either outcome.
    # Do not remove a directory that an unexpectedly surviving tool may still use.
    scratch = Path(tempfile.mkdtemp(prefix='antigravity-council-live-'))
    workspace, outside = scratch / 'workspace', scratch / 'outside'
    workspace.mkdir()
    outside.mkdir()
    run = None
    try:
        policy.setup_profiles()
        run = life.prepare(str(workspace), 'smoke-' + nonce, 'smoke-' + nonce,
                           print_timeout='10m', stall_seconds=300, heartbeat_seconds=15)
        label = ns.model or "agy's default model"
        if ns.restricted:
            role, context = restricted_inputs(workspace, nonce, label)
        else:
            role, context = full_inputs(outside, nonce, marker, label)
        if ns.model:
            role['model'] = ns.model
        if ns.effort:
            role['effort'] = ns.effort
        before = tree_signature(workspace)
        common.write_json(run / 'roles.json', [role])
        common.atomic_write(run / 'context.md', context)
        models.store_discovery(run, workspace)
        life.check(run)
        print(json.dumps({'billable_smoke_start': True, 'mode': 'restricted' if ns.restricted else 'full',
                          'model': ns.model, 'effort': ns.effort, 'run_dir': str(run),
                          'scratch': str(scratch)}), flush=True)
        code = life.start(run, attached=True)
        # The attached supervisor is this script, so it is still alive after its
        # lifetime lock is released. Read its persisted report directly; do not
        # lie to collect() about supervisor death while this script is executing.
        report = common.read_json(run / 'report.json')
        results = report.get('results', [])
        result = results[0] if results else {}
        response = result.get('response', '') or ''
        checks = {'transport_ok': bool(code == 0 and result.get('ok')), 'canary_in_reply': nonce in response}
        every_step = all_steps(run / 'logs' / f"{role['id']}.1.stdout.ndjson")
        steps = [s for s in every_step if s.get('step_type') == 'tool']
        # The capability checks look at turn one only, so the verification turn cannot redo
        # (and hide) a step that failed. agy opens turn two with a user_input step after turn one's result.
        turn_one = every_step
        inputs = [i for i, s in enumerate(every_step) if s.get('step_type') == 'user_input']
        if len(inputs) > 1:
            turn_one = every_step[:inputs[1]]
        names = sorted({s.get('tool_name') for s in steps if isinstance(s.get('tool_name'), str)})
        if ns.restricted:
            checks['workspace_unchanged'] = tree_signature(workspace) == before
            checks['no_warnings'] = not result.get('warnings')
        else:
            marker_file = workspace / 'shell-marker.txt'
            edit_file = workspace / 'edit-marker.txt'

            def targets(step: dict, name: str) -> bool:
                params = (step.get('tool_info') or {}).get('parameters') or {}
                return any(isinstance(v, str) and v.rstrip('/').endswith(name) for v in params.values())

            def output(step: dict) -> str:
                return str((step.get('tool_info') or {}).get('output', ''))

            done = [s for s in turn_one if s.get('step_type') == 'tool' and s.get('state') == 'DONE']
            commands = [s for s in done if s.get('tool_name') == 'run_command']
            writes = [s for s in done if s.get('tool_name') in {'write_file', 'write_to_file'} and targets(s, 'edit-marker.txt')]
            edits = [s for s in done if s.get('tool_name') in {'edit_file', 'replace_file_content', 'multi_replace_file_content'}
                     and targets(s, 'edit-marker.txt')]
            fetchers = [s for s in done if s.get('tool_name') in {'run_command', 'read_url_content'}]
            checks.update({
                'permission_mode_always_proceed': result.get('permission_mode') == policy.FULL_PERMISSION_MODE,
                'verification_turn_completed': result.get('verification_turn_completed') is True,
                'shell_wrote_marker': (marker_file.is_file() and marker_file.read_text().strip() == marker
                                       and any('shell-marker.txt' in str(((s.get('tool_info') or {}).get('parameters') or {}))
                                               for s in commands)),
                'file_tools_wrote_then_edited': (edit_file.is_file() and edit_file.read_text().strip() == marker
                                                 and bool(writes) and bool(edits)),
                'shell_network_200': any('HTTP_STATUS=200' in output(s) for s in commands),
                'web_search_step': any(s.get('tool_name') == 'search_web' for s in done),
                # agy 1.5.12 did not expose read_url_content; curl in the shell is an accepted route.
                # A search snippet does not count: the page must come from a shell or URL-tool output.
                'url_fetched': any('Example Domain' in output(s) for s in fetchers),
                # agy reports a running subagent as step_type "subagent"; require a finished one.
                'agy_subagent_completed': any(s.get('subagent_info') and s.get('state') == 'DONE' for s in turn_one),
                'no_denied_actions': not result.get('denied_actions'),
            })
        verified = all(checks.values())
        print(json.dumps({'passed': verified, 'mode': 'restricted' if ns.restricted else 'full', 'checks': checks,
                          'tool_steps_observed': names, 'model_requested': ns.model,
                          'model_reported': result.get('reported_model'), 'warnings': result.get('warnings'),
                          'error': result.get('error'), 'run_dir': str(run), 'scratch': str(scratch),
                          'scope': 'one live prompt; not a security or production certification'}, indent=2))
        return 0 if verified else 1
    except (common.CouncilError, OSError, ValueError) as exc:
        print(json.dumps({'passed': False, 'error': str(exc), 'run_dir': str(run) if run else None,
                          'scratch': str(scratch)}), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
