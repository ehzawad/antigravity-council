# Release validation: 1.0.0

Executed on **November 20, 2026** on macOS (Darwin 27.2.0) with Python 3.15 (offline suite also on 3.11), `agy` 1.5.12, and Claude Code 2.1.328. 1.0.0 makes full access the default, gives full workers agy's harness, and adds a verification turn; the restricted profiles are unchanged.

## Offline checks

```text
python3 scripts/verify.py                      ok (4 profiles)
python3 -m unittest discover -s tests          Ran 152 tests ... OK   (3.15 and 3.11; CI: Linux 3.11-3.13, macOS)
claude plugin validate ./plugins/antigravity-council   Validation passed
claude plugin validate .                               Validation passed (marketplace)
```

The fake `agy` mirrors the 1.5.12 behavior observed below: the flags the runner sends and their value checks, `always-proceed` under the bypass, one turn per stdin message with a `user_input` step and an advancing `num_turns`, subagents as `step_type: "subagent"`, and commands in their own process group. It encodes this implementation's reading of the CLI; a passing fake does not certify the vendor CLI.

## Vendor behavior observed live (agy 1.5.12)

| Behavior | Evidence |
|---|---|
| `--dangerously-skip-permissions` + session `--settings` → `init.permission_mode: always-proceed`; reads outside the workspace succeed; unsandboxed shell has the network | stream logs from probes and every full smoke run |
| A custom agent cannot list `browser_*`, `browser_subagent`, `call_mcp_tool`, `list_resources`, `read_resource`, `sed_file`, `wait_5_seconds`, `manage_inbox`, `notebook_execution` | agy's own "not found in registry" error, although `init.tools` lists them |
| A profile with no `tools:` key gets fewer tools (no shell, no edits) | model-reported tool list (weak evidence) |
| Model-facing names `read_file`, `write_file`, `edit_file` appear as step names | stream logs; now aliases of `view_file`, `write_to_file`, `replace_file_content` |
| `read_url_content`, `command_status`, `send_command_input`, `grep_search`, `list_dir`, `find_by_name` were not offered to the model | model-reported tool list (weak); URL fetching worked with `curl` |
| Two turns on one stream: one `init`, each turn opens with `user_input`, `num_turns` 1 → 2, same `conversation_id`, exit 0 when stdin closes | raw two-turn probe and smoke logs |
| `--print-timeout` is not a process-wide limit: a second turn finished after 20 s had passed with `--print-timeout 20s` | discriminating probe (consistent with a per-turn limit) |
| `--effort` beside a catalog variant ID fails: "--model … conflicts with --effort=high"; without `--model` it is accepted, but `init` does not show whether it applied | live probes |
| With the run directory added via `--add-dir`, commands ran there instead of the project | live smoke; fixed by giving full roles no `--add-dir` (next runs wrote their marker without `cd`) |
| agy starts each shell command in its own process group | cancelled worker's command survived as an orphan before the fix; none after |
| Plugin MCP tools and custom agents are visible to full workers, also under a profile with `mcpServers: []` and no `inheritMcp` | model-reported tool lists; the field's effect is unproven, skills unchecked |

## Live checks (billable, explicitly authorized)

| Check | Result |
|---|---|
| `live_smoke.py` (full, default) on the release code, owner-requested model pinned by exact ID | **passed 11/11**: canary read outside the workspace, shell marker without `cd`, HTTP 200, `search_web`, page fetched by the shell, file written then edited by the file tools, a completed agy subagent, verification turn completed, `always-proceed`, no denied actions. Capability checks use turn one's steps only |
| `live_smoke.py --restricted`, agy's default model | passed |
| Cancel during the verification turn while the worker runs a shell command | before the descendant-group fix the command survived as an orphan; after it, worker and command were gone and the run reported `cancelled` |
| Seeded-bug review: three full roles in parallel, a module with 4 planted bugs and 2 functions meant to be correct | every draft found all 4 planted bugs and also a real floating-point bug in the function meant to be correct (`ceil(0.07 * 100)` is 8); no false positives; the file was left unchanged as the capsule required. **All three verification turns failed with the account's quota error (429)**, so this run measured nothing about the verification turn's benefit |
| Independent release review by a full-access council of this runner | failed: the worker hit the same quota error, went silent while blocked, and the 1,800 s inactivity watchdog stopped it |

Findings fixed before release, each with a regression test: the verification prompt could follow a turn-one result that failed its audit; a repeated result could pass as the verification reply; a completed verification was discarded when the process failed afterwards; turn-one `denied_actions` were lost; a dead saved conversation made full roles fail on every rerun; the verification prompt invited edits under a review-only capsule; `--print-timeout` was doubled for full roles; `effort` beside a pinned model is now refused; concurrent `setup` could see a half-written profile; the smoke harness accepted a search snippet as a fetched page, an active subagent as a finished one, and pooled both turns. Reviews came from an independent Codex review of the diff and a multi-agent Claude review whose findings were each re-checked by two refuters.

## Cost observed

The verification turn worked mechanically in every completed run, and in one it spawned its own skeptic subagent unprompted. No run has shown it changing an answer. It costs time and quota: on a trivial task the role went from 88 s to 211 s, and three parallel high-effort workers with their subagents exhausted the account's quota within about seven minutes. `prepare --no-verify-turn` turns it off; size parallel councils to the account's quota.

## Not verified

- Whether the verification turn improves answers (needs a task hard enough that drafts are wrong, and quota to finish the turns).
- Several full roles editing the same files at once against the live CLI (live runs had three concurrent full roles, read-only).
- Restricted `write` and `execute` roles against the live CLI.
- Resuming a saved conversation (`--conversation`) with full access across runs.
- What `inheritMcp` does, and whether skills reach full workers.
- Gemini Enterprise admin policies, user deny rules, and the system command denylist under the bypass (documented to still apply; `denied_actions` handling is tested offline only).
- Work started with `schedule`, `run_workflow`, or `manage_task` that agy keeps outside its process tree (live runs used `schedule` only as an in-turn timer).
- Linux live execution (offline CI covers Linux), future `agy` releases, and hostile workspaces or prompt injection. Full access is not an OS sandbox.

A transport success is never verification of a user's engineering task.

## Operator-side acceptance

After installing and authenticating the official CLI, run `doctor` for metadata-only discovery, then opt into one scratch-workspace inference:

```zsh
python3 scripts/live_smoke.py --allow-billable                                   # full access, agy's default model
python3 scripts/live_smoke.py --allow-billable --model <id-from-agy-models>       # full access, pinned model
python3 scripts/live_smoke.py --allow-billable --restricted                      # restricted read-only canary
```

`--effort <level>` applies only without `--model`. The full check verifies each capability from the stream log and the filesystem, not from the model's prose. Both modes keep their private diagnostic run and scratch directory.
