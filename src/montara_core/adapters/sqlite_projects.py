"""Local project persistence; shared SQLite lifecycle."""
from montara_core.adapters.sqlite_db import SQLiteDatabase
from montara_core.domain.projects import Account, Project


class SQLiteProjectStore(SQLiteDatabase):
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
