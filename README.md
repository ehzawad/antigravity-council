# Antigravity Council

A Claude Code plugin that commissions independent **Antigravity CLI workers**, with first-class preferences for **Gemini 4 Argon** and **Claude Opus 5.5**. Claude remains the host: it understands the request, creates task-specific roles, selects each worker's model from current CLI metadata, checks the resulting evidence, and returns one answer.

Inspired by [ehzawad/codex-council](https://github.com/ehzawad/codex-council) and [ehzawad/cursor-cult](https://github.com/ehzawad/cursor-cult). This is a new implementation of their orchestration pattern, not a renamed Codex subprocess adapter. It uses Antigravity's own streaming input/output and custom-agent contracts.

**Release:** 0.1.0. **Documentation baseline:** October 20, 2026. **Validation:** offline unit and real-subprocess tests against a fake CLI; real authenticated Antigravity and Claude plugin loading have not been exercised here. See [VALIDATION.md](docs/VALIDATION.md).

## Install

Requirements: macOS or Linux, Python **3.11+**, Claude Code, and an installed, authenticated official `agy`. No third-party Python packages are required. Follow Google's [installation and authentication documentation](https://antigravity.google/docs/install?tab=cli), then open `agy` interactively once to establish credentials. Model availability and quota belong to your Antigravity account.

After extracting the ZIP, from the directory containing `antigravity-council/`:

```zsh
python3 ./antigravity-council/plugins/antigravity-council/scripts/antigravity_council.py setup
claude --plugin-dir "$PWD/antigravity-council/plugins/antigravity-council"
```

`setup` installs three deterministic, content-hash-named capability profiles in `~/.gemini/config/agents/`. It never changes Antigravity settings, browser credentials, API keys, or your project's files. Existing conflicting profiles are refused, not overwritten. These profiles describe **tool access**, not a permanent committee of personas.

For persistent installation, run these commands from the same directory instead of using `--plugin-dir`:

```zsh
claude plugin marketplace add ./antigravity-council
claude plugin install antigravity-council@antigravity-council
```

Start a new Claude Code session after installation. Validate the package using your installed Claude CLI:

```zsh
claude plugin validate ./antigravity-council/plugins/antigravity-council
```

Run metadata-only discovery from your actual project:

```zsh
python3 /absolute/path/to/antigravity-council/plugins/antigravity-council/scripts/antigravity_council.py doctor --cwd "$PWD"
```

`doctor` reads CLI version, help, and `agy models`; it does **not** send an inference prompt and does not prove entitlement or remaining quota. There is no automatic billable installation test.

## Invoke

```text
/antigravity-council:antigravity-council use Argon and Opus 5.5 to independently investigate this regression. Preserve uncommitted work; do not edit. Reconcile the evidence rather than voting.
```

Or ask Claude to use Antigravity Council in ordinary language. The skill creates only roles justified by the task; a single role is a valid council. It does not automatically launch two workers just because two model families are preferred.

A request authorizing implementation can appoint one local writer. A role that runs tests needs explicit `execute` access, because test commands can write caches, artifacts, or other files. Ordinary reviewers have no shell tool.

## How it works

```text
User request + live workspace
             |
Claude: immutable intent, dynamic roles, model selections
             |
Private run directory: launch.json, roles.json, context.md
             |
Discover -> preflight -> detached supervisor
             |
       one agy process per role
       separate conversation and capability profile
             |
Incremental replies + raw streams + event journal
             |
Verified supervisor exit -> collect -> Claude reconciles
             |
New phase and roles only when evidence justifies them
```

The runner manages processes, policy checks, locks, session state, and reports. It never invents roles, ranks evidence, or claims the user's task is complete. Workers cannot message one another through an authorized tool; every later round is briefed by Claude.

The Intent Capsule is hashed across rounds of a task. The mutable phase brief carries new evidence. Session continuity is scoped by canonical project, host session, task, role, exact model, and capability profile. Changing a model or access class starts a fresh worker conversation rather than importing another model's conclusions.

## Model selection

The preferred IDs are **candidates only**, usable when the installed `agy models` lists them:

| Family | Preferred candidates |
|---|---|
| Gemini 4 Argon | `gemini-4-argon-high`, `gemini-4-argon-medium` |
| Claude Opus 5.5 | `claude-opus-5-5-high`, `claude-opus-5-5-medium`, `claude-opus-5-5-max`, `claude-opus-5-5-low` |

Claude chooses the task-appropriate variant and records its reason. This release requires an explicit model on every role; it does not inherit an unknown default, silently downgrade, or substitute a model after rejection/quota exhaustion. Other explicitly requested models work when advertised by the local catalog.

Selections are rechecked with fresh discovery at launch. A mismatch between the requested model and the CLI's `init.model` fails that role. The report distinguishes the requested model from the CLI-reported one. Neither is independent proof of which remote weights served a request. Antigravity may also use background models for coordination.

No separate `effort` field is accepted: the exact model variant is pinned, avoiding an extra effort flag that could reinterpret it. See [model-selection.md](plugins/antigravity-council/skills/antigravity-council/references/model-selection.md).

## Authority and safety

`read` exposes `view_file`, `grep_search`, `list_dir`, and `find_by_name`. `write` adds the documented file-editing tools. `execute` additionally exposes `run_command`, requests the CLI sandbox, and retains your existing command policies. A single `write` or `execute` role must match the explicit `--writer` selection.

This is **vendor-level capability restriction, not an OS security boundary**. Startup hooks, global integrations, CLI bugs, and other processes running as the same user remain outside the guarantee. Runtime tool inventory is audited, but an audit observes events after the CLI emits them; it is not an execution interposer. Use trusted workspaces or an OS-isolated environment for untrusted code.

The plugin does not auto-enable `--dangerously-skip-permissions`. An optional `--unrestricted-shell` launch flag permits it only for an explicitly authorized executor. That is a deliberate escalation and must be specifically approved by the user. Prefer scoped command permissions configured by the operator.

Across plugin runs in one project, reader fleets share a lock; a fleet with a writer takes an exclusive lock. This does not coordinate with manual editors or unrelated agents. Readers in a mixed fleet can still see the writer's changing files; final verification must inspect the settled state.

## Lifecycle and limits

Default active concurrency is **6**. There is no default panel size. The native per-worker response timeout is explicitly set to **60m** and can be changed with `--print-timeout 2h`, for example. There is also a **1,800-second output-inactivity watchdog**; `--stall-seconds 0` disables that watchdog, not the native timeout. There is no separate total-council deadline.

Detached `start` launches a supervisor, not a completed answer. `follow` waits for the lifetime lock to be free and the recorded process identity to have ended. A terminal log line alone is insufficient. The skill follows within the host's available tools; no magical notification hook is assumed. Exiting a watcher does not cancel a council.

Use `status`, `follow`, `collect`, `cancel`, and `reap` with the exact durable run directory. Successful sibling replies survive a failed role. Partial transport success exits **3**, not 0. Tool denials remain coverage warnings even if a final answer arrives.

Raw outputs are streamed to private files. Context is not silently truncated; a single NDJSON event exceeding **32 MiB** fails explicitly. Physical memory, disk, provider limits, and quota still apply. Run directories are retained until you deliberately remove completed ones; there is no automatic retention policy.

## Direct runner use

See [RUNBOOK.md](docs/RUNBOOK.md) for a complete local invocation. The command surface is:

```text
setup | doctor | prepare | discover | check | start | run
status | follow | collect | cancel | reap
```

Global `--skill-contract 1` belongs **before** the subcommand. `ANTIGRAVITY_COUNCIL_AGY` can select an absolute CLI executable; `ANTIGRAVITY_COUNCIL_SESSION_KEY` can explicitly scope host sessions. Missing session identity is an error, not a project-wide sharing fallback.

## CLI versus Python SDK

This plugin uses the native authenticated Antigravity **CLI protocol**. It does not instantiate `google.antigravity.Agent`. Google's Python SDK authentication path and its local-model API are separate from the account-backed CLI model catalog. The implementation does not assume an undocumented SDK route to the account's Opus allocation. See [SDK-NOTES.md](docs/SDK-NOTES.md).

## Development and verification

```zsh
cd antigravity-council
python3 scripts/verify.py
python3 -m unittest discover -s tests -v
```

The tests create private temporary homes and scripted `agy` doubles, never consume model quota, and never modify your real settings. To exercise real model access and capability selection deliberately:

```zsh
python3 scripts/live_smoke.py --allow-billable --model gemini-4-argon-high
python3 scripts/live_smoke.py --allow-billable --model claude-opus-5-5-high
```

Live smoke tests use a scratch workspace and one read-only prompt, retain diagnostic run directories, and do not certify production safety. Their model IDs still have to appear in your current local catalog.

See [DESIGN.md](DESIGN.md), [SECURITY.md](SECURITY.md), and the versioned [source contract notes](docs/SOURCES.md). The release has no third-party Python runtime dependencies.

## License

MIT. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
