# Reconciliation with Codex Council and Cursor Cult

Reviewed **October 22, 2026** against codex-council 1.3.3, cursor-cult 0.7.0, `agy` 1.4.4, and Claude Code 2.1.307. The three plugins share one idea: the host owns intent, creates roles dynamically from the live task, runs independent workers, and reconciles evidence rather than votes. They differ because the vendor CLIs execute work in very different ways.

## What is shared

| Principle | Codex Council | Cursor Cult | Antigravity Council |
|---|---|---|---|
| Host owns intent and reconciliation | yes | yes | yes |
| No persona catalog, no vote-based answer | yes | yes | yes |
| Dynamic roles from the live task | `roles.json` | `roles.json` | `roles.json` |
| Immutable intent across rounds | brief per run | Intent Capsule | Intent Capsule, hash-bound to a task key |
| Detached supervisor, follow/status/cancel | yes | yes | yes |
| Turn-scoped skill pre-approval of runner commands | yes | yes | yes (0.2.0) |
| Install from the GitHub marketplace | yes | yes | yes (0.2.0) |

## Where Antigravity differs, and why

**Execution model.** Codex Council runs each contribution as a `codex exec` worker inside its own scratch copy of captured inputs, so workers cannot touch the live tree and writes are disposable. `agy` has no equivalent capture-and-copy contract. Its headless mode runs one agent turn in a real workspace (`cwd`) plus directories granted with `--add-dir`. This plugin therefore restricts workers through **custom-agent capability profiles** (`tools:` lists in `~/.gemini/config/agents/`) and an explicit single-writer gate rather than through scratch isolation. Readers in a mixed fleet observe a moving worktree, which the skill tells Claude to account for.

**Wire protocol.** Codex emits JSONL records keyed by `type`. `agy --output-format stream-json` emits envelopes keyed by `event` (`init`, `step_update`, `result`), and with `--input-format stream-json` it reads one `{"event":"user",...}` message per stdin line. The runner writes exactly one user message and then closes stdin, so each worker is exactly one turn.

**Permissions.** `agy` headless mode has no prompt. A tool that needs approval is *soft-denied*: the run continues and exits 0, and stderr names the tool. Reading outside `cwd` and `--add-dir` can end the turn with an empty response. The runner treats soft denials as coverage gaps, grants the private run directory with `--add-dir`, tells workers which directories they may read, and retries a read-only role once after an empty permission abort. It never enables `--dangerously-skip-permissions` by default.

**Models.** Codex pairs a model with a separate reasoning effort. `agy models` advertises effort-specific variant IDs (a family name with a `-low`/`-medium`/`-high` style suffix). The plugin pins the exact variant ID (or omits `--model` to inherit agy's default) and sends no `--effort`, so the two cannot contradict each other. For pinned roles `init.model` is checked against the pin. Like codex-council, there is no built-in model list; every choice comes from the run's live catalog.

**Continuity.** Codex resumes with an explicit `continuity: resume`. `agy` resumes with `--conversation <id>`. Session identity here includes project, host session, task, role, exact model, and capability profile, so a different model never inherits another model's thread.

**Plugin monitors.** Cursor Cult ships an experimental plugin monitor (`monitors/monitors.json`). This plugin instead has the skill run `follow` under the host's Monitor tool, because plugin monitors are experimental and run only in interactive sessions.

## The dynamic workflow

Claude Code's dynamic workflows (`workflows/` in a plugin) let the orchestration itself be a script. `/antigravity-council:council` frames the capsule and read-only roles (one Claude agent), runs the `agy` fleet through the runner (one agent), sends each consequential worker claim to an independent Claude verifier told to refute it (up to six), and reconciles the evidence (one agent). Adding Claude-side verifiers is the reconciliation step Codex Council leaves to the host, made explicit. The workflow refuses writer or executor roles, so write authority stays single-owner and explicitly granted through the skill.

## Documentation versus local CLI

Some public documentation and the installed `agy` 1.4.4 help disagree. The runner always passes explicit values, so neither default is relied on:

| Topic | Public docs (Oct 22, 2026) | `agy` 1.4.4 `--help` | Runner behavior |
|---|---|---|---|
| `--print-timeout` default | `5m` | `0s` (wait for the turn) | always passes `60m` or the operator's value |
| `--effort` levels | headless page: low/medium/high; effort page: up to `xhigh`/`max` | low/medium/high/xhigh/max | never passed; the variant ID is pinned |
| `--add-dir` | not on the headless page | documented in help | passed for the private run directory only |
| Custom-agent `--agent` selection | subagents page describes `/agents` and `invoke_subagent` | `--agent` documented in help | passed; `init.agent` is checked against the profile |
