"""Compose the existing project and job adapters over one SQLite lifecycle."""
from pathlib import Path

from montara_core.adapters.local_media import LocalMedia
from montara_core.adapters.sqlite_jobs import SQLiteJobStore
from montara_core.adapters.sqlite_media import SQLiteMediaStore
from montara_core.adapters.sqlite_projects import SQLiteProjectStore


class SQLiteCoreStore(SQLiteProjectStore, SQLiteJobStore, SQLiteMediaStore):
    """All ports share one initialized database; no new persistence authority."""

    def __init__(self, path: Path, local: LocalMedia | None = None) -> None:
        self._local = local
        super().__init__(path)
