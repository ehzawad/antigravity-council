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


def role(rid="independent-check", model="model-alpha-high", access="read", instruction=None):
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
        with self.assertRaises(common.CouncilError): common.parse_roles([dict(role(), temperature="0.2")])

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
        self.assertNotEqual(first, runtime.session_path(cfg, role(model="model-beta-high")))
        self.assertNotEqual(first, runtime.session_path(cfg, role(access="write")))
        self.assertNotEqual(first, runtime.session_path(dict(cfg, task_key="other"), role()))

    def test_duration_validation(self):
        self.assertEqual(runtime.seconds("60m"), 3600)
        for value in ("0", "0s", "-1s", "60", "unlimited"):
            with self.subTest(value=value), self.assertRaises(common.CouncilError): runtime.seconds(value)

    def test_argv_has_no_print_prompt(self):
        cfg = life.config_for(self.prepare())
        argv = runtime.argv_for("agy", role(), cfg, None)
        self.assertFalse({"-p", "--print", "--prompt"} & set(argv))
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
        parsed = models.parse_models("model-alpha-high   Model Alpha (High)\nmodel-beta-high   Model Beta (High)")
        self.assertEqual(len(parsed), 2)

    def test_ansi_table(self):
        self.assertEqual(models.parse_models("\x1b[32mmodel-alpha-high\x1b[0m  Model Alpha")[0]["id"], "model-alpha-high")

    def test_conflicting_catalog(self):
        with self.assertRaises(common.CouncilError): models.parse_models("model-a  A\nmodel-a  B")

    def test_unparseable_catalog_refused(self):
        with self.assertRaises(common.CouncilError): models.parse_models("unexpected output")

    def test_doctor_is_metadata_only(self):
        record = self.base / "calls.jsonl"
        with patch.dict(os.environ, {"FAKE_AGY_RECORD": str(record)}):
            snap = models.discover(self.project)
        self.assertIn("model-alpha-high", {m["id"] for m in snap["models"]})
        self.assertNotIn("preferred", snap)
        self.assertFalse(snap["auth"]["entitlement_verified"])
        calls = [json.loads(s) for s in record.read_text().splitlines()]
        self.assertEqual([c["args"] for c in calls], [["--version"], ["--help"], ["models"]])

    def test_missing_capability_fails_closed(self):
        with patch.dict(os.environ, {"FAKE_AGY_MISSING_FLAG": "--conversation"}), self.assertRaises(common.CouncilError): models.discover(self.project)

    def test_unknown_pin_not_substituted(self):
        snap = models.discover(self.project)
        with self.assertRaises(common.CouncilError): models.validate_models([role(model="alpha-guessed")], snap)


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

    def test_step_tool_without_a_name_refused(self):
        step = {"event": "step_update", "step_update": {"step_type": "tool", "state": "DONE"}}
        with self.assertRaises(common.CouncilError): feed(protocol.Stream(role()), init_event(), step)

    def test_step_tool_absent_from_the_registry_still_refused(self):
        # init.tools doesn't list everything agy can run, so absence proves nothing.
        step = {"event": "step_update", "step_update": {"step_type": "tool", "tool_name": "delete_file", "state": "ACTIVE"}}
        with self.assertRaises(common.CouncilError): feed(protocol.Stream(role()), init_event(), step)

    def test_read_file_step_is_view_file(self):
        # agy 1.5.2's step name for a read; the role was killed for it.
        step = {"event": "step_update", "step_update": {"step_type": "tool", "tool_name": "read_file", "state": "ACTIVE"}}
        s = protocol.Stream(role()); feed(s, init_event(), step, result_event())
        self.assertEqual(s.tool_errors, [])
        self.assertIsNotNone(s.result)

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
        run = self.prepare([role("one"), role("two", model="model-beta-high")])
        out = self.cli("start", run); self.assertEqual(out.returncode, 0, out.stderr)
        self.wait(run)
        results = life.collect(run)["results"]
        self.assertEqual({r["requested_model"] for r in results}, {"model-alpha-high", "model-beta-high"})

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
        third = self.prepare([role(model="model-beta-high")]); self.assertEqual(self.cli("run", third).returncode, 0)
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
            # Wait for the worker's first output too: killed before it writes,
            # the fake legitimately exits on EPIPE and there is nothing to reap.
            log = run / "logs" / "independent-check.1.stdout.ndjson"
            if worker and log.exists() and log.stat().st_size: break
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

    def test_restricted_profiles_do_not_contain_subagents(self):
        for access in ("read", "write", "execute"):
            self.assertNotIn("invoke_subagent", policy.profile_text(access))
        for access in policy.TOOLS:
            self.assertIn("subagent: false", policy.profile_text(access))

    def test_settings_and_credentials_unchanged(self):
        path = self.home / ".gemini/antigravity-cli"
        path.mkdir(); settings = path / "settings.json"
        settings.write_text('{"toolPermission":"request-review"}')
        before = settings.read_bytes()
        run = self.prepare(); self.cli("run", run)
        self.assertEqual(settings.read_bytes(), before)


