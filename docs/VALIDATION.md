# Release validation: 0.2.1

Executed on **October 22, 2026** (0.2.0 live runs; 0.2.1 only removes the separate workflow command and folds its claim-verification step into the skill, re-checked offline and with a live installed-copy run) on macOS (Darwin 27.0) with Python 3.15, `agy` 1.4.4, and Claude Code 2.1.307.

## Offline checks

```text
python3 scripts/verify.py                      ok (12 Python files, 3 profiles)
python3 -m unittest discover -s tests          Ran 108 tests ... OK
claude plugin validate ./plugins/antigravity-council   Validation passed
claude plugin validate .                               Validation passed (marketplace)
```

The full suite also passed six times in a row while three copies ran in parallel. That stress run found two load-dependent flakes that predated this release:

- macOS returns `EPERM` instead of `ESRCH` from `killpg` on a group whose leader just exited. Cleanup now treats both the same way.
- A reap test killed the supervisor before the fake worker had written anything, so the worker legitimately died on `EPIPE`. The test now waits for worker output first.

`verify.py` also checks that the shipped plugin files name no vendor models. The fake `agy` runs in isolated temporary HOME and state directories and uses no credentials or quota.

## Live checks (billable, explicitly authorized)

All runs were read-only and used the real authenticated CLI on this machine.

| Check | Result |
|---|---|
| `live_smoke.py`, Gemini-family catalog model pinned | passed: canary read, requested model equals `init.model`, workspace unchanged |
| `live_smoke.py`, Claude-family catalog model pinned | passed (same checks) |
| `live_smoke.py` with no `--model` (agy's default) | passed. `init.model` was absent, as the headless docs say it is when not overridden |
| Full council with independent claim verification (0.2.0 workflow form), run end to end on this repo | complete: 9 Claude agents. Both `agy` workers (two families) returned usable replies, each requested model matched `init.model`, and there were no warnings. Each consequential claim was independently verified, then reconciled |

Real streams confirmed the envelope keys (`event` + `init`/`step_update`/`result`), a top-level `conversation_id` on `init`, `init.tools` listing the full registry, `permission_mode: request-review`, and the `step_type` values `user_input`, `unknown`, `agent_response`, and `tool`. Every captured stream began with `init`.

Those verifiers raised findings that were fixed in 0.2.0:

- The read-only permission retry was an undocumented exception to the no-replay rule.
- The test of the stream-json invocation was too narrow.

One worker reported a system message from a user-installed agy plugin with hooks. See [SECURITY.md](../SECURITY.md).

## Not verified

- Writer and executor roles against the live CLI: the vendor sandbox, `accept-edits` behavior, and command permission rules.
- Real CLI conversation resumption across runs.
- Linux live execution (offline CI covers Linux).
- Behavior of future `agy` releases or newly added models.
- Resistance to hostile workspaces, startup hooks, or same-user processes.

A transport success is never verification of a user's engineering task.

## Operator-side acceptance

After installing and authenticating the official CLI, run `doctor` for metadata-only discovery, then opt into one scratch-workspace inference:

```zsh
python3 scripts/live_smoke.py --allow-billable                          # agy's default model
python3 scripts/live_smoke.py --allow-billable --model <id-from-agy-models>
```

The smoke test checks the requested and reported model, the exposed tool profile, a read-only canary answer, and unchanged scratch-file contents. It keeps its private diagnostic run and scratch directory.
