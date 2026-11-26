"""H2.1 contract regressions; byte fixtures, not audiovisual quality tests."""
from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from montara_core.adapters.sqlite_db import SQLiteDatabase, _MIGRATION_1, _MIGRATION_2, _SCHEMA_3
from montara_core.application.ports import StorageError
from montara_core.application.synthetic_worker import run_once
from montara_core.bootstrap.config import Config, ConfigError, load_config
from montara_core.bootstrap.core import build_core
from montara_core.domain.execution import OutputProfile, Shot
from montara_core.domain.jobs import AttemptRef, JobNotFound, JobState, TransitionError
from montara_core.domain.media import MediaNotFound
from montara_core.domain.projects import InvalidValue, ProjectNotFound


class MediaExecutionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.media = self.root / "originals"
        self.workspace = self.root / "attempts"
        self.media.mkdir()
        self.workspace.mkdir()
        self.config = Config(sqlite_path=self.root / "core.db",
                             media_root=self.media, workspace_root=self.workspace)
        self.core = build_core(self.config)
        self.a = self.core.projects.provision_account().id
        self.b = self.core.projects.provision_account().id
        self.pa = self.core.projects.create_project(self.a, "A").id
        self.pb = self.core.projects.create_project(self.b, "B").id
        for p in (self.pa, self.pb):
            (self.media / p).mkdir()
            (self.media / p / "a.mp4").write_bytes(b"camera-a")
            (self.media / p / "b.mp4").write_bytes(b"camera-b")
            (self.media / p / "master.wav").write_bytes(b"master")
        self.asset, self.cam, self.loc = self.core.media.register_original(
            self.a, self.pa, "video", "a.mp4")
        _, self.cam2, _ = self.core.media.register_original(self.a, self.pa, "video", "b.mp4")
        _, self.master, _ = self.core.media.register_original(self.a, self.pa, "audio", "master.wav")
        self.shots = (Shot(self.cam.id, 0, 3000), Shot(self.cam2.id, 3000, 6000))

    def recipe(self, expected=None, shots=None):
        return self.core.media.save_recipe(
            self.a, self.pa, expected, self.master.id, self.shots if shots is None else shots)

    def export(self, recipe=None):
        recipe = recipe or self.recipe()
        job = self.core.jobs.create_job(self.a, self.pa, "export", recipe.id)
        return recipe, job

    def test_original_copy_and_revision_identity(self):
        self.assertEqual(self.cam.sha256, hashlib.sha256(b"camera-a").hexdigest())
        self.assertEqual(self.cam.size_bytes, 8)
        (self.media / self.pa / "copy.mp4").write_bytes(b"camera-a")
        copy = self.core.media.add_location(self.a, self.cam.id, "copy.mp4")
        self.assertNotEqual(copy.id, self.loc.id)
        self.assertEqual(copy.representation_id, self.cam.id)
        self.assertEqual(self.core.media.add_location(self.a, self.cam.id, "copy.mp4"), copy)
        with self.assertRaises(TransitionError):
            self.core.media.revise_original(self.a, self.asset.id, self.cam.id, "copy.mp4")
        (self.media / self.pa / "new.mp4").write_bytes(b"new-camera")
        new = self.core.media.revise_original(self.a, self.asset.id, self.cam.id, "new.mp4")
        self.assertEqual(new.asset_id, self.cam.asset_id)
        self.assertEqual(new.revision, 2)
        self.assertNotEqual(new.id, self.cam.id)
        with self.assertRaises(TransitionError):
            self.core.media.revise_original(self.a, self.asset.id, self.cam.id, "a.mp4")
        self.assertEqual(self.core.media.get_representation(self.a, self.cam.id), self.cam)

    def test_owner_and_same_owner_other_project_rejected(self):
        _, foreign, _ = self.core.media.register_original(self.b, self.pb, "video", "a.mp4")
        with self.assertRaises(ProjectNotFound):
            self.core.media.register_original(self.b, self.pa, "video", "a.mp4")
        for operation in (
            lambda: self.core.media.get_representation(self.b, self.cam.id),
            lambda: self.core.media.add_location(self.b, self.cam.id, "a.mp4"),
            lambda: self.core.media.revise_original(self.b, self.asset.id, self.cam.id, "a.mp4"),
            lambda: self.recipe(shots=(Shot(foreign.id, 0, 1000),)),
        ):
            with self.assertRaises(MediaNotFound):
                operation()
        second = self.core.projects.create_project(self.a, "other").id
        (self.media / second).mkdir()
        (self.media / second / "foreign.mp4").write_bytes(b"other")
        _, other, _ = self.core.media.register_original(self.a, second, "video", "foreign.mp4")
        with self.assertRaises(MediaNotFound):
            self.recipe(shots=(Shot(other.id, 0, 1000),))
        r = self.recipe()
        with self.assertRaises(MediaNotFound):
            self.core.media.get_recipe(self.b, r.id)
        with self.assertRaises(TransitionError):
            self.core.jobs.create_job(self.b, self.pb, "foreign", r.id)
        _, job = self.export(r)
        with self.assertRaises(JobNotFound):
            self.core.jobs.get_task(self.b, job.id)

    def test_paths_and_symlinks_refused(self):
        (self.media / self.pa / "symlink.mp4").symlink_to(self.media / self.pb / "a.mp4")
        (self.media / self.pa / "linked").symlink_to(self.media / self.pb, target_is_directory=True)
        for bad in ("../" + self.pb + "/a.mp4", "/etc/passwd", "x//y", "x/../y"):
            with self.subTest(bad=bad), self.assertRaises(InvalidValue):
                self.core.media.register_original(self.a, self.pa, "video", bad)
        for bad in ("symlink.mp4", "linked/a.mp4", "missing.mp4"):
            with self.subTest(bad=bad), self.assertRaises(StorageError):
                self.core.media.register_original(self.a, self.pa, "video", bad)
        with self.assertRaises(StorageError):
            self.core.media.add_location(self.a, self.cam.id, "b.mp4")

    def test_recipe_immutable_cas_and_request_fingerprint(self):
        r1, job = self.export()
        self.assertEqual(r1.revision, 1)
        with self.assertRaises(FrozenInstanceError):
            r1.revision = 7
        r2 = self.recipe(r1.id, (Shot(self.cam2.id, 0, 6000),))
        self.assertEqual(r2.parent_id, r1.id)
        self.assertEqual(r2.revision, 2)
        with self.assertRaises(TransitionError):
            self.recipe(r1.id)
        with self.assertRaises(TransitionError):
            self.core.jobs.create_job(self.a, self.pa, "new-obsolete", r1.id)
        with self.assertRaises(TransitionError):
            self.core.jobs.create_job(self.a, self.pa, "export", r2.id)
        self.assertEqual(self.core.jobs.create_job(self.a, self.pa, "export", r1.id), job)
        self.assertEqual(build_core(self.config).media.get_recipe(self.a, r1.id), r1)
        ref = self.core.jobs.claim()
        spec = self.core.media.prepare_execution(ref)
        self.assertEqual(spec.recipe_revision_id, r1.id)
        self.assertEqual(spec.shots, r1.shots)
        self.assertEqual(spec.profile, OutputProfile())

    def test_invalid_recipe_and_obsolete_original(self):
        for shots in ((), (Shot(self.cam.id, 100, 100),), (Shot(self.cam.id, -1, 100),),
                      (Shot(self.cam.id, True, 100),)):
            with self.subTest(shots=shots), self.assertRaises(InvalidValue):
                self.recipe(shots=shots)
        with self.assertRaises(MediaNotFound):
            self.core.media.save_recipe(self.a, self.pa, None, self.cam2.id, self.shots)
        with self.assertRaises(InvalidValue):
            self.core.media.save_recipe(self.a, self.pa, None, self.master.id, self.shots,
                                        OutputProfile(width=1280))
        r1 = self.recipe()
        (self.media / self.pa / "new.mp4").write_bytes(b"new-camera")
        new = self.core.media.revise_original(self.a, self.asset.id, self.cam.id, "new.mp4")
        with self.assertRaises(TransitionError):
            self.recipe(r1.id)
        with self.assertRaises(TransitionError):
            self.core.jobs.create_job(self.a, self.pa, "new", r1.id)
        r2 = self.recipe(r1.id, (Shot(new.id, 0, 6000),))
        self.assertEqual(r2.shots[0].representation_id, new.id)

    def test_staging_exact_snapshot_and_private_candidate_boundary(self):
        r, job = self.export()
        self.assertIsNone(run_once(self.core.jobs))
        task = self.core.jobs.get_task(self.a, job.id)
        self.assertEqual((task.kind, task.recipe_id, task.state), ("export", r.id, JobState.QUEUED))
        ref = self.core.jobs.claim()
        self.assertEqual(ref.task_id, task.id)
        self.assertEqual(self.core.jobs.get_task(self.a, job.id).state, JobState.RUNNING)
        spec = self.core.media.prepare_execution(ref)
        self.assertEqual(spec.workspace, self.workspace / ref.id)
        self.assertEqual(spec.task_id, task.id)
        self.assertEqual(spec.master_representation_id, self.master.id)
        self.assertEqual({f.representation_id for f in spec.inputs}, {self.cam.id, self.cam2.id, self.master.id})
        for source in spec.inputs:
            self.assertEqual(source.path.parent, spec.workspace)
            self.assertEqual(hashlib.sha256(source.path.read_bytes()).hexdigest(), source.sha256)
            self.assertEqual(source.path.stat().st_size, source.size_bytes)
        with self.assertRaises(StorageError):
            self.core.media.prepare_execution(ref)
        with self.assertRaises(TransitionError):
            self.core.jobs.succeed(ref, "fake-mp4")
        self.assertIsNone(self.core.jobs.get_job(self.a, job.id).result)

    def test_same_size_corruption_and_verified_alternate(self):
        _, job = self.export()
        (self.media / self.pa / "copy.mp4").write_bytes(b"camera-a")
        self.core.media.add_location(self.a, self.cam.id, "copy.mp4")
        (self.media / self.pa / "a.mp4").write_bytes(b"corrupt!")
        ref = self.core.jobs.claim()
        spec = self.core.media.prepare_execution(ref)
        self.assertEqual(next(f for f in spec.inputs if f.representation_id == self.cam.id).path.read_bytes(),
                         b"camera-a")
        self.core.jobs.recover(ref)
        self.core.jobs.retry(self.a, ref)
        (self.media / self.pa / "copy.mp4").write_bytes(b"corrupt?")
        retry = self.core.jobs.claim()
        with self.assertRaises(StorageError):
            self.core.media.prepare_execution(retry)
        self.assertFalse((self.workspace / retry.id).exists())
        self.assertEqual(self.core.jobs.get_job(self.a, job.id).state, JobState.RUNNING)

    def test_task_attempt_fencing_recovery_and_cancel(self):
        _, job = self.export()
        first = self.core.jobs.claim()
        reopened = build_core(self.config)
        self.assertEqual(reopened.jobs.running_attempt(), first)
        for invalid in (AttemptRef(first.job_id, first.id, first.generation),
                        AttemptRef(first.job_id, first.id, first.generation, str(uuid4()))):
            with self.assertRaises(TransitionError):
                reopened.media.prepare_execution(invalid)
            with self.assertRaises(TransitionError):
                reopened.jobs.recover(invalid)
        reopened.jobs.recover(first)
        self.assertEqual(reopened.jobs.get_task(self.a, job.id).state, JobState.FAILED)
        reopened.jobs.retry(self.a, first)
        self.assertEqual(reopened.jobs.get_task(self.a, job.id).state, JobState.QUEUED)
        second = reopened.jobs.claim()
        self.assertEqual(second.task_id, first.task_id)
        self.assertEqual(second.generation, 2)
        with self.assertRaises(TransitionError):
            reopened.media.prepare_execution(first)
        with self.assertRaises(TransitionError):
            reopened.jobs.fail(first)
        reopened.jobs.cancel(self.a, job.id, second.generation, second.id)
        self.assertEqual(reopened.jobs.get_task(self.a, job.id).state, JobState.CANCELLED)
        with self.assertRaises(TransitionError):
            reopened.media.prepare_execution(second)

    def test_roots_config_and_no_implicit_storage(self):
        config_file = self.root / "config.toml"
        config_file.write_text(f'[storage]\nsqlite_path="{self.config.sqlite_path}"\n'
                               f'media_root="{self.media}"\nworkspace_root="{self.workspace}"\n')
        self.assertEqual(load_config(config_file), self.config)
        config_file.write_text(f'[storage]\nsqlite_path="{self.config.sqlite_path}"\n'
                               f'media_root="{self.media}"\n')
        with self.assertRaises(ConfigError):
            load_config(config_file)
        with self.assertRaises(StorageError):
            build_core(Config(sqlite_path=self.config.sqlite_path, media_root=self.media,
                              workspace_root=self.media))
        without = build_core(Config(sqlite_path=self.config.sqlite_path))
        with self.assertRaises(StorageError):
            without.media.register_original(self.a, self.pa, "video", "a.mp4")


