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
    # Mirrors the flags of the installed agy 1.5.12 help that the runner relies on.
    flags = ("--input-format --output-format --model --effort --agent --add-dir --conversation --print-timeout "
             "--mode --sandbox --dangerously-skip-permissions --settings")
    if os.environ.get("FAKE_AGY_MISSING_FLAG"):
        flags = " ".join(f for f in flags.split() if f != os.environ["FAKE_AGY_MISSING_FLAG"])
    # Real agy (Go flag package) writes usage to stderr with exit 0.
    print(flags, file=sys.stderr)
    raise SystemExit(0)
if args == ["models"]:
    if os.environ.get("FAKE_AGY_MODELS_BAD"):
        print("No parseable catalog here.")
    else:
        print("model-alpha-high    Model Alpha (High)")
        print("model-alpha-medium  Model Alpha (Medium)")
        print("model-beta-high   Model Beta (High)")
        print("model-beta-medium Model Beta (Medium)")
        print("model-beta-max    Model Beta (Max)")
    raise SystemExit(0)


def arg(key):
    return args[args.index(key) + 1] if key in args else None


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


if {"-p", "--print", "--prompt"} & set(args) or arg("--input-format") != "stream-json" or arg("--output-format") != "stream-json":
    print("wrong streaming invocation", file=sys.stderr)
    raise SystemExit(2)
bypass = "--dangerously-skip-permissions" in args
if "--settings" in args:
    # agy takes one JSON object (inline here) of settings.json keys for this session only.
    settings = json.loads(arg("--settings"))
    if not isinstance(settings, dict):
        print("--settings must be a JSON object", file=sys.stderr)
        raise SystemExit(2)
    for key, kind in (("enableTerminalSandbox", bool), ("allowNonWorkspaceAccess", bool), ("toolPermission", str)):
        if key in settings and not isinstance(settings[key], kind):
            print(f"invalid settings value for {key}", file=sys.stderr)
            raise SystemExit(2)
if "--effort" in args and arg("--effort") not in {"low", "medium", "high", "xhigh", "max"}:
    print("invalid --effort", file=sys.stderr)
    raise SystemExit(2)
if "--mode" in args and arg("--mode") not in {"accept-edits", "auto", "plan"}:
    print("invalid --mode", file=sys.stderr)
    raise SystemExit(2)
message = json.loads(sys.stdin.readline())
if message.get("event") != "user" or not isinstance(message.get("message", {}).get("content"), str):
    raise SystemExit(2)
prompt = message["message"]["content"]
# Like real agy, one turn runs per stdin message; the process exits when stdin closes.
# A runner that never closes stdin leaves this fake waiting, which the tests catch.
if record:
    with open(record, "a") as handle:
        handle.write(json.dumps({"turn": 1, "prompt_head": prompt[:60]}) + "\n")
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


responses = []


