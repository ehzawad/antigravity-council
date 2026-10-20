"""Durable supervisor lifecycle. The kernel lock, not a log line, proves liveness."""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import time

from council_common import (CONTRACT, VERSION, TERMINAL, CouncilError, atomic_write, digest,
                            host_session, intent_capsule, lock_held, locked, now, parse_roles,
                            positive, private_dir, process_identity, project_root, read_json,
                            regular_file, same_process, state_root, strict_json, write_json)
from council_models import discover, store_discovery, validate_models
from council_policy import check_profiles, validate_authority
from council_runtime import acquire, fleet, render_role, seconds

CONFIG_FIELDS = {"schema", "version", "contract", "cwd", "session_key", "task_key", "max_parallel",
                 "stall_seconds", "print_timeout", "heartbeat_seconds", "resume", "writer", "unrestricted_shell"}


def context_template() -> str:
    from council_common import HEADINGS
    return "# Intent Capsule\n\n" + "\n\n".join("## " + h + "\n" for h in HEADINGS) + "\n\n# Current Phase Brief\n"


def prepare(cwd: str, session: str | None, task: str, *, max_parallel: int = 6,
            stall_seconds: float = 1800, print_timeout: str = "60m", heartbeat_seconds: float = 300,
            writer: str | None = None, unrestricted_shell: bool = False, resume: bool = True) -> Path:
    root = project_root(cwd)
    session_key = host_session(session)
    if not task.strip():
        raise CouncilError("task key must be nonempty and stable across rounds of the same task")
    positive(max_parallel, "max_parallel")
    positive(stall_seconds, "stall_seconds", zero=True)
    positive(heartbeat_seconds, "heartbeat_seconds")
    seconds(print_timeout)
    runs = private_dir(state_root() / "runs", create=True)
    run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=runs))
    config = {"schema": 1, "version": VERSION, "contract": CONTRACT, "cwd": str(root),
              "session_key": session_key, "task_key": task,
              "max_parallel": max_parallel, "stall_seconds": stall_seconds,
              "print_timeout": print_timeout, "heartbeat_seconds": heartbeat_seconds,
              "resume": resume, "writer": writer, "unrestricted_shell": unrestricted_shell}
    write_json(run_dir / "launch.json", config)
    write_json(run_dir / "roles.json", [])
    atomic_write(run_dir / "context.md", context_template())
    return run_dir


def config_for(run_dir: Path) -> dict:
    private_dir(run_dir)
    path = run_dir / "launch.json"
    regular_file(path)
    config = read_json(path)
    if not isinstance(config, dict) or set(config) != CONFIG_FIELDS:
        raise CouncilError("launch.json schema mismatch; use prepare from this version")
    if config["schema"] != 1 or config["contract"] != CONTRACT:
        raise CouncilError("stale launch contract; prepare a new directory")
    for name in ("cwd", "session_key", "task_key", "print_timeout"):
        if not isinstance(config[name], str) or not config[name].strip():
            raise CouncilError(f"launch.{name} must be a nonempty string")
    if isinstance(config["max_parallel"], bool) or not isinstance(config["max_parallel"], int):
        raise CouncilError("max_parallel must be an integer")
    positive(config["max_parallel"], "max_parallel")
    positive(config["stall_seconds"], "stall_seconds", zero=True)
    positive(config["heartbeat_seconds"], "heartbeat_seconds")
    seconds(config["print_timeout"])
    for name in ("resume", "unrestricted_shell"):
        if not isinstance(config[name], bool):
            raise CouncilError(f"launch.{name} must be boolean")
    if config["writer"] is not None and not isinstance(config["writer"], str):
        raise CouncilError("writer must be null or a role ID")
    root = project_root(config["cwd"])
    if str(root) != config["cwd"]:
        raise CouncilError("workspace changed or is no longer the same canonical project root")
    if state_root().is_relative_to(root) or root.is_relative_to(state_root()):
        raise CouncilError("workspace and council state directory must not overlap")
    return config


def load_inputs(run_dir: Path) -> tuple[dict, list[dict], str, str]:
    config = config_for(run_dir)
    for name in ("roles.json", "context.md"):
        regular_file(run_dir / name)
    roles = parse_roles(read_json(run_dir / "roles.json"))
    context = (run_dir / "context.md").read_text(encoding="utf-8")
    capsule = intent_capsule(context)
    context.encode("utf-8")
    validate_authority(roles, config["writer"], config["unrestricted_shell"])
    check_profiles(Path(config["cwd"]), roles)
    return config, roles, context, capsule


