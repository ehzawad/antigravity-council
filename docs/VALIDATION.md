# Release validation: 0.1.0

Executed on **October 20, 2026**, using **Python 3.13.5 on Linux**.

## Observed results

```text
python -m unittest discover -s tests -v
Ran 100 tests in 140.500s
OK
```

The 100 tests include strict role/context validation, private storage, capsule
continuity, model discovery and launch-time revalidation, exact pins, profile
content checks, the Antigravity streaming contract, fragmented UTF-8 and large
responses, session resume, partial failures, narrowly permitted retries, explicit
writer rules, headless denial warnings, detached supervision, cancellation,
orphan recovery, follower completion races, pipe-holding descendants, and
post-spawn storage failures. The live smoke harness itself is tested using an
offline double; this does not mean the real model test was run.

`python scripts/verify.py` passed: **12 Python files compiled**, **3 synchronized
capability profiles**, **1 marketplace entry**, valid example role/capsule
contracts, plugin structure checks, and no broken local Markdown links.

Runtime code uses **zero third-party Python dependencies**. Compilation and
package checks do not install dependencies or start inference. The fake CLI runs
in temporary, isolated HOME/state directories and never uses actual account
credentials or model quota.

## Not executed here

Real authenticated Gemini 4 Argon or Claude Opus 5.5 inference, real Antigravity
custom-agent tool enforcement, vendor sandbox behavior, real CLI resumption,
macOS execution, and the authoritative installed `claude plugin validate`
command were not run. `agy` and `claude` were not installed in this environment.

The official documentation was reviewed, but a fake implementation of a contract
cannot certify a released vendor binary. Real CLI behavior and account-level
availability therefore remain local acceptance gates, not hidden assumptions of
a successful offline test result.

## Operator-side acceptance

After installing and authenticating the official CLI, run `doctor` for
metadata-only discovery. Run `claude plugin validate` on the plugin directory.
Then explicitly opt into one scratch-workspace inference per desired model:

```zsh
python3 scripts/live_smoke.py --allow-billable --model gemini-4-argon-high
python3 scripts/live_smoke.py --allow-billable --model claude-opus-5-5-high
```

The smoke test checks the requested/reported model, exposed tool profile, a
read-only canary answer, and unchanged scratch-file contents. It retains its
private diagnostic run and scratch directory. It does not prove resistance to
malicious code, arbitrary startup hooks, all permission escape paths, or future
CLI versions. Never treat a transport success as verification of a user's
engineering task.
