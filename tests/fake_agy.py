#!/usr/bin/env python3
"""Offline behavioral double for the documented Antigravity CLI contracts.

Not a real Antigravity binary. Passing these tests does not verify vendor behavior.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

args = sys.argv[1:]
record = os.environ.get("FAKE_AGY_RECORD")
if record:
    with open(record, "a") as handle:
        handle.write(json.dumps({"args": args, "pid": os.getpid(), "cwd": os.getcwd()}) + "\n")

if args == ["--version"]:
    print("fake-agy 1.2.11 (test fixture, not a vendor version assertion)")
    raise SystemExit(0)
if args == ["--help"]:
    flags = "--input-format --output-format --model --agent --conversation --print-timeout --mode --sandbox --dangerously-skip-permissions"
    if os.environ.get("FAKE_AGY_MISSING_FLAG"):
        flags = flags.replace(os.environ["FAKE_AGY_MISSING_FLAG"], "")
    # Real agy (Go flag package) writes usage to stderr with exit 0.
    print(flags, file=sys.stderr)
    raise SystemExit(0)
if args == ["models"]:
    if os.environ.get("FAKE_AGY_MODELS_BAD"):
        print("No parseable catalog here.")
    else:
        print("gemini-4-argon-high    Gemini 4 Argon (High)")
        print("gemini-4-argon-medium  Gemini 4 Argon (Medium)")
        print("claude-opus-5-5-high   Claude Opus 5.5 (High)")
        print("claude-opus-5-5-medium Claude Opus 5.5 (Medium)")
        print("claude-opus-5-5-max    Claude Opus 5.5 (Max)")
    raise SystemExit(0)


def arg(key):
    return args[args.index(key) + 1] if key in args else None


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


if "-p" in args or arg("--input-format") != "stream-json" or arg("--output-format") != "stream-json":
    print("wrong streaming invocation", file=sys.stderr)
    raise SystemExit(2)
message = json.loads(sys.stdin.readline())
if message.get("event") != "user" or not isinstance(message.get("message", {}).get("content"), str):
    raise SystemExit(2)
prompt = message["message"]["content"]
# The runner must close stdin after its only prompt, or this fake waits forever.
if sys.stdin.read():
    print("unexpected second user event", file=sys.stderr)
    raise SystemExit(2)
scenario = os.environ.get("FAKE_AGY_SCENARIO", "success")
if "[fake:fail]" in prompt:
    scenario = "quota"
if "[fake:sleep]" in prompt:
    scenario = "sleep"
model, agent = arg("--model"), arg("--agent")
sid = arg("--conversation") or str(uuid.uuid4())
profile = Path.home() / ".gemini/config/agents" / (agent + ".md")
text = profile.read_text()
tools = [line.strip()[2:] for line in text.splitlines() if line.startswith("  - ")]


def terminal(status="SUCCESS", response="Verified fixture reply.", error=None):
    result = {"conversation_id": sid, "status": status, "response": response, "num_turns": 2 if arg("--conversation") else 1,
              "duration_seconds": 0.05, "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}}
    if error:
        result["error"] = error
    emit({"event": "result", "result": result})


if scenario == "stale" and arg("--conversation"):
    terminal("ERROR", "", "conversation not found")
    raise SystemExit(1)
if scenario == "auth":
    terminal("ERROR", "", "authentication required")
    raise SystemExit(1)
if scenario == "bad-init-model":
    model = "wrong-model"
# Real agy reports its full registry in init.tools, not the profile subset.
registry = sorted(set(tools) | {"ask_permission", "run_command", "write_to_file", "browser_subagent", "search_web"})
if scenario == "bad-init-tools":
    registry.remove("view_file")
if scenario != "no-init":
    emit({"event": "init", "conversation_id": sid,
          "init": {"model": model, "agent": agent, "tools": registry, "permission_mode": "request-review"}})
if scenario == "sleep":
    if os.environ.get("FAKE_AGY_CHILD"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"])
        Path(os.environ["FAKE_AGY_CHILD"]).write_text(str(child.pid))
    time.sleep(90)
if scenario == "malformed":
    print('{"bad":', flush=True)
    time.sleep(90)
if scenario in {"tool-transient", "tool-error"}:
    payload = {"conversation_id": sid, "step_type": "tool", "state": "DONE", "tool_name": "view_file"}
    if scenario == "tool-error":
        payload["tool_info"] = {"error": {"type": "permission", "message": "permission denied"}}
        print("tool soft-denied; requires approval", file=sys.stderr, flush=True)
    emit({"event": "step_update", "step_update": payload})
if scenario in {"quota", "model-error", "transient", "tool-transient"}:
    errors = {"quota": "quota exhausted HTTP 429", "model-error": "invalid model selection",
              "transient": "HTTP 503 temporarily unavailable", "tool-transient": "HTTP 503 after a tool"}
    terminal("ERROR", "", errors[scenario])
    raise SystemExit(1)
if scenario == "missing-result":
    raise SystemExit(0)
if scenario == "future":
    emit({"event": "future_event", "extra": {"nothing": "to interpret"}})
if scenario == "canary":
    terminal(response=Path("canary.txt").read_text())
elif scenario == "large":
    terminal(response="é" * 100000)
elif scenario == "empty":
    terminal(response="  ")
elif scenario == "waiting":
    terminal(status="WAITING", response="waiting for approval")
else:
    terminal(response=f"Verified fixture reply. prompt_bytes={len(prompt.encode())}")
if scenario == "duplicate":
    terminal()
if scenario == "nonzero":
    raise SystemExit(1)
if scenario == "pipe-held":
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"])
