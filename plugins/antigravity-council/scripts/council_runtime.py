"""One authenticated agy subprocess per role; explicit state and bounded cleanup."""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import time
from typing import Callable

from council_common import (CouncilError, SESSION_RE, digest, now, open_lock, private_dir,
                            read_json, state_root, write_json)
from council_models import provider_check
from council_policy import check_profiles, profile_name
from council_protocol import Stream, classify, user_message


class Attempt:
    def __init__(self, stream: Stream):
        self.stream = stream
        self.error = ""
        self.failure_class = ""
        self.stderr = ""
        self.returncode: int | None = None
        self.warning: str | None = None


def seconds(duration: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)([smh])", duration)
    if not match:
        raise CouncilError("print timeout must be a positive integer duration, e.g. 60m or 2h")
    return int(match[1]) * {"s": 1, "m": 60, "h": 3600}[match[2]]


INTERNAL_RUN_FILES = {"launch.json", "launch-plan.json", "model-snapshot.json", "supervisor.lock",
                      "supervisor.json", "state.json", "events.ndjson", "err.log", "report.json",
                      "report.md", "supervisor-error.txt", "logs", "replies"}


def argv_for(binary: str, role: dict, config: dict, sid: str | None, run_dir: Path | None = None) -> list[str]:
    args = [binary, "--input-format", "stream-json", "--output-format", "stream-json",
            "--model", role["model"], "--agent", profile_name(role["access"]),
            "--print-timeout", config["print_timeout"]]
    if run_dir is not None:
        args += ["--add-dir", str(run_dir)]
    # Do not use -p with --input-format stream-json. Prompts belong on stdin.
    if sid:
        if not SESSION_RE.fullmatch(sid):
            raise CouncilError("saved conversation ID has invalid syntax")
        args += ["--conversation", sid]
    if role["access"] != "read":
        args += ["--mode", "accept-edits"]
    if role["access"] == "execute":
        args += ["--sandbox"]
        if config.get("unrestricted_shell"):
            args += ["--dangerously-skip-permissions"]
    return args


def staged_files(run_dir: Path | None) -> list[str]:
    if run_dir is None or not run_dir.is_dir():
        return []
    return sorted(p.name for p in run_dir.iterdir() if p.name not in INTERNAL_RUN_FILES)


def prompt_for(role: dict, context: str, *, cwd: Path | None = None,
               run_dir: Path | None = None, retry_note: str = "") -> str:
    access = {
        "read": "Read-only investigation. No file writes, shell execution, browser actuation, or MCP calls.",
        "write": "You are the sole authorized local file writer for this phase. Shell execution is unavailable.",
        "execute": "You are the sole authorized local writer/executor. Use only the granted workspace and tool permissions.",
    }[role["access"]]
    lens = "\n".join(role["instruction"])
    paths_block = ""
    if cwd is not None or run_dir is not None:
        staged = ", ".join(staged_files(run_dir)) or "context.md, roles.json"
        paths_block = (
            f"\nAUTHORIZED DIRECTORIES\n"
            f"- Workspace root (cwd): {cwd}\n"
            f"- Run directory (staged files, added via --add-dir): {run_dir}\n"
            f"- Staged files in run directory: {staged}\n"
            f"IMPORTANT: When instructions mention 'this run directory' or staged patch/spec files, read them "
            f"from {run_dir}. Only read, list, or search paths inside the Workspace root or Run directory "
            f"above. Never read or list paths outside those two directories (such as /tmp, /private/tmp, /var, "
            f"parent directories, or ~/.claude), because headless mode auto-denies outside paths and immediately "
            f"aborts your turn with empty output.\n"
        )
    if retry_note:
        paths_block += (
            f"\nRETRY NOTICE: Your previous attempt was terminated by a headless permission denial ({retry_note}). "
            f"Do NOT access that path or any path outside the Workspace root and Run directory; complete the "
            f"deliverable using only allowed paths.\n"
        )
    return f"""You are a task-specific worker in a Claude Code orchestrated Antigravity Council.
Role: {role['label']} ({role['id']})
{access}
The user requirements in the Intent Capsule are authoritative. Host conclusions are
claims to verify against current evidence, not facts to accept. Prior turns are
background; this brief and live evidence control the present task.
Do not spawn subagents, change your model, expand permissions, or contact sibling
workers. Do not commit, push, deploy, publish, or mutate external services without
explicit authorization in the capsule. Do not ask questions in this noninteractive
run; mark unresolved decisions as unknowns. A blocked check is a coverage gap.
Finish with findings, exact evidence (paths/lines or commands and observed results),
changed files, checks actually run, risks, and unknowns. Say 'nothing material' when
appropriate. The host reconciles evidence, never votes. Thoroughness beats speed.
{paths_block}
ROLE INSTRUCTION
{lens}

{context}

YOUR DELIVERABLE
{lens}
"""