class V3MigrationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "v2.db"
        self.owner, self.project, self.job, self.attempt, self.done, self.done_attempt = (
            str(uuid4()) for _ in range(6))
        db = sqlite3.connect(self.path)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            for ddl in _MIGRATION_1 + _MIGRATION_2:
                db.execute(ddl)
            db.execute("INSERT INTO accounts VALUES (?)", (self.owner,))
            db.execute("INSERT INTO projects VALUES (?, ?, ?)", (self.project, self.owner, "H1"))
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?)",
                       (self.job, self.project, "old", "running", 1, None))
            db.execute("INSERT INTO attempts VALUES (?,?,?,?)",
                       (self.attempt, self.job, 1, "running"))
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?)",
                       (self.done, self.project, "done", "succeeded", 1, "old-result"))
            db.execute("INSERT INTO attempts VALUES (?,?,?,?)",
                       (self.done_attempt, self.done, 1, "succeeded"))
            db.execute("PRAGMA user_version=2")
            db.commit()
        finally:
            db.close()

    def test_v2_jobs_attempts_receipts_and_recovery_preserved(self):
        core = build_core(Config(sqlite_path=self.path))
        self.assertEqual(core.projects.get_project(self.owner, self.project).name, "H1")
        completed = core.jobs.get_job(self.owner, self.done)
        self.assertEqual(completed.result, "old-result")
        self.assertEqual(core.jobs.get_task(self.owner, self.done).state, JobState.SUCCEEDED)
        self.assertEqual(core.jobs.succeed(AttemptRef(self.done, self.done_attempt, 1), "old-result"),
                         completed)
        ref = core.jobs.running_attempt()
        self.assertEqual((ref.job_id, ref.id, ref.generation), (self.job, self.attempt, 1))
        self.assertEqual(core.jobs.get_task(self.owner, self.job).id, ref.task_id)
        core.jobs.recover(ref)
        core.jobs.retry(self.owner, ref)
        next_ref = core.jobs.claim()
        self.assertEqual(next_ref.generation, 2)
        self.assertEqual(next_ref.task_id, ref.task_id)
        self.assertEqual(core.jobs.succeed(next_ref, "new").result, "new")
        with SQLiteDatabase(self.path)._connection() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone(), (3,))
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(db.execute("SELECT count(*) FROM attempts").fetchone(), (3,))

    def test_v3_migration_rollback_preserves_exact_v2(self):
        before = self.path.read_bytes()
        validate = SQLiteDatabase._validate_schema
        def reject(db, ddl):
            validate(db, ddl)
            if ddl == _SCHEMA_3:
                raise StorageError("injected destination failure")
        with patch.object(SQLiteDatabase, "_validate_schema", staticmethod(reject)):
            with self.assertRaisesRegex(StorageError, "injected destination failure"):
                SQLiteDatabase(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        SQLiteDatabase(self.path)

    def test_v3_reopen_rejects_missing_table(self):
        SQLiteDatabase(self.path)
        db = sqlite3.connect(self.path)
        try:
            db.execute("DROP TABLE recipe_inputs")
            db.commit()
        finally:
            db.close()
        before = self.path.read_bytes()
        with self.assertRaises(StorageError):
            SQLiteDatabase(self.path)
        self.assertEqual(self.path.read_bytes(), before)
