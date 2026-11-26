"""One durable intent per Attempt, one visible artifact per successful Job.

Files and SQLite cannot commit atomically. Intent is committed after a
verified durable copy, before promotion. The final file is synced before
the single fenced DB commit. Recovery uses the same intent and bytes.
"""
import json
from pathlib import Path
import re
from uuid import uuid4

from montara_core.adapters.local_publication import LocalPublication, read_manifest, verify_file
from montara_core.adapters.sqlite_db import SQLiteDatabase
from montara_core.adapters.sqlite_jobs import SQLiteJobStore
from montara_core.application.ports import StorageError
from montara_core.domain.execution import EngineCandidate, canonical_json, snapshot_digest
from montara_core.domain.jobs import AttemptRef, JobState, TransitionError
from montara_core.domain.publication import ArtifactReceipt


class SQLitePublicationStore(SQLiteDatabase):
    _artifact_root: Path | None = None

    def _publication_files(self):
        if self._artifact_root is None:
            raise StorageError("persistent artifact root not configured")
        return LocalPublication(self._artifact_root)

    def _candidate_path(self, attempt):
        return self._files().workspace_root / attempt.id / "engine" / "candidate.mp4"

    @staticmethod
    def _authority(db, actor, attempt):
        job = SQLiteJobStore._read(db, attempt.job_id, actor)
        state = SQLiteJobStore._exact_attempt(db, job, attempt)
        if not ((job.state == JobState.RUNNING and state == "running") or
                (job.state == JobState.SUCCEEDED and state == "succeeded")):
            raise TransitionError("attempt no longer authorized for publication")
        row = db.execute(
            "SELECT t.recipe_id,s.document FROM tasks t JOIN export_snapshots s ON s.task_id=t.id "
            "JOIN recipe_revisions r ON r.id=t.recipe_id AND r.project_id=? "
            "WHERE t.id=? AND t.job_id=? AND t.kind='export'",
            (job.project_id, attempt.task_id, job.id),
        ).fetchone()
        if row is None:
            raise TransitionError("export has no pinned execution snapshot")
        document = json.loads(row[1])
        if document["recipe_revision_id"] != row[0]:
            raise StorageError("snapshot identity mismatch")
        return job, document

    @staticmethod
    def _intent(db, attempt):
        row = db.execute(
            "SELECT job_id,manifest_json,staging_token,committed FROM artifact_publications "
            "WHERE attempt_id=?", (attempt.id,),
        ).fetchone()
        if row is not None and row[0] != attempt.job_id:
            raise StorageError("publication intent identity mismatch")
        return row

    @staticmethod
    def _expected(attempt, document, candidate):
        if (not isinstance(candidate, EngineCandidate)
                or not isinstance(candidate.sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", candidate.sha256)
                or type(candidate.size_bytes) is not int
                or not 0 < candidate.size_bytes <= 2**63 - 1):
            raise StorageError("invalid candidate metadata")
        semantics = document["stage_semantics"]
        if (candidate.engine_revision != semantics["engine_revision"] or
                candidate.engine_version != semantics["engine_version"]):
            raise StorageError("candidate Engine pin mismatch")
        return {"schema": 2, "job_id": attempt.job_id, "task_id": attempt.task_id,
                "attempt_id": attempt.id, "generation": attempt.generation,
                "recipe_revision_id": document["recipe_revision_id"],
                "snapshot_sha256": snapshot_digest(document),
                "candidate": {"sha256": candidate.sha256, "size_bytes": candidate.size_bytes,
                              "engine_version": candidate.engine_version,
                              "engine_revision": candidate.engine_revision},
                "stage_semantics": semantics}

    @staticmethod
    def _candidate_from_manifest(path, manifest):
        try:
            return EngineCandidate(path=path, **manifest["candidate"])
        except (KeyError, TypeError):
            raise StorageError("invalid candidate manifest metadata") from None

    @staticmethod
    def _canonical_manifest(value):
        try:
            return canonical_json(value)
        except (TypeError, ValueError, RecursionError):
            raise StorageError("invalid candidate manifest") from None

    def authorize_export(self, actor, attempt):
        self._publication_files()
        with self._connection() as db:
            db.execute("BEGIN")
            job, _ = self._authority(db, actor, attempt)
            if job.state != JobState.RUNNING or self._intent(db, attempt) is not None:
                raise TransitionError("recover existing publication instead of executing again")

    def publish(self, actor, attempt, candidate):
        files = self._publication_files()
        with self._connection() as db:
            db.execute("BEGIN")
            job, document = self._authority(db, actor, attempt)
            existing = self._intent(db, attempt)
        path = self._candidate_path(attempt)
        if not isinstance(candidate, EngineCandidate) or candidate.path != path:
            raise StorageError("candidate path must be the exact private attempt output")
        expected = self._expected(attempt, document, candidate)
        serialized = canonical_json(expected)
        if existing is not None:
            if existing[1] != serialized:
                raise TransitionError("conflicting publication replay")
            return self._complete(actor, attempt)
        if job.state != JobState.RUNNING:
            raise StorageError("successful export missing publication receipt")
        manifest = read_manifest(path.parent / "candidate-manifest.json")
        if self._canonical_manifest(manifest) != serialized:
            raise StorageError("candidate manifest does not match pinned snapshot and attempt")
        token = str(uuid4())
        staged = files.staged(attempt.id, token)
        files.copy_verified(path, staged, candidate.sha256, candidate.size_bytes)
        # A cancelled/stale attempt may leave only an invisible temp; no
        # state changes or published pointer. Never delete an adopted intent
        # on ambiguous commit failure.
        adopted = False
        try:
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                current, latest_document = self._authority(db, actor, attempt)
                if self._expected(attempt, latest_document, candidate) != expected:
                    raise TransitionError("snapshot changed during publication")
                row = self._intent(db, attempt)
                if row is None:
                    if current.state != JobState.RUNNING:
                        raise TransitionError("attempt no longer running")
                    adopted = True
                    db.execute("INSERT INTO artifact_publications VALUES (?,?,?,?,0)",
                               (attempt.id, attempt.job_id, serialized, token))
                elif row[1] != serialized:
                    raise TransitionError("conflicting publication replay")
        finally:
            if not adopted:
                staged.unlink(missing_ok=True)
        return self._complete(actor, attempt)

    def recover_publication(self, actor, attempt):
        self._publication_files()
        with self._connection() as db:
            db.execute("BEGIN")
            _, _ = self._authority(db, actor, attempt)
            row = self._intent(db, attempt)
        if row is not None:
            return self._complete(actor, attempt)
        # Adapter finished but its response was lost before intent creation.
        # Only the exact private attestation can initiate publication.
        path = self._candidate_path(attempt)
        manifest = read_manifest(path.parent / "candidate-manifest.json")
        return self.publish(actor, attempt, self._candidate_from_manifest(path, manifest))

    @staticmethod
    def _commit_publication(db, attempt):
        # Called only inside the same write transaction as the authority check
        # and durable no-overwrite promotion.
        db.execute("UPDATE attempts SET state='succeeded' WHERE id=?", (attempt.id,))
        db.execute("UPDATE tasks SET state='succeeded' WHERE id=?", (attempt.task_id,))
        db.execute("UPDATE jobs SET state='succeeded',result=? WHERE id=?",
                   (attempt.id, attempt.job_id))
        db.execute("UPDATE artifact_publications SET committed=1 WHERE attempt_id=?", (attempt.id,))

    def _complete(self, actor, attempt):
        files = self._publication_files()
        with self._connection() as db:
            db.execute("BEGIN")
            job, document = self._authority(db, actor, attempt)
            row = self._intent(db, attempt)
        if row is None:
            raise StorageError("publication intent missing")
        manifest = json.loads(row[1])
        candidate = self._candidate_from_manifest(self._candidate_path(attempt), manifest)
        if canonical_json(self._expected(attempt, document, candidate)) != row[1]:
            raise StorageError("publication intent mismatch")
        final = files.final(attempt.id)
        staged = files.staged(attempt.id, row[2])
        if row[3]:
            if job.state != JobState.SUCCEEDED or job.result != attempt.id:
                raise StorageError("inconsistent publication receipt")
            verify_file(final, candidate.sha256, candidate.size_bytes)
        else:
            if job.state != JobState.RUNNING:
                raise TransitionError("attempt no longer running")
            # Hash outside SQLite's write lock; under the fence compare the
            # verified inode/size/mtime and only perform constant-size work.
            verified_path = final if final.exists() or final.is_symlink() else staged
            try:
                verified_stamp = verify_file(verified_path, candidate.sha256, candidate.size_bytes)
            except StorageError:
                # Another successful publisher may have removed the temp link.
                if verified_path != staged or not final.exists():
                    raise
                verified_path = final
                verified_stamp = verify_file(final, candidate.sha256, candidate.size_bytes)
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                current, latest_document = self._authority(db, actor, attempt)
                latest = self._intent(db, attempt)
                if latest is None or latest[:3] != row[:3] or latest_document != document:
                    raise TransitionError("publication intent changed")
                if latest[3]:
                    if current.state != JobState.SUCCEEDED or current.result != attempt.id:
                        raise StorageError("inconsistent publication receipt")
                    # Verify the exact file after the transaction below.
                else:
                    if current.state != JobState.RUNNING:
                        raise TransitionError("attempt no longer running")
                    files.promote(staged, final, verified_path, verified_stamp)
                    self._commit_publication(db, attempt)
            # Also catches a missing/corrupt final when the other process won.
            verify_file(final, candidate.sha256, candidate.size_bytes)
        files.discard_staging_link(staged, final)
        return self._receipt(attempt, manifest, final)

    @staticmethod
    def _receipt(attempt, manifest, final):
        candidate, sem = manifest["candidate"], manifest["stage_semantics"]
        return ArtifactReceipt(attempt.id, attempt.job_id, attempt.task_id, attempt.id,
                               attempt.generation, manifest["recipe_revision_id"],
                               manifest["snapshot_sha256"], candidate["sha256"],
                               candidate["size_bytes"], sem["id"], sem["engine_revision"],
                               sem["engine_version"], sem["renderer_revision"], final)

    def get_artifact(self, actor, job_id):
        # Owner-scoped lookup, no arbitrary path or artifact ID accepted.
        with self._connection() as db:
            db.execute("BEGIN")
            job = SQLiteJobStore._read(db, job_id, actor)
            if job.state != JobState.SUCCEEDED or job.result != job.attempt_id:
                raise TransitionError("job has no committed MP4")
            ref = AttemptRef(job.id, job.attempt_id, job.generation, job.task_id)
            row = self._intent(db, ref)
            if row is None or not row[3]:
                raise StorageError("committed artifact receipt missing")
        return self._complete(actor, ref)