async def acquire(path: Path, *, shared: bool = False) -> int:
    fd = open_lock(path)
    try:
        while True:
            try:
                fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                await asyncio.sleep(0.1)
    except BaseException:
        os.close(fd)
        raise


async def terminate(proc: asyncio.subprocess.Process) -> None:
    """Best effort for this attempt's process group, including pipe-holding children.

    Tools that daemonize into unrelated sessions can escape this group. Do not call
    this function an OS containment boundary or an exactly-once guarantee.
    """
    for sig in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, sig)
        if sig == signal.SIGTERM:
            await asyncio.sleep(0.25)
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(proc.wait(), 2)


async def run_attempt(binary: str, role: dict, context: str, config: dict, sid: str | None,
                      log_dir: Path, attempt_number: int, update: Callable,
                      run_dir: Path | None = None, retry_note: str = "") -> Attempt:
    stream = Stream(role)
    out = Attempt(stream)
    root = Path(config["cwd"])
    # Encoding and config validation happen before spawning a child.
    payload = user_message(prompt_for(role, context, cwd=root, run_dir=run_dir, retry_note=retry_note))
    command = argv_for(binary, role, config, sid, run_dir)
    provider_check()
    check_profiles(root, [role])
    stdout_file = log_dir / f"{role['id']}.{attempt_number}.stdout.ndjson"
    stderr_file = log_dir / f"{role['id']}.{attempt_number}.stderr.log"
    proc = await asyncio.create_subprocess_exec(
        *command, cwd=root, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    from council_common import process_identity
    identity = process_identity(proc.pid)
    activity = time.monotonic()
    started = activity
    helpers = []

    async def pump(pipe: asyncio.StreamReader, path: Path, parse: bool) -> None:
        nonlocal activity
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as handle:
            while True:
                chunk = await pipe.read(65536)
                if not chunk:
                    break
                activity = time.monotonic()
                handle.write(chunk)
                handle.flush()
                update(role["id"], last_output_at=now(), publish=False)
                if parse:
                    stream.feed(chunk)
                else:
                    out.stderr = (out.stderr + chunk.decode("utf-8", "replace"))[-8192:]
            if parse:
                stream.finish()

    async def feed() -> None:
        try:
            proc.stdin.write(payload)
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            proc.stdin.close()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                await proc.stdin.wait_closed()

    try:
        helpers = [asyncio.create_task(pump(proc.stdout, stdout_file, True)),
                   asyncio.create_task(pump(proc.stderr, stderr_file, False)),
                   asyncio.create_task(feed())]
        await asyncio.sleep(0)
        update(role["id"], phase="running", process=identity, attempt=attempt_number)
        while proc.returncode is None:
            for task in helpers:
                if task.done() and not task.cancelled() and task.exception():
                    raise task.exception()
            quiet = time.monotonic() - activity
            if config["stall_seconds"] and quiet >= config["stall_seconds"]:
                out.failure_class, out.error = "stall", f"no stdout/stderr bytes for {config['stall_seconds']} seconds"
                await terminate(proc)
                break
            if time.monotonic() - started >= seconds(config["print_timeout"]) + 5:
                out.failure_class, out.error = "timeout", "response timeout exceeded (including CLI exit grace)"
                await terminate(proc)
                break
            await asyncio.sleep(0.05)
        # Child exit is not pipe EOF. Bound descendants holding output open.
        _, pending = await asyncio.wait(helpers, timeout=3)
        if pending:
            await terminate(proc)
            for task in pending:
                task.cancel()
            out.warning = "agy exited but output pipes did not close; remaining process group terminated"
        outcomes = await asyncio.gather(*helpers, return_exceptions=True)
        for item in outcomes:
            if isinstance(item, BaseException) and not isinstance(item, asyncio.CancelledError):
                raise item
        out.returncode = proc.returncode
        if not out.error:
            ok, error = stream.outcome(proc.returncode)
            if not ok:
                out.error = error
                out.failure_class = classify(error + "\n" + out.stderr,
                                             (stream.result or {}).get("status"))
        return out
    except asyncio.CancelledError:
        await terminate(proc)
        raise
    except Exception as exc:
        await terminate(proc)
        out.failure_class = "protocol" if isinstance(exc, CouncilError) else "io"
        out.error = str(exc)
        stream.unsafe_to_replay = True
        out.returncode = proc.returncode
        return out
    finally:
        for task in helpers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*helpers, return_exceptions=True)
        # Sweep an inherited process group even after normal CLI exit.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
        with contextlib.suppress(Exception):
            update(role["id"], process=None, publish=False)


