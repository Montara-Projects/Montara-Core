"""Adversarial publication tests. Byte fixtures test storage, not video quality."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from montara_core.adapters.local_publication import LocalPublication
from montara_core.adapters.sqlite_db import SQLiteDatabase, _SCHEMA_4
from montara_core.adapters.sqlite_publication import SQLitePublicationStore
from montara_core.application.ports import StorageError
from montara_core.bootstrap.config import Config, ConfigError, load_config
from montara_core.bootstrap.core import build_core
from montara_core.domain.execution import (
    ENGINE_REVISION, ENGINE_VERSION, EngineCandidate, Shot, stage_semantics,
    snapshot_digest, spec_document,
)
from montara_core.domain.jobs import AttemptRef, JobNotFound, JobState, TransitionError


def candidate_for(spec, content=b"verified candidate bytes"):
    """Test double of the trusted adapter's successful attestation only."""
    directory = spec.workspace / "engine"
    directory.mkdir()
    path = directory / "candidate.mp4"
    path.write_bytes(content)
    candidate = EngineCandidate(path, hashlib.sha256(content).hexdigest(),
                                len(content), ENGINE_VERSION, ENGINE_REVISION)
    manifest = {"schema": 2, "job_id": spec.job_id, "task_id": spec.task_id,
                "attempt_id": spec.attempt_id, "generation": spec.generation,
                "recipe_revision_id": spec.recipe_revision_id,
                "snapshot_sha256": spec.snapshot_sha256,
                "candidate": {"sha256": candidate.sha256, "size_bytes": candidate.size_bytes,
                              "engine_version": candidate.engine_version,
                              "engine_revision": candidate.engine_revision},
                "stage_semantics": stage_semantics()}
    (directory / "candidate-manifest.json").write_text(json.dumps(manifest))
    return candidate


