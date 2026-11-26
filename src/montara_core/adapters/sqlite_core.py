"""Compose the existing project and job adapters over one SQLite lifecycle."""
from pathlib import Path

from montara_core.adapters.local_media import LocalMedia
from montara_core.adapters.sqlite_jobs import SQLiteJobStore
from montara_core.adapters.sqlite_media import SQLiteMediaStore
from montara_core.adapters.sqlite_projects import SQLiteProjectStore
from montara_core.adapters.sqlite_publication import SQLitePublicationStore
from montara_core.adapters.sqlite_identity import SQLiteIdentityStore


class SQLiteCoreStore(SQLiteProjectStore, SQLiteJobStore, SQLiteMediaStore, SQLitePublicationStore, SQLiteIdentityStore):
    """All ports share one initialized database; no new persistence authority."""

    def __init__(self, path: Path, local: LocalMedia | None = None,
                 artifact_root: Path | None = None, pilot=None) -> None:
        self._local = local
        self._artifact_root = artifact_root
        self._pilot = pilot
        super().__init__(path)
