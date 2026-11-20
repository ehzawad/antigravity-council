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
import subprocess
import time
from typing import Callable

from council_common import (CouncilError, SESSION_RE, digest, now, open_lock, private_dir,
                            read_json, state_root, write_json)
from council_models import provider_check
from council_policy import EXECUTE_SETTINGS, FULL_SETTINGS, check_profiles, profile_name
from council_protocol import Stream, classify, user_message


class Attempt:
    def __init__(self, stream: Stream):
        self.stream = stream
        self.error = ""
        self.failure_class = ""
        self.stderr = ""
        self.returncode: int | None = None
        self.warning: str | None = None


# Process-watchdog slack beyond turns x --print-timeout, for CLI startup and exit.
WATCHDOG_GRACE = 5.0


def seconds(duration: str) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)([smh])", duration)
    if not match:
        raise CouncilError("print timeout must be a positive integer duration, e.g. 60m or 2h")
    return int(match[1]) * {"s": 1, "m": 60, "h": 3600}[match[2]]


INTERNAL_RUN_FILES = {"launch.json", "launch-plan.json", "model-snapshot.json", "supervisor.lock",
                      "supervisor.json", "state.json", "events.ndjson", "err.log", "report.json",
                      "report.md", "supervisor-error.txt", "logs", "replies"}


def turns_for(role: dict, config: dict) -> int:
    """Full roles get a forced self-verification turn on the same conversation unless the
    run was prepared with --no-verify-turn."""
    return 2 if role["access"] == "full" and config.get("verify_turn", True) else 1


def argv_for(binary: str, role: dict, config: dict, sid: str | None, run_dir: Path | None = None) -> list[str]:
    # agy applies --print-timeout per turn, not per process (observed on 1.5.12: a second
    # turn finished after the limit had passed for the process as a whole). The runner's
    # own process watchdog allows turns x timeout.
    args = [binary, "--input-format", "stream-json", "--output-format", "stream-json",
            "--agent", profile_name(role["access"]),
            "--print-timeout", config["print_timeout"]]
    if "model" in role:
        args += ["--model", role["model"]]
    if "effort" in role:
        args += ["--effort", role["effort"]]
    # Restricted roles need the run directory as a second workspace root to read staged
    # files. Full roles read anywhere, and in agy 1.5.12 an added root became the default
    # command working directory (observed live), so they get no --add-dir.
    if run_dir is not None and role["access"] != "full":
        args += ["--add-dir", str(run_dir)]
    # Do not use -p with --input-format stream-json. Prompts belong on stdin.
    if sid:
        if not SESSION_RE.fullmatch(sid):
            raise CouncilError("saved conversation ID has invalid syntax")
        args += ["--conversation", sid]
    if role["access"] == "full":
        # Full access: approve every tool, no terminal sandbox, reads/edits anywhere.
        # An explicit --mode displaces agy's headless Auto default, which would turn
        # the terminal sandbox (and its no-network rule) back on.
        args += ["--dangerously-skip-permissions", "--mode", "accept-edits",
                 "--settings", json.dumps(FULL_SETTINGS, separators=(",", ":"), sort_keys=True)]
        return args
    if role["access"] != "read":
        args += ["--mode", "accept-edits"]
    if role["access"] == "execute":
        args += ["--sandbox"]
        if config.get("unrestricted_shell"):
            args += ["--dangerously-skip-permissions"]
        else:
            args += ["--settings", json.dumps(EXECUTE_SETTINGS, separators=(",", ":"), sort_keys=True)]
    return args


VERIFY_PROMPT = """SELF-VERIFICATION TURN. Your previous answer does not reach the host until you finish this turn.
Treat your previous answer as a draft written by someone else, not as fact.
1. List every consequential claim you made and every change you made.
2. Re-check each one against live evidence now: re-read the cited files and lines, inspect the
   actual diff, and re-run the relevant tests or commands. Do not rely on memory of earlier output.
3. Use your own harness to try to refute your most consequential claims: invoke subagents and brief
   them with the claim and its evidence only, not your conclusion, and ask them to find what is wrong.
4. Before accepting any refutation, check that the defect it cites actually exists; reviewers can
   invent problems.
5. Fix what is wrong where the Intent Capsule allows changes, and re-run the checks; if it forbids
   edits, correct your report instead. Retract what you cannot support, and repeat the check until
   it finds nothing new.
6. Be honest: an acknowledged error or an openly unverified claim is better than a false claim of
   correctness.
Then write your complete final report. It replaces your previous answer, so include everything the
host needs: findings with exact evidence (paths/lines or commands and observed results), changed files,
checks actually run and their results, what you verified, what you corrected or retracted in this turn,
what remains unverified, risks, and unknowns."""