def session_path(config: dict, role: dict) -> Path:
    # Model and capability changes start fresh so independent cross-model checks
    # never silently inherit another model's answer or a writer's tool history.
    key = json.dumps([config["cwd"], config["session_key"], config["task_key"],
                      role["id"], role["model"], profile_name(role["access"])])
    directory = private_dir(state_root() / "sessions", create=True)
    return directory / (digest(key) + ".json")


async def execute_role(binary: str, role: dict, context: str, config: dict,
                       run_dir: Path, semaphore: asyncio.Semaphore, update: Callable) -> dict:
    started = time.monotonic()
    spath = session_path(config, role)
    # Acquire continuity before the fan-out permit: lock waiters don't occupy a slot.
    fd = await acquire(spath.with_suffix(".lock"))
    warnings = []
    try:
        sid = None
        if config["resume"] and spath.exists():
            try:
                saved = read_json(spath)
                candidate = saved.get("conversation_id") if isinstance(saved, dict) else None
                if isinstance(candidate, str) and SESSION_RE.fullmatch(candidate):
                    sid = candidate
                else:
                    warnings.append("saved session invalid; started fresh")
            except CouncilError:
                warnings.append("saved session unreadable; started fresh")
        async with semaphore:
            attempt = None
            count = 0
            retry_note = ""
            while True:
                count += 1
                attempt = await run_attempt(binary, role, context, config, sid, run_dir / "logs",
                                            count, update, run_dir=run_dir, retry_note=retry_note)
                stream = attempt.stream
                if attempt.warning:
                    warnings.append(attempt.warning)
                # Never automatically replay writers or an attempt that used tools.
                retry_safe = role["access"] == "read" and not stream.unsafe_to_replay
                if count < 2 and retry_safe and sid and attempt.failure_class == "stale-session":
                    sid = None
                    with contextlib.suppress(FileNotFoundError):
                        spath.unlink()
                    warnings.append("saved conversation unavailable; restarted fresh, prior continuity lost")
                    continue
                if count < 2 and retry_safe and attempt.failure_class == "transient":
                    update(role["id"], phase="retry-wait")
                    await asyncio.sleep(2)
                    continue
                # If a read-only worker wandered into an outside/forbidden path and
                # headless jetski killed the turn with an empty terminal response,
                # retry once fresh with an explicit path-denial note.
                if (count < 2 and role["access"] == "read" and attempt.failure_class == "permission"
                        and attempt.error == "empty terminal response"):
                    sid = None
                    retry_note = "; ".join(stream.tool_errors) or "outside-workspace read_file auto-denied"
                    warnings.append("headless permission abort on read role; retried once with explicit path boundary")
                    continue
                break
            result = attempt.stream.result or {}
            succeeded = not attempt.error
            new_id = attempt.stream.conversation_id
            if sid and new_id and sid != new_id:
                warnings.append("resume returned a different conversation; continuity lost")
            if succeeded and isinstance(new_id, str) and SESSION_RE.fullmatch(new_id):
                try:
                    write_json(spath, {"conversation_id": new_id, "updated_at": now(), "model": role["model"]})
                except OSError as exc:
                    warnings.append(f"reply retained, but session save failed: {exc}")
            if attempt.stream.tool_errors:
                warnings.append("one or more tool calls failed; inspect coverage gaps and raw logs")
            if re.search(r"(?:soft.denied|permission.{0,40}denied|requires approval)", attempt.stderr, re.I):
                warnings.append("permission denial in stderr; a clean terminal result does not verify the blocked check")
            return {"role_id": role["id"], "label": role["label"], "ok": succeeded,
                    "requested_model": role["model"], "reported_model": (attempt.stream.init or {}).get("model"),
                    "access": role["access"], "profile": profile_name(role["access"]),
                    "selection_reason": role["selection_reason"],
                    "conversation_id": new_id, "attempts": count,
                    "response": result.get("response", ""), "error": attempt.error,
                    "failure_class": attempt.failure_class, "warnings": warnings,
                    "tool_errors": attempt.stream.tool_errors,
                    "usage": result.get("usage"), "usage_scope": "CLI-reported conversation/session cumulative; not per-role billing",
                    "duration_seconds": round(time.monotonic() - started, 3),
                    "returncode": attempt.returncode, "terminal_status": result.get("status"),
                    "stderr_tail": attempt.stderr}
    finally:
        os.close(fd)


