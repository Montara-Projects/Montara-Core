"""Internal development composition boundary, not a public or authenticated API."""
from dataclasses import dataclass

from montara_core.adapters.sqlite_core import SQLiteCoreStore
from montara_core.application.jobs import JobService
from montara_core.application.identity import IdentityStore
from montara_core.application.media import MediaService
from montara_core.application.publication import PublicationService
from montara_core.adapters.local_media import LocalMedia
from montara_core.application.ports import StorageError
from montara_core.application.projects import ProjectService
from montara_core.bootstrap.config import Config, ConfigError, validate_roots, validate_artifact_root


@dataclass(frozen=True)
class CoreServices:
    """Trusted in-process callers only; no actor claims from untrusted input."""

    projects: ProjectService
    jobs: JobService
    media: MediaService
    publication: PublicationService
    identity: IdentityStore


def build_core(config: Config) -> CoreServices:
    if config.sqlite_path is None:
        raise StorageError("Core storage is not configured")
    if (config.media_root is None) != (config.workspace_root is None):
        raise StorageError("media roots must be configured together")
    local = None
    if config.media_root is not None:
        try:
            validate_roots(config.sqlite_path, config.media_root, config.workspace_root)
        except ConfigError as exc:
            raise StorageError("invalid local media roots") from exc
        local = LocalMedia(config.media_root, config.workspace_root)
    if config.artifact_root is not None:
        if local is None:
            raise StorageError("artifact root requires media and workspace roots")
        try:
            validate_artifact_root(config.sqlite_path, config.media_root, config.workspace_root,
                                   config.artifact_root)
        except ConfigError as exc:
            raise StorageError("invalid artifact root") from exc
    store = SQLiteCoreStore(config.sqlite_path, local=local, artifact_root=config.artifact_root)
    media = MediaService(store)
    return CoreServices(ProjectService(store), JobService(store), media,
                        PublicationService(store, media), store)
