"""Antigravity NDJSON, deliberately not the Codex or Claude SDK wire protocol."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from council_common import CouncilError, strict_json
from council_policy import INFRA_TOOLS, TOOLS, profile_name

MAX_EVENT_BYTES = 32 * 1024 * 1024


@dataclass
class Stream:
    role: dict
    pending: bytearray = field(default_factory=bytearray)
    init: dict | None = None
    result: dict | None = None
    conversation_id: str | None = None
    unsafe_to_replay: bool = False
    tool_errors: list[str] = field(default_factory=list)
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
        elif kind == "step_update":
            if self.init is None or self.result is not None:
                raise CouncilError("step_update outside the one active turn")
            step = event.get("step_update")
            if not isinstance(step, dict):
                self.unsafe_to_replay = True
                raise CouncilError("invalid step_update payload")
            st = step.get("step_type")
            if st not in {"user_input", "agent_response", "checkpoint", "unknown"}:
                self.unsafe_to_replay = True
            if st == "tool":
                name = step.get("tool_name")
                if name not in TOOLS[self.role["access"]] | INFRA_TOOLS:
                    raise CouncilError(f"worker attempted tool outside profile: {name!r}")
                info = step.get("tool_info")
                if isinstance(info, dict) and info.get("error"):
                    self.tool_errors.append(json.dumps(info["error"], ensure_ascii=True))
            if step.get("subagent_info"):
                self.unsafe_to_replay = True
                raise CouncilError("nested subagents are not authorized in this worker profile")
        elif kind == "result":
            if self.result is not None:
                raise CouncilError("multiple terminal results for a single submitted prompt")
            result = event.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("status"), str):
                raise CouncilError("missing result.status")
            self.result = result
            sid = result.get("conversation_id")
            if self.conversation_id and sid and self.conversation_id != sid:
                raise CouncilError("conversation_id changed within the worker stream")
            self.conversation_id = sid or self.conversation_id
        else:
            # Future event types are observational, never counted as success or
            # taken as permission to replay an uncertain attempt.
            self.unsafe_to_replay = True

    def outcome(self, returncode: int | None) -> tuple[bool, str]:
        if self.result is None:
            return False, "stream ended without a terminal result"
        if returncode != 0:
            return False, str(self.result.get("error") or f"agy exited {returncode}")
        if self.result.get("status") != "SUCCESS":
            return False, str(self.result.get("error") or f"terminal status {self.result.get('status')}")
        if self.init is None:
            return False, "SUCCESS without a validated init event"
        if self.result.get("error"):
            return False, str(self.result["error"])
        text = self.result.get("response")
        if not isinstance(text, str) or not text.strip():
            return False, "empty terminal response"
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
