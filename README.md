# Antigravity Council

A Claude Code plugin that commissions independent **Antigravity CLI (`agy`) workers** on whatever models your `agy` catalog advertises today. There is no built-in model list or model preference: the catalog changes roughly monthly, so Claude reads it live at each run, and you pick the model. Claude remains the host: it understands the request, creates task-specific roles, resolves each worker's model to an exact ID from current CLI metadata (or leaves it to agy's configured default), checks the resulting evidence, and returns one answer.

Inspired by [ehzawad/codex-council](https://github.com/ehzawad/codex-council) and [ehzawad/cursor-cult](https://github.com/ehzawad/cursor-cult). This is a new implementation of their orchestration pattern, not a renamed Codex subprocess adapter: `agy` has no `codex exec`-style scratch-copy execution model, so this plugin uses Antigravity's own streaming input/output, custom-agent profiles, and its documented permission flags and session-only settings.

**Release:** 1.1.0. **Documentation baseline:** November 20, 2026, against `agy` 1.5.12 and Claude Code 2.1.328. Breaking change since 1.0.0: workers run with full access by default (see [Authority and safety](#authority-and-safety)). 1.0.1 adds `inheritMcp: false` to the restricted profiles, which renames them (run `setup` after updating), and lets a restricted executor run commands in agy's terminal sandbox. 1.1.0 moves the work out of your tree: by default a council whose roles can change files works in its own git worktree, built from a snapshot of exactly what you see, and Claude brings the result back with `diff` and `land` (see [Workspaces, diff, and land](#workspaces-diff-and-land)). 1.1.0 also keeps restricted roles from loading skills and moves to skill contract 3; the worker profiles are unchanged from 1.0.1. The full-access launch settings were spot-checked live on `agy` 1.5.12: the CLI reported `permission_mode` `always-proceed`, a headless read outside the workspace succeeded, and an unsandboxed networked command ran. Live results for each release are summarized in [CHANGELOG.md](CHANGELOG.md).

## Requirements

| Component | Supported |
|---|---|
| Claude Code | 2.1.x with plugin skills |
| Antigravity CLI | `agy` 1.5.12 or later (tested on 1.5.12; the full-access profile's harness tools were confirmed there, and an older CLI that lacks one fails full roles at startup with "profile tools absent from this agy registry"), installed and signed in interactively once |
| Python | 3.11 or later, standard library only, on macOS or Linux |

Model availability and quota belong to your Antigravity account and plan; `agy models` shows what yours can use. Follow Google's [installation and authentication documentation](https://antigravity.google/docs/install?tab=cli).

## Install and update

```zsh
claude plugin marketplace add ehzawad/antigravity-council
claude plugin install antigravity-council@antigravity-council
```

Update to a newer release:

```zsh
claude plugin marketplace update antigravity-council
claude plugin update antigravity-council@antigravity-council
```

Start a new Claude Code session after installing or updating. Then install the worker profiles once, and again after any update that changes a profile (a changed profile gets a new name; upgrading from 0.2.x adds the full-access profile, and 1.0.1 renames the three restricted profiles to `agc-read-cc5e5a5b5eef`, `agc-write-91558a374436`, and `agc-execute-70e52c3e3680`; the full profile stays `agc-full-7a01e9b77a7c`):

```zsh
python3 "$(ls -d ~/.claude/plugins/cache/antigravity-council/antigravity-council/*/ | grep -E '/[0-9]+(\.[0-9]+)+/$' | sort -V | tail -1)scripts/antigravity_council.py" setup
```

The command picks the newest numbered version in the cache, so it still works when an update leaves two versions there or Claude Code keeps non-version folders beside them. `setup` installs four deterministic, content-hash-named profiles in `~/.gemini/config/agents/`: the default full-access profile and the three restricted opt-ins (`read`, `write`, `execute`). It is idempotent. It never changes Antigravity settings, browser credentials, API keys, or your project's files. Existing conflicting profiles are refused, not overwritten. These profiles describe **tool access**, not a permanent committee of personas. Preflight fails with a pointer to `setup` when a needed profile is missing, and the skill runs `setup` in that case.

Run metadata-only discovery from your actual project (no inference, no quota):

```zsh
python3 "$(ls -d ~/.claude/plugins/cache/antigravity-council/antigravity-council/*/ | grep -E '/[0-9]+(\.[0-9]+)+/$' | sort -V | tail -1)scripts/antigravity_council.py" doctor --cwd "$PWD"
```

To develop from a checkout instead, use `claude --plugin-dir ./plugins/antigravity-council` and validate with `claude plugin validate ./plugins/antigravity-council`.

## Invoke

```text
/antigravity-council:antigravity-council use <model-id-a> and <model-id-b> from my agy catalog to independently investigate this regression and fix it. Preserve uncommitted work. Reconcile the evidence rather than voting.
```

Or ask Claude to use Antigravity Council in ordinary language. Name the models you want with IDs from `agy models`; if you name none, workers use agy's configured default.

There is one command. By default, each full worker checks its own draft in a forced self-verification turn before its reply reaches Claude. That turn can improve the evidence but never replaces Claude's own check: after the workers finish, Claude still checks each consequential claim against the live workspace, sending hard ones to an independent read-only check that tries to refute them, and confirms that a cited defect really exists before a refutation overturns a worker's claim. Then it reconciles the evidence (no voting).

The skill pre-approves its runner commands for the invoking turn only. Writing `context.md`/`roles.json` into the private run directory (under `~/.local/state/antigravity-council`) needs Write permission; in `dontAsk` or unattended sessions add allow rules for that directory and the runner command. The skill creates only roles justified by the task; a single role is a valid council. It does not launch extra workers just to use more models.

Every role has full access unless you ask for narrower workers: it can edit files, run commands (including tests), use the network and the web, and use agy's own subagents, workflows, and scheduling. In live runs, a full worker also called a tool from your installed agy plugins (MCP servers), and your custom agents were visible to full workers. By default a council whose roles can change files works in its own git worktree rather than your tree, several roles may edit that one worktree, and your tree changes only when Claude lands the reviewed result (see [Workspaces, diff, and land](#workspaces-diff-and-land)). Claude does not ask you to authorize full access on each run; it still asks before commits, pushes, deploys, publishing, or widening the task. Ask for "read-only workers" (or a restricted writer or executor) to get the restricted profiles described below.

## How it works

```text
User request + live workspace
             |
Claude: immutable intent, dynamic roles, model selections
             |
Private run directory: launch.json, roles.json, context.md
             |
Discover -> preflight -> start
             |
Snapshot of your tree -> git worktree(s), or your tree (live)
             |
Detached supervisor
             |
       one agy process per role
       separate conversation and capability profile
       full roles: answer turn, then verification turn (default on)
             |
Incremental replies + raw streams + event journal
             |
Verified supervisor exit -> collect -> Claude reconciles
             |
diff -> Claude reviews -> land (all or nothing) into your tree
             |
New phase and roles only when evidence justifies them
```

The runner manages processes, policy checks, locks, session state, and reports. It never invents roles, ranks evidence, or claims the user's task is complete. Workers are told not to contact one another (a full worker's messaging and subagent tools are for its own agy subagents); every later round is briefed by Claude. Roles that share a workspace (the shared worktree, or your tree under `live`) do see each other's file changes; per-role workspaces do not.

By default each full role works in two turns on one agy conversation. agy runs one turn per message it reads on stdin, so after the first turn succeeds with a non-empty answer, the runner sends a second message: a forced self-verification turn. It tells the worker to treat its draft as someone else's, re-check every claim against live evidence (re-read the lines, inspect the diff, re-run the tests), brief its own agy subagents with only the claim and its evidence to try to refute the most consequential claims, confirm that a cited defect exists before accepting a refutation, fix or retract and repeat until nothing new turns up, and report honestly. Its complete final report replaces the draft. In the reply, `response` is that final report and `draft_response` keeps the turn-one draft whenever a verification turn was used. `verification_turn` is `completed`, `failed`, `not-run`, or `off` (`null` for restricted roles), and `verification_turn_completed` (true or false) and `turns_completed` record the rest; the markdown report shows `verification turn: completed`, `failed`, `not-run`, or `off` for each full role. A completed verification turn means the worker re-checked its work, not that the answer is correct. If the first turn fails, no verification turn is sent. If the verification turn fails, the role fails and `response` carries the unverified draft with a warning that says so. If the process fails after a completed verification turn (a nonzero exit, a stall, or a late event), `response` keeps the verified final report, and the role still fails with a warning. `prepare --no-verify-turn` (`verify_turn: false` in `launch.json`) runs full roles in one turn (`verification_turn` `off`). Restricted roles answer in one turn. Reports stay free-form.

The verification turn costs time and quota. Live, it worked mechanically and once spawned its own skeptic subagent, but no live run has shown it changing an answer. A trivial task went from 88s to 211s, and three parallel high-effort workers plus their subagents hit the account's quota, so their verification turns failed. Size parallel councils to your quota.

The Intent Capsule is hashed across rounds of a task. The mutable phase brief carries new evidence. Session continuity is scoped by canonical project, host session, task, role, exact model, capability profile, and effort when one is set. Changing a model, effort, or access class starts a fresh worker conversation rather than importing another model's conclusions.

## Model selection

There is no hardcoded model list, preference table, or ranking. `discover` records the live `agy models` catalog for each run, and each role is either:

- **Pinned:** an exact ID from that catalog. An explicit user choice wins and is never substituted; an ID the catalog does not list fails preflight.
- **Inherited:** `model` omitted, so no `--model` is sent and agy uses its own configured default. This is what Claude does when you name no model; it does not pick one itself.

Claude records each choice's reason and does not infer capability from product names, version numbers, or catalog order. Selections are rechecked with fresh discovery at launch. For a pinned role, a mismatch with the CLI's `init.model` fails the role. Reports distinguish the requested model from the CLI-reported one; neither is independent proof of which remote weights served a request. Antigravity may also use background models for coordination.

A role that omits `model` may set an optional `effort` (`low`, `medium`, `high`, `xhigh`, or `max`), sent as agy's documented `--effort`. There is no default. Catalog IDs already encode effort, and `agy` 1.5.12 rejects `--effort` beside a catalog variant ID (`--model <id> conflicts with --effort=<level>`), so when you ask for an effort level Claude selects the catalog variant ID for it, and the runner refuses a role that sets both `effort` and `model`. agy's `init` event does not report effort, so whether agy applied it cannot be observed. Preflight fails if the installed CLI does not advertise `--effort`. See [model-selection.md](plugins/antigravity-council/skills/antigravity-council/references/model-selection.md).

## Authority and safety

A role's `access` is `full` when omitted (or set to `"full"`). A full role launches with the `agc-full-*` profile, `--dangerously-skip-permissions`, `--mode accept-edits`, and session-only `--settings '{"allowNonWorkspaceAccess":true,"enableTerminalSandbox":false,"toolPermission":"always-proceed"}'`, and without `--sandbox`. It can read and edit any file, run any command outside the terminal sandbox with your network, search the web, and fetch URLs, without approval prompts. The explicit `--mode` matters because `agy` 1.5.7 and later start headless runs in Auto mode, which turns the terminal sandbox (no network) on. `--settings` applies to that session only and is never written to `settings.json`. Preflight requires the installed CLI to advertise `--dangerously-skip-permissions`, `--mode`, and `--settings`.

A full worker also gets agy's own harness: its subagents, workflows, scheduling, and task tools. The full profile sets `inheritMcp: true` (an undocumented field) and `commandExecutionPolicy: eager` and has no MCP server, skill, or plugin lists. In live runs, a full worker successfully called a tool from your installed agy plugins (an MCP server), and your custom agents were visible to full workers. Inheriting MCP is agy's default: a profile with no `inheritMcp` key and no lists also called the tool, and only `inheritMcp: false` switched MCP off. Workers listed your plugin skills under every profile tried, including ones with `skills: []` and `plugins: []` (model self-report), so assume skills reach full workers (restricted roles get deny rules on the skill folders; see below). Full workers have no browser tools: `agy` 1.5.12 lists browser tools in its `init.tools` but refuses them in a custom agent ("not found in registry"), so browser-like work goes through the shell. The same refusal covers `call_mcp_tool` and a few other registry names, so the profile does not list them. On 1.5.12 the model was not offered agy's URL-reading tool even though the profile lists it; workers fetch pages with `curl` in the unsandboxed shell. Full roles get no `--add-dir`: with the run directory added, agy 1.5.12 used it as the default working directory for commands, so commands ran outside the project. Instead the prompt gives the run directory's absolute path and tells the worker to run commands from the workspace root.

Full access is the configured default, so there is no per-run authorization step. There is also no writer limit: several full roles may edit the same workspace at once (the shared worktree, or your tree under `live`), and Claude gives overlapping editors distinct files and tells each what the others own. Workers are told to commit, push, deploy, publish, change accounts, or mutate external services only when the capsule asks, and Claude puts those actions in a capsule only when you have asked for or approved them.

The stream is audited. A full role whose `init` event does not report `permission_mode` `always-proceed` (for example, because an enterprise admin policy keeps review on) fails as a protocol error instead of running half-restricted. A tool step outside the full profile's tool list is recorded once as a warning. A full worker may use agy subagents: the reply records the warning "worker used agy subagents; their work is part of this reply's evidence". A nested subagent still fails a restricted role. A later turn's result must follow a new `user_input` step, keep the stream's `conversation_id`, and advance `num_turns`; after a result, only the next turn's `user_input` may come first. The verification prompt is never sent after the stream ended, failed its audit, or had no validated `init`. agy 1.5.12 reports some steps under new names, so the runner maps `read_file`, `write_file`, and `edit_file` to `view_file`, `write_to_file`, and `replace_file_content` (each observed live); that also keeps a restricted writer from failing on its first write. Actions agy blocks (your deny rules, its system command denylist, or admin policy) are kept in the reply as `denied_actions` and reported as coverage warnings. A dead saved conversation (`stale-session`) is dropped for every role, and any role, full included, restarts fresh once when that error came before any tool work. Otherwise full roles and restricted mutators are never replayed automatically, because they may already have edited files, run commands, or started subagents; only restricted read roles get the one transient retry and the headless permission-abort retry. The audit observes events after the CLI emits them; it is not an execution interposer.

The restricted profiles are explicit opt-ins. `read` exposes `view_file`, `grep_search`, `list_dir`, and `find_by_name`, and its prompt keeps paths to the workspace and run directory. `write` adds the documented file-editing tools. `execute` additionally exposes `run_command`, requests the CLI sandbox with `--sandbox`, gets `"toolPermission":"proceed-in-sandbox"` in its session-only settings, and retains your existing command policies. Through 1.0.0, headless agy denied every command of a restricted executor (`request-review`), so it could edit files but not run commands. Live on 1.0.1, its commands ran in agy's terminal sandbox without network (an outbound request got HTTP 403 from the sandbox proxy). Preflight requires `--settings` for every restricted role. Restricted roles set `inheritMcp: false` and have empty MCP server, skill, and plugin lists, may not use subagents, answer in one turn, and still receive the run directory through `--add-dir`. `inheritMcp: false` is what keeps MCP tools out: the 1.0.0 restricted read profile, with `mcpServers: []` alone, successfully called a plugin MCP tool (its strict audit then failed the role, but only after the call ran). Workers still listed your plugin skills with `skills: []` and `plugins: []` (model self-report), so the empty lists do not keep skills out, and agy 1.5.12 has no switch that removes skills (`--disable-slash-commands` and `pluginsDisabled` were tried). A worker loads a skill by reading its `SKILL.md`, so since 1.1.0 every restricted role's session settings deny `read_file(<dir>)` for `~/.gemini/config/plugins`, `~/.gemini/config/skills`, `~/.gemini/antigravity-cli/skills`, `~/.gemini/skills`, and the `.agents/skills` and `.agent/skills` folders of the project and of the role's workspace. These rules are merged with your own `allow`, `ask`, and `deny` rules from `~/.gemini/antigravity-cli/settings.json`; if that file exists but cannot be read or parsed, restricted roles fail closed rather than run without your rules. The settings go to a private 0600 file, `settings-<role>.json` in the run directory, passed as the `--settings` path (agy accepts a file path; verified live), so your rules are not on the command line. Live, a restricted read role's attempt to read a plugin `SKILL.md` was refused and the role continued. Restricted roles still see skill names, and plugin hooks still run. Full roles keep skills, plugins, and inherited MCP. At most one restricted `write` or `execute` role is allowed per council, and it must match the explicit `--writer` selection; its prompt says it is the only restricted writer and that full-access workers, if any, may also edit. `--unrestricted-shell` remains only as a legacy option that gives that restricted executor the permission bypass instead of the `proceed-in-sandbox` setting.

None of this is an OS sandbox or a security boundary. Tool lists, the permission mode, and the workspace lock are CLI and plugin settings. Full workers run with your OS account's privileges and network. The council worktrees are not an isolation boundary either: a full worker can reach your tree, and anything else your account can, by absolute path. Your own deny and ask rules, agy's system command denylist, and enterprise admin policy still apply. Prompt injection in repository or web content can drive commands. Tools from your agy plugins (MCP servers), which live runs showed full workers can call, and your custom agents and skills widen what a full worker can reach. agy starts each shell command in its own process group, and before 1.0.0 a cancelled worker's command survived as an orphan (observed live). On cancel or timeout the runner now walks agy's descendants and kills their process groups too; verified live, no command survived a cancel. A tool that double-forks into an unrelated session before cleanup can still escape. Work a worker starts with `schedule`, `run_workflow`, or `manage_task` that agy keeps outside the process tree may outlive the council, and `cancel` and `reap` cannot reach it (in live runs, `schedule` was used only as an in-turn wake-up timer and was cancelled early). The provider receives whatever workers read. Startup hooks, global integrations, CLI bugs, and other processes running as the same user are outside the plugin's control. Use a disposable or OS-isolated environment for untrusted code or content.

Across plugin runs in one project, councils without a restricted writer (full roles, read roles, or both) share a workspace lock and are not serialized against each other; a council with a restricted `write` or `execute` role takes an exclusive lock. This does not coordinate with manual editors or unrelated agents. Workers that share a workspace can see files that other workers are changing; final verification must inspect the settled state.

## Workspaces, diff, and land

`prepare --workspace auto|shared|per-role|live` chooses where workers work, by how the roles relate:

- **`auto`** (the default): a council with any role whose access is not `read` gets one shared workspace (unit `council`) that every role works in, for roles building one change together (one implements, one writes tests) who must see each other's edits. A council of only restricted `read` roles works in your tree.
- **`shared`**: that one shared workspace, whatever the roles.
- **`per-role`**: each role whose access is not `read` gets its own workspace from the same snapshot, for separate or competing changes (two candidate fixes, two approaches to compare); `read` roles work in your tree.
- **`live`**: your tree, the behavior before 1.1.0.

Every workspace is a git worktree under the council state directory (`worktrees/<run-id>/<unit>`, where the unit is `council` or the role ID), on its own branch `antigravity-council-runs/<run-id>/<unit>`. It starts from a snapshot commit of exactly what you see, built with a temporary index (a copy of yours when the project is a git repository, so force-added ignored files are kept), `git add -A`, `write-tree`, and `commit-tree`; your index is never touched. Untracked files that are not ignored are included. Ignored files (`.env`, build output) are not; full workers can read them in your tree by absolute path, restricted roles cannot. A git repository keeps the snapshot in its own object store (with no commits yet, as a parentless commit). A folder that is not a git repository gets a private bare repository in the run directory (`private.git`); nothing is copied into the project. Submodules are empty in the workspace. Every git run drops all `GIT_*` environment variables and disables hooks (`core.hooksPath=/dev/null`, `core.fsmonitor=false`), and comparisons use plumbing (`diff-index`, `diff-tree`) with `--no-textconv --no-ext-diff --no-color` and explicit `a/` and `b/` prefixes, so your diff settings cannot distort a snapshot or a patch. If creating the workspaces fails, the worktrees and branches already made are removed. `status` shows the workspaces.

After the council ends, Claude reviews the result and brings it back with `diff <run_dir> [--role ID]` and `land <run_dir> [--role ID]`. Both need a verified end with no recorded worker process alive (run `reap` first).

`diff` commits the workspace's changes on its branch (it refuses if the worktree is not on that branch), records that exact result in `diffs.json`, writes `<unit>.patch` in the run directory, and returns the changed `files`, `not_landable` (submodule changes), `ignored_not_landed` (`.gitignore`'d files the workers created, which never land), and `looks_generated` (caches, bytecode, and build output from the workers' own runs: remove them in the workspace and run `diff` again, because `land` takes the whole result). For a per-role council, `diff` without `--role` returns every unit.

`land` applies exactly the result `diff` recorded, all or nothing; a per-role council needs `--role`. It refuses without a prior `diff`; if the workspace changed after `diff` (a different HEAD or uncommitted changes); if the branch no longer descends from the snapshot; if the result changes a submodule; if any path the council touched differs in your tree from the snapshot (content as git hashes it, mode, or type) or has a parent that is a symlink or not a directory; and a second time for the same unit. For competing per-role results, land the winner: a second result that touches the same files is refused. `land` copies the originals aside, deletes, removes directories it emptied, writes through a temporary index with `checkout-index` (checkout filters apply; your index is untouched), and restores everything if a write fails. When it refuses because you changed the same files, it points to the patch and the workspace and warns that merging the branch would also bring its snapshot of your earlier work. Branches and worktrees are kept; the output says how to remove them. Landing changes only the working tree: Claude does not commit, push, or merge unless you ask.

Live on `agy` 1.5.12: in a git repository with an uncommitted edit and an untracked file, a shared-worktree worker fixed the bug, `diff` listed only its file, `land` applied it, the test passed in the user's tree, the user's edit was kept, and the index was untouched. A per-role council with two competing fixes got separate worktrees; the winner landed and the second `land` was refused. In that run the workers' test runs produced `__pycache__` files that the diff listed and that landed with the winner, which is why `looks_generated` exists. A plain folder was fixed and landed through a private repository, leaving no `.git` in the project.

Limits: case-only renames cannot land on case-insensitive file systems. Empty directories are not tracked. Absolute symlinks inside the project still point at your tree. Local edits hidden by `assume-unchanged` or `skip-worktree` are not in the snapshot, and neither are nested untracked git repositories. A non-git folder with no `.gitignore` is snapshotted whole, which is slow for huge trees. Each launch gets a fresh workspace from your current tree, so land a round's result first if the next round should build on it. None of this is an OS isolation boundary: full workers can reach your tree by absolute path.

## Lifecycle and limits

Default active concurrency is **6**. There is no default panel size. The native response timeout, agy's `--print-timeout`, is explicitly set to **60m** and can be changed with `--print-timeout 2h`, for example. agy applies it per turn (observed live: a second turn finished after the limit had passed for the process as a whole), so the runner passes the configured value unchanged. The runner's own process watchdog allows turns times the timeout plus 5 seconds. There is also a **1,800-second output-inactivity watchdog**; `--stall-seconds 0` disables that watchdog, not the native timeout. There is no separate total-council deadline.

Detached `start` launches a supervisor, not a completed answer. `follow` waits for the lifetime lock to be free and the recorded process identity to have ended. A terminal log line alone is insufficient. The skill follows within the host's available tools; no magical notification hook is assumed. Exiting a watcher does not cancel a council.

Use `status`, `follow`, `collect`, `cancel`, and `reap` with the exact durable run directory. Successful sibling replies survive a failed role. Partial transport success exits **3**, not 0. Tool denials remain coverage warnings even if a final answer arrives.

Raw outputs are streamed to private files. Context is not silently truncated; a single NDJSON event exceeding **32 MiB** fails explicitly. Physical memory, disk, provider limits, and quota still apply. Run directories, and the council worktrees and branches, are retained until you deliberately remove completed ones; there is no automatic retention policy.

## Direct runner use

The command surface is:

```text
setup | doctor | prepare | discover | check | start | run
status | follow | collect | cancel | reap | diff | land
```

Global `--skill-contract 3` belongs **before** the subcommand. `ANTIGRAVITY_COUNCIL_AGY` can select an absolute CLI executable; `ANTIGRAVITY_COUNCIL_SESSION_KEY` can explicitly scope host sessions. Missing session identity is an error, not a project-wide sharing fallback.

## CLI versus Python SDK

This plugin uses the native authenticated Antigravity **CLI protocol**. It does not instantiate `google.antigravity.Agent`. Google's Python SDK authentication path and its local-model API are separate from the account-backed CLI model catalog. The implementation does not assume an undocumented SDK route to the account's CLI model allocation.

## Development and verification

The repository ships only what the plugin needs. The maintainer's offline test suite, scripted `agy` double, package checks, and billable live smoke test are kept outside the repository; every release is checked with them first, and each release's live results are summarized in [CHANGELOG.md](CHANGELOG.md). A passing offline test does not certify the live CLI, which is why each release also records what was run against real `agy`.

The release has no third-party Python runtime dependencies.

## License

MIT. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
