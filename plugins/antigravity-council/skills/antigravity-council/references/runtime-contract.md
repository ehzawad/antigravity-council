# Runtime operations

`setup` writes only named capability profiles. `prepare` creates a private durable run and invalid-until-filled input templates. `discover` performs metadata probes. `check` validates without starting workers. `start` rechecks metadata, exclusively claims the directory, and starts a detached supervisor. `run` is the attached equivalent for direct CLI use.

One launch owns one directory. Never reuse a directory after either launch path has claimed it, including after failure. The root lives under `$XDG_STATE_HOME/antigravity-council`, or `~/.local/state/antigravity-council`. No automatic cleanup occurs.

Files: `launch.json`, `roles.json`, `context.md`, `model-snapshot.json`, `launch-plan.json`, `supervisor.lock`, `supervisor.json`, `state.json`, `events.ndjson`, `logs/`, `replies/`, and final `report.json`/`report.md`. Detached stdout/stderr go to `err.log`. Raw worker streams go to per-attempt files; fields may contain private project data. Files are 0600 and run directories 0700.

`status` combines the immutable lock-file identity, lock ownership, and process birth identity. `follow` emits sequenced JSON events and a final verified-end record only after the supervisor has ended. Inconsistent snapshots are retried briefly; persistent uncertainty exits 4 without claiming completion. `follow --after-sequence N` reconnects. `follow --timeout S` stops the watcher only, returning 5.

`collect` refuses a still-running supervisor. `collect --partial` explicitly returns incomplete evidence. `cancel` signals a verified supervisor and waits for it to settle; `cancel --force` may kill that same identity if graceful cancellation fails. `reap` only targets recorded surviving worker groups after the supervisor is verified gone. It refuses an active or unknown supervisor. Daemonized or reparented tools can escape process-group cleanup; use OS containment for stronger guarantees.

Exit codes: 0 all roles supplied usable terminal results; 3 some did; 1 all failed or supervisor failed; 2 usage, staging, model, or capability preflight error; 130 cancellation. Follower-only 4 means uncertain liveness and 5 means its own timeout. Read the report, not just an exit code. Tool denials are coverage gaps.

Model sessions are bound to project + host session + task + role + model + profile. A stale read-only session may restart once with a continuity-lost warning; mutating roles never auto-replay. Retryable read-only transient errors have one retry only if no tool work or undecodable event was observed. No exactly-once claim is made.

Concurrency defaults to six active roles. Project locks exclude another plugin fleet while a writer fleet is active; role locks preserve continuity. Locks do not coordinate other editors, and a mixed fleet's readers still observe a moving worktree.

Worker response timeout defaults to 60m, inactivity watchdog 1,800s, advisory heartbeat 300s, internal state tick at most 15s. `--stall-seconds 0` disables only inactivity checks. This plugin does not claim to disable Antigravity's native timeout. Per-event output bound is 32 MiB; failures are explicit. Stderr diagnostics are tailed in summaries and fully retained in private raw logs.

No automatic permission bypass. `write` allows only file tools. `execute` adds shell and requests the vendor sandbox; existing operator rules still apply. An explicit user-authorized unrestricted executor can add the bypass flag. The host must not choose it simply to avoid a denial.
