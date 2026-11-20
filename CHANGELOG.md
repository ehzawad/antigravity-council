# Changelog

## 1.0.1 (2026-11-20)

Found by live tests of the three items 1.0.0 left unverified (run on agy 1.5.12):

- Restricted profiles add `inheritMcp: false`. `mcpServers: []` alone did not keep MCP out: the 1.0.0 restricted read worker successfully called a plugin's MCP tool (the strict audit would then fail the role, but only after the call ran). With `inheritMcp: false` the tool is gone. The restricted profile names change (`agc-read-cc5e5a5b5eef`, `agc-write-91558a374436`, `agc-execute-70e52c3e3680`); run `setup` after updating. The full profile is unchanged.
- Restricted `execute` roles can now run commands. Headless agy denied every command under the default `request-review`, so the role edited files and then failed; this was true since 0.1.0. They now get `--settings '{"toolPermission":"proceed-in-sandbox"}'` with `--sandbox`: commands run in agy's terminal sandbox without prompting (live: a command ran; an outbound request got HTTP 403 from the sandbox proxy). `--unrestricted-shell` still uses the bypass instead. Preflight requires `--settings` for this role.
- Verified live and unchanged: four full roles editing different functions of one file at the same time all landed their fixes and the test file passed; a restricted `write` role edits through the `edit_file` alias without being killed; full workers really do inherit the user's MCP servers (a plugin MCP tool call succeeded), which is agy's default; skills were listed by workers under every profile, including ones with `skills: []`.


## 1.0.0 (2026-11-20)

Breaking: **full access is the default.** Checked against `agy` 1.5.12, its `--help`, the official Antigravity CLI docs, and live runs (see [VALIDATION.md](docs/VALIDATION.md)).

- New `full` access, the default when a role omits `access`. Every full role gets `--dangerously-skip-permissions`, `--mode accept-edits` (displacing agy's headless Auto default, which turns the terminal sandbox on), and session-only `--settings '{"allowNonWorkspaceAccess":true,"enableTerminalSandbox":false,"toolPermission":"always-proceed"}'`; no `--sandbox` and no `--add-dir` (with the run directory added, agy 1.5.12 ran commands there instead of in the project). Workers read and edit anywhere, run any command, and use the network and web search without prompts.
- Full workers get agy's own harness: the profile (`agc-full-*`, `commandExecutionPolicy: eager`, `inheritMcp: true`) adds subagents (`invoke_subagent`, `define_subagent`, `manage_subagents`, `send_message`, `wait`), `run_workflow`, `schedule`, and `notebook_edit`. agy subagent activity is recorded, not fatal. agy 1.5.12 refuses browser tools in a custom agent ("not found in registry"), so there is no browser; URL fetching goes through the shell. The user's plugin MCP tools and custom agents were visible to workers; whether `inheritMcp` (undocumented) is what provides them, and whether skills arrive, is not established.
- Forced self-verification turn for every full role, on the same agy conversation once turn one succeeds: re-check each claim against live evidence, use its own subagents to try to refute the important ones, confirm a cited defect exists before accepting it, fix within the capsule's authority or correct the report, then write the final report. Replies carry `verification_turn` (`completed`, `failed`, `not-run`, `off`), `verification_turn_completed`, `turns_completed`, and the turn-one `draft_response`. A completed turn means the worker re-checked, not that it is right; a failed one keeps the unverified draft; a verified report survives a later process failure, and a cancel during the verification turn keeps the turn-one draft. `prepare --no-verify-turn` skips it. The idea comes from DeepSeekMath-V2's self-verification and meta-verification (arXiv 2511.22570) and DeepSeek's super-agent note that a merely permitted step goes unused. Live, it worked and once spawned its own skeptic subagent, but it has not yet been shown to change an answer, and it costs time and quota.
- The host skill confirms a refuter's cited defect exists before it overturns a worker claim, and no longer picks a model when the user names none: it omits `model` so agy's configured default applies.
- No writer limit for full roles: several may edit the same worktree, and full councils on one worktree are not serialized. The restricted `read`/`write`/`execute` profiles keep their bytes and hash names and remain opt-ins with their single restricted writer, `--writer`, and exclusive workspace lock; their prompt now says full workers may also edit.
- Stream audit: full roles must report `init.permission_mode == "always-proceed"` (documented for the bypass flag) or fail as a protocol error. A tool step outside the full profile list is a warning, not fatal; restricted roles keep the strict allowlist and still fail on subagents. A later turn's result must follow a new `user_input` step, keep the conversation ID, and advance `num_turns`; the verification prompt is never sent after a stream ended, failed its audit, or had no `init`.
- agy 1.5.12 reports writes and edits as `write_file` and `edit_file` steps; both are aliases now (like `read_file`), so a restricted writer is no longer killed on its first write.
- Cancellation and timeouts kill the process groups of agy's descendants as well: agy starts each shell command in its own group, and a cancelled worker's command survived as an orphan before this fix (verified gone after it).
- agy's `result.denied_actions` from every turn are kept in the reply and reported as coverage warnings. Replies also record `permission_mode` and `effort`.
- `--print-timeout` is passed unchanged: agy applies it per turn (a second turn finished after the limit had passed for the process); the runner's process watchdog allows turns × timeout + 5s.
- Optional role `effort` (`low`…`max`) is sent as agy's `--effort`, only for roles that omit `model`: agy rejects `--effort` beside a catalog variant ID, and catalog IDs already encode effort. agy does not report whether it applied it.
- A dead saved conversation (`stale-session`) is dropped for every role, and any role, restricted writers included, restarts fresh once when the error came before any tool work.
- `setup` installs profiles atomically (temp file plus hard link, with an exclusive-create fallback), so concurrent first runs never see a half-written profile. A missing profile now says to run `setup`.
- Preflight requires `--dangerously-skip-permissions`, `--mode`, and `--settings` for full roles, and `--effort` when a role sets one. Skill contract 2 (`--skill-contract 2`).
- `scripts/live_smoke.py` checks full access end to end by default, from turn one's stream steps and the filesystem: a canary read outside the workspace, a networked shell command run without `cd`, a web search, a page fetched by the shell or a URL tool, a file written then edited by the file tools, a completed agy subagent, and a completed verification turn. `--restricted` runs the read-only canary; `--effort` is optional.
- Fake `agy` mirrors 1.5.12's flags, reports `always-proceed` under the bypass, validates flag values, runs one turn per stdin message, counts `num_turns`, and starts commands in their own process group where a test needs it.

## 0.2.3 (2026-11-06)

- Fix: agy 1.5.2 reports a file read as `read_file` in its step events while its registry and profiles still say `view_file`, and the stream audit killed the role. `read_file` now counts as `view_file`.
- Reverts 0.2.2's rule, which recorded any tool name missing from `init.tools` and let the turn go on. That rested on a wrong diagnosis: `init.tools` doesn't list everything agy can run, so a missing name proves nothing. Every other name outside the profile stops the role again.

## 0.2.1 (2026-10-22)

- One command: `/antigravity-council:antigravity-council`. The separate `/antigravity-council:council` dynamic workflow is removed, and its independent claim-verification step is now part of the skill's reconcile phase.

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
