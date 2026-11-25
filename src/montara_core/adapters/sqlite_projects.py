"""Local SQLite adapter; short transactions, one connection per operation."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator

from montara_core.application.ports import StorageError
from montara_core.domain.projects import Account, Project

SCHEMA_VERSION = 1

# Migration 0 -> 1. Future migrations must be explicit and transactional.
# user_version belongs exclusively to this Core database.
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


class SQLiteProjectStore:
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
            if version == SCHEMA_VERSION:
                return
            if version != 0:
                raise StorageError("unsupported database schema version")
            existing = connection.execute(
                "SELECT 1 FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone()
            if existing:
                raise StorageError("refusing nonempty unversioned database")
            for statement in _MIGRATION_1:
                connection.execute(statement)
            connection.execute("PRAGMA user_version = 1")

    def add_account(self, account: Account) -> None:
        with self._connection() as connection:
            connection.execute("INSERT INTO accounts(id) VALUES (?)", (account.id,))

    def account_exists(self, account_id: str) -> bool:
        with self._connection() as connection:
            return connection.execute(
                "SELECT 1 FROM accounts WHERE id = ?", (account_id,)
            ).fetchone() is not None

    def add_project(self, project: Project) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO projects(id, owner_id, name) VALUES (?, ?, ?)",
                (project.id, project.owner_id, project.name),
            )

    def find_project(self, owner_id: str, project_id: str) -> Project | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT id, owner_id, name FROM projects WHERE owner_id = ? AND id = ?",
                (owner_id, project_id),
            ).fetchone()
            return Project(*row) if row else None

    def list_projects(self, owner_id: str) -> list[Project]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, owner_id, name FROM projects WHERE owner_id = ? ORDER BY id",
                (owner_id,),
            ).fetchall()
            return [Project(*row) for row in rows]