class FixtureAdapter:
    def execute(self, spec):
        assert snapshot_digest(spec_document(spec)) == spec.snapshot_sha256
        return candidate_for(spec)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.originals, self.workspaces, self.artifacts = (
            self.root / name for name in ("originals", "workspaces", "artifacts"))
        for p in (self.originals, self.workspaces, self.artifacts):
            p.mkdir()
        self.config = Config(sqlite_path=self.root / "core.db", media_root=self.originals,
                             workspace_root=self.workspaces, artifact_root=self.artifacts)
        self.core = build_core(self.config)
        self.owner = self.core.projects.provision_account().id
        self.other = self.core.projects.provision_account().id
        self.project = self.core.projects.create_project(self.owner, "publication").id
        folder = self.originals / self.project
        folder.mkdir()
        (folder / "camera.mp4").write_bytes(b"camera")
        (folder / "master.wav").write_bytes(b"master")
        _, camera, _ = self.core.media.register_original(self.owner, self.project, "video", "camera.mp4")
        _, master, _ = self.core.media.register_original(self.owner, self.project, "audio", "master.wav")
        self.recipe = self.core.media.save_recipe(self.owner, self.project, None, master.id,
                                                  (Shot(camera.id, 0, 1000),))
        self.job = self.core.jobs.create_job(self.owner, self.project, "export", self.recipe.id)
        self.ref = self.core.jobs.claim()

    def prepare(self):
        spec = self.core.media.prepare_execution(self.ref)
        self.assertEqual(snapshot_digest(spec_document(spec)), spec.snapshot_sha256)
        return spec, candidate_for(spec)

    def assert_running(self):
        self.assertEqual(self.core.jobs.get_job(self.owner, self.job.id).state, JobState.RUNNING)
        self.assertIsNone(self.core.jobs.get_job(self.owner, self.job.id).result)
        with self.assertRaises(TransitionError):
            self.core.publication.get_artifact(self.owner, self.job.id)

    def assert_receipt(self, receipt):
        self.assertEqual(receipt.id, self.ref.id)
        self.assertEqual(receipt.job_id, self.job.id)
        self.assertEqual(receipt.recipe_revision_id, self.recipe.id)
        self.assertEqual(receipt.stage_profile, "stage-engineering-v0")
        self.assertEqual(receipt.engine_revision, ENGINE_REVISION)
        self.assertEqual(receipt.path, self.artifacts / (self.ref.id + ".mp4"))
        self.assertEqual(hashlib.sha256(receipt.path.read_bytes()).hexdigest(), receipt.sha256)
        self.assertEqual(receipt.path.stat().st_size, receipt.size_bytes)
        job = self.core.jobs.get_job(self.owner, self.job.id)
        self.assertEqual((job.state, job.result), (JobState.SUCCEEDED, receipt.id))
        self.assertEqual(self.core.jobs.get_task(self.owner, self.job.id).state, JobState.SUCCEEDED)
        with SQLiteDatabase(self.config.sqlite_path)._connection() as db:
            self.assertEqual(db.execute("SELECT state FROM attempts WHERE id=?", (self.ref.id,)).fetchone(),
                             ("succeeded",))
            self.assertEqual(db.execute("SELECT count(*) FROM artifact_publications WHERE committed=1").fetchone(), (1,))

    def test_execute_and_reopen_after_workspace_removed(self):
        receipt = self.core.publication.execute(self.owner, self.ref, FixtureAdapter())
        self.assert_receipt(receipt)
        self.assertEqual(len(list(self.artifacts.iterdir())), 1)
        shutil.rmtree(self.workspaces / self.ref.id)
        reopened = build_core(self.config)
        self.assertEqual(reopened.publication.recover(self.owner, self.ref), receipt)
        self.assertEqual(reopened.publication.get_artifact(self.owner, self.job.id), receipt)
        with self.assertRaises(TransitionError):
            reopened.publication.execute(self.owner, self.ref, FixtureAdapter())

    def test_lost_adapter_response_and_exact_replay(self):
        spec, candidate = self.prepare()
        self.assert_running()
        receipt = build_core(self.config).publication.recover(self.owner, self.ref)
        self.assert_receipt(receipt)
        self.assertEqual(receipt.snapshot_sha256, spec.snapshot_sha256)
        self.assertEqual(self.core.publication.publish(self.owner, self.ref, candidate), receipt)
        with self.assertRaises(TransitionError):
            self.core.publication.publish(self.owner, self.ref, replace(candidate, sha256="1"*64))

    def test_owner_and_exact_attempt_required(self):
        spec, candidate = self.prepare()
        for fn in (lambda: self.core.publication.publish(self.other, self.ref, candidate),
                   lambda: self.core.publication.recover(self.other, self.ref),
                   lambda: self.core.publication.get_artifact(self.other, self.job.id),
                   lambda: self.core.publication.execute(self.other, self.ref, FixtureAdapter())):
            with self.assertRaises(JobNotFound):
                fn()
        for invalid in (AttemptRef(self.ref.job_id, self.ref.id, self.ref.generation, str(uuid4())),
                        AttemptRef(self.ref.job_id, self.ref.id, self.ref.generation + 1, self.ref.task_id),
                        AttemptRef(self.ref.job_id, self.ref.id, self.ref.generation)):
            with self.assertRaises(TransitionError):
                self.core.publication.publish(self.owner, invalid, candidate)
        self.assert_running()
        self.assertEqual(list(self.artifacts.iterdir()), [])
        receipt = self.core.publication.publish(self.owner, self.ref, candidate)
        with self.assertRaises(JobNotFound):
            self.core.publication.get_artifact(self.other, receipt.job_id)

    def test_wrong_manifest_identity_semantics_pins_and_duplicate_keys(self):
        spec, candidate = self.prepare()
        path = candidate.path.parent / "candidate-manifest.json"
        original = path.read_text()
        for change in ({"job_id": str(uuid4())}, {"task_id": str(uuid4())},
                       {"attempt_id": str(uuid4())}, {"generation": True},
                       {"recipe_revision_id": str(uuid4())}, {"snapshot_sha256": "0"*64},
                       {"stage_semantics": {**stage_semantics(), "neutral": True}},
                       {"stage_semantics": {**stage_semantics(), "renderer_revision": "0"*40}},
                       {"candidate": {"sha256": candidate.sha256, "size_bytes": candidate.size_bytes,
                                      "engine_version": "other", "engine_revision": ENGINE_REVISION}},
                       {"schema": 1}):
            with self.subTest(change=change):
                path.write_text(json.dumps({**json.loads(original), **change}))
                with self.assertRaises(StorageError):
                    self.core.publication.publish(self.owner, self.ref, candidate)
        path.write_text(original[:-1]+', "schema": 2}')
        with self.assertRaises(StorageError):
            self.core.publication.publish(self.owner, self.ref, candidate)
        path.write_text(original)
        self.assert_running()
        self.assertEqual(list(self.artifacts.iterdir()), [])
        self.assert_receipt(self.core.publication.publish(self.owner, self.ref, candidate))

    def test_candidate_corruption_wrong_path_and_symlink(self):
        spec, candidate = self.prepare()
        path = candidate.path
        data = path.read_bytes()
        path.write_bytes(b"x"*len(data))
        with self.assertRaises(StorageError):
            self.core.publication.publish(self.owner, self.ref, candidate)
        path.write_bytes(data)
        foreign = self.root / "foreign.mp4"
        foreign.write_bytes(data)
        with self.assertRaises(StorageError):
            self.core.publication.publish(self.owner, self.ref, replace(candidate, path=foreign))
        path.unlink()
        path.symlink_to(foreign)
        with self.assertRaises(StorageError):
            self.core.publication.publish(self.owner, self.ref, candidate)
        path.unlink()
        path.write_bytes(data)
        manifest = path.parent / "candidate-manifest.json"
        foreign_manifest = self.root / "foreign.json"
        manifest.rename(foreign_manifest)
        manifest.symlink_to(foreign_manifest)
        with self.assertRaises(StorageError):
            self.core.publication.publish(self.owner, self.ref, candidate)
        manifest.unlink()
        foreign_manifest.rename(manifest)
        self.assert_running()
        self.assertEqual(list(self.artifacts.iterdir()), [])
        self.assert_receipt(self.core.publication.publish(self.owner, self.ref, candidate))

    def test_intent_durable_before_promotion_and_recovery_without_workspace(self):
        _, candidate = self.prepare()
        with patch.object(SQLitePublicationStore, "_complete", side_effect=RuntimeError("cut before link")):
            with self.assertRaisesRegex(RuntimeError, "cut before link"):
                self.core.publication.publish(self.owner, self.ref, candidate)
        self.assert_running()
        self.assertEqual(len(list(self.artifacts.glob("*.partial"))), 1)
        self.assertEqual(list(self.artifacts.glob("*.mp4")), [])
        shutil.rmtree(self.workspaces / self.ref.id)
        receipt = build_core(self.config).publication.recover(self.owner, self.ref)
        self.assert_receipt(receipt)

    def test_real_process_exit_after_link_before_db_commit(self):
        _, candidate = self.prepare()
        # Kill the isolated interpreter after the file link + directory fsync,
        # but before the SQLite transaction can mark success.
        code = """
import os, sys
from pathlib import Path
from unittest.mock import patch
from montara_core.bootstrap.config import Config
from montara_core.bootstrap.core import build_core
from montara_core.adapters.sqlite_publication import SQLitePublicationStore
from montara_core.domain.jobs import AttemptRef
root=Path(sys.argv[1])
core=build_core(Config(sqlite_path=root/'core.db',media_root=root/'originals',
                       workspace_root=root/'workspaces',artifact_root=root/'artifacts'))
ref=AttemptRef(sys.argv[3],sys.argv[4],int(sys.argv[5]),sys.argv[6])
with patch.object(SQLitePublicationStore, '_commit_publication', side_effect=lambda *a:os._exit(77)):
    core.publication.recover(sys.argv[2],ref)
"""
        child = subprocess.run([sys.executable, "-I", "-c", code, str(self.root), self.owner,
                                self.ref.job_id, self.ref.id, str(self.ref.generation), self.ref.task_id],
                               cwd=self.root, capture_output=True, text=True, timeout=30)
        self.assertEqual(child.returncode, 77, child.stderr)
        final = self.artifacts / (self.ref.id+".mp4")
        self.assertEqual(final.read_bytes(), candidate.path.read_bytes())
        self.assert_running()
        shutil.rmtree(self.workspaces / self.ref.id)
        receipt = build_core(self.config).publication.recover(self.owner, self.ref)
        self.assert_receipt(receipt)

    def test_cancel_after_intent_and_stale_retry_cannot_publish(self):
        _, candidate = self.prepare()
        with patch.object(SQLitePublicationStore, "_complete", side_effect=RuntimeError("cut")):
            with self.assertRaises(RuntimeError):
                self.core.publication.publish(self.owner, self.ref, candidate)
        self.core.jobs.recover(self.ref)
        self.core.jobs.retry(self.owner, self.ref)
        next_ref = self.core.jobs.claim()
        self.assertEqual(next_ref.generation, 2)
        with self.assertRaises(TransitionError):
            self.core.publication.recover(self.owner, self.ref)
        with self.assertRaises(TransitionError):
            self.core.publication.publish(self.owner, self.ref, candidate)
        self.core.jobs.cancel(self.owner, next_ref.job_id, next_ref.generation, next_ref.id)
        with self.assertRaises(TransitionError):
            self.core.publication.recover(self.owner, next_ref)
        self.assertEqual(self.core.jobs.get_job(self.owner, self.job.id).state, JobState.CANCELLED)
        self.assertEqual(list(self.artifacts.glob("*.mp4")), [])
        self.assertTrue(candidate.path.exists())

    def test_concurrent_cancel_during_copy(self):
        _, candidate = self.prepare()
        copied = threading.Event()
        resume = threading.Event()
        original = LocalPublication.copy_verified
        def pause(files,*args):
            original(files,*args)
            copied.set()
            if not resume.wait(10):
                raise RuntimeError("test barrier timed out")
        with patch.object(LocalPublication, "copy_verified", pause):
            with ThreadPoolExecutor(1) as executor:
                future = executor.submit(self.core.publication.publish,self.owner,self.ref,candidate)
                try:
                    self.assertTrue(copied.wait(10))
                    build_core(self.config).jobs.cancel(self.owner,self.job.id,self.ref.generation,self.ref.id)
                finally:
                    resume.set()
                with self.assertRaises(TransitionError):
                    future.result(timeout=10)
        self.assertEqual(list(self.artifacts.iterdir()), [])
        self.assertTrue(candidate.path.exists())

    def test_concurrent_identical_publish_one_receipt(self):
        _, candidate = self.prepare()
        barrier = threading.Barrier(2)
        def work():
            service = build_core(self.config).publication
            barrier.wait(timeout=10)
            return service.publish(self.owner,self.ref,candidate)
        with ThreadPoolExecutor(2) as executor:
            a,b = [executor.submit(work) for _ in range(2)]
            first,second = a.result(timeout=20),b.result(timeout=20)
        self.assertEqual(first,second)
        self.assert_receipt(first)
        self.assertEqual(list(self.artifacts.iterdir()), [first.path])

    def test_no_overwrite_and_committed_missing_or_corrupt_fails_closed(self):
        _, candidate = self.prepare()
        final = self.artifacts / (self.ref.id+".mp4")
        final.write_bytes(b"unrelated")
        with self.assertRaises(StorageError):
            self.core.publication.publish(self.owner,self.ref,candidate)
        self.assertEqual(final.read_bytes(), b"unrelated")
        self.assert_running()
        final.unlink()  # operator resolves a conflict, not automatic overwrite
        receipt = self.core.publication.recover(self.owner,self.ref)
        self.assert_receipt(receipt)
        final.write_bytes(b"x"*receipt.size_bytes)
        with self.assertRaises(StorageError):
            self.core.publication.get_artifact(self.owner,self.job.id)
        final.unlink()
        with self.assertRaises(StorageError):
            self.core.publication.recover(self.owner,self.ref)
        self.assertEqual(self.core.jobs.get_job(self.owner,self.job.id).state,JobState.SUCCEEDED)

    def test_snapshot_pinned_despite_new_recipe_and_original(self):
        spec,candidate = self.prepare()
        next_recipe = self.core.media.save_recipe(self.owner,self.project,self.recipe.id,
                                                  self.recipe.master_representation_id,
                                                  (Shot(self.recipe.shots[0].representation_id,0,2000),))
        self.assertNotEqual(next_recipe.id,self.recipe.id)
        receipt = self.core.publication.publish(self.owner,self.ref,candidate)
        self.assertEqual(receipt.recipe_revision_id,self.recipe.id)
        self.assertEqual(receipt.snapshot_sha256,spec.snapshot_sha256)
        self.assertEqual(self.core.media.get_recipe(self.owner,self.recipe.id),self.recipe)

    def test_legacy_v3_preserved_without_retroactive_snapshot_and_rollback(self):
        # Simulate exact old schema with a previously created export job,
        # without an attributed Stage profile. No real downgrade path.
        with sqlite3.connect(self.config.sqlite_path) as db:
            db.execute("DROP TABLE oidc_identities")
            db.execute("DROP TABLE artifact_publications")
            db.execute("DROP TABLE export_snapshots")
            db.execute("PRAGMA user_version=3")
        before = self.config.sqlite_path.read_bytes()
        original = SQLiteDatabase._validate_schema
        def reject(db, ddl):
            original(db,ddl)
            if ddl==_SCHEMA_4:
                raise StorageError("injected v4 failure")
        with patch.object(SQLiteDatabase,"_validate_schema",staticmethod(reject)):
            with self.assertRaisesRegex(StorageError,"injected v4 failure"):
                build_core(self.config)
        self.assertEqual(self.config.sqlite_path.read_bytes(),before)
        reopened = build_core(self.config)
        self.assertEqual(reopened.media.get_recipe(self.owner,self.recipe.id),self.recipe)
        self.assertEqual(reopened.jobs.get_job(self.owner,self.job.id).state,JobState.RUNNING)
        self.assertEqual(reopened.jobs.create_job(self.owner,self.project,"export",self.recipe.id).id,self.job.id)
        with self.assertRaisesRegex(TransitionError,"legacy export"):
            reopened.media.prepare_execution(self.ref)
        with self.assertRaises(TransitionError):
            reopened.publication.recover(self.owner,self.ref)
        reopened.jobs.cancel(self.owner,self.job.id,self.ref.generation,self.ref.id)
        new = reopened.jobs.create_job(self.owner,self.project,"explicit-new",self.recipe.id)
        self.assertNotEqual(new.id,self.job.id)
        next_ref = reopened.jobs.claim()
        self.assertTrue(reopened.media.prepare_execution(next_ref).snapshot_sha256)

    def test_artifact_root_explicit_and_separate(self):
        with self.assertRaises(StorageError):
            build_core(replace(self.config,artifact_root=self.workspaces))
        with self.assertRaises(StorageError):
            build_core(replace(self.config,media_root=None,workspace_root=None))
        no_root = build_core(replace(self.config,artifact_root=None))
        with self.assertRaises(StorageError):
            no_root.publication.execute(self.owner,self.ref,FixtureAdapter())
        config = self.root/"config.toml"
        config.write_text(f'[storage]\nsqlite_path="{self.config.sqlite_path}"\n'
                          f'media_root="{self.originals}"\nworkspace_root="{self.workspaces}"\n'
                          f'artifact_root="{self.artifacts}"\n')
        self.assertEqual(load_config(config),self.config)
        config.write_text(f'[storage]\nsqlite_path="{self.config.sqlite_path}"\n'
                          f'artifact_root="{self.artifacts}"\n')
        with self.assertRaises(ConfigError):
            load_config(config)
