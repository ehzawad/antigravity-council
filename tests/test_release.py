"""Release acceptance and additional failure-path regression checks, all offline."""
from __future__ import annotations
import asyncio
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from unittest.mock import patch

from test_council import (Isolated, CLI, ROOT, common, life, models, policy, protocol,
                          role, runtime)


class ReleaseTests(Isolated):
    def test_package_structure_and_sources_compile(self):
        result = subprocess.run([sys.executable, str(ROOT/'scripts/verify.py')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_profile_mirrors_are_current(self):
        for access in policy.TOOLS:
            path = policy.PROFILE_DIR / (policy.profile_name(access)+'.md')
            self.assertEqual(path.read_text(), policy.profile_text(access))

    def test_bad_access_type_is_usage_error(self):
        for access in ([], {}, True, 1, None):
            with self.subTest(access=access), self.assertRaises(common.CouncilError):
                common.parse_roles([dict(role(), access=access)])

    def test_bad_follow_timeout_is_usage_error(self):
        run=self.prepare()
        self.assertEqual(self.cli('follow',run,'--timeout','nan').returncode,2)

    def test_metadata_catalog_refreshed_at_launch(self):
        run=self.prepare(); life.check(run)
        with patch.dict(os.environ, {'FAKE_AGY_MODELS_BAD':'1'}):
            result=self.cli('start',run)
        self.assertEqual(result.returncode,2)
        self.assertFalse((run/'supervisor.lock').exists())

    def test_context_replacement_before_supervisor_dispatch_is_refused(self):
        run=self.prepare(); plan=life.check(run); fd=life.claim(run,plan)
        try:
            common.atomic_write(run/'context.md',(run/'context.md').read_text()+'modified phase\n')
            # Run in a subprocess so the test runner does not own supervisor signals.
            result=subprocess.run([sys.executable,str(CLI),'_supervise',str(run),
                                  '--lock-fd',str(fd),'--input-hash',plan['input_hash'],
                                  '--binary',plan['binary']],pass_fds=(fd,),capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,1)
            self.assertIn('staged inputs changed',(run/'supervisor-error.txt').read_text())
            self.assertEqual(list((run/'logs').iterdir()),[])
        finally:os.close(fd)

    def test_follower_timeout_does_not_cancel(self):
        run=self.prepare()
        with patch.dict(os.environ, {'FAKE_AGY_SCENARIO':'sleep'}): self.cli('start',run)
        result=self.cli('follow',run,'--timeout','0.2')
        self.assertEqual(result.returncode,5,result.stderr)
        self.assertEqual(life.observe(run)['lifecycle'],'running')

    def test_follower_tolerates_nonatomic_exit_snapshot(self):
        run=self.prepare()
        with patch.object(life,'observe',side_effect=[{'lifecycle':'unknown'}, {'lifecycle':'done','exit_code':0}]), \
                patch.object(life.time,'sleep'), contextlib.redirect_stdout(io.StringIO()) as output:
            code=life.follow(run)
        self.assertEqual(code,0)
        self.assertIn('verified_end',output.getvalue())

    def test_startup_update_failure_terminates_spawned_worker(self):
        run=self.prepare(); logdir=common.private_dir(run/'logs',create=True)
        seen=[]
        def update(rid,**kw):
            if kw.get('process'):
                seen.append(kw['process']);raise OSError('fixture status storage failure')
        with patch.dict(os.environ,{'FAKE_AGY_SCENARIO':'sleep'}):
            result=asyncio.run(runtime.run_attempt(models.cli_binary(),role(),(run/'context.md').read_text(),
                                                   life.config_for(run),None,logdir,1,update))
        self.assertEqual(result.failure_class,'io')
        self.assertTrue(seen)
        self.assertFalse(common.same_process(seen[0]))

    def test_pipe_holding_descendant_does_not_hang(self):
        run=self.prepare()
        with patch.dict(os.environ,{'FAKE_AGY_SCENARIO':'pipe-held'}): result=self.cli('run',run)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue(life.collect(run)['results'][0]['warnings'])

    def test_live_smoke_refuses_implicit_spending(self):
        result=subprocess.run([sys.executable,str(ROOT/'scripts/live_smoke.py'),'--model','gemini-4-argon-high'],
                              capture_output=True,text=True)
        self.assertEqual(result.returncode,2)
        self.assertIn('allow-billable',result.stderr)

    def test_live_smoke_harness_with_offline_double(self):
        with patch.dict(os.environ,{'FAKE_AGY_SCENARIO':'canary'}):
            result=subprocess.run([sys.executable,str(ROOT/'scripts/live_smoke.py'),'--allow-billable',
                                   '--model','gemini-4-argon-high'],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('"passed": true',result.stdout)
        # Scratch retention is intended for live use. This test removes only its
        # own scratch path after the script has exited.
        first=json.loads(result.stdout.splitlines()[0])
        import shutil
        shutil.rmtree(first['scratch_workspace'])

    def test_unstarted_directory_is_not_final_evidence(self):
        run=self.prepare()
        with self.assertRaises(common.CouncilError):life.collect(run)

    def test_only_known_event_limit_is_explicit(self):
        stream=protocol.Stream(role())
        with patch.object(protocol,'MAX_EVENT_BYTES',16),self.assertRaises(common.CouncilError):
            stream.feed(b'x'*17)
