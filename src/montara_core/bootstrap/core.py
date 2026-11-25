"""Internal development composition boundary, not a public or authenticated API."""
from dataclasses import dataclass

from montara_core.adapters.sqlite_core import SQLiteCoreStore
from montara_core.application.jobs import JobService
from montara_core.application.ports import StorageError
from montara_core.application.projects import ProjectService
from montara_core.bootstrap.config import Config


@dataclass(frozen=True)
class CoreServices:
    """Trusted in-process callers only; no actor claims from untrusted input."""

    projects: ProjectService
    jobs: JobService


def build_core(config: Config) -> CoreServices:
    if config.sqlite_path is None:
        raise StorageError("Core storage is not configured")
    store = SQLiteCoreStore(config.sqlite_path)
    return CoreServices(ProjectService(store), JobService(store))