def task_path(config: dict) -> Path:
    tasks = private_dir(state_root() / "tasks", create=True)
    key = json.dumps([config["cwd"], config["session_key"], config["task_key"]])
    return tasks / (digest(key) + ".json")


def check_intent(config: dict, capsule: str, *, bind: bool = False) -> None:
    path = task_path(config)
    with locked(path.with_suffix(".lock")):
        if path.exists():
            prior = read_json(path)
            if not isinstance(prior, dict) or prior.get("capsule_sha256") != digest(capsule):
                raise CouncilError("Intent Capsule changed for this task key; preserve it across rounds, "
                                   "or use a new task key for a genuinely revised user request")
        elif bind:
            write_json(path, {"capsule_sha256": digest(capsule), "created_at": now()})


def unused(run_dir: Path) -> None:
    for name in ("supervisor.lock", "supervisor.json", "state.json", "events.ndjson", "replies", "logs", "err.log"):
        if (run_dir / name).exists() or (run_dir / name).is_symlink():
            raise CouncilError("one launch per directory; prepare a new run rather than overwriting evidence")


def input_hash(config: dict, roles: list[dict], context: str) -> str:
    return digest(json.dumps([config, roles, context], sort_keys=True, ensure_ascii=True))


def check(run_dir: Path, *, refresh: bool = False) -> dict:
    unused(run_dir)
    config, roles, context, capsule = load_inputs(run_dir)
    snapshot_path = run_dir / "model-snapshot.json"
    if refresh or not snapshot_path.exists():
        snapshot = store_discovery(run_dir, Path(config["cwd"]))
    else:
        regular_file(snapshot_path)
        snapshot = read_json(snapshot_path)
    if not isinstance(snapshot, dict) or snapshot.get("schema") != 1 or not isinstance(snapshot.get("models"), list):
        raise CouncilError("invalid discovery snapshot; run discover again")
    validate_models(roles, snapshot)
    flags = set(snapshot.get("flags", []))
    if any(r["access"] != "read" for r in roles) and "--mode" not in flags:
        raise CouncilError("installed CLI does not advertise --mode for authorized writers")
    if any(r["access"] == "execute" for r in roles) and "--sandbox" not in flags:
        raise CouncilError("execute roles require the documented --sandbox flag")
    if config["unrestricted_shell"] and "--dangerously-skip-permissions" not in flags:
        raise CouncilError("CLI lacks explicit permission-bypass flag; refusing to guess")
    check_intent(config, capsule)
    return {"ok": True, "version": VERSION, "run_dir": str(run_dir),
            "input_hash": input_hash(config, roles, context), "binary": snapshot["binary"],
            "models": [{"role": r["id"], "model": r["model"], "access": r["access"]} for r in roles],
            "writer": config["writer"], "unrestricted_shell": config["unrestricted_shell"],
            "entitlement_verified": False, "snapshot_id": snapshot["snapshot_id"]}


