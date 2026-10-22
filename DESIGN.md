# Design

The host owns meaning; the runtime owns execution. Claude reads the conversation and worktree, frames independent questions, selects models, and reconciles. Python validates contracts, launches children, records evidence, and reports failures. There is no role catalog, vote aggregation, or hidden planner in the runner.

## Module boundaries

| Module | Responsibility |
|---|---|
| `antigravity_council.py` | CLI dispatch and skill/runtime handshake |
| `council_common.py` | Strict data, private storage, capsule parsing, identity and lock primitives |
| `council_models.py` | Native provider check, metadata probes, catalog parsing, exact model membership |
| `council_policy.py` | Content-addressed capability profiles and explicit writer validation |
| `council_protocol.py` | Antigravity stdin/event contract, terminal validation, diagnostic classification |
| `council_runtime.py` | Per-role subprocesses, locks, continuation, retries, streams, and reply persistence |
| `council_lifecycle.py` | Run creation, input binding, supervisor, durable status, follow/collect/cancel/reap |

No external service, database, API key manager, or Python dependency is required. Separate modules make vendor protocol changes reviewable without touching host role synthesis. A microservice deployment would add network and authentication surfaces without helping this local plugin's job.

## Execution and state

Preparation records the exact canonical workspace, host session, task key, concurrency, writer, and timeout policy. The host authors roles/context. Preflight validates files, authority, profiles, models, and capsule. Launch repeats discovery, claims a directory exactly once, hashes the inputs, and transfers an open kernel lock descriptor to a child in its own session. The child publishes its own birth identity and verifies the hash before dispatch.

Each role has a separate CLI process and a separately keyed saved conversation. Its stdin receives one JSON user message, then EOF. Both output streams are consumed concurrently. The parser accepts the documented event structure, ignores extra fields, and treats malformed or unauthorized behavior as failure. A successful return requires a validated init and one nonempty SUCCESS result with process exit 0.

A settled reply is written before its role-completed event. Results remain useful when a sibling fails or the host disconnects. Final reports state transport status and leave semantic acceptance to Claude. A per-process response timeout and output-inactivity watchdog prevent a worker from silently persisting forever under default settings.

The event journal is informational; lifecycle decisions use the supervisor lock and process birth identity. Lock/process reads are non-atomic, so the follower briefly reobserves an inconsistent snapshot rather than announcing completion or immediately failing. Persistent uncertainty remains explicit and never authorizes a signal.

## Deliberate differences from Codex Council

This is not an exhaustive feature-for-feature port. It retains host-defined roles, private staged context, independent persisted conversations, bounded fan-out, detached supervision, early replies, final reconciliation, and conservative recovery.

Antigravity-specific differences include its `event`-based wire protocol, streaming stdin, explicit per-worker native timeout, exact model variants instead of a separate effort field, custom-agent capability profiles, requested-versus-CLI-reported model checks, and treatment of soft permission denials as coverage gaps. It does not use Codex app-server RPC, Codex JSONL `type` records, native Codex configuration inheritance, or its sandbox-bypass launch defaults.

The Intent Capsule and explicit single-writer gate draw on Cursor Cult's design. This implementation additionally binds a capsule digest across a stable task key and includes model/access in session identity. An expressly requested new model therefore starts an independent thread.

## Non-goals and known gaps

No peer messaging, nested subagent trees, automatic worktree creation, model ranking, quota bypass, fallback model hopping, cloud service, or guaranteed host push notifications. No generic SDK abstraction is claimed. No automatic run retention or live provider validation occurs during installation. Process groups and CLI tool profiles are not a hostile same-user security boundary.

Current test fixtures validate this implementation's interpretation of the documented contract. They cannot prove that a newly released `agy` binary implements the contract identically. The opt-in smoke test and a real Claude plugin validation remain the final local acceptance gates.
