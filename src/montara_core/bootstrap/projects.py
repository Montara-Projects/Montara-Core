"""Compose the internal project service using explicitly configured storage."""
from montara_core.adapters.sqlite_projects import SQLiteProjectStore
from montara_core.application.ports import StorageError
from montara_core.application.projects import ProjectService
from montara_core.bootstrap.config import Config


def build_project_service(config: Config) -> ProjectService:
    if config.sqlite_path is None:
        raise StorageError("project storage is not configured")
    return ProjectService(SQLiteProjectStore(config.sqlite_path))