def full(rid="full-worker", model="model-alpha-high", instruction=None, **extra):
    return dict(role(rid, model=model, access="full", instruction=instruction), **extra)


class FullAccessTests(Isolated):
    """Full access is the default: bypass, no sandbox, edits, shell, network, web, no writer limit."""

    def test_omitted_access_is_full(self):
        r = role(); del r["access"]
        self.assertEqual(common.parse_roles([r])[0]["access"], "full")

    def test_full_profile_grants_edit_shell_and_web(self):
        tools = policy.TOOLS["full"]
        for name in ("view_file", "write_to_file", "replace_file_content", "multi_replace_file_content",
                     "run_command", "command_status", "send_command_input", "manage_task",
                     "search_web", "read_url_content"):
            self.assertIn(name, tools)
        text = policy.profile_text("full")
        self.assertIn("commandExecutionPolicy: eager", text)
        self.assertNotIn("auto-denied", text)
        # Restricted profiles keep their exact bytes, so their hash names do not move.
        self.assertEqual(policy.profile_name("read"), "agc-read-10bf6f2220bd")
        self.assertEqual(policy.profile_name("execute"), "agc-execute-01334d90a963")

    def test_full_argv_bypasses_permissions_and_sandbox(self):
        cfg = life.config_for(self.prepare([full()]))
        argv = runtime.argv_for("agy", full(), cfg, None, self.project)
        self.assertIn("--dangerously-skip-permissions", argv)
        self.assertNotIn("--sandbox", argv)
        self.assertEqual(argv[argv.index("--mode") + 1], "accept-edits")
        self.assertEqual(json.loads(argv[argv.index("--settings") + 1]), policy.FULL_SETTINGS)
        self.assertEqual(policy.FULL_SETTINGS["enableTerminalSandbox"], False)
        self.assertEqual(policy.FULL_SETTINGS["allowNonWorkspaceAccess"], True)
        self.assertEqual(argv[argv.index("--agent") + 1], policy.profile_name("full"))
        self.assertFalse({"-p", "--print", "--prompt"} & set(argv))
        self.assertNotIn("--effort", argv)
        # agy 1.5.12 made an added root the command cwd; full roles read anywhere instead.
        self.assertNotIn("--add-dir", argv)
        self.assertIn("--add-dir", runtime.argv_for("agy", role(), cfg, None, self.project))

    def test_observed_step_aliases_for_restricted_roles(self):
        w = role(access="write")
        steps = [{"event": "step_update", "step_update": {"step_type": "tool", "tool_name": n, "state": "DONE"}}
                 for n in ("read_file", "write_file", "edit_file")]
        s = protocol.Stream(w); feed(s, init_event(w), *steps, result_event())
        self.assertEqual(s.outcome(0), (True, ""))
        r = role()
        with self.assertRaises(common.CouncilError):
            feed(protocol.Stream(r), init_event(r), steps[1])

    def test_concurrent_setup_never_sees_a_partial_profile(self):
        for path in policy.target_dir().glob("agc-*.md"):
            path.unlink()
        procs = [subprocess.Popen([sys.executable, str(CLI), "setup"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for _ in range(8)]
        codes = [p.wait(timeout=30) for p in procs]
        self.assertEqual(codes, [0] * 8, [p.stderr.read() for p in procs])
        for access in policy.TOOLS:
            path = policy.target_dir() / (policy.profile_name(access) + ".md")
            self.assertEqual(path.read_text(), policy.profile_text(access))
        self.assertEqual(sorted(p.name for p in policy.target_dir().iterdir() if p.name.startswith(".")), [])

    def test_full_prompt_has_no_path_fence(self):
        prompt = runtime.prompt_for(full(), context(), cwd=self.project, run_dir=self.base)
        self.assertIn("Full access", prompt)
        self.assertIn("any path on this machine", prompt)
        self.assertNotIn("auto-denies", prompt)
        self.assertIn("auto-denies", runtime.prompt_for(role(), context(), cwd=self.project, run_dir=self.base))
        self.assertIn("full agy harness", prompt)
        self.assertNotIn("Do not spawn subagents", prompt)
        self.assertIn("Do not spawn subagents", runtime.prompt_for(role(), context(), cwd=self.project, run_dir=self.base))

    def unpinned(self, **extra):
        r = full(**extra); del r["model"]; return r

    def test_effort_is_optional_and_validated(self):
        self.assertEqual(common.parse_roles([self.unpinned(effort="xhigh")])[0]["effort"], "xhigh")
        self.assertNotIn("effort", common.parse_roles([full()])[0])
        for bad in ("ultra", "", "High", 3, None):
            with self.subTest(bad=bad), self.assertRaises(common.CouncilError):
                common.parse_roles([self.unpinned(effort=bad)])
        # agy 1.5.12: "--model <variant> conflicts with --effort"; catalog IDs encode effort.
        with self.assertRaisesRegex(common.CouncilError, "omit model"):
            common.parse_roles([full(effort="high")])
        cfg = life.config_for(self.prepare([self.unpinned(effort="max")]))
        argv = runtime.argv_for("agy", self.unpinned(effort="max"), cfg, None)
        self.assertEqual(argv[argv.index("--effort") + 1], "max")
        self.assertNotIn("--model", argv)
        self.assertNotEqual(runtime.session_path(cfg, self.unpinned(effort="max")),
                            runtime.session_path(cfg, self.unpinned()))

    def test_full_roles_have_no_writer_limit(self):
        policy.validate_authority([full("one"), full("two"), full("three")], None)
        # The restricted single-writer rule still applies to restricted mutators.
        with self.assertRaises(common.CouncilError):
            policy.validate_authority([full("one"), role("two", access="write"), role("three", access="execute")], "two")
        policy.validate_authority([full("one"), role("two", access="write")], "two")
        with self.assertRaises(common.CouncilError): policy.validate_authority([full("one")], "one")

    def test_full_init_requires_always_proceed(self):
        r = full()
        feed(protocol.Stream(r), init_event(r, permission_mode="always-proceed"))
        for mode in ("request-review", None):
            with self.subTest(mode=mode), self.assertRaises(common.CouncilError):
                feed(protocol.Stream(r), init_event(r, permission_mode=mode))

    def test_full_step_outside_profile_is_recorded_not_fatal(self):
        r = full()
        steps = [{"event": "step_update", "step_update": {"step_type": "tool", "tool_name": n, "state": "DONE"}}
                 for n in ("read_file", "run_command", "search_web", "sed_file", "sed_file")]
        s = protocol.Stream(r); feed(s, init_event(r, permission_mode="always-proceed"), *steps, result_event())
        self.assertEqual(s.outcome(0), (True, ""))
        self.assertEqual(len(s.audit_warnings), 1)
        self.assertIn("sed_file", s.audit_warnings[0])
        nameless = {"event": "step_update", "step_update": {"step_type": "tool", "state": "DONE"}}
        with self.assertRaises(common.CouncilError):
            feed(protocol.Stream(r), init_event(r, permission_mode="always-proceed"), nameless)

    def test_missing_full_profile_names_setup(self):
        (policy.target_dir() / (policy.profile_name("full") + ".md")).unlink()
        with self.assertRaisesRegex(common.CouncilError, "run `setup`"):
            policy.check_profiles(self.project, [full()])

    def test_full_role_may_use_agy_subagents(self):
        r = full()
        step = {"event": "step_update", "step_update": {"step_type": "tool", "tool_name": "invoke_subagent",
                                                        "subagent_info": {"subagents": [{}]}}}
        s = protocol.Stream(r); feed(s, init_event(r, permission_mode="always-proceed"), step, result_event())
        self.assertEqual(s.outcome(0), (True, ""))
        self.assertTrue(s.unsafe_to_replay)
        self.assertIn("subagents", " ".join(s.audit_warnings))
        with self.assertRaises(common.CouncilError):
            feed(protocol.Stream(role()), init_event(), step)

    def test_full_profile_has_agy_harness(self):
        for name in ("invoke_subagent", "define_subagent", "manage_subagents", "run_workflow", "schedule"):
            self.assertIn(name, policy.TOOLS["full"])
        text = policy.profile_text("full")
        self.assertIn("inheritMcp: true", text)
        self.assertNotIn("mcpServers: []", text)
        # Names agy 1.5.12 refused in a custom agent ("not found in registry").
        for refused in ("browser_subagent", "call_mcp_tool", "sed_file", "wait_5_seconds"):
            self.assertNotIn(refused, policy.TOOLS["full"])

    def test_two_turn_stream_accounting(self):
        r = full(); init = init_event(r, permission_mode="always-proceed")
        step = {"event": "step_update", "step_update": {"step_type": "tool", "tool_name": "run_command", "state": "DONE"}}
        # A result or step before the second prompt is sent is out of order.
        s = protocol.Stream(r); feed(s, init, result_event(response="draft"))
        with self.assertRaises(common.CouncilError): feed(s, step)
        s = protocol.Stream(r); feed(s, init, result_event(response="draft"))
        with self.assertRaises(common.CouncilError): feed(s, result_event())
        # Both turns must succeed; the latest result is the final answer.
        new_turn = {"event": "step_update", "step_update": {"step_type": "user_input", "state": "DONE"}}
        s = protocol.Stream(r); feed(s, init, result_event(response="draft", num_turns=1)); s.prompts_sent = 2
        feed(s, new_turn, step, result_event(response="final", num_turns=2))
        self.assertEqual(s.outcome(0), (True, "")); self.assertEqual(s.result["response"], "final")
        s = protocol.Stream(r); feed(s, init, result_event(response="draft", num_turns=1)); s.prompts_sent = 2
        self.assertIn("turn 2", s.outcome(0)[1])
        feed(s, new_turn, result_event(status="ERROR", response="", error="boom", num_turns=2))
        self.assertEqual(s.outcome(1)[0], False)
        self.assertEqual(s.outcome(0), (False, "self-verification turn: boom"))

    def test_full_role_runs_forced_self_verification_turn(self):
        record = self.home / "calls.ndjson"
        run = self.prepare([full()])
        with patch.dict(os.environ, {"FAKE_AGY_RECORD": str(record)}): out = self.cli("run", run)
        self.assertEqual(out.returncode, 0, out.stderr)
        result = life.collect(run)["results"][0]
        self.assertTrue(result["verification_turn_completed"]); self.assertEqual(result["turns_completed"], 2)
        self.assertTrue(result["response"].startswith("Self-verified final report (turn 2)"))
        self.assertTrue(result["draft_response"].startswith("Verified fixture reply."))
        turns = [json.loads(l) for l in record.read_text().splitlines() if '"turn"' in l]
        self.assertEqual([t["turn"] for t in turns], [1, 2])
        self.assertTrue(turns[1]["prompt_head"].startswith("SELF-VERIFICATION TURN"))
        self.assertIn("verification turn: completed", (run / "replies" / "full-worker.md").read_text())
        self.assertEqual(result["verification_turn"], "completed")
        args = [json.loads(l)["args"] for l in record.read_text().splitlines() if '"args"' in l and "--input-format" in l][0]
        # agy enforces --print-timeout per turn, so the configured value is passed unchanged.
        self.assertEqual(args[args.index("--print-timeout") + 1], "60m")

    def test_no_verify_turn_opt_out_runs_full_roles_single_turn(self):
        record = self.home / "calls.ndjson"
        run = self.prepare([full()], verify_turn=False)
        self.assertFalse(life.check(run)["verify_turn"])
        with patch.dict(os.environ, {"FAKE_AGY_RECORD": str(record)}): out = self.cli("run", run)
        self.assertEqual(out.returncode, 0, out.stderr)
        turns = [json.loads(l)["turn"] for l in record.read_text().splitlines() if '"turn"' in l]
        self.assertEqual(turns, [1])
        result = life.collect(run)["results"][0]
        self.assertEqual(result["verification_turn"], "off")
        self.assertFalse(result["verification_turn_completed"]); self.assertIsNone(result["draft_response"])
        self.assertEqual(result["warnings"], [])
        self.assertIn("verification turn: off", (run / "replies" / "full-worker.md").read_text())
        # The CLI flag reaches launch.json.
        out = self.cli("prepare", "--cwd", self.project, "--session-key", "s", "--task-key", "t", "--no-verify-turn")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(common.read_json(Path(json.loads(out.stdout)["run_dir"]) / "launch.json")["verify_turn"])

    def test_restricted_role_stays_single_turn(self):
        record = self.home / "calls.ndjson"
        run = self.prepare()
        with patch.dict(os.environ, {"FAKE_AGY_RECORD": str(record)}): out = self.cli("run", run)
        self.assertEqual(out.returncode, 0, out.stderr)
        turns = [json.loads(l)["turn"] for l in record.read_text().splitlines() if '"turn"' in l]
        self.assertEqual(turns, [1])
        self.assertFalse(life.collect(run)["results"][0]["verification_turn_completed"])

    def test_failed_turn_one_skips_verification(self):
        record = self.home / "calls.ndjson"
        run = self.prepare([full()])
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "quota", "FAKE_AGY_RECORD": str(record)}):
            out = self.cli("run", run)
        self.assertEqual(out.returncode, 1)
        turns = [json.loads(l)["turn"] for l in record.read_text().splitlines() if '"turn"' in l]
        self.assertEqual(turns, [1])
        self.assertEqual(life.collect(run)["results"][0]["failure_class"], "quota")
        self.assertEqual(life.collect(run)["results"][0]["verification_turn"], "not-run")

    def test_failed_verification_keeps_draft_unverified(self):
        run = self.prepare([full()])
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "verify-fail"}): out = self.cli("run", run)
        self.assertEqual(out.returncode, 1)
        result = life.collect(run)["results"][0]
        self.assertFalse(result["ok"]); self.assertFalse(result["verification_turn_completed"])
        self.assertEqual(result["turns_completed"], 1); self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["verification_turn"], "failed")
        self.assertTrue(result["response"].startswith("Verified fixture reply."))
        self.assertIn("self-verification", result["error"])
        self.assertIn("unverified first-turn draft", " ".join(result["warnings"]))

    def test_rejected_first_result_never_gets_a_verify_turn(self):
        # Sol review P1: the audit failed on turn one, yet the verify prompt was still sent.
        record = self.home / "calls.ndjson"
        run = self.prepare([full()])
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "conv-mismatch", "FAKE_AGY_RECORD": str(record)}):
            out = self.cli("run", run)
        self.assertEqual(out.returncode, 1)
        self.assertEqual(life.collect(run)["results"][0]["failure_class"], "protocol")
        turns = [json.loads(l)["turn"] for l in record.read_text().splitlines() if '"turn"' in l]
        self.assertEqual(turns, [1])

    def test_duplicate_result_is_not_accepted_as_verification(self):
        # Sol review P1: a repeated turn-one result must not pass as the verification reply.
        run = self.prepare([full()])
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "dup-result"}): out = self.cli("run", run)
        self.assertEqual(out.returncode, 1)
        result = life.collect(run)["results"][0]
        self.assertFalse(result["ok"]); self.assertFalse(result["verification_turn_completed"])
        self.assertEqual(result["failure_class"], "protocol")

    def test_later_result_must_be_a_new_turn_of_the_same_conversation(self):
        r = full(); init = init_event(r, permission_mode="always-proceed")
        new_turn = {"event": "step_update", "step_update": {"step_type": "user_input", "state": "DONE"}}
        def two(second, with_input=True):
            s = protocol.Stream(r); feed(s, init, result_event(response="draft", num_turns=1)); s.prompts_sent = 2
            feed(s, *([new_turn] if with_input else []), second)
            return s
        two(result_event(response="final", num_turns=2))
        with self.assertRaisesRegex(common.CouncilError, "new user turn"):
            two(result_event(response="final", num_turns=2), with_input=False)
        with self.assertRaisesRegex(common.CouncilError, "num_turns"):
            two(result_event(response="final", num_turns=1))
        missing = result_event(response="final", num_turns=2); del missing["result"]["conversation_id"]
        with self.assertRaisesRegex(common.CouncilError, "conversation_id"):
            two(missing)

    def test_denied_actions_from_any_turn_are_kept(self):
        # Sol review P2: a turn-one denial vanished when the verify turn was clean.
        run = self.prepare([full()])
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "denied-first"}): out = self.cli("run", run)
        self.assertEqual(out.returncode, 0, out.stderr)
        result = life.collect(run)["results"][0]
        self.assertEqual(result["denied_actions"], [{"action": "command"}])
        self.assertIn("denied actions", " ".join(result["warnings"]))

    def test_cancel_kills_commands_in_their_own_process_group(self):
        # Observed live on agy 1.5.12: a cancelled worker's shell command survived as an orphan.
        child_file = self.home / "child.pid"
        run = self.prepare([full()])
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "session-child", "FAKE_AGY_CHILD": str(child_file)}):
            self.assertEqual(self.cli("start", run).returncode, 0)
        deadline = time.monotonic() + 10
        while not child_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        child = int(child_file.read_text())
        self.assertNotEqual(os.getpgid(child), os.getpgid(os.getpid()))
        self.assertEqual(self.cli("cancel", run).returncode, 0)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            os.kill(child, signal.SIGKILL)
            self.fail("command process group survived cancel")

    def test_failed_turn_one_with_live_process_gets_no_verify_prompt(self):
        # Review finding: the earlier test passed only because the fake exited after the error.
        record = self.home / "calls.ndjson"
        run = self.prepare([full()])
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "error-stay", "FAKE_AGY_RECORD": str(record)}):
            out = self.cli("run", run)
        self.assertEqual(out.returncode, 1)
        turns = [json.loads(l)["turn"] for l in record.read_text().splitlines() if '"turn"' in l]
        self.assertEqual(turns, [1])

    def test_completed_verification_survives_a_later_process_failure(self):
        # Review finding: a nonzero exit after a good verify turn discarded the verified report.
        run = self.prepare([full()])
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "exit1-after-verify"}): out = self.cli("run", run)
        self.assertEqual(out.returncode, 1)
        result = life.collect(run)["results"][0]
        self.assertFalse(result["ok"])
        self.assertTrue(result["verification_turn_completed"])
        self.assertTrue(result["response"].startswith("Self-verified final report (turn 2)"))
        self.assertTrue(result["draft_response"].startswith("Verified fixture reply."))
        self.assertIn("failed afterwards", " ".join(result["warnings"]))

    def test_full_role_with_dead_saved_conversation_restarts_fresh(self):
        # Review finding: only read roles dropped a dead pointer, so full roles failed on every rerun.
        first = self.prepare([full()]); self.assertEqual(self.cli("run", first).returncode, 0)
        second = self.prepare([full()])
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "stale"}): out = self.cli("run", second)
        self.assertEqual(out.returncode, 0, out.stderr)
        result = life.collect(second)["results"][0]
        self.assertEqual(result["attempts"], 2)
        self.assertIn("continuity lost", " ".join(result["warnings"]))

    def test_restricted_role_rejects_real_subagent_step_shape(self):
        # agy 1.5.12 reports a running subagent as step_type "subagent" with subagent_info.
        step = {"event": "step_update", "step_update": {"step_type": "subagent", "state": "ACTIVE",
                                                        "tool_name": "invoke_subagent", "subagent_info": {"subagents": [{}]}}}
        with self.assertRaises(common.CouncilError):
            feed(protocol.Stream(role()), init_event(), step)
        r = full(); s = protocol.Stream(r)
        feed(s, init_event(r, permission_mode="always-proceed"), step, result_event())
        self.assertEqual(s.outcome(0), (True, ""))

    def test_stray_step_between_turns_is_refused_deterministically(self):
        r = full(); stray = {"event": "step_update", "step_update": {"step_type": "tool", "tool_name": "run_command"}}
        s = protocol.Stream(r); feed(s, init_event(r, permission_mode="always-proceed"), result_event(num_turns=1))
        s.prompts_sent = 2  # even after the next prompt is out, a non-input step may not open the turn
        with self.assertRaisesRegex(common.CouncilError, "user_input"):
            feed(s, stray)

    def test_cancel_during_verification_keeps_the_draft(self):
        run = self.prepare([full()])
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "verify-hang"}):
            self.assertEqual(self.cli("start", run).returncode, 0)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = common.read_json(run / "state.json") if (run / "state.json").exists() else {}
            if state.get("roles", {}).get("full-worker", {}).get("phase") == "self-verifying":
                break
            time.sleep(0.05)
        else:
            self.fail("verification turn never started")
        self.assertEqual(self.cli("cancel", run).returncode, 0)
        result = life.collect(run)["results"][0]
        self.assertEqual(result["failure_class"], "cancelled")
        self.assertTrue(result["response"].startswith("Verified fixture reply."))
        self.assertEqual(result["draft_response"], result["response"])
        self.assertEqual(result["verification_turn"], "failed")
        self.assertIn("unverified first-turn draft", " ".join(result["warnings"]))

    def test_watchdog_allows_each_turn_its_own_print_timeout(self):
        # Two 0.7 s turns under a 1 s --print-timeout: 1.4 s in total exceeds one turn's
        # budget but not two. With the grace shrunk, a one-turn watchdog would kill this.
        run = self.prepare([full()], print_timeout="1s")
        logdir = common.private_dir(run / "logs", create=True)
        cfg = life.config_for(run)
        with patch.object(runtime, "WATCHDOG_GRACE", 0.3), \
                patch.dict(os.environ, {"FAKE_AGY_TURN_SECONDS": "0.7"}):
            two = asyncio.run(runtime.run_attempt(models.cli_binary(), full(), context(), cfg, None,
                                                  logdir, 1, lambda *a, **k: None))
            self.assertEqual(two.error, "", two.error)
            self.assertEqual(len(two.stream.results), 2)
            # Control: the same work as one 1.4 s turn on a single-turn role is cut off.
            with patch.dict(os.environ, {"FAKE_AGY_TURN_SECONDS": "1.4"}):
                one = asyncio.run(runtime.run_attempt(models.cli_binary(), role(), context(), cfg, None,
                                                      logdir, 2, lambda *a, **k: None))
        self.assertEqual(one.failure_class, "timeout")

    def test_subagents_in_verification_are_evidence(self):
        run = self.prepare([full()])
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "verify-subagent"}): out = self.cli("run", run)
        self.assertEqual(out.returncode, 0, out.stderr)
        result = life.collect(run)["results"][0]
        self.assertTrue(result["verification_turn_completed"])
        self.assertIn("subagents", " ".join(result["warnings"]))

    def test_preflight_requires_full_access_flags(self):
        for flag in ("--settings", "--dangerously-skip-permissions", "--mode"):
            run = self.prepare([full()], task=f"flag-{flag}")
            with self.subTest(flag=flag), patch.dict(os.environ, {"FAKE_AGY_MISSING_FLAG": flag}):
                with self.assertRaisesRegex(common.CouncilError, "full access needs"):
                    life.check(run, refresh=True)

    def test_preflight_requires_effort_flag_only_when_used(self):
        run = self.prepare([self.unpinned(effort="high")])
        with patch.dict(os.environ, {"FAKE_AGY_MISSING_FLAG": "--effort"}):
            with self.assertRaisesRegex(common.CouncilError, "--effort"):
                life.check(run, refresh=True)
            life.check(self.prepare([full()], task="no-effort"), refresh=True)

    def test_several_full_editors_run_end_to_end(self):
        record = self.home / "calls.ndjson"
        run = self.prepare([full("one"), full("two", model="model-beta-high")])
        self.assertEqual(life.check(run)["full_access_roles"], ["one", "two"])
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "full-ops", "FAKE_AGY_RECORD": str(record)}):
            out = self.cli("run", run)
        self.assertEqual(out.returncode, 0, out.stderr)
        results = life.collect(run)["results"]
        self.assertTrue(all(r["ok"] and r["permission_mode"] == "always-proceed" for r in results))
        self.assertEqual({r["access"] for r in results}, {"full"})
        self.assertIn("sed_file", " ".join(results[0]["warnings"]))
        calls = [json.loads(line)["args"] for line in record.read_text().splitlines() if "--input-format" in line]
        self.assertEqual(len(calls), 2)
        for args in calls:
            self.assertIn("--dangerously-skip-permissions", args)
            self.assertNotIn("--sandbox", args)
            self.assertIn("--settings", args)

    def test_permission_mode_mismatch_fails_role(self):
        run = self.prepare([full()])
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "admin-review"}): out = self.cli("run", run)
        self.assertEqual(out.returncode, 1)
        result = life.collect(run)["results"][0]
        self.assertEqual(result["failure_class"], "protocol")
        self.assertIn("always-proceed", result["error"])

    def test_denied_actions_are_coverage_warnings(self):
        run = self.prepare([full()])
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "denied"}): out = self.cli("run", run)
        self.assertEqual(out.returncode, 0, out.stderr)
        result = life.collect(run)["results"][0]
        # The fixture denies an action in each of the two turns; both are kept.
        self.assertEqual(result["denied_actions"], [{"action": "command"}, {"action": "command"}])
        self.assertIn("denied actions", " ".join(result["warnings"]))

    def test_full_role_is_never_replayed(self):
        run = self.prepare([full()])
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "transient"}): self.cli("run", run)
        self.assertEqual(life.collect(run)["results"][0]["attempts"], 1)

    def test_full_fleets_share_the_workspace(self):
        # No writer limit: full councils on one worktree are not serialized.
        first, second = self.prepare([full()], task="a"), self.prepare([full()], task="b")
        with patch.dict(os.environ, {"FAKE_AGY_SCENARIO": "sleep"}):
            self.assertEqual(self.cli("start", first).returncode, 0)
            self.assertEqual(self.cli("start", second).returncode, 0)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            phases = [life.observe(r)["state"].get("roles", {}).get("full-worker", {}).get("phase") for r in (first, second)]
            if phases == ["running", "running"]:
                break
            time.sleep(0.05)
        self.assertEqual(phases, ["running", "running"])


