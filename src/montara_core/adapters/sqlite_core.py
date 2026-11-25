"""Compose the existing project and job adapters over one SQLite lifecycle."""
from montara_core.adapters.sqlite_jobs import SQLiteJobStore
from montara_core.adapters.sqlite_projects import SQLiteProjectStore


class SQLiteCoreStore(SQLiteProjectStore, SQLiteJobStore):
    """Both ports share one initialized database; no new persistence authority."""