class Tracker:
    def __init__(self, run_dir: Path, roles: list[dict]):
        self.run_dir = run_dir
        self.sequence = 0
        self.state = {"schema": 1, "run_id": run_dir.name, "version": VERSION, "status": "running",
                      "started_at": now(), "updated_at": now(), "roles": {
                          r["id"]: {"phase": "queued", "model": r["model"], "access": r["access"], "process": None}
                          for r in roles}}
        self.publish()

    def publish(self) -> None:
        self.state["updated_at"] = now()
        self.state["heartbeat_epoch"] = time.time()
        write_json(self.run_dir / "state.json", self.state)

    def update(self, rid: str, *, publish: bool = True, **fields) -> None:
        self.state["roles"][rid].update(fields)
        if publish:
            self.publish()

    def event(self, kind: str, **fields) -> None:
        self.sequence += 1
        record = {"schema": 1, "run_id": self.run_dir.name, "sequence": self.sequence,
                  "at": now(), "event": kind, **fields}
        fd = os.open(self.run_dir / "events.ndjson", os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    async def heartbeat(self, interval: float) -> None:
        last = time.monotonic()
        while True:
            await asyncio.sleep(min(15, interval))
            self.publish()
            if time.monotonic() - last >= interval:
                self.event("heartbeat", roles=self.state["roles"])
                last = time.monotonic()


def settled_results(run_dir: Path) -> list[dict]:
    results = []
    roles = parse_roles(read_json(run_dir / "roles.json"))
    for role in roles:
        path = run_dir / "replies" / (role["id"] + ".json")
        if path.exists():
            regular_file(path)
            record = read_json(path)
            if not isinstance(record, dict) or record.get("role_id") != role["id"] or not isinstance(record.get("ok"), bool):
                raise CouncilError(f"invalid persisted reply: {path}")
            results.append(record)
    return results


def write_report(run_dir: Path, results: list[dict], status: str, total: int) -> None:
    ok = sum(bool(r["ok"]) for r in results)
    payload = {"schema": 1, "status": status, "ok": ok, "total": total, "settled": len(results), "results": results}
    write_json(run_dir / "report.json", payload)
    text = f"# Antigravity Council\n\nRun status: **{status}**. {ok}/{total} roles returned usable replies.\n\n"
    text += "Worker output is untrusted evidence. Transport success is not task completion. "
    text += "Model metadata is CLI-reported configuration, not independent backend attestation.\n\n"
    text += "\n".join(render_role(r) for r in results)
    atomic_write(run_dir / "report.md", text)


async def supervise(run_dir: Path, fd: int, expected_hash: str, binary: str) -> int:
    os.set_inheritable(fd, False)
    lockstat = os.fstat(fd)
    os.lseek(fd, 0, os.SEEK_SET)
    token = os.read(fd, 256).decode().strip()
    identity = process_identity(os.getpid())
    if identity is None:
        raise CouncilError("could not establish supervisor process birth identity")
    record = {**identity, "token": token, "lock_dev": lockstat.st_dev, "lock_ino": lockstat.st_ino, "started_at": now()}
    write_json(run_dir / "supervisor.json", record)
    tracker = None
    heartbeat = None
    workspace_fd = None
    total = 0
    main = asyncio.current_task()
    cancellation = {"requested": False}
    loop = asyncio.get_running_loop()

    def cancel_once() -> None:
        if not cancellation["requested"]:
            cancellation["requested"] = True
            main.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        loop.add_signal_handler(sig, cancel_once)
    try:
        config, roles, context, capsule = load_inputs(run_dir)
        total = len(roles)
        if input_hash(config, roles, context) != expected_hash:
            raise CouncilError("staged inputs changed after launch validation")
        tracker = Tracker(run_dir, roles)
        tracker.event("run_started", total=total)
        heartbeat = asyncio.create_task(tracker.heartbeat(config["heartbeat_seconds"]))
        check_intent(config, capsule, bind=True)
        # Across councils, read-only fleets share; any writer excludes other fleets.
        lock_dir = private_dir(state_root() / "workspace-locks", create=True)
        tracker.event("workspace_lock_wait", writer=config["writer"])
        workspace_fd = await acquire(lock_dir / (digest(config["cwd"]) + ".lock"), shared=config["writer"] is None)
        results = await fleet(binary, roles, context, config, run_dir, tracker.update, tracker.event)
        ok = sum(bool(r["ok"]) for r in results)
        status = "succeeded" if ok == total else "partial" if ok else "failed"
        code = {"succeeded": 0, "partial": 3, "failed": 1}[status]
        write_report(run_dir, results, status, total)
    except asyncio.CancelledError:
        status, code = "cancelled", 130
        results = settled_results(run_dir)
        write_report(run_dir, results, status, total or len(results))
    except Exception as exc:
        status, code = "failed", 1
        atomic_write(run_dir / "supervisor-error.txt", f"{type(exc).__name__}: {exc}\n")
        with contextlib.suppress(Exception):
            results = settled_results(run_dir)
            write_report(run_dir, results, status, total or len(results))
    finally:
        if heartbeat:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError, OSError):
                await heartbeat
        if workspace_fd is not None:
            os.close(workspace_fd)
    if tracker is not None:
        tracker.state.update(status=status, exit_code=code, finished_at=now())
        tracker.publish()
        tracker.event("run_completed", status=status, exit_code=code)
    else:
        write_json(run_dir / "state.json", {"schema": 1, "status": status, "exit_code": code,
                                           "roles": {}, "finished_at": now()})
    # This marker is useful telemetry, not the controller's completion condition.
    print(f"ANTIGRAVITY_COUNCIL_DONE status={status} exit={code}", flush=True)
    return code