def staged_files(run_dir: Path | None) -> list[str]:
    if run_dir is None or not run_dir.is_dir():
        return []
    return sorted(p.name for p in run_dir.iterdir() if p.name not in INTERNAL_RUN_FILES)


def prompt_for(role: dict, context: str, *, cwd: Path | None = None,
               run_dir: Path | None = None, retry_note: str = "") -> str:
    access = {
        "full": ("Full access: you may read and edit any file, run any command, and use the network and web "
                 "search without approval prompts. Other workers in this council may edit the same worktree "
                 "concurrently; re-read files before editing and keep changes scoped to your deliverable."),
        "read": "Read-only investigation. No file writes, shell execution, browser actuation, or MCP calls.",
        "write": ("You are the only restricted file writer in this council; full-access workers, if any, may also "
                  "edit files. Shell execution is unavailable."),
        "execute": ("You are the only restricted writer/executor in this council; full-access workers, if any, may "
                    "also edit files. Use only the granted workspace and tool permissions."),
    }[role["access"]]
    lens = "\n".join(role["instruction"])
    harness = ("Use your full agy harness (subagents, workflows, your MCP servers) as the task needs, but do not "
               "change your model or contact sibling council workers; your self-verification turn follows."
               if role["access"] == "full" else
               "Do not spawn subagents, change your model, expand permissions, or contact sibling workers.")
    paths_block = ""
    if role["access"] == "full" and (cwd is not None or run_dir is not None):
        staged = ", ".join(staged_files(run_dir)) or "context.md, roles.json"
        paths_block = (
            f"\nPATHS\n"
            f"- Workspace root (cwd): {cwd}\n"
            f"- Run directory (host-staged files): {run_dir}\n"
            f"- Staged files in run directory: {staged}\n"
            f"You may read any path on this machine that the task needs. Treat the run directory as host-owned: "
            f"read it, but do not edit or delete its files. Run shell commands from the workspace root; if a "
            f"command starts anywhere else, cd to the workspace root first.\n"
        )
    elif cwd is not None or run_dir is not None:
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
{harness}
Do not commit, push, deploy, publish, or mutate external services unless the capsule asks for it. Do not ask questions in this noninteractive
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


def descendant_groups(pid: int) -> set[int]:
    """Process groups of every live descendant of pid, excluding our own.

    agy 1.5.12 starts each run_command in its own process group (observed live: a
    cancelled worker's shell command survived a killpg of agy's group), so cleanup
    must find them while agy is still their ancestor.
    """
    try:
        listing = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "pgid="],
                                 capture_output=True, text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    children: dict[int, list[tuple[int, int]]] = {}
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) == 3 and all(f.isdigit() for f in fields):
            child, parent, group = map(int, fields)
            children.setdefault(parent, []).append((child, group))
    groups, stack, seen = set(), [pid], {pid}
    while stack:
        for child, group in children.get(stack.pop(), []):
            if child not in seen:
                seen.add(child)
                groups.add(group)
                stack.append(child)
    groups.discard(os.getpgrp())
    groups.discard(pid)
    return groups


async def terminate(proc: asyncio.subprocess.Process) -> None:
    """Best effort for this attempt's process group, its descendants' groups, and
    pipe-holding children.

    A tool that double-forks into an unrelated session before cleanup runs can still
    escape. Do not call this function an OS containment boundary or an exactly-once
    guarantee.
    """
    extra = descendant_groups(proc.pid) if proc.returncode is None else set()
    for sig in (signal.SIGTERM, signal.SIGKILL):
        # macOS reports EPERM, not ESRCH, for a group whose leader just exited.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, sig)
        for group in extra:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(group, sig)
        if sig == signal.SIGTERM:
            await asyncio.sleep(0.25)
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(proc.wait(), 2)


