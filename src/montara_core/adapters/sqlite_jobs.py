"""Single-node authority for jobs and attempts; all transitions atomic."""
import sqlite3
from uuid import uuid4

from montara_core.adapters.sqlite_db import SQLiteDatabase
from montara_core.adapters.sqlite_media import SQLiteMediaStore
from montara_core.application.ports import StorageError
from montara_core.domain.execution import canonical_json
from montara_core.domain.jobs import AttemptRef, ExportClaim, Job, JobNotFound, JobState, Task, TransitionError
from montara_core.domain.projects import ProjectNotFound
from montara_core.domain.pilot import MAX_JOBS, validate_pilot_snapshot


_COLUMNS = (
    "j.id, j.project_id, j.state, j.generation, j.result, "
    "(SELECT a.id FROM attempts a WHERE a.job_id = j.id AND a.generation = j.generation), "
    "t.id, t.state"
)


def _job(row: tuple) -> Job:
    return Job(row[0], row[1], JobState(row[2]), row[3], row[4], row[5], row[6])


class SQLiteJobStore(SQLiteDatabase):
    _pilot = None
    @staticmethod
    def _read(connection: sqlite3.Connection, job_id: str, actor: str | None = None) -> Job:
        sql = f"SELECT {_COLUMNS} FROM jobs j JOIN projects p ON p.id = j.project_id LEFT JOIN tasks t ON t.job_id=j.id WHERE j.id = ?"
        params = (job_id,)
        if actor is not None:
            sql += " AND p.owner_id = ?"
            params += (actor,)
        row = connection.execute(sql, params).fetchone()
        if row is None:
            raise JobNotFound("job not found")
        if row[6] is None or row[7] != row[2]:
            raise StorageError("inconsistent task state")
        return _job(row)

    @staticmethod
    def _attempt(connection: sqlite3.Connection, job: Job) -> tuple[str, str] | None:
        return connection.execute(
            "SELECT id, state FROM attempts WHERE job_id = ? AND generation = ?",
            (job.id, job.generation),
        ).fetchone()

    @classmethod
    def _exact_attempt(cls, connection: sqlite3.Connection, job: Job,
                       attempt: AttemptRef) -> str:
        current = cls._attempt(connection, job)
        task = connection.execute("SELECT id, kind FROM tasks WHERE job_id = ?", (job.id,)).fetchone()
        if (task is None or (attempt.task_id != task[0] and
                            not (attempt.task_id is None and task[1] == "synthetic"))):
            raise TransitionError("stale or invalid task")
        if (job.id != attempt.job_id or job.generation != attempt.generation
                or current is None or current[0] != attempt.id):
            raise TransitionError("stale or invalid attempt")
        return current[1]

    def create(self, actor: str, project: str, key: str, recipe_id: str | None = None) -> Job:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not connection.execute(
                "SELECT 1 FROM projects WHERE id = ? AND owner_id = ?", (project, actor)
            ).fetchone():
                raise ProjectNotFound("project not found")
            existing = connection.execute(
                "SELECT j.id, t.recipe_id FROM jobs j JOIN tasks t ON t.job_id = j.id "
                "WHERE j.project_id = ? AND j.request_key = ?", (project, key)
            ).fetchone()
            if existing:
                if existing[1] != recipe_id:
                    raise TransitionError("request key conflict")
                return self._read(connection, existing[0], actor)
            if self._pilot is not None and recipe_id is not None:
                if connection.execute(
                    "SELECT count(*) FROM jobs j JOIN projects p ON p.id=j.project_id WHERE p.owner_id=?",
                    (actor,),
                ).fetchone()[0] >= MAX_JOBS:
                    raise TransitionError("pilot job limit reached")
                if connection.execute(
                    "SELECT 1 FROM jobs j JOIN projects p ON p.id=j.project_id WHERE p.owner_id=? "
                    "AND j.state IN ('queued','running')", (actor,)
                ).fetchone():
                    raise TransitionError("pilot permits one active job per owner")
            snapshot = None
            if recipe_id is not None:
                latest = connection.execute(
                    "SELECT * FROM recipe_revisions WHERE project_id = ? ORDER BY revision DESC LIMIT 1",
                    (project,),
                ).fetchone()
                if latest is None or latest[0] != recipe_id:
                    raise TransitionError("recipe is not the current authorized revision")
                if connection.execute(
                    "SELECT 1 FROM recipe_inputs i JOIN representations r ON r.id = i.representation_id "
                    "WHERE i.recipe_id = ? AND EXISTS (SELECT 1 FROM representations newer "
                    "WHERE newer.asset_id = r.asset_id AND newer.revision > r.revision)", (recipe_id,),
                ).fetchone():
                    raise TransitionError("recipe references obsolete original")
                recipe = SQLiteMediaStore._recipe(latest)
                SQLiteMediaStore._validated_recipe_locations(connection, recipe)
                snapshot = SQLiteMediaStore._snapshot_document(connection, recipe)
                if self._pilot is not None:
                    validate_pilot_snapshot(snapshot)
            job_id = str(uuid4())
            connection.execute(
                "INSERT INTO jobs(id, project_id, request_key, state, generation) "
                "VALUES (?, ?, ?, 'queued', 0)", (job_id, project, key),
            )
            task_id = str(uuid4())
            connection.execute(
                "INSERT INTO tasks(id, job_id, kind, recipe_id, state) VALUES (?, ?, ?, ?, 'queued')",
                (task_id, job_id, "export" if recipe_id is not None else "synthetic", recipe_id),
            )
            if recipe_id is not None:
                connection.execute("INSERT INTO export_snapshots VALUES (?, ?)",
                                   (task_id, canonical_json(snapshot)))
            return self._read(connection, job_id, actor)

    def get(self, actor: str, job_id: str) -> Job:
        with self._connection() as connection:
            return self._read(connection, job_id, actor)

    def get_task(self, actor: str, job_id: str) -> Task:
        with self._connection() as connection:
            # One consistent read transaction for job and task.
            connection.execute("BEGIN")
            job = self._read(connection, job_id, actor)
            row = connection.execute(
                "SELECT id,job_id,kind,recipe_id,state FROM tasks WHERE id=?", (job.task_id,)
            ).fetchone()
            return Task(*row[:4], JobState(row[4]))

    def claim(self, synthetic_only: bool = False) -> AttemptRef | None:
        return self._claim(synthetic_only=synthetic_only)

    def claim_export(self) -> ExportClaim | None:
        if self._pilot is None:
            raise TransitionError("pilot worker is not configured")
        return self._claim(export_only=True)

    def _claim(self, synthetic_only=False, export_only=False):
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM jobs WHERE state = 'running'").fetchone():
                return None
            sql = ("SELECT j.id, j.generation, t.id, p.owner_id FROM jobs j "
                   "JOIN tasks t ON t.job_id=j.id JOIN projects p ON p.id=j.project_id "
                   "WHERE j.state='queued' AND t.state='queued'")
            params = ()
            if synthetic_only:
                sql += " AND t.kind='synthetic'"
            if export_only:
                marks = ",".join("?" for _ in self._pilot.allowed_subjects)
                sql += (" AND t.kind='export' AND EXISTS(SELECT 1 FROM export_snapshots s WHERE s.task_id=t.id)"
                        " AND EXISTS(SELECT 1 FROM oidc_identities i WHERE i.account_id=p.owner_id "
                        f"AND i.issuer=? AND i.subject IN ({marks}))")
                params = (self._pilot.issuer, *self._pilot.allowed_subjects)
            row = connection.execute(sql + " ORDER BY j.rowid LIMIT 1", params).fetchone()
            if row is None:
                return None
            job_id, generation, task_id, owner = row
            ref = AttemptRef(job_id, str(uuid4()), generation + 1, task_id)
            connection.execute(
                "UPDATE jobs SET state = 'running', generation = ? WHERE id = ?",
                (ref.generation, job_id),
            )
            connection.execute(
                "INSERT INTO attempts(id, job_id, task_id, generation, state) VALUES (?, ?, ?, ?, 'running')",
                (ref.id, job_id, task_id, ref.generation),
            )
            connection.execute("UPDATE tasks SET state = 'running' WHERE id = ?", (task_id,))
            return ExportClaim(ref, owner) if export_only else ref

    def running_export(self) -> ExportClaim | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT j.id,a.id,j.generation,a.task_id,p.owner_id FROM jobs j "
                "JOIN projects p ON p.id=j.project_id JOIN attempts a ON a.job_id=j.id AND a.generation=j.generation "
                "JOIN tasks t ON t.id=a.task_id AND t.job_id=j.id "
                "WHERE j.state='running' AND a.state='running' AND t.state='running' AND t.kind='export'"
            ).fetchone()
            return ExportClaim(AttemptRef(*row[:4]), row[4]) if row else None

    def current_export(self, attempt_id: str) -> ExportClaim | None:
        """Exact current attempt, including a successful publication whose response was lost."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT j.id,a.id,j.generation,a.task_id,p.owner_id FROM jobs j "
                "JOIN projects p ON p.id=j.project_id JOIN attempts a ON a.job_id=j.id AND a.generation=j.generation "
                "JOIN tasks t ON t.id=a.task_id AND t.job_id=j.id "
                "WHERE a.id=? AND t.kind='export' AND "
                "((j.state='running' AND t.state='running' AND a.state='running') OR "
                "(j.state='succeeded' AND t.state='succeeded' AND a.state='succeeded'))", (attempt_id,)
            ).fetchone()
            return ExportClaim(AttemptRef(*row[:4]), row[4]) if row else None

    def running_attempt(self) -> AttemptRef | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT j.id, a.id, j.generation, a.task_id FROM jobs j JOIN attempts a "
                "ON a.job_id = j.id AND a.generation = j.generation "
                "WHERE j.state = 'running' AND a.state = 'running'"
            ).fetchone()
            return AttemptRef(*row) if row else None

    def finish(self, attempt: AttemptRef, outcome: str, result: str | None) -> Job:
        if outcome not in ("succeeded", "failed", "interrupted"):
            raise TransitionError("invalid outcome")
        if (outcome == "succeeded") != (result is not None):
            raise TransitionError("invalid outcome result")
        target = JobState.FAILED if outcome == "interrupted" else JobState(outcome)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = self._read(connection, attempt.job_id)
            state = self._exact_attempt(connection, job, attempt)
            kind = connection.execute("SELECT kind FROM tasks WHERE id = ?", (job.task_id,)).fetchone()[0]
            if outcome == "succeeded" and kind != "synthetic":
                raise TransitionError("export publication requires verified artifact integration")
            if job.state == target and state == outcome and job.result == result:
                return job  # exact terminal receipt replay
            if job.state != JobState.RUNNING or state != "running":
                raise TransitionError("attempt no longer active")
            connection.execute("UPDATE attempts SET state = ? WHERE id = ?", (outcome, attempt.id))
            connection.execute(
                "UPDATE jobs SET state = ?, result = ? WHERE id = ?", (target, result, job.id)
            )
            connection.execute("UPDATE tasks SET state = ? WHERE id = ?", (target, job.task_id))
            return self._read(connection, job.id)

    def retry(self, actor: str, attempt: AttemptRef) -> Job:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = self._read(connection, attempt.job_id, actor)
            state = self._exact_attempt(connection, job, attempt)
            if state not in ("failed", "interrupted"):
                raise TransitionError("attempt not retryable")
            if job.state == JobState.QUEUED:
                return job  # exact retry replay until next claim
            if job.state != JobState.FAILED:
                raise TransitionError("job not retryable")
            if self._pilot is not None and connection.execute(
                "SELECT 1 FROM jobs j JOIN projects p ON p.id=j.project_id WHERE p.owner_id=? "
                "AND j.state IN ('queued','running')", (actor,)
            ).fetchone():
                raise TransitionError("pilot permits one active job per owner")
            connection.execute("UPDATE jobs SET state = 'queued' WHERE id = ?", (job.id,))
            connection.execute("UPDATE tasks SET state = 'queued' WHERE id = ?", (job.task_id,))
            return self._read(connection, job.id, actor)

    def cancel(self, actor: str, job_id: str, generation: int, attempt_id: str | None) -> Job:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = self._read(connection, job_id, actor)
            if job.generation != generation:
                raise TransitionError("stale cancellation")
            current = self._attempt(connection, job)
            if attempt_id is not None:
                if current is None or current[0] != attempt_id:
                    raise TransitionError("stale cancellation")
                if job.state == JobState.CANCELLED and current[1] == "cancelled":
                    return job
                if job.state != JobState.RUNNING or current[1] != "running":
                    raise TransitionError("attempt no longer active")
                connection.execute("UPDATE attempts SET state = 'cancelled' WHERE id = ?", (attempt_id,))
            else:
                if job.state == JobState.CANCELLED and (current is None or current[1] != "cancelled"):
                    return job
                if job.state != JobState.QUEUED:
                    raise TransitionError("queued cancellation required")
            connection.execute("UPDATE jobs SET state = 'cancelled' WHERE id = ?", (job.id,))
            connection.execute("UPDATE tasks SET state = 'cancelled' WHERE id = ?", (job.task_id,))
            return self._read(connection, job.id, actor)
