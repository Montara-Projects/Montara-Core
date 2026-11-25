"""Compose the internal jobs service using configured SQLite."""
from montara_core.adapters.sqlite_jobs import SQLiteJobStore
from montara_core.application.jobs import JobService
from montara_core.application.ports import StorageError
from montara_core.bootstrap.config import Config


def build_job_service(config: Config) -> JobService:
    if config.sqlite_path is None:
        raise StorageError("job storage is not configured")
    return JobService(SQLiteJobStore(config.sqlite_path))
