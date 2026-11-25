import contextlib
import io
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4
from unittest.mock import patch

from montara_core.adapters.sqlite_projects import SQLiteProjectStore
from montara_core.application.ports import StorageError
from montara_core.bootstrap.config import Config, ConfigError, load_config
from montara_core.bootstrap.main import main
from montara_core.bootstrap.projects import build_project_service
from montara_core.domain.projects import Account, InvalidValue, Project, ProjectNotFound, UnknownAccount


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "core.sqlite"
        self.config = Config(sqlite_path=self.path)
        self.service = build_project_service(self.config)
        self.a = self.service.provision_account()
        self.b = self.service.provision_account()

    def test_create_read_list_and_reopen(self):
        first = self.service.create_project(self.a.id, "Concert A")
        second = self.service.create_project(self.a.id, "Concert B")
        self.assertEqual(first.owner_id, self.a.id)
        self.assertNotEqual(first.id, second.id)
        # Every adapter operation closes its connection. Fresh composition
        # below reopens the on-disk database without carrying any state.
        del self.service
        reopened = build_project_service(self.config)
        self.assertEqual(reopened.get_project(self.a.id, first.id), first)
        self.assertCountEqual(reopened.list_projects(self.a.id), [first, second])
        self.assertEqual(reopened.list_projects(self.b.id), [])

    def test_cross_owner_indistinguishable_from_missing(self):
        a_project = self.service.create_project(self.a.id, "Private A")
        b_project = self.service.create_project(self.b.id, "Private B")
        self.assertEqual(self.service.list_projects(self.a.id), [a_project])
        self.assertEqual(self.service.list_projects(self.b.id), [b_project])
        for actor, foreign in [(self.a.id, b_project.id), (self.b.id, a_project.id),
                               (self.a.id, str(uuid4()))]:
            with self.assertRaisesRegex(ProjectNotFound, "^project not found$"):
                self.service.get_project(actor, foreign)

    def test_unknown_actor_and_invalid_id(self):
        unknown = str(uuid4())
        for operation in [
            lambda: self.service.create_project(unknown, "X"),
            lambda: self.service.get_project(unknown, str(uuid4())),
            lambda: self.service.list_projects(unknown),
        ]:
            with self.assertRaises(UnknownAccount):
                operation()
        with self.assertRaises(InvalidValue):
            self.service.list_projects("not-an-id")
        with self.assertRaises(InvalidValue):
            Account("not-an-id")

    def test_name_validation_no_write(self):
        for name in ("", " ", " leading", "trailing ", "x"*121, "a\x00b", "a\nb", None):
            with self.subTest(name=name), self.assertRaises(InvalidValue):
                self.service.create_project(self.a.id, name)
        self.assertEqual(self.service.list_projects(self.a.id), [])
        text = "Concierto '); DROP TABLE projects; --"
        created = self.service.create_project(self.a.id, text)
        self.assertEqual(self.service.get_project(self.a.id, created.id).name, text)

    def test_fk_and_unique_constraint_rollback(self):
        store = SQLiteProjectStore(self.path)
        orphan = Project(str(uuid4()), str(uuid4()), "orphan")
        with self.assertRaises(StorageError):
            store.add_project(orphan)
        self.assertIsNone(store.find_project(orphan.owner_id, orphan.id))
        with self.assertRaises(StorageError):
            store.add_account(self.a)
        original = self.service.create_project(self.a.id, "original")
        with self.assertRaises(StorageError):
            store.add_project(Project(original.id, self.b.id, "duplicate"))
        self.assertEqual(self.service.get_project(self.a.id, original.id), original)
        self.assertEqual(self.service.list_projects(self.b.id), [])
        # A failed transaction must not poison the next one.
        self.assertEqual(self.service.create_project(self.b.id, "valid").owner_id, self.b.id)

    def test_schema_version_and_fk_on_every_connection(self):
        store = SQLiteProjectStore(self.path)
        for _ in range(2):
            with store._connection() as connection:
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone(), (1,))
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone(), (1,))
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_transaction_rolls_back_on_error(self):
        store = SQLiteProjectStore(self.path)
        new_id = str(uuid4())
        with self.assertRaises(StorageError):
            with store._connection() as connection:
                connection.execute("INSERT INTO accounts(id) VALUES (?)", (new_id,))
                connection.execute("INSERT INTO accounts(id) VALUES (?)", (new_id,))
        self.assertFalse(store.account_exists(new_id))

    def test_unknown_or_unversioned_schema_refused(self):
        for index, statement in enumerate(("PRAGMA user_version = 99", "CREATE TABLE foreign_table(x)")):
            other = Path(self.tmp.name) / f"other{index}.sqlite"
            connection = sqlite3.connect(other)
            try:
                connection.execute(statement)
                connection.commit()
            finally:
                connection.close()
            with self.assertRaises(StorageError):
                SQLiteProjectStore(other)
            connection = sqlite3.connect(other)
            try:
                self.assertEqual(connection.execute("SELECT name FROM sqlite_schema WHERE name='accounts'").fetchall(), [])
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 99 if index == 0 else 0)
            finally:
                connection.close()

    def test_explicit_storage_config_and_check_no_write(self):
        config_file = Path(self.tmp.name) / "core.toml"
        fresh = Path(self.tmp.name) / "fresh.sqlite"
        config_file.write_text(f'[storage]\nsqlite_path = "{fresh.as_posix()}"\n', encoding="utf-8")
        loaded = load_config(config_file)
        self.assertEqual(loaded.sqlite_path, fresh)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--config", str(config_file), "--check-config"]), 0)
        self.assertFalse(fresh.exists())
        build_project_service(loaded)
        self.assertTrue(fresh.is_file())
        with self.assertRaises(StorageError):
            build_project_service(Config())
        for value in ('"relative.db"', '""', 'true', '"/missing/parent/core.sqlite"'):
            config_file.write_text(f"[storage]\nsqlite_path = {value}\n", encoding="utf-8")
            with self.subTest(value=value), self.assertRaises(ConfigError):
                load_config(config_file)

    def _assert_incompatible_unchanged(self, path):
        before = path.read_bytes()
        with self.assertRaisesRegex(StorageError, "^incompatible database schema$"):
            SQLiteProjectStore(path)
        self.assertEqual(path.read_bytes(), before)

    def test_empty_database_marked_v1_rejected_without_mutation(self):
        other = Path(self.tmp.name) / "empty-v1.sqlite"
        connection = sqlite3.connect(other)
        try:
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        finally:
            connection.close()
        self._assert_incompatible_unchanged(other)

    def test_v1_without_owner_fk_rejected_without_mutation(self):
        other = Path(self.tmp.name) / "no-fk.sqlite"
        connection = sqlite3.connect(other)
        try:
            # Same columns, PKs, checks and index as v1; only the owner FK
            # is missing. PRAGMA foreign_keys=ON cannot restore it.
            connection.execute("""CREATE TABLE accounts (
        id TEXT PRIMARY KEY NOT NULL CHECK(length(id) = 36)
    )""")
            connection.execute("""CREATE TABLE projects (
        id TEXT PRIMARY KEY NOT NULL CHECK(length(id) = 36),
        owner_id TEXT NOT NULL,
        name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120 AND name = trim(name))
    )""")
            connection.execute("CREATE INDEX projects_owner_id_idx ON projects(owner_id, id)")
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        finally:
            connection.close()
        self._assert_incompatible_unchanged(other)

    def test_v1_missing_index_rejected_without_repair(self):
        original = self.service.create_project(self.a.id, "preserve me")
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("DROP INDEX projects_owner_id_idx")
            connection.commit()
        finally:
            connection.close()
        self._assert_incompatible_unchanged(self.path)
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute(
                "SELECT name FROM projects WHERE id = ?", (original.id,)
            ).fetchone(), ("preserve me",))
            self.assertEqual(connection.execute(
                "SELECT name FROM sqlite_schema WHERE name = 'projects_owner_id_idx'"
            ).fetchall(), [])
        finally:
            connection.close()

    def test_cli_rejects_incompatible_v1_before_bind(self):
        other = Path(self.tmp.name) / "cli-v1.sqlite"
        connection = sqlite3.connect(other)
        try:
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        finally:
            connection.close()
        before = other.read_bytes()
        config = Path(self.tmp.name) / "cli.toml"
        config.write_text(
            f'[http]\nport = 0\n[storage]\nsqlite_path = "{other.as_posix()}"\n',
            encoding="utf-8",
        )
        with patch("montara_core.bootstrap.main.create_server") as bind:
            with contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(main(["--config", str(config)]), 1)
            bind.assert_not_called()
            self.assertIn("Storage error: incompatible database schema", error.getvalue())
        result = subprocess.run(
            [sys.executable, "-m", "montara_core", "--config", str(config)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("Storage error: incompatible database schema", result.stderr)
        self.assertNotIn(str(other), result.stderr)
        self.assertEqual(other.read_bytes(), before)