async def run_attempt(binary: str, role: dict, context: str, config: dict, sid: str | None,
                      log_dir: Path, attempt_number: int, update: Callable,
                      run_dir: Path | None = None, retry_note: str = "", live: dict | None = None) -> Attempt:
    stream = Stream(role)
    if live is not None:
        live["stream"] = stream  # lets a cancelled fleet keep what this attempt already produced
    out = Attempt(stream)
    root = Path(config["cwd"])
    turns = turns_for(role, config)
    # Encoding and config validation happen before spawning a child.
    payloads = [user_message(prompt_for(role, context, cwd=root, run_dir=run_dir, retry_note=retry_note))]
    if turns > 1:
        payloads.append(user_message(VERIFY_PROMPT))
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
    turn_settled = asyncio.Event()
    stdout_closed = False

    async def pump(pipe: asyncio.StreamReader, path: Path, parse: bool) -> None:
        nonlocal activity, stdout_closed
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
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
                        before = len(stream.results)
                        stream.feed(chunk)
                        if len(stream.results) != before:
                            turn_settled.set()
                    else:
                        out.stderr = (out.stderr + chunk.decode("utf-8", "replace"))[-8192:]
                if parse:
                    stream.finish()
        finally:
            if parse:
                stdout_closed = True
                turn_settled.set()

    async def feed() -> None:
        try:
            proc.stdin.write(payloads[0])
            await proc.stdin.drain()
            # The verify turn goes on the same stream only after turn one succeeded;
            # agy runs one turn per stdin message and exits when stdin closes.
            for index, payload in enumerate(payloads[1:], start=1):
                while len(stream.results) < index and not stdout_closed:
                    await turn_settled.wait()
                    turn_settled.clear()
                last = stream.results[-1] if len(stream.results) >= index else None
                # Never continue a stream that ended or failed its audit.
                if (stdout_closed or stream.init is None or last is None or last.get("status") != "SUCCESS" or last.get("error")
                        or not str(last.get("response") or "").strip()):
                    break
                stream.prompts_sent += 1
                update(role["id"], phase="self-verifying")
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
            if time.monotonic() - started >= seconds(config["print_timeout"]) * turns + WATCHDOG_GRACE:
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
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGTERM)
        with contextlib.suppress(Exception):
            update(role["id"], process=None, publish=False)


def session_path(config: dict, role: dict) -> Path:
    # Model and capability changes start fresh so independent cross-model checks
    # never silently inherit another model's answer or a writer's tool history.
    parts = [config["cwd"], config["session_key"], config["task_key"],
             role["id"], role.get("model"), profile_name(role["access"])]
    if "effort" in role:
        parts.append(role["effort"])
    key = json.dumps(parts)
    directory = private_dir(state_root() / "sessions", create=True)
    return directory / (digest(key) + ".json")


