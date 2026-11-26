"""Shared local database lifecycle and canonical transactional migrations."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator
from uuid import uuid4

from montara_core.application.ports import StorageError

SCHEMA_VERSION = 5

_MIGRATION_1 = (
    """CREATE TABLE accounts (
        id TEXT PRIMARY KEY NOT NULL CHECK(length(id) = 36)
    )""",
    """CREATE TABLE projects (
        id TEXT PRIMARY KEY NOT NULL CHECK(length(id) = 36),
        owner_id TEXT NOT NULL REFERENCES accounts(id),
        name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120 AND name = trim(name))
    )""",
    "CREATE INDEX projects_owner_id_idx ON projects(owner_id, id)",
)

_MIGRATION_2 = (
    """CREATE TABLE jobs (
        id TEXT PRIMARY KEY NOT NULL CHECK(length(id) = 36),
        project_id TEXT NOT NULL REFERENCES projects(id),
        request_key TEXT NOT NULL CHECK(length(request_key) BETWEEN 1 AND 120),
        state TEXT NOT NULL CHECK(state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
        generation INTEGER NOT NULL CHECK(generation >= 0),
        result TEXT,
        UNIQUE(project_id, request_key),
        CHECK((state = 'succeeded' AND result IS NOT NULL) OR
              (state <> 'succeeded' AND result IS NULL)),
        CHECK(state IN ('queued', 'cancelled') OR generation > 0)
    )""",
    """CREATE TABLE attempts (
        id TEXT PRIMARY KEY NOT NULL CHECK(length(id) = 36),
        job_id TEXT NOT NULL REFERENCES jobs(id),
        generation INTEGER NOT NULL CHECK(generation > 0),
        state TEXT NOT NULL CHECK(state IN ('running', 'succeeded', 'failed', 'cancelled', 'interrupted')),
        UNIQUE(job_id, generation)
    )""",
    "CREATE UNIQUE INDEX jobs_one_running_idx ON jobs(state) WHERE state = 'running'",
    "CREATE UNIQUE INDEX attempts_one_running_idx ON attempts(job_id) WHERE state = 'running'",
)


# v3 introduces original revisions, local copies, immutable recipes and one
# Task per Job. The old attempt rows are copied into the new Task relationship.
_MEDIA_DDL = (
    """CREATE TABLE assets (
        id TEXT PRIMARY KEY NOT NULL CHECK(length(id) = 36),
        project_id TEXT NOT NULL REFERENCES projects(id),
        kind TEXT NOT NULL CHECK(kind IN ('video', 'audio'))
    )""",
    """CREATE TABLE representations (
        id TEXT PRIMARY KEY NOT NULL CHECK(length(id) = 36),
        asset_id TEXT NOT NULL REFERENCES assets(id),
        revision INTEGER NOT NULL CHECK(revision > 0),
        sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
        size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
        UNIQUE(asset_id, revision)
    )""",
    """CREATE TABLE media_locations (
        id TEXT PRIMARY KEY NOT NULL CHECK(length(id) = 36),
        representation_id TEXT NOT NULL REFERENCES representations(id),
        locator TEXT NOT NULL,
        UNIQUE(representation_id, locator)
    )""",
    """CREATE TABLE recipe_revisions (
        id TEXT PRIMARY KEY NOT NULL CHECK(length(id) = 36),
        project_id TEXT NOT NULL REFERENCES projects(id),
        revision INTEGER NOT NULL CHECK(revision > 0),
        parent_id TEXT REFERENCES recipe_revisions(id),
        master_id TEXT NOT NULL REFERENCES representations(id),
        shots_json TEXT NOT NULL,
        profile_json TEXT NOT NULL,
        UNIQUE(project_id, revision)
    )""",
    """CREATE TABLE recipe_inputs (
        recipe_id TEXT NOT NULL REFERENCES recipe_revisions(id),
        representation_id TEXT NOT NULL REFERENCES representations(id),
        PRIMARY KEY(recipe_id, representation_id)
    )""",
    """CREATE TABLE tasks (
        id TEXT PRIMARY KEY NOT NULL CHECK(length(id) = 36),
        job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id),
        kind TEXT NOT NULL CHECK(kind IN ('synthetic', 'export')),
        recipe_id TEXT REFERENCES recipe_revisions(id),
        state TEXT NOT NULL CHECK(state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
        UNIQUE(id, job_id),
        CHECK((kind = 'synthetic' AND recipe_id IS NULL) OR
              (kind = 'export' AND recipe_id IS NOT NULL))
    )""",
)
_ATTEMPTS_V3 = """CREATE TABLE attempts (
        id TEXT PRIMARY KEY NOT NULL CHECK(length(id) = 36),
        job_id TEXT NOT NULL REFERENCES jobs(id),
        task_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(generation > 0),
        state TEXT NOT NULL CHECK(state IN ('running', 'succeeded', 'failed', 'cancelled', 'interrupted')),
        UNIQUE(job_id, generation),
        FOREIGN KEY(task_id, job_id) REFERENCES tasks(id, job_id)
    )"""
_ATTEMPTS_INDEX_V3 = "CREATE UNIQUE INDEX attempts_one_running_idx ON attempts(task_id) WHERE state = 'running'"
_SCHEMA_3 = _MIGRATION_1 + (_MIGRATION_2[0], _MIGRATION_2[2]) + _MEDIA_DDL + (
    _ATTEMPTS_V3, _ATTEMPTS_INDEX_V3,
)


# v4 is additive: historical v3 exports have no attributed execution contract.
_PUBLICATION_DDL = (
    """CREATE TABLE export_snapshots (
        task_id TEXT PRIMARY KEY NOT NULL REFERENCES tasks(id),
        document TEXT NOT NULL
    )""",
    """CREATE TABLE artifact_publications (
        attempt_id TEXT PRIMARY KEY NOT NULL REFERENCES attempts(id),
        job_id TEXT NOT NULL REFERENCES jobs(id),
        manifest_json TEXT NOT NULL,
        staging_token TEXT NOT NULL CHECK(length(staging_token) = 36),
        committed INTEGER NOT NULL CHECK(committed IN (0, 1))
    )""",
    "CREATE UNIQUE INDEX artifact_one_committed_idx ON artifact_publications(job_id) WHERE committed = 1",
)
_SCHEMA_4 = _SCHEMA_3 + _PUBLICATION_DDL


_IDENTITY_DDL = (
    """CREATE TABLE oidc_identities (
        issuer TEXT NOT NULL,
        subject TEXT NOT NULL,
        account_id TEXT NOT NULL REFERENCES accounts(id),
        PRIMARY KEY(issuer, subject),
        UNIQUE(account_id)
    )""",
)
_SCHEMA_5 = _SCHEMA_4 + _IDENTITY_DDL


class SQLiteDatabase:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        if not self._path.is_absolute() or not self._path.parent.is_dir():
            raise StorageError("database requires an absolute path and existing directory")
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            connection = sqlite3.connect(self._path, timeout=5.0)
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA foreign_keys = ON")
            if connection.execute("PRAGMA foreign_keys").fetchone() != (1,):
                raise StorageError("foreign key enforcement unavailable")
            with connection:
                yield connection
        except sqlite3.Error:
            raise StorageError("SQLite operation failed") from None
        finally:
            if connection is not None:
                connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                if connection.execute(
                    "SELECT 1 FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*' LIMIT 1"
                ).fetchone():
                    raise StorageError("refusing nonempty unversioned database")
                for statement in _MIGRATION_1:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 1")
                version = 1
            if version == 1:
                self._validate_schema(connection, _MIGRATION_1)
                if connection.execute("PRAGMA foreign_key_check").fetchone():
                    raise StorageError("invalid foreign keys in source database")
                for statement in _MIGRATION_2:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 2")
                self._validate_schema(connection, _MIGRATION_1 + _MIGRATION_2)
                if connection.execute("PRAGMA foreign_key_check").fetchone():
                    raise StorageError("invalid foreign keys in migrated database")
                version = 2
            if version == 2:
                self._validate_schema(connection, _MIGRATION_1 + _MIGRATION_2)
                if connection.execute("PRAGMA foreign_key_check").fetchone():
                    raise StorageError("invalid foreign keys in source database")
                for statement in _MEDIA_DDL:
                    connection.execute(statement)
                for job_id, state in connection.execute("SELECT id, state FROM jobs").fetchall():
                    connection.execute(
                        "INSERT INTO tasks(id, job_id, kind, state) VALUES (?, ?, 'synthetic', ?)",
                        (str(uuid4()), job_id, state),
                    )
                connection.execute("DROP INDEX attempts_one_running_idx")
                connection.execute("ALTER TABLE attempts RENAME TO attempts_v2")
                connection.execute(_ATTEMPTS_V3)
                connection.execute(
                    "INSERT INTO attempts(id, job_id, task_id, generation, state) "
                    "SELECT a.id, a.job_id, t.id, a.generation, a.state "
                    "FROM attempts_v2 a JOIN tasks t ON t.job_id = a.job_id"
                )
                connection.execute("DROP TABLE attempts_v2")
                connection.execute(_ATTEMPTS_INDEX_V3)
                connection.execute("PRAGMA user_version = 3")
                self._validate_schema(connection, _SCHEMA_3)
                if connection.execute("PRAGMA foreign_key_check").fetchone():
                    raise StorageError("invalid foreign keys in migrated database")
                version = 3
            if version == 3:
                self._validate_schema(connection, _SCHEMA_3)
                if connection.execute("PRAGMA foreign_key_check").fetchone():
                    raise StorageError("invalid foreign keys in source database")
                for statement in _PUBLICATION_DDL:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 4")
                self._validate_schema(connection, _SCHEMA_4)
                if connection.execute("PRAGMA foreign_key_check").fetchone():
                    raise StorageError("invalid foreign keys in migrated database")
                version = 4
            if version == 4:
                self._validate_schema(connection, _SCHEMA_4)
                if connection.execute("PRAGMA foreign_key_check").fetchone():
                    raise StorageError("invalid foreign keys in source database")
                for statement in _IDENTITY_DDL:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 5")
                self._validate_schema(connection, _SCHEMA_5)
                if connection.execute("PRAGMA foreign_key_check").fetchone():
                    raise StorageError("invalid foreign keys in migrated database")
            elif version == SCHEMA_VERSION:
                self._validate_schema(connection, _SCHEMA_5)
            else:
                raise StorageError("unsupported database schema version")

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection, ddl: tuple[str, ...]) -> None:
        actual = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE name NOT GLOB 'sqlite_*'"
        ).fetchall()
        if set(actual) != {(statement,) for statement in ddl}:
            raise StorageError("incompatible database schema")