def claim(run_dir: Path, plan: dict) -> int:
    unused(run_dir)
    fd = os.open(run_dir / "supervisor.lock", os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(fd, (secrets.token_hex(16) + "\n").encode())
        os.fsync(fd)
        private_dir(run_dir / "logs", create=True)
        private_dir(run_dir / "replies", create=True)
        write_json(run_dir / "launch-plan.json", plan)
        return fd
    except BaseException:
        os.close(fd)
        raise


def start(run_dir: Path, *, attached: bool = False) -> dict | int:
    # The launch always refreshes models and checks local capabilities, even after check.
    plan = check(run_dir, refresh=True)
    fd = claim(run_dir, plan)
    if attached:
        try:
            return asyncio.run(supervise(run_dir, fd, plan["input_hash"], plan["binary"]))
        finally:
            os.close(fd)
    script = Path(__file__).parent / "antigravity_council.py"
    log_fd = os.open(run_dir / "err.log", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(log_fd, "wb", buffering=0) as log:
            proc = subprocess.Popen([sys.executable, str(script), "_supervise", str(run_dir),
                                     "--lock-fd", str(fd), "--input-hash", plan["input_hash"],
                                     "--binary", plan["binary"]],
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                    start_new_session=True, close_fds=True, pass_fds=(fd,))
    finally:
        # Child retains the lifetime lock. Never unlink a used lock file.
        os.close(fd)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if (run_dir / "supervisor.json").exists():
            return {"started": True, "run_dir": str(run_dir), "pid": proc.pid,
                    "follow": [sys.executable, str(script), "follow", str(run_dir)],
                    "status": [sys.executable, str(script), "status", str(run_dir)],
                    "cancel": [sys.executable, str(script), "cancel", str(run_dir)]}
        if proc.poll() is not None:
            raise CouncilError(f"supervisor exited during startup; inspect {run_dir / 'err.log'}")
        time.sleep(0.05)
    raise CouncilError(f"supervisor readiness not confirmed; inspect status and err.log in {run_dir}; do not relaunch it")


def identity_state(record: dict) -> str:
    current = process_identity(record.get("pid"))
    if current is not None:
        return "alive" if same_process(record) else "reused"
    pid = record.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return "unknown"
    try:
        stat_path = Path(f"/proc/{pid}/stat")
        if stat_path.exists():
            text = stat_path.read_text()
            if text[text.rfind(")") + 2:].split()[0] == "Z":
                return "gone"
        os.kill(pid, 0)
    except ProcessLookupError:
        return "gone"
    except (OSError, IndexError):
        return "unknown"
    return "unknown"


def observe(run_dir: Path) -> dict:
    private_dir(run_dir)
    state = read_json(run_dir / "state.json") if (run_dir / "state.json").exists() else {}
    held = lock_held(run_dir / "supervisor.lock")
    record_path = run_dir / "supervisor.json"
    if not record_path.exists():
        return {"lifecycle": "starting" if held else "not-started" if held is None else "aborted",
                "run_status": state.get("status"), "state": state}
    regular_file(record_path, private=True)
    record = read_json(record_path)
    if not isinstance(record, dict):
        raise CouncilError("invalid supervisor identity")
    path = run_dir / "supervisor.lock"
    regular_file(path, private=True)
    st = path.stat()
    if st.st_dev != record.get("lock_dev") or st.st_ino != record.get("lock_ino") or path.read_text().strip() != record.get("token"):
        return {"lifecycle": "unknown", "reason": "supervisor lock identity changed", "state": state}
    identity = identity_state(record)
    if held is True and identity == "alive":
        life = "finishing" if state.get("status") in TERMINAL else "running"
    elif held is False and identity in {"gone", "reused"}:
        life = "done" if state.get("status") in TERMINAL else "aborted"
    else:
        life = "unknown"
    return {"lifecycle": life, "run_status": state.get("status"), "exit_code": state.get("exit_code"),
            "lock_held": held, "identity": identity, "supervisor": record, "state": state}


def follow(run_dir: Path, after: int = 0, timeout: float = 0) -> int:
    start_at = time.monotonic()
    cursor = 0
    pending = b""
    last_heartbeat = 0.0
    unknown_since = None
    while True:
        path = run_dir / "events.ndjson"
        if path.exists():
            regular_file(path)
            with path.open("rb") as handle:
                handle.seek(cursor)
                chunk = handle.read()
                cursor += len(chunk)
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                event = strict_json(line.decode("utf-8"))
                if (not isinstance(event, dict) or event.get("run_id") != run_dir.name
                        or not isinstance(event.get("sequence"), int)):
                    raise CouncilError("invalid journal record")
                if event["sequence"] > after:
                    print(json.dumps(event, ensure_ascii=True), flush=True)
                    after = event["sequence"]
        view = observe(run_dir)
        if view["lifecycle"] in {"done", "aborted", "not-started"}:
            # One final drain closes the event/write versus status-observation race.
            if path.exists() and path.stat().st_size > cursor:
                continue
            print(json.dumps({"event": "verified_end", "lifecycle": view["lifecycle"],
                              "run_status": view.get("run_status"), "exit_code": view.get("exit_code")}), flush=True)
            return int(view.get("exit_code") if view.get("exit_code") is not None else 1)
        if view["lifecycle"] == "unknown":
            # Lock and process observations are not one atomic snapshot. In normal
            # shutdown, the lock can be released a moment before the process exits,
            # or the process can exit between the two probes. Reobserve briefly;
            # never promote this inconsistent snapshot to completion or signal it.
            if unknown_since is None:
                unknown_since = time.monotonic()
            elif time.monotonic() - unknown_since >= 2:
                print(json.dumps({"event": "liveness_unknown", "run_dir": str(run_dir)}), flush=True)
                return 4
        else:
            unknown_since = None
        epoch = view.get("state", {}).get("heartbeat_epoch")
        if isinstance(epoch, (int, float)) and time.time() - epoch > 120 and time.monotonic() - last_heartbeat > 120:
            print(json.dumps({"event": "supervisor_tick_stale", "age_seconds": time.time() - epoch}), flush=True)
            last_heartbeat = time.monotonic()
        if timeout and time.monotonic() - start_at >= timeout:
            print(json.dumps({"event": "follow_detached", "after_sequence": after,
                              "note": "follower stopped; council is still running"}), flush=True)
            return 5
        time.sleep(0.15)


def cancel(run_dir: Path, *, force: bool = False) -> dict:
    view = observe(run_dir)
    if view["lifecycle"] in {"done", "aborted", "not-started"}:
        return view
    if view["lifecycle"] not in {"running", "finishing"} or not same_process(view["supervisor"]):
        raise CouncilError("cannot verify live supervisor identity; refusing to signal an unverified PID")
    os.kill(view["supervisor"]["pid"], signal.SIGTERM)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        current = observe(run_dir)
        if current["lifecycle"] in {"done", "aborted"}:
            return current
        time.sleep(0.1)
    if force and same_process(view["supervisor"]):
        os.kill(view["supervisor"]["pid"], signal.SIGKILL)
        time.sleep(0.2)
        return {"forced": True, "status": observe(run_dir), "next": "reap recorded surviving workers"}
    raise CouncilError("supervisor still present after SIGTERM; inspect status or explicitly use cancel --force")


def reap(run_dir: Path) -> dict:
    view = observe(run_dir)
    if view["lifecycle"] not in {"done", "aborted"}:
        raise CouncilError("reap requires a verified-ended supervisor")
    reaped, unverified = [], []
    for rid, row in view.get("state", {}).get("roles", {}).items():
        proc = row.get("process")
        if not isinstance(proc, dict):
            continue
        if not same_process(proc) or proc.get("pid") != proc.get("pgid"):
            unverified.append(rid)
            continue
        os.killpg(proc["pgid"], signal.SIGTERM)
        time.sleep(0.2)
        if same_process(proc):
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc["pgid"], signal.SIGKILL)
        reaped.append(rid)
    return {"reaped": reaped, "unverified_or_already_gone": unverified,
            "limit": "process groups only; independently daemonized tools may escape"}


def collect(run_dir: Path, *, partial: bool = False) -> dict:
    view = observe(run_dir)
    if not partial and view["lifecycle"] not in {"done", "aborted"}:
        raise CouncilError("run is not verified ended; use collect --partial only for intermediate evidence")
    results = settled_results(run_dir)
    total = len(parse_roles(read_json(run_dir / "roles.json")))
    return {"lifecycle": view["lifecycle"], "run_status": view.get("run_status"),
            "settled": len(results), "total": total, "results": results,
            "complete_transport": view["lifecycle"] == "done" and len(results) == total,
            "task_verified": False}