async def execute_role(binary: str, role: dict, context: str, config: dict,
                       run_dir: Path, semaphore: asyncio.Semaphore, update: Callable,
                       live: dict | None = None) -> dict:
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
                                            count, update, run_dir=run_dir, retry_note=retry_note, live=live)
                stream = attempt.stream
                if attempt.warning:
                    warnings.append(attempt.warning)
                # Never automatically replay writers or an attempt that used tools,
                # with one documented exception below for read-only permission aborts.
                retry_safe = role["access"] == "read" and not stream.unsafe_to_replay
                if sid and attempt.failure_class == "stale-session":
                    # A dead pointer would fail every later run the same way, whatever the role.
                    with contextlib.suppress(FileNotFoundError):
                        spath.unlink()
                # The conversation never started, so even a full role has done nothing yet.
                if count < 2 and sid and attempt.failure_class == "stale-session" and not stream.unsafe_to_replay:
                    sid = None
                    with contextlib.suppress(FileNotFoundError):
                        spath.unlink()
                    warnings.append("saved conversation unavailable; restarted fresh, prior continuity lost")
                    continue
                if count < 2 and retry_safe and attempt.failure_class == "transient":
                    update(role["id"], phase="retry-wait")
                    await asyncio.sleep(2)
                    continue
                # Exception: a read-only worker that wandered outside the granted
                # directories gets its turn killed by headless mode with an empty
                # response. Its profile has only read/search tools, so replaying it
                # cannot repeat a side effect even though tool steps were observed.
                # Retry once fresh with an explicit path-denial note.
                if (count < 2 and role["access"] == "read" and attempt.failure_class == "permission"
                        and attempt.error == "empty terminal response"):
                    if sid:
                        warnings.append("permission retry started a fresh conversation; prior continuity lost")
                    sid = None
                    retry_note = "; ".join(stream.tool_errors) or "outside-workspace read_file auto-denied"
                    warnings.append("headless permission abort on read role; retried once with explicit path boundary")
                    continue
                break
            result = attempt.stream.result or {}
            succeeded = not attempt.error
            results = attempt.stream.results
            verify = turns_for(role, config) > 1
            draft = results[0].get("response", "") if verify and results else None
            response = result.get("response", "")
            def usable(item: dict) -> bool:
                return bool(item.get("status") == "SUCCESS" and not item.get("error")
                            and str(item.get("response") or "").strip())
            verified = bool(verify and len(results) >= 2 and usable(results[1]))
            if verify and not succeeded:
                if verified:
                    response = results[1].get("response", "")
                    warnings.append("verification turn completed, but the attempt failed afterwards; "
                                    "the response is the verified final report")
                elif results and usable(results[0]):
                    response = draft or ""
                    warnings.append("verification turn did not complete; the response is the unverified first-turn draft")
            new_id = attempt.stream.conversation_id
            if sid and new_id and sid != new_id:
                warnings.append("resume returned a different conversation; continuity lost")
            if succeeded and isinstance(new_id, str) and SESSION_RE.fullmatch(new_id):
                try:
                    write_json(spath, {"conversation_id": new_id, "updated_at": now(), "model": role.get("model")})
                except OSError as exc:
                    warnings.append(f"reply retained, but session save failed: {exc}")
            warnings.extend(attempt.stream.audit_warnings)
            if attempt.stream.tool_errors:
                warnings.append("one or more tool calls failed; inspect coverage gaps and raw logs")
            denied = []
            for item in results:  # every turn, not only the last
                value = item.get("denied_actions")
                denied.extend(value if isinstance(value, list) else [value] if value else [])
            denied = denied or None
            if denied:
                warnings.append("agy denied actions (deny rule, system denylist, or admin policy); those checks did not run: "
                                + json.dumps(denied, ensure_ascii=True)[:2000])
            if re.search(r"(?:soft.denied|permission.{0,40}denied|requires approval)", attempt.stderr, re.I):
                warnings.append("permission denial in stderr; a clean terminal result does not verify the blocked check")
            return {"role_id": role["id"], "label": role["label"], "ok": succeeded,
                    "requested_model": role.get("model"), "reported_model": (attempt.stream.init or {}).get("model"),
                    "access": role["access"], "profile": profile_name(role["access"]),
                    "effort": role.get("effort"),
                    "permission_mode": (attempt.stream.init or {}).get("permission_mode"),
                    "denied_actions": denied,
                    "selection_reason": role["selection_reason"],
                    "conversation_id": new_id, "attempts": count,
                    "response": response, "error": attempt.error,
                    # completed | failed (turn one was usable, the check was not) | not-run (turn one
                    # failed, so no check was sent) | off (--no-verify-turn) | None (restricted role)
                    "verification_turn": ("completed" if verified else
                                          ("failed" if results and usable(results[0]) else "not-run") if verify else
                                          "off" if role["access"] == "full" else None),
                    "verification_turn_completed": verified,
                    "turns_completed": sum(r.get("status") == "SUCCESS" for r in results),
                    "draft_response": draft if verify else None,
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
                f"Status: {'ok' if result['ok'] else 'failed'}; model sent: `{result['requested_model'] or 'agy default (not pinned)'}`; "
                f"access: `{result['access']}`" + (f"; effort: `{result['effort']}`" if result.get("effort") else "")
                + (f"; verification turn: {result['verification_turn']}" if result.get("verification_turn") else "") + ".", ""]
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
        live: dict = {}
        try:
            result = await execute_role(binary, role, context, config, run_dir, semaphore, update, live)
        except asyncio.CancelledError:
            result = {"role_id": role["id"], "label": role["label"], "requested_model": role.get("model"),
                      "access": role["access"], "ok": False, "error": "cancelled", "failure_class": "cancelled", "response": ""}
            # Keep a finished turn-one draft when the cancel lands during the verification turn.
            stream = live.get("stream")
            first = stream.results[0] if stream is not None and stream.results else None
            if (first and first.get("status") == "SUCCESS" and not first.get("error")
                    and str(first.get("response") or "").strip()):
                result.update(response=first["response"], draft_response=first["response"],
                              verification_turn="failed" if turns_for(role, config) > 1 else None,
                              verification_turn_completed=False, turns_completed=1,
                              conversation_id=stream.conversation_id,
                              warnings=["cancelled after turn one; the response is the unverified first-turn draft"])
        except Exception as exc:
            result = {"role_id": role["id"], "label": role["label"], "requested_model": role.get("model"),
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