class NativeDefaultModelTests(Isolated):
    """A role may omit model to inherit agy's configured default; no model list is built in."""

    def unpinned(self, rid="native-default"):
        r = role(rid)
        del r["model"]
        return r

    def test_omitted_model_parses_and_placeholders_refused(self):
        self.assertNotIn("model", common.parse_roles([self.unpinned()])[0])
        for placeholder in ("inherit", "Default"):
            with self.subTest(placeholder=placeholder), self.assertRaises(common.CouncilError):
                common.parse_roles([role(model=placeholder)])

    def test_omitted_model_sends_no_model_flag(self):
        run = self.prepare([self.unpinned()])
        argv = runtime.argv_for("agy", self.unpinned(), life.config_for(run), None)
        self.assertNotIn("--model", argv)
        self.assertIn("--agent", argv)

    def test_init_without_model_accepted_only_when_unpinned(self):
        r = self.unpinned()
        init = init_event(role())
        del init["init"]["model"]
        s = protocol.Stream(r); feed(s, init, result_event()); self.assertEqual(s.outcome(0), (True, ""))
        with self.assertRaises(common.CouncilError):
            feed(protocol.Stream(role()), init)

    def test_unpinned_role_runs_end_to_end_and_reports_no_pin(self):
        run = self.prepare([self.unpinned()])
        self.assertEqual(self.cli("run", run).returncode, 0)
        result = life.collect(run)["results"][0]
        self.assertTrue(result["ok"])
        self.assertIsNone(result["requested_model"])
        self.assertIn("agy default", (run / "replies" / "native-default.md").read_text())

    def test_unpinned_session_is_isolated_from_pinned(self):
        cfg = life.config_for(self.prepare())
        self.assertNotEqual(runtime.session_path(cfg, self.unpinned("independent-check")),
                            runtime.session_path(cfg, role()))


if __name__ == "__main__":
    unittest.main()
