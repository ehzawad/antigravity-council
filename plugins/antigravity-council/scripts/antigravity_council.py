#!/usr/bin/env python3
"""Host-driven Argon/Opus council for Claude Code. No external Python dependencies."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from council_common import CONTRACT, VERSION, CouncilError, positive, private_dir
from council_lifecycle import (cancel, check, collect, config_for, follow, observe, prepare,
                               reap, start, supervise, unused)
from council_models import discover, store_discovery
from council_policy import setup_profiles


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--version", action="version", version=VERSION)
    p.add_argument("--skill-contract", type=int, default=CONTRACT)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="install capability profiles without altering settings or auth")
    d = sub.add_parser("doctor", help="metadata-only local contract and preferred-model discovery")
    d.add_argument("--cwd", default=os.getcwd())
    prep = sub.add_parser("prepare", help="create a private durable run directory")
    prep.add_argument("--cwd", default=os.getcwd())
    prep.add_argument("--session-key")
    prep.add_argument("--task-key", required=True)
    prep.add_argument("--max-parallel", type=int, default=6)
    prep.add_argument("--stall-seconds", type=float, default=1800)
    prep.add_argument("--print-timeout", default="60m")
    prep.add_argument("--heartbeat-seconds", type=float, default=300)
    prep.add_argument("--writer")
    prep.add_argument("--unrestricted-shell", action="store_true")
    prep.add_argument("--no-resume", action="store_true")
    for name in ("discover", "check", "start", "run", "status", "collect", "follow", "cancel", "reap"):
        cmd = sub.add_parser(name)
        cmd.add_argument("run_dir", type=Path)
        if name == "follow":
            cmd.add_argument("--after-sequence", type=int, default=0)
            cmd.add_argument("--timeout", type=float, default=0,
                             help="limit follower lifetime only; leaves council running")
        if name == "collect":
            cmd.add_argument("--partial", action="store_true")
        if name == "cancel":
            cmd.add_argument("--force", action="store_true")
    internal = sub.add_parser("_supervise", help=argparse.SUPPRESS)
    internal.add_argument("run_dir", type=Path)
    internal.add_argument("--lock-fd", required=True, type=int)
    internal.add_argument("--input-hash", required=True)
    internal.add_argument("--binary", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    p = parser()
    ns = p.parse_args(argv)
    if ns.skill_contract != CONTRACT:
        p.error("skill/runtime contract mismatch; update the plugin, do not bypass the handshake")
    if os.name != "posix":
        p.error("this runner supports macOS and Linux; use a POSIX host")
    try:
        cmd = ns.command
        if cmd == "setup":
            answer = {"profiles": setup_profiles(), "settings_changed": False, "credentials_changed": False}
        elif cmd == "doctor":
            from council_common import project_root
            answer = discover(project_root(ns.cwd))
        elif cmd == "prepare":
            run_dir = prepare(ns.cwd, ns.session_key, ns.task_key, max_parallel=ns.max_parallel,
                              stall_seconds=ns.stall_seconds, print_timeout=ns.print_timeout,
                              heartbeat_seconds=ns.heartbeat_seconds, writer=ns.writer,
                              unrestricted_shell=ns.unrestricted_shell, resume=not ns.no_resume)
            answer = {"run_dir": str(run_dir), "roles_file": str(run_dir / "roles.json"),
                      "context_file": str(run_dir / "context.md"), "next": "discover, author inputs, check, start, follow, collect"}
        else:
            run_dir = private_dir(ns.run_dir)
            if cmd == "discover":
                unused(run_dir)
                answer = store_discovery(run_dir, Path(config_for(run_dir)["cwd"]))
            elif cmd == "check":
                answer = check(run_dir)
            elif cmd in {"start", "run"}:
                answer = start(run_dir, attached=cmd == "run")
                if isinstance(answer, int):
                    return answer
            elif cmd == "status":
                answer = observe(run_dir)
            elif cmd == "collect":
                answer = collect(run_dir, partial=ns.partial)
            elif cmd == "follow":
                positive(ns.timeout, "follower timeout", zero=True)
                if ns.after_sequence < 0:
                    raise CouncilError("follower sequence must be nonnegative")
                return follow(run_dir, ns.after_sequence, ns.timeout)
            elif cmd == "cancel":
                answer = cancel(run_dir, force=ns.force)
            elif cmd == "reap":
                answer = reap(run_dir)
            elif cmd == "_supervise":
                try:
                    return asyncio.run(supervise(run_dir, ns.lock_fd, ns.input_hash, ns.binary))
                finally:
                    os.close(ns.lock_fd)
            else:
                raise CouncilError("unknown command")
        print(json.dumps(answer, indent=2, ensure_ascii=True, allow_nan=False), flush=True)
        return 0
    except (CouncilError, OSError, UnicodeError, ValueError) as exc:
        print(f"antigravity-council: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
