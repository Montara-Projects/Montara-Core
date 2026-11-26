"""Local private file promotion with no-overwrite and explicit durability barriers.

Roots belong to the trusted Core operator; not a hostile-host sandbox.
The staged copy is Core-owned, never a hardlink to Engine's candidate.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat

from montara_core.application.ports import StorageError
from montara_core.domain.projects import validate_id


def stamp(info):
    if not stat.S_ISREG(info.st_mode):
        raise StorageError("publication requires a regular file")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def file_stamp(path):
    try:
        return stamp(path.lstat())
    except OSError:
        raise StorageError("publication file unavailable") from None


@contextmanager
def open_regular(path: Path):
    try:
        if path.parent.resolve() != path.parent:
            raise StorageError("publication path escaped its canonical directory")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            stamp(os.fstat(stream.fileno()))
            yield stream
    except OSError:
        raise StorageError("publication file unavailable") from None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest key")
        result[key] = value
    return result


def read_manifest(path):
    with open_regular(path) as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise StorageError("candidate manifest too large")
    try:
        return json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise StorageError("invalid candidate manifest") from None


def verify_file(path, expected_hash, expected_size):
    with open_regular(path) as stream:
        before = stamp(os.fstat(stream.fileno()))
        if before[2] != expected_size or hashlib.file_digest(stream, "sha256").hexdigest() != expected_hash:
            raise StorageError("artifact checksum or size mismatch")
        if stamp(os.fstat(stream.fileno())) != before or file_stamp(path) != before:
            raise StorageError("artifact changed during verification")
        return before


class LocalPublication:
    def __init__(self, root: Path):
        self.root = root
        if not root.is_absolute() or not root.is_dir() or root.resolve() != root:
            raise StorageError("artifact root unavailable")

    def sync_directory(self):
        try:
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            raise StorageError("artifact directory sync failed") from None

    def final(self, attempt_id):
        validate_id(attempt_id)
        return self.root / (attempt_id + ".mp4")

    def staged(self, attempt_id, token):
        validate_id(attempt_id)
        validate_id(token)
        return self.root / (attempt_id + "." + token + ".partial")

    def copy_verified(self, source, target, digest, size):
        created = False
        try:
            with open_regular(source) as stream:
                before = stamp(os.fstat(stream.fileno()))
                if before[2] != size:
                    raise StorageError("candidate size mismatch")
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                created = True
                with os.fdopen(fd, "wb") as out:
                    checksum = hashlib.sha256()
                    count = 0
                    while chunk := stream.read(1024 * 1024):
                        count += len(chunk)
                        if count > size:
                            raise StorageError("candidate size mismatch")
                        checksum.update(chunk)
                        out.write(chunk)
                    if (count != size or checksum.hexdigest() != digest or
                            stamp(os.fstat(stream.fileno())) != before or file_stamp(source) != before):
                        raise StorageError("candidate checksum or size mismatch")
                    out.flush()
                    os.fsync(out.fileno())
            self.sync_directory()
        except (OSError, StorageError) as exc:
            if created:
                target.unlink(missing_ok=True)
            if isinstance(exc, StorageError):
                raise
            raise StorageError("candidate staging failed") from None

    def promote(self, staged, final, verified_path, verified_stamp):
        """Called under the DB write fence, without rehashing large files."""
        try:
            if final.exists() or final.is_symlink():
                if file_stamp(final) != verified_stamp:
                    raise StorageError("artifact destination conflict")
            else:
                if verified_path != staged or file_stamp(staged) != verified_stamp:
                    raise StorageError("verified staged artifact disappeared")
                try:
                    # Same filesystem, no overwrite. Link only the Core-owned
                    # verified temp, not the renderer's mutable source.
                    os.link(staged, final, follow_symlinks=False)
                except FileExistsError:
                    pass
                if file_stamp(final) != verified_stamp:
                    raise StorageError("artifact destination conflict")
            self.sync_directory()
        except OSError:
            raise StorageError("artifact promotion failed") from None

    def discard_staging_link(self, staged, final):
        # After commit only. A leftover link is harmless and recoverable;
        # never remove a different inode or a runner workspace.
        try:
            if file_stamp(staged) == file_stamp(final):
                staged.unlink()
                self.sync_directory()
        except (OSError, StorageError):
            pass
