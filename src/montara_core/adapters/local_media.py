"""Project-rooted originals and exclusive per-attempt private staging."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat

from montara_core.application.ports import StorageError
from montara_core.domain.media import validate_locator
from montara_core.domain.projects import validate_id


class LocalMedia:
    def __init__(self, media_root: Path, workspace_root: Path, *, max_bytes=None):
        self.media_root = media_root
        self.workspace_root = workspace_root
        self.max_bytes = max_bytes

    @contextmanager
    def _open_source(self, project: str, locator: str):
        validate_id(project)
        validate_locator(locator)
        fd = None
        try:
            fd = os.open(self.media_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            parts = [project, *locator.split("/")]
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            source = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(source, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise StorageError("media must be a regular file")
                yield stream
        except OSError:
            raise StorageError("local media unavailable") from None
        finally:
            if fd is not None:
                os.close(fd)

    def read(self, project: str, locator: str, destination: Path | None = None) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with self._open_source(project, locator) as source:
            target = destination.open("xb") if destination is not None else None
            try:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                    if self.max_bytes is not None and size > self.max_bytes:
                        raise StorageError("pilot media size limit exceeded")
                    if target is not None:
                        target.write(chunk)
            finally:
                if target is not None:
                    target.close()
        if not size:
            raise StorageError("empty media")
        return digest.hexdigest(), size
