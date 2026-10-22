# Changelog

## 0.2.0 (2026-10-22)

Reconciled with codex-council 1.3.3, cursor-cult 0.7.0, `agy` 1.4.4, and the current Claude Code plugin, skill, and workflow docs.

- Dynamic workflow `/antigravity-council:council`: read-only council framed by Claude, run through the existing runner, each consequential claim independently verified by a Claude agent, evidence reconciled without voting. Refuses writer/executor roles.
- Skill pre-approves the runner command for the invoking turn (`allowed-tools`), documents permissions across turns, and follows with the host Monitor tool.
- GitHub marketplace install and update (`claude plugin marketplace add ehzawad/antigravity-council`); manifest gains `displayName`, `homepage`, `repository`.
- Model-agnostic: no model names or preference table in code, skill, workflow, docs, or manifest; discovery returns only the live `agy models` catalog (the `preferred` field is gone). A role pins an exact catalog ID or omits `model` to inherit agy's configured default; literal `inherit`/`default` is refused. `live_smoke.py --model` is optional.
- New `docs/RECONCILIATION.md`; sources, validation, and README updated. The beta/incomplete banner is removed now that live read-only councils pass on two model families and on agy's default; the remaining live gates stay documented.
- `verify.py` checks the workflow file, skill pre-approval, and marketplace version; CI adds macOS.
- Fix: macOS `killpg` EPERM on an exited group leader no longer escapes best-effort cleanup (misclassified roles as `runtime` under load); `reap` treats it as already gone.
- Read-only permission retry now documents its exception to the no-replay rule and warns when it drops a saved conversation.
- Tests: stream-json invocation rejects `--print`/`--prompt` too; reap test no longer races worker startup.

## Post-0.1.0 fixes (2026-10-20, against agy 1.4.3)

Fixes found by running against the real authenticated CLI:

- `doctor`/`discover`: real `agy --help` prints usage on stderr (exit 0); help probes now read both streams. The test fake now mirrors this.
- Capability profiles: `code_search` is not in the agy 1.4.3 tool registry and made the CLI refuse to construct the executor. Read tools are now `view_file`, `grep_search`, `list_dir`, `find_by_name`; profile hash names changed accordingly.
- Init audit: real `init.tools` reports the CLI's full registry, not the profile subset. The audit now checks that every profile tool exists in the registry; profile enforcement is done by the CLI and observed per tool step. The fake emits a registry superset.
- Staged run directory & headless permission aborts: `agy` headless mode (`request-review`) auto-denies `read_file` outside `cwd` and `--add-dir` directories and immediately terminates the turn with an empty response (`jetski: no output produced`). Workers now receive `--add-dir <run_dir>`, the prompt explicitly advertises the workspace root, run directory path, and staged files while forbidding outside-directory probes, `step_type: "unknown"` no longer disables replay, and read-only roles that hit a headless permission abort retry once with an explicit path boundary note.

## 0.1.0 (2026-10-20)

Initial Claude Code plugin with dynamic Argon/Opus councils, metadata-grounded exact model selection, custom capability profiles, immutable task capsules, scoped resumable CLI conversations, detached supervision, partial reply persistence, follower/cancellation/reaping controls, and offline protocol/lifecycle tests.

Deliberate release boundary: native CLI transport only; no Python SDK account-backed Opus path is claimed. Live model execution and the installed Claude plugin validator require operator-side verification.