def terminal(status="SUCCESS", response="Verified fixture reply.", error=None):
    responses.append(response)
    # Like real agy 1.5.12, num_turns counts the conversation's turns (prior ones on resume).
    turns = len(responses) + (1 if arg("--conversation") else 0)
    result = {"conversation_id": sid if scenario != "conv-mismatch" else str(uuid.uuid4()), "status": status,
              "response": response, "num_turns": turns,
              "duration_seconds": 0.05, "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}}
    if scenario == "denied" or (scenario == "denied-first" and len(responses) == 1):
        # Real agy reports actions a deny rule, the system denylist, or admin policy blocked.
        result["denied_actions"] = [{"action": "command"}]
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
registry = sorted(set(tools) | {"ask_permission", "run_command", "write_to_file", "browser_subagent", "search_web",
                                 "read_url_content", "command_status", "send_command_input", "sed_file", "finish"})
if scenario == "bad-init-tools":
    registry.remove("view_file")
if scenario != "no-init":
    emit({"event": "init", "conversation_id": sid,
          # Like real agy, init.model is present only when --model overrides it.
          # Documented: always-proceed under --dangerously-skip-permissions, else request-review.
          # admin-review simulates a policy that keeps review on despite the flag.
          "init": dict({"agent": agent, "tools": registry,
                        "permission_mode": "always-proceed" if bypass and scenario != "admin-review" else "request-review"},
                       **({"model": model} if model else {}))})
if scenario == "session-child":
    # Like agy 1.5.12's run_command: the command runs in its own process group.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"], start_new_session=True)
    Path(os.environ["FAKE_AGY_CHILD"]).write_text(str(child.pid))
    time.sleep(90)
if scenario == "sleep":
    if os.environ.get("FAKE_AGY_CHILD"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"])
        Path(os.environ["FAKE_AGY_CHILD"]).write_text(str(child.pid))
    time.sleep(90)
if scenario == "malformed":
    print('{"bad":', flush=True)
    time.sleep(90)
# Under the bypass (agy >= 1.5.2) outside-workspace reads go through; only restricted roles abort.
if scenario == "permission-abort-once" and not bypass:
    if "RETRY NOTICE:" not in prompt:
        emit({"event": "step_update", "step_update": {"conversation_id": sid, "step_type": "unknown", "state": "DONE"}})
        emit({"event": "step_update", "step_update": {
            "conversation_id": sid, "step_type": "tool", "state": "DONE", "tool_name": "list_dir",
            "tool_info": {"error": {"type": "TOOL_ERROR", "message": "permission check failed for read_file \"/private/tmp\""}},
        }})
        print('jetski: no output produced — a tool required the "read_file" permission that headless mode cannot prompt for, so it was auto-denied.',
              file=sys.stderr, flush=True)
        terminal("SUCCESS", "")
        raise SystemExit(0)
if scenario == "full-ops":
    for name, params in (("read_file", {"Path": "/etc/hosts"}), ("search_web", {"query": "x"}),
                         ("read_url_content", {"Url": "https://example.com"}),
                         ("run_command", {"CommandLine": "curl -s https://example.com"}),
                         ("write_to_file", {"TargetFile": "out.txt"}), ("sed_file", {"File": "out.txt"})):
        emit({"event": "step_update", "step_update": {"conversation_id": sid, "step_type": "tool", "state": "DONE",
                                                      "tool_name": name, "tool_info": {"name": name, "parameters": params}}})
if scenario == "subagent":
    emit({"event": "step_update", "step_update": {"conversation_id": sid, "step_type": "tool", "state": "DONE",
                                                  "tool_name": "run_command", "subagent_info": {"subagents": [{"role": "x"}]}}})
if scenario in {"tool-transient", "tool-error"}:
    payload = {"conversation_id": sid, "step_type": "tool", "state": "DONE", "tool_name": "view_file"}
    if scenario == "tool-error":
        payload["tool_info"] = {"error": {"type": "permission", "message": "permission denied"}}
        print("tool soft-denied; requires approval", file=sys.stderr, flush=True)
    emit({"event": "step_update", "step_update": payload})
if scenario == "error-stay":
    # Turn one fails but the process keeps reading stdin, so a premature verify prompt would be seen.
    terminal("ERROR", "", "quota exhausted HTTP 429")
    for line in sys.stdin:
        if record:
            with open(record, "a") as handle:
                handle.write(json.dumps({"turn": 2, "prompt_head": json.loads(line)["message"]["content"][:60]}) + "\n")
    raise SystemExit(1)
if scenario in {"quota", "model-error", "transient", "tool-transient"}:
    errors = {"quota": "quota exhausted HTTP 429", "model-error": "invalid model selection",
              "transient": "HTTP 503 temporarily unavailable", "tool-transient": "HTTP 503 after a tool"}
    terminal("ERROR", "", errors[scenario])
    raise SystemExit(1)
if scenario == "missing-result":
    raise SystemExit(0)
if scenario == "future":
    emit({"event": "future_event", "extra": {"nothing": "to interpret"}})
if scenario == "smoke-full":
    # Test double for scripts/live_smoke.py: perform the five steps it asks for.
    import re
    canary = Path(re.search(r"1\. Read the file (\S+canary\.txt)", prompt)[1]).read_text().strip()
    marker = re.search(r"printf %s ([0-9a-f]+) > shell-marker\.txt", prompt)[1]
    Path("shell-marker.txt").write_text(marker)
    Path("edit-marker.txt").write_text(marker)
    emit({"event": "step_update", "step_update": {"conversation_id": sid, "step_type": "user_input", "state": "DONE"}})
    shell = prompt[prompt.index("printf %s"):].split(" and report")[0]
    for name, output, params in (("read_file", canary, {"Path": "canary.txt"}),
                                 ("run_command", "HTTP_STATUS=200", {"CommandLine": shell}),
                                 ("search_web", "Example Domain snippet", {"query": "IANA example domain"}),
                                 ("run_command", "<title>Example Domain</title>", {"CommandLine": "curl -sS https://example.com"}),
                                 ("write_file", "Created file.", {"Path": str(Path.cwd() / "edit-marker.txt")}),
                                 ("edit_file", "", {"Path": str(Path.cwd() / "edit-marker.txt")}),
                                 ("invoke_subagent", canary, {})):
        step = {"conversation_id": sid, "step_type": "tool", "state": "DONE",
                "tool_name": name, "tool_info": {"name": name, "parameters": params, "output": output}}
        if name == "invoke_subagent":
            # Real agy 1.5.12 reports a running subagent as its own step type.
            step["step_type"] = "subagent"
            step["subagent_info"] = {"subagents": [{"role": "canary-reader"}]}
        emit({"event": "step_update", "step_update": step})
    terminal(response=f"canary: {canary}; HTTP_STATUS=200")
elif scenario == "canary":
    terminal(response=Path("canary.txt").read_text())
elif scenario == "large":
    terminal(response="é" * 100000)
elif scenario == "empty":
    terminal(response="  ")
elif scenario == "waiting":
    terminal(status="WAITING", response="waiting for approval")
else:
    terminal(response=f"Verified fixture reply. prompt_bytes={len(prompt.encode())}")
if scenario in {"duplicate", "dup-result"}:
    terminal()
if scenario == "nonzero":
    raise SystemExit(1)
if scenario == "pipe-held":
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)"])

# Further turns on the same conversation (the runner's forced self-verification turn).
turn = 1
for line in sys.stdin:
    if not line.strip():
        continue
    follow_up = json.loads(line)
    if follow_up.get("event") != "user":
        raise SystemExit(2)
    turn += 1
    text = follow_up["message"]["content"]
    if record:
        with open(record, "a") as handle:
            handle.write(json.dumps({"turn": turn, "prompt_head": text[:60]}) + "\n")
    emit({"event": "step_update", "step_update": {"conversation_id": sid, "step_type": "user_input", "state": "DONE"}})
    if scenario == "verify-fail":
        terminal("ERROR", "", "HTTP 503 during self-verification")
        raise SystemExit(1)
    if scenario == "verify-subagent":
        emit({"event": "step_update", "step_update": {"conversation_id": sid, "step_type": "tool", "state": "DONE",
                                                      "tool_name": "invoke_subagent",
                                                      "subagent_info": {"subagents": [{"role": "refuter"}]}}})
    terminal(response=f"Self-verified final report (turn {turn}). Draft: {responses[0] if responses else ''}")
    if scenario == "exit1-after-verify":
        raise SystemExit(1)
