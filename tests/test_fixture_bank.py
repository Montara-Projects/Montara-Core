"""H2.10 focused installed gate. Small synthetic bytes, no Engine/render.

Test-only substitute pins and bank UID; production pins and root ownership
are checked separately, never configured by an HTTP client.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import fcntl
import hashlib
from http.client import HTTPConnection
from importlib.resources import files
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch

from montara_core.adapters import local_fixture
from montara_core.adapters.sqlite_core import SQLiteCoreStore
from montara_core.application.identity import AuthenticationService, OIDCSettings
from montara_core.application.ports import StorageError
from montara_core.api.authenticated import create_authenticated_server, SESSION
from montara_core.bootstrap.config import Config, ConfigError, load_config
from montara_core.bootstrap.core import build_core
from montara_core.domain.jobs import TransitionError
from montara_core.domain.pilot import FIXTURE_PINS, FIXTURES, PilotSettings
from montara_core.domain.projects import ProjectNotFound
from test_pilot import TestProvider


class FixtureBankTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bank, self.media, self.work, self.artifacts, self.renderer, self.engine = [
            self.root / name for name in ("bank","media","work","artifacts","renderer","engine")]
        for directory in (self.bank,self.media,self.work,self.artifacts,self.renderer,self.engine):
            directory.mkdir(mode=0o700)
        self.bank.chmod(0o750)
        self.data = {name: ("test-only:"+name).encode() for name in FIXTURES}
        self.pins = tuple((name, hashlib.sha256(data).hexdigest(), len(data))
                          for name,data in self.data.items())
        for name,data in self.data.items():
            path = self.bank/name
            path.write_bytes(data)
            path.chmod(0o440)
        for target,value in (("montara_core.adapters.local_fixture.FIXTURE_PINS", self.pins),
                             ("montara_core.adapters.sqlite_media.FIXTURE_PINS", self.pins),
                             ("montara_core.adapters.local_fixture._BANK_OWNER", os.getuid())):
            patcher=patch(target,value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.issuer="https://idp.invalid"
        self.oidc=OIDCSettings(self.issuer,self.issuer+"/authorize",self.issuer+"/token",
                               self.issuer+"/jwks","test",self.root/"secret","https://pilot.invalid")
        pilot=PilotSettings(self.issuer,("a","b","c","d"),self.renderer,self.engine,
                            fixture_bank_root=self.bank)
        self.config=Config(sqlite_path=self.root/"core.db",media_root=self.media,
                           workspace_root=self.work,artifact_root=self.artifacts,oidc=self.oidc,pilot=pilot)
        self.core=build_core(self.config)
        self.owners=[self.core.identity.resolve_identity(self.issuer,s) for s in pilot.allowed_subjects]
        self.a,self.b=self.owners[:2]
        self.project=self.core.projects.create_project(self.a,"Same name").id

    def folder(self):
        path=self.media/self.project
        path.mkdir(mode=0o700)
        return path

    def place(self, path, name, data=None):
        file=path/name
        file.write_bytes(self.data[name] if data is None else data)
        file.chmod(0o600)
        return file

    def prepare(self):
        return self.core.media.prepare_fixture(self.a,self.project)

    def test_production_pins_unchanged_and_bounded(self):
        self.assertEqual(FIXTURE_PINS, (
            ("camera-a.mp4","f290175c1fb7f2587baf6e535b0f6adde0ad78b2d31ec250ae027a0590abfbc7",63410),
            ("camera-b.mp4","2817a6ab4b4833f40a5eb9534a90c2c173d3affe408045594f54daa09d87674d",63194),
            ("master.wav","8ae3abdb2fef54830f15e0177af8daa92cfb8481564fc9822a9acee950eaf33e",576078)))
        self.assertEqual(sum(s for _,_,s in FIXTURE_PINS),702682)

    def test_four_owners_same_name_idempotent_and_distinct(self):
        ids=set()
        for i,owner in enumerate(self.owners):
            project=self.project if i==0 else self.core.projects.create_project(owner,"Same name").id
            self.assertFalse((self.media/project).exists())
            first=self.core.media.prepare_fixture(owner,project)
            second=build_core(self.config).media.prepare_fixture(owner,project)
            self.assertEqual(first,second)
            self.assertTrue(first["fixture_available"])
            self.assertEqual(len(first["media"]),3)
            self.assertFalse(ids.intersection(r["id"] for r in first["media"]))
            ids.update(r["id"] for r in first["media"])
            for name,data in self.data.items():
                path=self.media/project/name
                self.assertEqual(path.read_bytes(),data)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
                self.assertNotEqual(path.stat().st_ino,(self.bank/name).stat().st_ino)
                self.assertEqual((self.bank/name).read_bytes(),data)
        self.assertEqual(len(ids),12)

    def test_owner_checked_before_any_filesystem_work(self):
        with patch.object(local_fixture.LocalFixtureBank,"prepare",side_effect=AssertionError("FS accessed")):
            with self.assertRaises(ProjectNotFound):
                self.core.media.prepare_fixture(self.b,self.project)
        self.assertEqual(list(self.media.iterdir()),[])

    def test_partial_existing_exact_and_legacy_registration(self):
        path=self.folder()
        existing=self.place(path,"camera-a.mp4")
        stamp=existing.stat().st_ino
        legacy=build_core(replace(self.config,pilot=replace(self.config.pilot,fixture_bank_root=None)))
        _,rep,_=legacy.media.register_original(self.a,self.project,"video","camera-a.mp4")
        result=self.prepare()
        self.assertEqual(len(result["media"]),3)
        self.assertIn(rep.id,[r["id"] for r in result["media"]])
        self.assertEqual(existing.stat().st_ino,stamp)
        self.assertEqual(self.prepare(),result)

    def test_conflict_preflight_does_not_copy_or_register(self):
        path=self.folder()
        wrong=self.place(path,"master.wav",b"wrong")
        with self.assertRaises(TransitionError): self.prepare()
        self.assertEqual(wrong.read_bytes(),b"wrong")
        self.assertEqual(set(p.name for p in path.iterdir()),{"master.wav"})
        self.assertEqual(self.core.media.pilot_summary(self.a,self.project)["media"],[])

    def test_legacy_conflicting_metadata_precedes_filesystem(self):
        path=self.folder()
        file=self.place(path,"master.wav",b"wrong")
        legacy=build_core(replace(self.config,pilot=replace(self.config.pilot,fixture_bank_root=None)))
        legacy.media.register_original(self.a,self.project,"audio","master.wav")
        file.unlink()
        with patch.object(local_fixture.LocalFixtureBank,"prepare",side_effect=AssertionError("FS accessed")):
            with self.assertRaises(TransitionError): self.prepare()
        self.assertEqual(list(path.iterdir()),[])

    def test_symlink_project_destination_and_hardlink(self):
        outside=self.root/"outside"
        outside.mkdir()
        (self.media/self.project).symlink_to(outside,target_is_directory=True)
        with self.assertRaises(TransitionError): self.prepare()
        self.assertEqual(list(outside.iterdir()),[])
        (self.media/self.project).unlink()
        path=self.folder()
        external=self.place(outside,"master.wav")
        target=path/"master.wav"
        target.symlink_to(external)
        with self.assertRaises(TransitionError): self.prepare()
        target.unlink()
        os.link(external,target)
        with self.assertRaises(TransitionError): self.prepare()
        self.assertEqual(external.read_bytes(),self.data["master.wav"])
        self.assertFalse((path/"camera-a.mp4").exists())

    def test_bank_mismatch_permissions_missing_and_symlink(self):
        source=self.bank/"master.wav"
        original=self.data["master.wav"]
        source.chmod(0o640)
        with self.assertRaises(StorageError): self.prepare()
        source.write_bytes(b"x"*len(original)); source.chmod(0o440)
        with self.assertRaises(StorageError): self.prepare()
        source.unlink()
        with self.assertRaises(StorageError): self.prepare()
        external=self.root/"external"; external.write_bytes(original)
        source.symlink_to(external)
        with self.assertRaises(StorageError): self.prepare()
        self.assertFalse((self.media/self.project).exists())

    def test_bank_owner_and_project_mode(self):
        with patch("montara_core.adapters.local_fixture._BANK_OWNER",os.getuid()+1):
            with self.assertRaises(StorageError): self.prepare()
        path=self.folder(); path.chmod(0o777)
        with self.assertRaises(TransitionError): self.prepare()
        self.assertEqual(list(path.iterdir()),[])

    def test_copy_failure_recovery_and_atomic_metadata(self):
        original=local_fixture.LocalFixtureBank._copy
        def copy(directory,name,data):
            if name=="camera-b.mp4": raise StorageError("injected")
            original(directory,name,data)
        with patch.object(local_fixture.LocalFixtureBank,"_copy",staticmethod(copy)):
            with self.assertRaises(StorageError): self.prepare()
        path=self.media/self.project
        self.assertEqual({p.name for p in path.iterdir()},{"camera-a.mp4"})
        self.assertEqual(self.core.media.pilot_summary(self.a,self.project)["media"],[])
        original_register=SQLiteCoreStore._register_verified
        def register(store,db,project,kind,locator,digest,size):
            if locator=="camera-b.mp4": raise StorageError("injected DB failure")
            return original_register(store,db,project,kind,locator,digest,size)
        with patch.object(SQLiteCoreStore,"_register_verified",register):
            with self.assertRaises(StorageError): self.prepare()
        self.assertEqual(self.core.media.pilot_summary(self.a,self.project)["media"],[])
        self.assertEqual({p.name for p in path.iterdir()},set(self.data))
        self.assertEqual(len(self.prepare()["media"]),3)

    def test_reserved_stage_crash_recovery_and_conflict(self):
        path=self.folder()
        first=self.place(path,"camera-a.mp4")
        linked=path/".fixture-camera-a.mp4.partial"
        os.link(first,linked)
        torn=path/".fixture-camera-b.mp4.partial"
        torn.write_bytes(b"x"); torn.chmod(0o600)
        bad=path/".fixture-master.wav.partial"
        bad.symlink_to(first)
        with self.assertRaises(TransitionError): self.prepare()
        self.assertTrue(linked.exists()); self.assertTrue(torn.exists())
        bad.unlink()
        ino=first.stat().st_ino
        self.assertEqual(len(self.prepare()["media"]),3)
        self.assertEqual(first.stat().st_ino,ino)
        self.assertEqual(first.stat().st_nlink,1)
        self.assertFalse(linked.exists()); self.assertFalse(torn.exists())

    def test_concurrency_and_project_lock(self):
        path=self.folder()
        fd=os.open(path,os.O_DIRECTORY|os.O_RDONLY)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(TransitionError): self.prepare()
        finally: os.close(fd)
        with ThreadPoolExecutor(4) as pool:
            results=list(pool.map(lambda _:build_core(self.config).media.prepare_fixture(self.a,self.project),range(4)))
        self.assertTrue(all(result==results[0] for result in results))
        self.assertEqual(len(results[0]["media"]),3)

    def test_limits_and_disabled(self):
        with patch("montara_core.adapters.local_fixture.MAX_INPUT_BYTES",1):
            with self.assertRaises(StorageError): self.prepare()
        self.assertFalse((self.media/self.project).exists())
        with patch("montara_core.adapters.local_fixture.MIN_FREE_BYTES",2**63):
            with self.assertRaises(StorageError): self.prepare()
        self.assertEqual(list((self.media/self.project).iterdir()),[])
        legacy=build_core(replace(self.config,pilot=replace(self.config.pilot,fixture_bank_root=None)))
        self.assertFalse(legacy.media.pilot_summary(self.a,self.project)["fixture_available"])
        with self.assertRaises(ProjectNotFound): legacy.media.prepare_fixture(self.a,self.project)
        with self.assertRaises(StorageError): build_core(replace(self.config,oidc=None))

    def test_config_opt_in_and_root_separation(self):
        cfg=self.root/"core.toml"
        text="[storage]\n"+"\n".join(f'{k}={json.dumps(str(getattr(self.config,k)))}'
              for k in ("sqlite_path","media_root","workspace_root","artifact_root"))
        text+="\n[oidc]\n"+"\n".join(f'{k}={json.dumps(str(getattr(self.oidc,k)))}'
              for k in ("issuer","authorization_endpoint","token_endpoint","jwks_uri","client_id",
                        "client_secret_file","public_origin"))
        text+='\n[pilot]\nallowed_subjects=["a","b","c","d"]\n'
        text+=f'renderer_root={json.dumps(str(self.renderer))}\nengine_root={json.dumps(str(self.engine))}\n'
        text+=f'fixture_bank_root={json.dumps(str(self.bank))}\n'
        cfg.write_text(text)
        self.assertEqual(load_config(cfg),self.config)
        cfg.write_text(text.replace(str(self.bank),str(self.media)))
        with self.assertRaises(ConfigError): load_config(cfg)
        cfg.write_text(text.replace("[pilot]","[production]"))
        with self.assertRaises(ConfigError): load_config(cfg)
        link=self.root/"bank-link"; link.symlink_to(self.bank,target_is_directory=True)
        cfg.write_text(text.replace(str(self.bank),str(link)))
        with self.assertRaises(ConfigError): load_config(cfg)

    def test_http_owner_csrf_body_legacy_bypass_and_packaged_ui(self):
        provider=TestProvider()
        auth=AuthenticationService(self.core.identity,provider,
             admitted={(self.issuer,s) for s in self.config.pilot.allowed_subjects})
        tokens=[]
        for sub in ("a","b"):
            pending,_=auth.begin()
            tokens.append(auth.complete(pending,provider.state,sub))
        server=create_authenticated_server(0,self.oidc.public_origin,self.core,auth,pilot=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        def request(path, token=None, body=None, origin=True, method="POST"):
            headers={"Host":"pilot.invalid"}
            if token: headers["Cookie"]=SESSION+"="+token
            if body is not None:
                headers["Content-Type"]="application/json"
                if origin: headers["Origin"]=self.oidc.public_origin
                body=json.dumps(body).encode()
            conn=HTTPConnection("127.0.0.1",server.server_port,timeout=5)
            try:
                conn.request(method,path,body=body,headers=headers)
                res=conn.getresponse(); return res.status,res.read()
            finally: conn.close()
        try:
            path=f"/api/projects/{self.project}/fixture"
            self.assertEqual(request(path,body={})[0],401)
            self.assertEqual(request(path,tokens[1],{})[0],404)
            self.assertEqual(request(path,tokens[0],{},False)[0],401)
            self.assertEqual(request(path,tokens[0],{"locator":"master.wav"})[0],400)
            self.assertEqual(request(path,tokens[0],{"owner_id":self.b})[0],400)
            self.assertFalse((self.media/self.project).exists())
            self.assertEqual(request(f"/api/projects/{self.project}/media",tokens[0],
                                     {"kind":"audio","locator":"master.wav"})[0],409)
            status,body=request(path,tokens[0],{})
            self.assertEqual(status,200)
            self.assertEqual(len(json.loads(body)["media"]),3)
            self.assertEqual(json.loads(request(path,tokens[0],{})[1]),json.loads(body))
            self.assertEqual(request(path,tokens[1],{})[0],404)
            status,script=request("/pilot.js",method="GET")
            self.assertEqual(status,200)
            self.assertEqual(script,files("montara_core.api").joinpath("pilot.js").read_bytes())
            self.assertIn(b'"/fixture", {}',script)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
