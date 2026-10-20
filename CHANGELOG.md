# Changelog

## Unreleased (2026-10-20, beta/incomplete — real CLI fixes against agy 1.4.3)

> Note: This project is currently in **beta and incomplete**.

Fixes found by running against the real authenticated CLI:

- `doctor`/`discover`: real `agy --help` prints usage on stderr (exit 0); help probes now read both streams. The test fake now mirrors this.
- Capability profiles: `code_search` is not in the agy 1.4.3 tool registry and made the CLI refuse to construct the executor. Read tools are now `view_file`, `grep_search`, `list_dir`, `find_by_name`; profile hash names changed accordingly.
- Init audit: real `init.tools` reports the CLI's full registry, not the profile subset. The audit now checks that every profile tool exists in the registry; profile enforcement is done by the CLI and observed per tool step. The fake emits a registry superset.
- Staged run directory & headless permission aborts: `agy` headless mode (`request-review`) auto-denies `read_file` outside `cwd` and `--add-dir` directories and immediately terminates the turn with an empty response (`jetski: no output produced`). Workers now receive `--add-dir <run_dir>`, the prompt explicitly advertises the workspace root, run directory path, and staged files while forbidding outside-directory probes, `step_type: "unknown"` no longer disables replay, and read-only roles that hit a headless permission abort retry once with an explicit path boundary note.

## 0.1.0 (2026-10-20)

Initial Claude Code plugin with dynamic Argon/Opus councils, metadata-grounded exact model selection, custom capability profiles, immutable task capsules, scoped resumable CLI conversations, detached supervision, partial reply persistence, follower/cancellation/reaping controls, and offline protocol/lifecycle tests.

Deliberate release boundary: native CLI transport only; no Python SDK account-backed Opus path is claimed. Live model execution and the installed Claude plugin validator require operator-side verification.
