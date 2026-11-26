"""Pilot policy and worker state-machine tests (fixture bytes, not render)."""
import contextlib
import io
import json
from dataclasses import replace
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from montara_core.adapters.sqlite_db import SQLiteDatabase
from montara_core.adapters.zucker_engine import EngineExecutionError
from montara_core.application.identity import AuthenticationService, AuthError, OIDCSettings
from montara_core.application.ports import StorageError
from montara_core.bootstrap.config import Config, ConfigError, load_config
from montara_core.bootstrap.core import build_core
from montara_core.bootstrap.worker import _run, run_worker, worker_lock, WorkerBusy
from montara_core.domain.execution import Shot
from montara_core.domain.jobs import JobState, TransitionError
from montara_core.domain.pilot import PilotSettings, MAX_FILE_BYTES, validate_pilot_snapshot
from montara_core.domain.projects import InvalidValue, ProjectNotFound
from test_publication import candidate_for, FixtureAdapter


class TestProvider:
    def authorization_url(self, state, nonce, challenge):
        self.state = state
        return "https://idp.invalid/authorize"

    def exchange(self, code, verifier, nonce):
        return "https://idp.invalid", code


class PilotTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.media,self.workspace,self.artifacts,self.renderer,self.engine = [
            self.root/n for n in ("media","workspace","artifacts","renderer","engine")]
        for p in (self.media,self.workspace,self.artifacts,self.renderer,self.engine): p.mkdir()
        self.issuer="https://idp.invalid"
        self.oidc=OIDCSettings(self.issuer,self.issuer+"/authorize",self.issuer+"/token",
                               self.issuer+"/jwks","test",self.root/"secret","https://pilot.invalid")
        self.pilot=PilotSettings(self.issuer,("a","b"),self.renderer,self.engine)
        self.config=Config(sqlite_path=self.root/"core.db",media_root=self.media,
                           workspace_root=self.workspace,artifact_root=self.artifacts,
                           oidc=self.oidc,pilot=self.pilot)
        self.core=build_core(self.config)
        self.a=self.core.identity.resolve_identity(self.issuer,"a")
        self.b=self.core.identity.resolve_identity(self.issuer,"b")

    def make_recipe(self, owner=None, name="test"):
        owner=owner or self.a
        p=self.core.projects.create_project(owner,name).id
        folder=self.media/p; folder.mkdir()
        for fn in ("camera-a.mp4","camera-b.mp4","master.wav"):
            (folder/fn).write_bytes(fn.encode())
        _,camera,_=self.core.media.register_original(owner,p,"video","camera-a.mp4")
        _,master,_=self.core.media.register_original(owner,p,"audio","master.wav")
        recipe=self.core.media.save_recipe(owner,p,None,master.id,(Shot(camera.id,0,1000),))
        return p,recipe

    def make_job(self, owner=None, name="test"):
        owner=owner or self.a
        p,r=self.make_recipe(owner,name)
        return p,r,self.core.jobs.create_job(owner,p,"export",r.id)

    def run_once(self, adapter=None, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()) as report:
            code=_run(self.core,self.config,adapter or FixtureAdapter(),**kwargs)
        return code,json.loads(report.getvalue())

    def test_admission_before_account_provision_and_issuer_exact(self):
        provider=TestProvider()
        auth=AuthenticationService(self.core.identity,provider,admitted={(self.issuer,"a")})
        browser,_=auth.begin()
        with self.assertRaises(AuthError): auth.complete(browser,provider.state,"unadmitted")
        with SQLiteDatabase(self.config.sqlite_path)._connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM accounts").fetchone(),(2,))
        browser,_=auth.begin()
        token=auth.complete(browser,provider.state,"a")
        self.assertEqual(auth.actor(token),self.a)

    def test_fixture_registration_idempotent_and_project_scope(self):
        p,r=self.make_recipe()
        first=self.core.media.register_original(self.a,p,"video","camera-a.mp4")
        second=self.core.media.register_original(self.a,p,"video","camera-a.mp4")
        self.assertEqual(first,second)
        with self.assertRaises(InvalidValue):
            self.core.media.register_original(self.a,p,"video","other.mp4")
        with self.assertRaises(ProjectNotFound):
            self.core.media.pilot_summary(self.b,p)
        summary=self.core.media.pilot_summary(self.a,p)
        self.assertEqual(len(summary["media"]),2)
        self.assertEqual(summary["recipe"]["id"],r.id)
        (self.media/p/"camera-a.mp4").write_bytes(b"changed")
        with self.assertRaises(TransitionError):
            self.core.media.register_original(self.a,p,"video","camera-a.mp4")

    def test_atomic_project_and_job_limits_and_replay(self):
        p,r=self.make_recipe()
        q,s=self.make_recipe()
        def create(args):
            try:
                return build_core(self.config).jobs.create_job(self.a,*args)
            except TransitionError:
                return None
        with ThreadPoolExecutor(2) as pool:
            jobs=list(pool.map(create,[(p,"same",r.id),(q,"same",s.id)]))
        self.assertEqual(sum(j is not None for j in jobs),1)
        winner=next(j for j in jobs if j is not None)
        recipe=r if winner.project_id==p else s
        self.assertEqual(self.core.jobs.create_job(self.a,winner.project_id,"same",recipe.id),winner)
        for _ in range(2): self.core.projects.create_project(self.a,"more")
        with self.assertRaises(TransitionError): self.core.projects.create_project(self.a,"too many")
        self.core.projects.create_project(self.b,"independent")
        ref=self.core.jobs.claim_export().attempt
        self.core.jobs.fail(ref)
        for i in range(7):
            j=self.core.jobs.create_job(self.a,p,"again-"+str(i),r.id)
            self.core.jobs.fail(self.core.jobs.claim_export().attempt)
        with self.assertRaises(TransitionError): self.core.jobs.create_job(self.a,p,"ninth",r.id)

    def test_pilot_snapshot_and_recipe_limits(self):
        p,r=self.make_recipe()
        with self.assertRaises(InvalidValue):
            self.core.media.save_recipe(self.a,p,r.id,r.master_representation_id,
                                         (Shot(r.shots[0].representation_id,0,60100),))
        with self.assertRaises(InvalidValue):
            validate_pilot_snapshot({"shots":[{"start_ms":0,"end_ms":1000}],
                                     "inputs":[{"size_bytes":MAX_FILE_BYTES+1}]})
        # Size cap enforced during hashing, not only on caller metadata.
        (self.media/p/"camera-b.mp4").write_bytes(b"12345")
        from montara_core.adapters.local_media import LocalMedia
        with self.assertRaises(StorageError):
            LocalMedia(self.media,self.workspace,max_bytes=4).read(p,"camera-b.mp4")

    def test_worker_claims_export_and_owner_from_core(self):
        # Earlier synthetic and unadmitted export must not be executed.
        synthetic=self.core.jobs.create_job(self.a,self.core.projects.create_project(self.a,"synthetic").id,"s")
        alien=self.core.identity.resolve_identity(self.issuer,"unadmitted")
        _,_,foreign=self.make_job(alien)
        _,_,job=self.make_job(self.b)
        code,report=self.run_once()
        self.assertEqual((code,report["worker"],report["job_id"]),(0,"published",job.id))
        self.assertEqual(self.core.jobs.get_job(self.b,job.id).state,JobState.SUCCEEDED)
        self.assertEqual(self.core.jobs.get_job(alien,foreign.id).state,JobState.QUEUED)
        self.assertEqual(self.core.jobs.get_job(self.a,synthetic.id).state,JobState.QUEUED)
        self.assertEqual(self.run_once()[1]["worker"],"idle")

    def test_known_engine_failure_no_automatic_retry(self):
        _,_,job=self.make_job()
        class Broken:
            def execute(self,spec): raise EngineExecutionError("private error")
        code,report=self.run_once(Broken())
        self.assertEqual((code,report["worker"]),(4,"failed"))
        self.assertEqual(self.core.jobs.get_job(self.a,job.id).state,JobState.FAILED)
        self.assertEqual(self.run_once()[1]["worker"],"idle")

    def test_uncertain_publication_recover_and_stale_interrupt(self):
        _,_,job=self.make_job()
        with patch.object(type(self.core.publication),"publish",side_effect=StorageError("lost response")):
            code,report=self.run_once()
        self.assertEqual((code,report["worker"]),(5,"recovery_required"))
        ref=self.core.jobs.running_attempt()
        self.assertEqual(self.run_once()[0],3)
        self.assertEqual(self.run_once(recover="00000000-0000-4000-8000-000000000000")[0],3)
        self.assertEqual(self.run_once(recover=ref.id)[1]["worker"],"recovered")
        self.assertEqual(self.core.jobs.get_job(self.a,job.id).state,JobState.SUCCEEDED)
        self.assertEqual(self.run_once(interrupt=ref.id)[0],3)
        self.assertEqual(self.run_once(recover=ref.id)[1]["worker"],"recovered")

    def test_interrupt_and_lock_and_disk(self):
        _,_,job=self.make_job()
        ref=self.core.jobs.claim_export().attempt
        self.assertEqual(self.run_once()[0],3)
        self.assertEqual(self.run_once(interrupt=ref.id)[1]["worker"],"interrupted")
        self.assertEqual(self.core.jobs.get_job(self.a,job.id).state,JobState.FAILED)
        with worker_lock(self.config):
            with self.assertRaises(WorkerBusy):
                with worker_lock(self.config): pass
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_worker(self.config),3)
        with patch("montara_core.bootstrap.worker.shutil.disk_usage",
                   return_value=shutil._ntuple_diskusage(1,1,0)):
            self.assertEqual(self.run_once()[1]["worker"],"insufficient_disk")
        self.assertIsNone(self.core.jobs.running_attempt())

    def test_config_and_installed_worker_cli_idle(self):
        config=self.root/"core.toml"
        config.write_text("[storage]\n"+"\n".join(
            f'{k}={json.dumps(str(getattr(self.config,k)))}' for k in
            ("sqlite_path","media_root","workspace_root","artifact_root"))+
            "\n[oidc]\n"+"\n".join(f'{k}={json.dumps(str(getattr(self.oidc,k)))}' for k in
            ("issuer","authorization_endpoint","token_endpoint","jwks_uri","client_id",
             "client_secret_file","public_origin"))+
            "\n[pilot]\nallowed_subjects=[\"a\",\"b\"]\n"+
            f'renderer_root={json.dumps(str(self.renderer))}\nengine_root={json.dumps(str(self.engine))}\n')
        self.assertEqual(load_config(config),self.config)
        # Worker uses no IdP secret and binds no HTTP listener.
        child=subprocess.run([sys.executable,"-I","-m","montara_core","--config",str(config),"--worker-once"],
                             cwd=self.root,text=True,capture_output=True,timeout=20)
        self.assertEqual(child.returncode,0,child.stderr)
        self.assertEqual(json.loads(child.stdout)["worker"],"idle")
        config.write_text(config.read_text().replace('["a","b"]','[]'))
        with self.assertRaises(ConfigError): load_config(config)

    def test_pilot_rate_limits_and_logout_not_blocked(self):
        provider=TestProvider()
        auth=AuthenticationService(self.core.identity,provider,admitted={(self.issuer,"a")})
        browser,_=auth.begin()
        token=auth.complete(browser,provider.state,"a")
        for _ in range(29): auth.begin()
        with self.assertRaises(AuthError): auth.begin()
        for _ in range(30): auth.allow_mutation(self.a)
        with self.assertRaises(AuthError): auth.allow_mutation(self.a)
        auth.logout(token)
        with self.assertRaises(AuthError): auth.actor(token)
