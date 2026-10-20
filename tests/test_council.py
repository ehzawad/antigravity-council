"""Offline contract tests and real-subprocess integration tests. No inference."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/antigravity-council/scripts"
sys.path.insert(0, str(SCRIPTS))
import council_common as common
import council_lifecycle as life
import council_models as models
import council_policy as policy
import council_protocol as protocol
import council_runtime as runtime

CLI = SCRIPTS / "antigravity_council.py"
FAKE = ROOT / "tests/fake_agy.py"


def context(phase="Inspect current evidence."):
    return "# Intent Capsule\n\n" + "\n\n".join(f"## {h}\nValue for {h}." for h in common.HEADINGS) + "\n\n# Current Phase Brief\n" + phase + "\n"


def role(rid="independent-check", model="gemini-4-argon-high", access="read", instruction=None):
    return {"id": rid, "label": "Independent check", "instruction": instruction or ["Inspect the stated claim.", "Report nothing material when justified."],
            "model": model, "access": access, "selection_reason": "User-requested model; task-specific evidence check."}


def init_event(r=None, **changes):
    r = r or role()
    init = {"model": r["model"], "agent": policy.profile_name(r["access"]), "tools": sorted(policy.TOOLS[r["access"]])}
    init.update(changes)
    return {"event": "init", "conversation_id": "11111111-1111-1111-1111-111111111111", "init": init}


def result_event(status="SUCCESS", response="evidence", **fields):
    return {"event": "result", "result": {"conversation_id": "11111111-1111-1111-1111-111111111111", "status": status, "response": response, **fields}}


def feed(stream, *events):
    for event in events:
        stream.feed((json.dumps(event, ensure_ascii=False) + "\n").encode())


class Isolated(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.project = self.base / "project"
        self.project.mkdir()
        self.env = patch.dict(os.environ, {"HOME": str(self.home), "XDG_STATE_HOME": str(self.base / "state"),
                                          "ANTIGRAVITY_COUNCIL_AGY": str(FAKE)}, clear=False)
        self.env.start()
        for name in list(os.environ):
            if name.startswith("FAKE_AGY_") or name == "GOOGLE_GEMINI_BASE_URL":
                os.environ.pop(name)
        policy.setup_profiles()
        self.runs = []

    def tearDown(self):
        for run in self.runs:
            with contextlib.suppress(Exception):
                view = life.observe(run)
                if view["lifecycle"] in {"running", "finishing"}:
                    life.cancel(run, force=True)
                life.reap(run)
        self.env.stop()
        self.tmp.cleanup()

    def prepare(self, roles=None, **kwargs):
        run = life.prepare(str(self.project), "test-session", kwargs.pop("task", "test-task"),
                           heartbeat_seconds=kwargs.pop("heartbeat_seconds", 0.2), **kwargs)
        self.runs.append(run)
        common.write_json(run / "roles.json", roles or [role()])
        common.atomic_write(run / "context.md", context())
        return run

    def cli(self, *args, timeout=15):
        return subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True,
                              text=True, timeout=timeout, check=False)

    def wait(self, run, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            view = life.observe(run)
            if view["lifecycle"] in {"done", "aborted"}:
                return view
            time.sleep(0.05)
        self.fail(f"run did not settle: {life.observe(run)}")


class InputTests(Isolated):
    def test_duplicate_json_key(self):
        with self.assertRaises(common.CouncilError): common.strict_json('{"x":1,"x":2}')

    def test_nested_duplicate_json_key(self):
        with self.assertRaises(common.CouncilError): common.strict_json('{"a":{"x":1,"x":2}}')

    def test_nonfinite_json(self):
        for n in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(n=n), self.assertRaises(common.CouncilError): common.strict_json(n)

    def test_unknown_role_field(self):
        with self.assertRaises(common.CouncilError): common.parse_roles([dict(role(), effort="high")])

    def test_duplicate_role_id(self):
        with self.assertRaises(common.CouncilError): common.parse_roles([role(), role()])

    def test_instruction_list_required(self):
        with self.assertRaises(common.CouncilError): common.parse_roles([dict(role(), instruction="text")])

    def test_empty_panel(self):
        with self.assertRaises(common.CouncilError): common.parse_roles([])

    def test_large_context_is_not_truncated(self):
        text = context("data " * 100000)
        self.assertTrue(common.intent_capsule(text))
        self.assertIn("data " * 100000, runtime.prompt_for(role(), text))

    def test_valid_single_role(self):
        self.assertEqual(len(common.parse_roles([role()])), 1)

    def test_arbitrary_dynamic_role(self):
        self.assertEqual(common.parse_roles([role("queue-interleaving-proof")])[0]["id"], "queue-interleaving-proof")

    def test_host_session_requires_scope(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(common.CouncilError): common.host_session(None)

    def test_host_session_detected(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "abc"}, clear=True):
            self.assertEqual(common.host_session(None), "CLAUDE_CODE_SESSION_ID=abc")

    def test_phase_can_change_without_capsule_change(self):
        self.assertEqual(common.intent_capsule(context("one")), common.intent_capsule(context("two")))

    def test_capsule_missing_heading(self):
        with self.assertRaises(common.CouncilError): common.intent_capsule(context().replace("## Authority boundaries", "## Other"))

    def test_capsule_duplicate_heading(self):
        with self.assertRaises(common.CouncilError): common.intent_capsule(context().replace("## Acceptance evidence", "## Authority boundaries"))

    def test_capsule_empty_section(self):
        with self.assertRaises(common.CouncilError): common.intent_capsule(context().replace("Value for Verbatim request.", ""))

    def test_capsule_bound_across_rounds(self):
        cfg = life.config_for(self.prepare())
        life.check_intent(cfg, common.intent_capsule(context()), bind=True)
        with self.assertRaises(common.CouncilError): life.check_intent(cfg, common.intent_capsule(context().replace("Value for Verbatim", "Changed Verbatim")))

    def test_new_task_can_have_new_capsule(self):
        cfg = life.config_for(self.prepare())
        life.check_intent(cfg, common.intent_capsule(context()), bind=True)
        cfg["task_key"] = "second-task"
        life.check_intent(cfg, common.intent_capsule(context().replace("Value", "Changed")), bind=True)

    def test_private_directory_required(self):
        p = self.base / "public"
        p.mkdir(mode=0o755)
        with self.assertRaises(common.CouncilError): common.private_dir(p)

    def test_symlink_directory_refused(self):
        p = self.base / "link"
        p.symlink_to(self.home)
        with self.assertRaises(common.CouncilError): common.private_dir(p)

    def test_atomic_files_private(self):
        p = self.home / "out.json"
        common.write_json(p, {"yes": True})
        self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_writer_is_explicit(self):
        with self.assertRaises(common.CouncilError): policy.validate_authority([role(access="write")], None)

    def test_multiple_writers_refused(self):
        with self.assertRaises(common.CouncilError): policy.validate_authority([role("one", access="write"), role("two", access="execute")], "one")

    def test_wrong_writer_refused(self):
        with self.assertRaises(common.CouncilError): policy.validate_authority([role()], "independent-check")

    def test_unrestricted_only_execute(self):
        with self.assertRaises(common.CouncilError): policy.validate_authority([role(access="write")], "independent-check", True)

    def test_profile_setup_idempotent(self):
        self.assertEqual(policy.setup_profiles(), policy.setup_profiles())

    def test_modified_profile_not_overwritten(self):
        p = policy.target_dir() / (policy.profile_name("read") + ".md")
        p.write_text("modified")
        with self.assertRaises(common.CouncilError): policy.setup_profiles()
        self.assertEqual(p.read_text(), "modified")

    def test_workspace_shadow_refused(self):
        path = self.project / ".agents/agents"
        path.mkdir(parents=True)
        (path / (policy.profile_name("read") + ".md")).write_text("hostile")
        with self.assertRaises(common.CouncilError): policy.check_profiles(self.project, [role()])

    def test_session_scopes_model_access_and_task(self):
        cfg = life.config_for(self.prepare())
        first = runtime.session_path(cfg, role())
        self.assertNotEqual(first, runtime.session_path(cfg, role(model="claude-opus-5-5-high")))
        self.assertNotEqual(first, runtime.session_path(cfg, role(access="write")))
        self.assertNotEqual(first, runtime.session_path(dict(cfg, task_key="other"), role()))

    def test_duration_validation(self):
        self.assertEqual(runtime.seconds("60m"), 3600)
        for value in ("0", "0s", "-1s", "60", "unlimited"):
            with self.subTest(value=value), self.assertRaises(common.CouncilError): runtime.seconds(value)

    def test_argv_has_no_print_prompt(self):
        cfg = life.config_for(self.prepare())
        argv = runtime.argv_for("agy", role(), cfg, None)
        self.assertNotIn("-p", argv)
        self.assertNotIn("--effort", argv)
        self.assertNotIn("--dangerously-skip-permissions", argv)

    def test_executor_requests_sandbox(self):
        cfg = life.config_for(self.prepare([role(access="execute")], writer="independent-check"))
        argv = runtime.argv_for("agy", role(access="execute"), cfg, None)
        self.assertIn("--sandbox", argv)
        self.assertNotIn("--dangerously-skip-permissions", argv)

    def test_changed_provider_refused(self):
        path = self.home / ".gemini/antigravity-cli"
        path.mkdir()
        (path / "settings.json").write_text('{"modelProvider":"gemini"}')
        with self.assertRaises(common.CouncilError): models.provider_check()

    def test_custom_endpoint_refused(self):
        with patch.dict(os.environ, {"GOOGLE_GEMINI_BASE_URL": "https://example.invalid"}), self.assertRaises(common.CouncilError): models.provider_check()


class ModelTests(Isolated):
    def test_documented_table(self):
        parsed = models.parse_models("gemini-4-argon-high   Gemini 4 Argon (High)\nclaude-opus-5-5-high   Claude Opus 5.5 (High)")
        self.assertEqual(len(parsed), 2)

    def test_ansi_table(self):
        self.assertEqual(models.parse_models("\x1b[32mgemini-4-argon-high\x1b[0m  Gemini 4 Argon")[0]["id"], "gemini-4-argon-high")

    def test_conflicting_catalog(self):
        with self.assertRaises(common.CouncilError): models.parse_models("model-a  A\nmodel-a  B")

    def test_unparseable_catalog_refused(self):
        with self.assertRaises(common.CouncilError): models.parse_models("unexpected output")

    def test_doctor_is_metadata_only(self):
        record = self.base / "calls.jsonl"
        with patch.dict(os.environ, {"FAKE_AGY_RECORD": str(record)}):
            snap = models.discover(self.project)
        self.assertIn("gemini-4-argon-high", snap["preferred"]["argon"])
        self.assertFalse(snap["auth"]["entitlement_verified"])
        calls = [json.loads(s) for s in record.read_text().splitlines()]
        self.assertEqual([c["args"] for c in calls], [["--version"], ["--help"], ["models"]])

    def test_missing_capability_fails_closed(self):
        with patch.dict(os.environ, {"FAKE_AGY_MISSING_FLAG": "--conversation"}), self.assertRaises(common.CouncilError): models.discover(self.project)

    def test_unknown_pin_not_substituted(self):
        snap = models.discover(self.project)
        with self.assertRaises(common.CouncilError): models.validate_models([role(model="argon-guessed")], snap)


class ProtocolTests(unittest.TestCase):
    def test_success(self):
        s = protocol.Stream(role()); feed(s, init_event(), result_event()); self.assertEqual(s.outcome(0), (True, ""))

    def test_fragmented_utf8(self):
        s = protocol.Stream(role()); feed(s, init_event())
        value = (json.dumps(result_event(response="বাংলা"), ensure_ascii=False) + "\n").encode()
        for byte in value: s.feed(bytes([byte]))
        self.assertTrue(s.outcome(0)[0]); self.assertEqual(s.result["response"], "বাংলা")

    def test_large_event(self):
        s = protocol.Stream(role()); feed(s, init_event(), result_event(response="x" * 100000)); self.assertTrue(s.outcome(0)[0])

    def test_future_fields(self):
        s = protocol.Stream(role()); feed(s, dict(init_event(), future=42), dict(result_event(), future=43)); self.assertTrue(s.outcome(0)[0])

    def test_future_events_disable_retry_not_success(self):
        s = protocol.Stream(role()); feed(s, init_event(), {"event": "future"}, result_event()); self.assertTrue(s.unsafe_to_replay); self.assertTrue(s.outcome(0)[0])

    def test_wrong_protocol_refused(self):
        with self.assertRaises(common.CouncilError): feed(protocol.Stream(role()), {"type": "result", "result": "yes"})

    def test_missing_result(self):
        s = protocol.Stream(role()); feed(s, init_event()); self.assertFalse(s.outcome(0)[0])

    def test_empty_result(self):
        s = protocol.Stream(role()); feed(s, init_event(), result_event(response=" ")); self.assertFalse(s.outcome(0)[0])

    def test_success_requires_zero_exit(self):
        s = protocol.Stream(role()); feed(s, init_event(), result_event()); self.assertFalse(s.outcome(1)[0])

    def test_success_requires_init(self):
        s = protocol.Stream(role()); feed(s, result_event()); self.assertFalse(s.outcome(0)[0])

    def test_result_error_is_failure(self):
        s = protocol.Stream(role()); feed(s, init_event(), result_event(error="error")); self.assertFalse(s.outcome(0)[0])

    def test_non_success_statuses(self):
        for status in ("ERROR", "CANCELED", "INTERRUPTED", "INVALID", "WAITING", "RUNNING"):
            with self.subTest(status=status):
                s = protocol.Stream(role()); feed(s, init_event(), result_event(status=status)); self.assertFalse(s.outcome(0)[0])

    def test_wrong_model_init(self):
        with self.assertRaises(common.CouncilError): feed(protocol.Stream(role()), init_event(model="other"))

    def test_wrong_agent_init(self):
        with self.assertRaises(common.CouncilError): feed(protocol.Stream(role()), init_event(agent="other"))

    def test_init_registry_missing_profile_tool(self):
        with self.assertRaises(common.CouncilError): feed(protocol.Stream(role()), init_event(tools=["run_command"]))

    def test_init_full_registry_accepted(self):
        # Real agy lists its whole registry in init.tools; enforcement is per step.
        registry = sorted(policy.TOOLS["read"] | {"run_command", "write_to_file", "browser_subagent"})
        feed(protocol.Stream(role()), init_event(tools=registry))

    def test_step_tool_outside_profile_refused(self):
        step = {"event": "step_update", "step_update": {"step_type": "tool", "tool_name": "run_command", "state": "DONE"}}
        with self.assertRaises(common.CouncilError): feed(protocol.Stream(role()), init_event(), step)

    def test_duplicate_terminal_refused(self):
        s = protocol.Stream(role()); feed(s, init_event(), result_event())
        with self.assertRaises(common.CouncilError): feed(s, result_event())

    def test_changed_conversation_refused(self):
        s = protocol.Stream(role()); feed(s, init_event()); event = result_event(); event["result"]["conversation_id"] = "22222222-2222-2222-2222-222222222222"
        with self.assertRaises(common.CouncilError): feed(s, event)

    def test_quota_is_not_transient(self):
        self.assertEqual(protocol.classify("quota exhausted HTTP 429"), "quota")

    def test_auth_is_not_transient(self):
        self.assertEqual(protocol.classify("authentication required HTTP 503"), "auth")

    def test_cli_reported_usage_retained(self):
        s = protocol.Stream(role()); feed(s, init_event(), result_event(usage={"total_tokens": 200})); self.assertEqual(s.result["usage"]["total_tokens"], 200)


class IntegrationTests(Isolated):
    def test_foreground_single_role(self):
        run = self.prepare(); output = self.cli("run", run)
        self.assertEqual(output.returncode, 0, output.stderr)
        result = life.collect(run)
        self.assertTrue(result["complete_transport"]); self.assertFalse(result["task_verified"])
        self.assertTrue(result["results"][0]["ok"])

    def test_detached_multi_model_council(self):
        run = self.prepare([role("one"), role("two", model="claude-opus-5-5-high")])
        out = self.cli("start", run); self.assertEqual(out.returncode, 0, out.stderr)
        self.wait(run)
        results = life.collect(run)["results"]
        self.assertEqual({r["requested_model"] for r in results}, {"gemini-4-argon-high", "claude-opus-5-5-high"})

    def test_partial_results_survive_failure(self):
        run = self.prepare([role("one"), role("two", instruction=["[fake:fail]"])])
        out = self.cli("run", run); self.assertEqual(out.returncode, 3, out.stderr)
        report = common.read_json(run / "report.json")
        self.assertEqual(report["status"], "partial"); self.assertEqual(report["ok"], 1)

    def test_model_failure_never_hops(self):
        run = self.prepare()
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "model-error"}): out = self.cli("run", run)
        self.assertEqual(out.returncode, 1)
        result = life.collect(run)["results"][0]
        self.assertEqual(result["failure_class"], "model-rejected"); self.assertEqual(result["attempts"], 1)

    def test_resume_and_model_change(self):
        first = self.prepare(); self.assertEqual(self.cli("run", first).returncode, 0)
        sid = life.collect(first)["results"][0]["conversation_id"]
        second = self.prepare(); self.assertEqual(self.cli("run", second).returncode, 0)
        self.assertEqual(life.collect(second)["results"][0]["conversation_id"], sid)
        third = self.prepare([role(model="claude-opus-5-5-high")]); self.assertEqual(self.cli("run", third).returncode, 0)
        self.assertNotEqual(life.collect(third)["results"][0]["conversation_id"], sid)

    def test_stale_resume_read_retries_fresh_once(self):
        first = self.prepare(); self.cli("run", first)
        second = self.prepare()
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "stale"}): out = self.cli("run", second)
        self.assertEqual(out.returncode, 0, out.stderr)
        result = life.collect(second)["results"][0]
        self.assertEqual(result["attempts"], 2); self.assertIn("continuity lost", " ".join(result["warnings"]))

    def test_transient_without_tools_retries_once(self):
        run = self.prepare()
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "transient"}): out = self.cli("run", run)
        self.assertEqual(out.returncode, 1)
        self.assertEqual(life.collect(run)["results"][0]["attempts"], 2)

    def test_transient_after_tool_not_retried(self):
        run = self.prepare()
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "tool-transient"}): self.cli("run", run)
        self.assertEqual(life.collect(run)["results"][0]["attempts"], 1)

    def test_writer_never_retried(self):
        run = self.prepare([role(access="write")], writer="independent-check")
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "transient"}): self.cli("run", run)
        self.assertEqual(life.collect(run)["results"][0]["attempts"], 1)

    def test_large_reply(self):
        run = self.prepare()
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "large"}): out = self.cli("run", run)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(len(life.collect(run)["results"][0]["response"]), 100000)

    def test_missing_result_fails(self):
        run = self.prepare()
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "missing-result"}): out = self.cli("run", run)
        self.assertEqual(out.returncode, 1)

    def test_bad_init_fails(self):
        run = self.prepare()
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "bad-init-tools"}): out = self.cli("run", run)
        self.assertEqual(out.returncode, 1)
        self.assertEqual(life.collect(run)["results"][0]["failure_class"], "protocol")

    def test_malformed_stdout_is_torn_down(self):
        run = self.prepare()
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "malformed"}): out = self.cli("run", run)
        self.assertEqual(out.returncode, 1)
        self.assertIn("malformed", life.collect(run)["results"][0]["error"])

    def test_tool_denial_is_coverage_warning(self):
        run = self.prepare()
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "tool-error"}): out = self.cli("run", run)
        self.assertEqual(out.returncode, 0)
        self.assertTrue(life.collect(run)["results"][0]["warnings"])

    def test_permission_abort_on_read_retries_once_with_boundary_note(self):
        run = self.prepare()
        (run / "diff-ui.patch").write_text("diff\n")
        record = self.home / "calls.ndjson"
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "permission-abort-once", "FAKE_AGY_RECORD": str(record)}):
            out = self.cli("run", run)
        self.assertEqual(out.returncode, 0, out.stderr)
        result = life.collect(run)["results"][0]
        self.assertEqual(result["attempts"], 2)
        self.assertIn("headless permission abort", " ".join(result["warnings"]))
        calls = [json.loads(line) for line in record.read_text().splitlines() if "--input-format" in line]
        self.assertIn("--add-dir", calls[0]["args"])
        self.assertIn(str(run), calls[0]["args"])

    def test_stall_kills_worker(self):
        run = self.prepare(stall_seconds=0.2)
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "sleep"}): out = self.cli("run", run)
        self.assertEqual(out.returncode, 1)
        self.assertEqual(life.collect(run)["results"][0]["failure_class"], "stall")

    def test_follower_replays_and_terminates(self):
        run = self.prepare(); self.cli("start", run)
        out = self.cli("follow", run)
        self.assertEqual(out.returncode, 0, out.stderr)
        events = [json.loads(line) for line in out.stdout.splitlines()]
        self.assertEqual(events[-1]["event"], "verified_end")
        self.assertIn("role_completed", [e["event"] for e in events])

    def test_follower_after_sequence(self):
        run = self.prepare(); self.cli("run", run)
        out = self.cli("follow", run, "--after-sequence", "999")
        self.assertEqual(len(out.stdout.splitlines()), 1)

    def test_cannot_collect_running_as_final(self):
        run = self.prepare()
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "sleep"}): self.cli("start", run)
        out = self.cli("collect", run)
        self.assertEqual(out.returncode, 2)
        self.assertEqual(self.cli("collect", run, "--partial").returncode, 0)

    def test_cancel_detached(self):
        run = self.prepare()
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "sleep"}): self.cli("start", run)
        time.sleep(0.3)
        out = self.cli("cancel", run)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.wait(run)["run_status"], "cancelled")

    def test_no_double_launch(self):
        run = self.prepare(); self.cli("start", run); self.wait(run)
        before = (run / "events.ndjson").read_bytes()
        out = self.cli("start", run)
        self.assertEqual(out.returncode, 2); self.assertEqual((run / "events.ndjson").read_bytes(), before)

    def test_killed_supervisor_detected_and_worker_reaped(self):
        run = self.prepare()
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "sleep"}): self.cli("start", run)
        deadline = time.monotonic() + 5
        worker = None
        while time.monotonic() < deadline:
            view = life.observe(run)
            worker = view.get("state", {}).get("roles", {}).get("independent-check", {}).get("process")
            if worker: break
            time.sleep(0.05)
        self.assertIsNotNone(worker)
        os.kill(view["supervisor"]["pid"], signal.SIGKILL)
        self.assertEqual(self.wait(run)["lifecycle"], "aborted")
        result = life.reap(run)
        self.assertIn("independent-check", result["reaped"])

    def test_reap_living_runner_refused(self):
        run = self.prepare()
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "sleep"}): self.cli("start", run)
        self.assertEqual(self.cli("reap", run).returncode, 2)

    def test_contract_mismatch(self):
        out = self.cli("--skill-contract", "999", "doctor")
        self.assertEqual(out.returncode, 2)

    def test_permission_profiles_do_not_contain_subagents(self):
        for access in policy.TOOLS:
            self.assertNotIn("invoke_subagent", policy.profile_text(access))
            self.assertIn("subagent: false", policy.profile_text(access))

    def test_settings_and_credentials_unchanged(self):
        path = self.home / ".gemini/antigravity-cli"
        path.mkdir(); settings = path / "settings.json"
        settings.write_text('{"toolPermission":"request-review"}')
        before = settings.read_bytes()
        run = self.prepare(); self.cli("run", run)
        self.assertEqual(settings.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
