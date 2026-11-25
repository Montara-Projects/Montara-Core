import concurrent.futures
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from montara_core.adapters.sqlite_db import SQLiteDatabase, _MIGRATION_1, _MIGRATION_2
from montara_core.application.ports import StorageError
from montara_core.application.synthetic_worker import run_once
from montara_core.bootstrap.config import Config
from montara_core.bootstrap.jobs import build_job_service
from montara_core.bootstrap.projects import build_project_service
from montara_core.domain.jobs import AttemptRef, JobNotFound, JobState, TransitionError
from montara_core.domain.projects import InvalidValue, ProjectNotFound


class JobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "core.sqlite"
        self.config = Config(sqlite_path=self.path)
        projects = build_project_service(self.config)
        self.a = projects.provision_account().id
        self.b = projects.provision_account().id
        self.pa = projects.create_project(self.a, "A").id
        self.pb = projects.create_project(self.b, "B").id
        self.jobs = build_job_service(self.config)

    def create(self, key="req"):
        return self.jobs.create_job(self.a, self.pa, key)

    def counts(self):
        with SQLiteDatabase(self.path)._connection() as db:
            return (db.execute("SELECT count(*) FROM jobs").fetchone()[0],
                    db.execute("SELECT count(*) FROM attempts").fetchone()[0])

    def test_create_idempotent_and_scoped(self):
        job = self.create()
        self.assertEqual(job.state, JobState.QUEUED)
        self.assertEqual(job.generation, 0)
        self.assertIsNone(job.attempt_id)
        self.assertIsNone(job.result)
        self.assertEqual(self.create(), job)
        self.assertNotEqual(self.create("other").id, job.id)
        self.assertNotEqual(self.jobs.create_job(self.b, self.pb, "req").id, job.id)
        self.assertEqual(self.counts(), (3, 0))
        for key in ("", " a", "a\x00b", "x" * 121, None):
            with self.subTest(key=key), self.assertRaises(InvalidValue):
                self.create(key)
        self.assertEqual(self.counts(), (3, 0))

    def test_owner_isolation(self):
        job = self.create()
        with self.assertRaises(ProjectNotFound):
            self.jobs.create_job(self.b, self.pa, "req")
        with self.assertRaises(ProjectNotFound):
            self.jobs.create_job(str(uuid4()), self.pa, "req")
        for unknown in (job.id, str(uuid4())):
            with self.assertRaisesRegex(JobNotFound, "^job not found$"):
                self.jobs.get_job(self.b, unknown)
        with self.assertRaises(JobNotFound):
            self.jobs.cancel(self.b, job.id, 0)
        attempt = self.jobs.claim()
        with self.assertRaises(JobNotFound):
            self.jobs.cancel(self.b, job.id, attempt.generation, attempt.id)
        self.jobs.fail(attempt)
        with self.assertRaises(JobNotFound):
            self.jobs.retry(self.b, attempt)
        self.assertEqual(self.jobs.get_job(self.a, job.id).state, JobState.FAILED)

    def test_worker_and_persisted_receipt(self):
        job = self.create()
        done = run_once(self.jobs)
        self.assertEqual(done.id, job.id)
        self.assertEqual(done.state, JobState.SUCCEEDED)
        self.assertEqual(done.result, "synthetic:" + job.id)
        self.assertEqual(done.generation, 1)
        self.assertIsNotNone(done.attempt_id)
        self.assertIsNone(run_once(self.jobs))
        reopened = build_job_service(self.config)
        self.assertEqual(reopened.get_job(self.a, job.id), done)
        self.assertEqual(reopened.create_job(self.a, self.pa, "req"), done)
        self.assertEqual(self.counts(), (1, 1))

    def test_exact_completion_replay_and_conflict(self):
        job = self.create()
        attempt = self.jobs.claim()
        with self.assertRaises(InvalidValue):
            self.jobs.succeed(attempt, "")
        forged = AttemptRef(job.id, str(uuid4()), attempt.generation)
        with self.assertRaises(TransitionError):
            self.jobs.succeed(forged, "wrong")
        self.assertEqual(self.jobs.get_job(self.a, job.id).state, JobState.RUNNING)
        done = self.jobs.succeed(attempt, "result")
        self.assertEqual(self.jobs.succeed(attempt, "result"), done)
        with self.assertRaises(TransitionError):
            self.jobs.succeed(attempt, "different")
        with self.assertRaises(TransitionError):
            self.jobs.fail(attempt)
        with self.assertRaises(TransitionError):
            self.jobs.retry(self.a, attempt)
        self.assertEqual(self.jobs.get_job(self.a, job.id), done)
        self.assertEqual(self.counts(), (1, 1))

    def test_recovery_after_lost_claim_and_restart(self):
        job = self.create()
        first = self.jobs.claim()
        del self.jobs
        self.jobs = build_job_service(self.config)
        self.assertIsNone(self.jobs.claim())
        self.assertEqual(self.jobs.running_attempt(), first)
        self.assertEqual(self.jobs.get_job(self.a, job.id).attempt_id, first.id)
        recovered = self.jobs.recover(first)
        self.assertEqual(recovered.state, JobState.FAILED)
        self.assertEqual(self.jobs.recover(first), recovered)
        self.assertIsNone(self.jobs.running_attempt())
        for action in (lambda: self.jobs.succeed(first, "late"), lambda: self.jobs.fail(first)):
            with self.assertRaises(TransitionError):
                action()
        queued = self.jobs.retry(self.a, first)
        self.assertEqual(queued.state, JobState.QUEUED)
        self.assertEqual(self.jobs.retry(self.a, first), queued)
        with self.assertRaises(TransitionError):
            self.jobs.succeed(first, "late")
        second = self.jobs.claim()
        self.assertEqual(second.generation, 2)
        self.assertNotEqual(second.id, first.id)
        for action in (lambda: self.jobs.succeed(first, "late"),
                       lambda: self.jobs.fail(first),
                       lambda: self.jobs.recover(first),
                       lambda: self.jobs.retry(self.a, first),
                       lambda: self.jobs.cancel(self.a, job.id, first.generation, first.id)):
            with self.assertRaises(TransitionError):
                action()
        done = self.jobs.succeed(second, "winner")
        self.assertEqual(done.result, "winner")
        self.assertEqual(self.counts(), (1, 2))
        with SQLiteDatabase(self.path)._connection() as db:
            self.assertEqual(db.execute(
                "SELECT state FROM attempts WHERE job_id = ? ORDER BY generation", (job.id,)
            ).fetchall(), [("interrupted",), ("succeeded",)])

    def test_failed_attempt_retry(self):
        job = self.create()
        first = self.jobs.claim()
        failed = self.jobs.fail(first)
        self.assertEqual(failed.state, JobState.FAILED)
        self.assertEqual(self.jobs.fail(first), failed)
        self.assertEqual(self.jobs.retry(self.a, first).state, JobState.QUEUED)
        second = self.jobs.claim()
        self.assertEqual(second.generation, 2)
        with self.assertRaises(TransitionError):
            self.jobs.fail(first)
        self.assertEqual(self.jobs.succeed(second, "ok").state, JobState.SUCCEEDED)
        self.assertEqual(self.counts(), (1, 2))

    def test_cancel_queued_and_running(self):
        job = self.create()
        cancelled = self.jobs.cancel(self.a, job.id, 0)
        self.assertEqual(cancelled.state, JobState.CANCELLED)
        self.assertEqual(self.jobs.cancel(self.a, job.id, 0), cancelled)
        self.assertIsNone(self.jobs.claim())
        another = self.create("running")
        attempt = self.jobs.claim()
        for generation, token in [(0, None), (1, None), (1, str(uuid4()))]:
            with self.assertRaises(TransitionError):
                self.jobs.cancel(self.a, another.id, generation, token)
        cancelled = self.jobs.cancel(self.a, another.id, attempt.generation, attempt.id)
        self.assertEqual(cancelled.state, JobState.CANCELLED)
        self.assertEqual(self.jobs.cancel(self.a, another.id, attempt.generation, attempt.id), cancelled)
        for action in (lambda: self.jobs.succeed(attempt, "late"),
                       lambda: self.jobs.fail(attempt),
                       lambda: self.jobs.recover(attempt),
                       lambda: self.jobs.retry(self.a, attempt)):
            with self.assertRaises(TransitionError):
                action()
        self.assertIsNone(self.jobs.claim())
        self.assertIsNone(self.jobs.get_job(self.a, another.id).result)

    def test_cancel_requeued_job_requires_observed_generation(self):
        job = self.create()
        first = self.jobs.claim()
        self.jobs.fail(first)
        self.jobs.retry(self.a, first)
        with self.assertRaises(TransitionError):
            self.jobs.cancel(self.a, job.id, 0)
        with self.assertRaises(TransitionError):
            self.jobs.cancel(self.a, job.id, 1, first.id)
        self.assertEqual(self.jobs.cancel(self.a, job.id, 1).state, JobState.CANCELLED)
        self.assertIsNone(self.jobs.claim())

    def test_one_global_claim_and_queue_order(self):
        first, second = self.create("first"), self.create("second")
        barrier = threading.Barrier(2)
        def claim():
            service = build_job_service(self.config)
            barrier.wait(timeout=5)
            return service.claim()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: claim(), range(2)))
        winners = [ref for ref in claims if ref is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0].job_id, first.id)
        self.assertEqual(self.counts(), (2, 1))
        self.jobs.succeed(winners[0], "first")
        next_ref = self.jobs.claim()
        self.assertEqual(next_ref.job_id, second.id)
        self.assertEqual(self.counts(), (2, 2))

    def test_concurrent_create_same_key(self):
        barrier = threading.Barrier(2)
        def create():
            service = build_job_service(self.config)
            barrier.wait(timeout=5)
            return service.create_job(self.a, self.pa, "same")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            jobs = list(pool.map(lambda _: create(), range(2)))
        self.assertEqual(jobs[0], jobs[1])
        self.assertEqual(self.counts(), (1, 0))

    def test_cancel_finish_race_has_one_terminal_winner(self):
        job = self.create()
        attempt = self.jobs.claim()
        barrier = threading.Barrier(2)
        def transition(cancel):
            service = build_job_service(self.config)
            barrier.wait(timeout=5)
            try:
                if cancel:
                    return service.cancel(self.a, job.id, attempt.generation, attempt.id).state
                return service.succeed(attempt, "winner").state
            except TransitionError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(transition, [True, False]))
        self.assertEqual(outcomes.count(None), 1)
        final = self.jobs.get_job(self.a, job.id)
        self.assertIn(final.state, (JobState.CANCELLED, JobState.SUCCEEDED))
        self.assertIn(final.state, outcomes)
        self.assertEqual(final.result, "winner" if final.state == JobState.SUCCEEDED else None)
        self.assertEqual(self.counts(), (1, 1))

    def test_claim_transaction_rolls_back_when_attempt_insert_fails(self):
        job = self.create()
        # Fault injection via UUID collision with existing terminal attempt.
        other = self.create("other")
        first = self.jobs.claim()
        self.jobs.succeed(first, "ok")
        with patch("montara_core.adapters.sqlite_jobs.uuid4", return_value=first.id):
            with self.assertRaises(StorageError):
                self.jobs.claim()
        queued = self.jobs.get_job(self.a, other.id)
        self.assertEqual(queued.state, JobState.QUEUED)
        self.assertEqual(queued.generation, 0)
        self.assertEqual(self.counts(), (2, 1))
        self.assertEqual(self.jobs.claim().job_id, other.id)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "v1.sqlite"
        self.owner, self.project = str(uuid4()), str(uuid4())
        db = sqlite3.connect(self.path)
        try:
            for ddl in _MIGRATION_1:
                db.execute(ddl)
            db.execute("INSERT INTO accounts VALUES (?)", (self.owner,))
            db.execute("INSERT INTO projects VALUES (?, ?, ?)", (self.project, self.owner, "Existing"))
            db.execute("PRAGMA user_version = 1")
            db.commit()
        finally:
            db.close()

    def test_migration_preserves_v1_data_and_reopens(self):
        config = Config(sqlite_path=self.path)
        projects = build_project_service(config)
        self.assertEqual(projects.get_project(self.owner, self.project).name, "Existing")
        jobs = build_job_service(config)
        job = jobs.create_job(self.owner, self.project, "after-migration")
        self.assertEqual(run_once(jobs).id, job.id)
        self.assertEqual(build_job_service(config).get_job(self.owner, job.id).state, JobState.SUCCEEDED)
        with SQLiteDatabase(self.path)._connection() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone(), (2,))
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_invalid_source_rejected_unchanged(self):
        db = sqlite3.connect(self.path)
        try:
            db.execute("DROP INDEX projects_owner_id_idx")
            db.commit()
        finally:
            db.close()
        before = self.path.read_bytes()
        with self.assertRaises(StorageError):
            SQLiteDatabase(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_migration_rollback_on_destination_validation_failure(self):
        before = self.path.read_bytes()
        original = SQLiteDatabase._validate_schema
        def validate(db, ddl):
            original(db, ddl)
            if len(ddl) > len(_MIGRATION_1):
                raise StorageError("injected destination failure")
        with patch.object(SQLiteDatabase, "_validate_schema", staticmethod(validate)):
            with self.assertRaisesRegex(StorageError, "injected destination failure"):
                SQLiteDatabase(self.path)
        self.assertEqual(self.path.read_bytes(), before)
        db = sqlite3.connect(self.path)
        try:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone(), (1,))
            self.assertEqual(db.execute("SELECT name FROM projects").fetchall(), [("Existing",)])
            self.assertEqual(db.execute("SELECT name FROM sqlite_schema WHERE name='jobs'").fetchall(), [])
        finally:
            db.close()
        SQLiteDatabase(self.path)

    def test_invalid_v2_rejected_unchanged(self):
        SQLiteDatabase(self.path)
        db = sqlite3.connect(self.path)
        try:
            db.execute("DROP INDEX jobs_one_running_idx")
            db.commit()
        finally:
            db.close()
        before = self.path.read_bytes()
        with self.assertRaises(StorageError):
            SQLiteDatabase(self.path)
        self.assertEqual(self.path.read_bytes(), before)
