"""Antigravity NDJSON, deliberately not the Codex or Claude SDK wire protocol."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from council_common import CouncilError, strict_json
from council_policy import FULL_PERMISSION_MODE, INFRA_TOOLS, STEP_TOOL_NAMES, TOOLS, profile_name

MAX_EVENT_BYTES = 32 * 1024 * 1024


@dataclass
class Stream:
    role: dict
    pending: bytearray = field(default_factory=bytearray)
    init: dict | None = None
    result: dict | None = None  # the latest terminal result
    results: list[dict] = field(default_factory=list)
    # One terminal result is allowed per submitted prompt. The runner raises this
    # before it sends a full role's self-verification turn on the same stream.
    prompts_sent: int = 1
    inputs_since_result: int = 0
    conversation_id: str | None = None
    unsafe_to_replay: bool = False
    tool_errors: list[str] = field(default_factory=list)
    audit_warnings: list[str] = field(default_factory=list)
    events: int = 0

    def feed(self, data: bytes) -> None:
        self.pending.extend(data)
        while True:
            cut = self.pending.find(b"\n")
            if cut < 0:
                if len(self.pending) > MAX_EVENT_BYTES:
                    raise CouncilError("one NDJSON event exceeds 32 MiB; output was not truncated into success")
                break
            if cut > MAX_EVENT_BYTES:
                raise CouncilError("one NDJSON event exceeds 32 MiB")
            line = bytes(self.pending[:cut])
            del self.pending[:cut + 1]
            self.line(line)

    def finish(self) -> None:
        if self.pending.strip():
            self.line(bytes(self.pending))
        self.pending.clear()

    def line(self, line: bytes) -> None:
        if not line.strip():
            return
        try:
            event = strict_json(line.decode("utf-8"))
        except (CouncilError, UnicodeError) as exc:
            self.unsafe_to_replay = True
            raise CouncilError(f"malformed Antigravity stdout event: {exc}") from exc
        if not isinstance(event, dict) or not isinstance(event.get("event"), str):
            self.unsafe_to_replay = True
            raise CouncilError("expected Antigravity event key, not Codex/Claude type messages")
        self.events += 1
        kind = event["event"]
        if kind == "init":
            if self.init is not None or self.result is not None:
                raise CouncilError("duplicate or out-of-order init")
            init = event.get("init")
            if not isinstance(init, dict):
                raise CouncilError("init payload missing")
            self.init = init
            self.conversation_id = event.get("conversation_id")
            # init.model is reported only when overridden, so a role that
            # inherits agy's default has nothing to compare against.
            if "model" in self.role and init.get("model") != self.role["model"]:
                raise CouncilError(f"init model {init.get('model')!r} differs from pin {self.role['model']!r}")
            expected_agent = profile_name(self.role["access"])
            if init.get("agent") != expected_agent:
                raise CouncilError(f"init agent {init.get('agent')!r} differs from trusted profile {expected_agent!r}")
            tools = init.get("tools")
            if not isinstance(tools, list) or not tools or not all(isinstance(t, str) for t in tools):
                raise CouncilError("init.tools missing or invalid; cannot audit capability boundary")
            # Real agy reports its whole tool registry in init.tools, before the
            # profile's tool list is applied, so a superset is expected here. The
            # profile restriction itself is enforced by the CLI executor and
            # observed per tool step below. Here, only confirm that every profile
            # tool exists in this CLI's registry.
            missing = TOOLS[self.role["access"]] - set(tools)
            if missing:
                raise CouncilError("profile tools absent from this agy registry: " + ", ".join(sorted(missing)))
            # Documented: permission_mode is always-proceed under --dangerously-skip-permissions.
            # Anything else means full access did not take effect (e.g. an admin policy).
            if self.role["access"] == "full" and init.get("permission_mode") != FULL_PERMISSION_MODE:
                raise CouncilError(f"full access requested but init.permission_mode is "
                                   f"{init.get('permission_mode')!r}, not {FULL_PERMISSION_MODE!r}")
        elif kind == "step_update":
            if self.init is None or len(self.results) >= self.prompts_sent:
                raise CouncilError("step_update outside an active turn")
            step = event.get("step_update")
            if not isinstance(step, dict):
                self.unsafe_to_replay = True
                raise CouncilError("invalid step_update payload")
            st = step.get("step_type")
            if self.results and not self.inputs_since_result and st != "user_input":
                raise CouncilError("step_update before the next turn's user_input")
            if st == "user_input":
                self.inputs_since_result += 1
            if st not in {"user_input", "agent_response", "checkpoint", "unknown"}:
                self.unsafe_to_replay = True
            if st == "tool":
                name = step.get("tool_name")
                if STEP_TOOL_NAMES.get(name, name) not in TOOLS[self.role["access"]] | INFRA_TOOLS:
                    if self.role["access"] != "full" or not isinstance(name, str) or not name:
                        raise CouncilError(f"worker attempted tool outside profile: {name!r}")
                    # Full roles have no capability boundary to defend: the CLI enforces
                    # the profile, and a renamed or auxiliary step name must not discard
                    # the role's work. Record it once as evidence instead.
                    note = f"tool step outside the full profile list (recorded, not fatal): {name}"
                    if note not in self.audit_warnings:
                        self.audit_warnings.append(note)
                info = step.get("tool_info")
                if isinstance(info, dict) and info.get("error"):
                    self.tool_errors.append(json.dumps(info["error"], ensure_ascii=True))
            if step.get("subagent_info"):
                self.unsafe_to_replay = True
                if self.role["access"] != "full":
                    raise CouncilError("nested subagents are not authorized in this worker profile")
                # Full workers may use agy's own subagents; their work is evidence too.
                note = "worker used agy subagents; their work is part of this reply's evidence"
                if note not in self.audit_warnings:
                    self.audit_warnings.append(note)
        elif kind == "result":
            if len(self.results) >= self.prompts_sent:
                raise CouncilError("multiple terminal results for a single submitted prompt")
            result = event.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("status"), str):
                raise CouncilError("missing result.status")
            sid = result.get("conversation_id")
            if self.conversation_id and sid and self.conversation_id != sid:
                raise CouncilError("conversation_id changed within the worker stream")
            if self.results:
                # A later turn's result must belong to a new turn of the same conversation:
                # agy 1.5.12 opens each turn with a user_input step and counts num_turns up.
                previous = self.results[-1]
                if not self.inputs_since_result:
                    raise CouncilError("terminal result without a new user turn; not accepted as the next turn")
                if not sid or sid != self.conversation_id:
                    raise CouncilError("later turn's result lacks the stream's conversation_id")
                turns, before = result.get("num_turns"), previous.get("num_turns")
                if isinstance(turns, int) and isinstance(before, int) and turns <= before:
                    raise CouncilError("later turn's result does not advance num_turns")
            self.result = result
            self.results.append(result)
            self.inputs_since_result = 0
            self.conversation_id = sid or self.conversation_id
        else:
            # Future event types are observational, never counted as success or
            # taken as permission to replay an uncertain attempt.
            self.unsafe_to_replay = True

    def outcome(self, returncode: int | None) -> tuple[bool, str]:
        if self.result is None:
            return False, "stream ended without a terminal result"
        if len(self.results) < self.prompts_sent:
            return False, f"stream ended before the terminal result of turn {len(self.results) + 1} (self-verification)"
        if returncode != 0:
            return False, str(self.result.get("error") or f"agy exited {returncode}")
        for index, result in enumerate(self.results):
            label = "self-verification turn: " if index else ""
            if result.get("status") != "SUCCESS":
                return False, label + str(result.get("error") or f"terminal status {result.get('status')}")
            if self.init is None:
                return False, "SUCCESS without a validated init event"
            if result.get("error"):
                return False, label + str(result["error"])
            text = result.get("response")
            if not isinstance(text, str) or not text.strip():
                return False, label + "empty terminal response"
        return True, ""


def user_message(prompt: str) -> bytes:
    return (json.dumps({"event": "user", "message": {"content": prompt}}, ensure_ascii=False) + "\n").encode("utf-8")


def classify(error: str, status: str | None = None) -> str:
    """Conservative diagnostics, not a promise of exactly-once agent work."""
    lower = error.lower()
    if status in {"CANCELED", "INTERRUPTED"}:
        return "cancelled"
    if any(s in lower for s in ("authentication required", "not authenticated", "sign in", "sign-in", "unauthorized", "invalid credential")):
        return "auth"
    if any(s in lower for s in ("quota", "credit", "usage limit", "subscription", "plan limit")):
        return "quota"
    if any(s in lower for s in ("invalid model selection", "unknown model", "model not found", "not recognized as a known model", "unsupported model")):
        return "model-rejected"
    if any(s in lower for s in ("conversation not found", "unknown conversation", "conversation does not exist")):
        return "stale-session"
    import re
    if re.search(r"\b(?:http|status(?: code)?)\s*[:=]?\s*(?:429|5\d\d)\b", lower) or "too many requests" in lower:
        return "transient"
    if "permission" in lower or "denied" in lower:
        return "permission"
    return "provider"