def render_role(result: dict) -> str:
    title = str(result["label"]).replace("\n", " ")
    sections = [f"## {title} ({result['role_id']})", "",
                f"Status: {'ok' if result['ok'] else 'failed'}; model sent: `{result['requested_model']}`; "
                f"access: `{result['access']}`.", ""]
    if result.get("error"):
        sections += [f"Failure class: `{result.get('failure_class', 'runtime')}`", "", result["error"], ""]
    for warning in result.get("warnings", []):
        sections += ["Warning: " + warning, ""]
    sections += [str(result.get("response") or ""), ""]
    return "\n".join(sections)


async def fleet(binary: str, roles: list[dict], context: str, config: dict,
                run_dir: Path, update: Callable, event: Callable) -> list[dict]:
    semaphore = asyncio.Semaphore(config["max_parallel"])
    tasks = []

    async def one(role: dict) -> dict:
        try:
            result = await execute_role(binary, role, context, config, run_dir, semaphore, update)
        except asyncio.CancelledError:
            result = {"role_id": role["id"], "label": role["label"], "requested_model": role["model"],
                      "access": role["access"], "ok": False, "error": "cancelled", "failure_class": "cancelled", "response": ""}
        except Exception as exc:
            result = {"role_id": role["id"], "label": role["label"], "requested_model": role["model"],
                      "access": role["access"], "ok": False, "error": f"{type(exc).__name__}: {exc}",
                      "failure_class": "runtime", "response": ""}
        # A reply is durable before its completion event is published.
        write_json(run_dir / "replies" / f"{role['id']}.json", result)
        from council_common import atomic_write
        atomic_write(run_dir / "replies" / f"{role['id']}.md", render_role(result))
        update(role["id"], phase="settled", ok=result["ok"], process=None)
        event("role_completed", role_id=role["id"], ok=result["ok"], reply=f"replies/{role['id']}.md")
        return result

    for role in roles:
        tasks.append(asyncio.create_task(one(role)))
    try:
        # One failing role never cancels unrelated siblings.
        results = await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    for item in results:
        if isinstance(item, BaseException):
            # Storage failure is a supervisor-level fault, but settled sibling
            # files still survive. Never pretend that every reply was persisted.
            raise CouncilError(f"reply persistence failed: {item}")
    return results
