"""Copy the small pinned synthetic bank into a private project, never overwrite.

Trusted operator controls roots and their ancestors; not a sandbox against a
privileged or same-UID filesystem writer. No renderer, upload or shell here.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import os
import stat

from montara_core.application.ports import StorageError
from montara_core.domain.jobs import TransitionError
from montara_core.domain.pilot import FIXTURE_PINS, MAX_FILE_BYTES, MAX_INPUT_BYTES, MIN_FREE_BYTES
from montara_core.domain.projects import validate_id

_BANK_OWNER = 0
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_exact(directory, name, digest, size, *, bank_group=None, links=1):
    error = StorageError if bank_group is not None else TransitionError
    try:
        fd = os.open(name, _READ, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            mode = stat.S_IMODE(before.st_mode)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != links
                    or before.st_size != size):
                raise error("fixture content or file type mismatch")
            if bank_group is not None:
                if before.st_uid != _BANK_OWNER or before.st_gid != bank_group or mode != 0o440:
                    raise error("fixture bank file permissions invalid")
            elif before.st_uid != os.getuid() or mode not in (0o400, 0o440, 0o600, 0o640):
                raise error("project fixture permissions invalid")
            data = stream.read(size + 1)
            after = os.fstat(stream.fileno())
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (len(data) != size or hashlib.sha256(data).hexdigest() != digest
                    or _stamp(before) != _stamp(after) or _stamp(before) != _stamp(current)):
                raise error("fixture content mismatch")
            return data
    except FileNotFoundError:
        raise
    except OSError:
        raise error("fixture file unavailable") from None


class LocalFixtureBank:
    def __init__(self, bank_root, media_root):
        self.bank_root = bank_root
        self.media_root = media_root

    def _bank(self):
        # Bound total work before reading. Production pins are immutable code.
        if (any(not 0 < size <= MAX_FILE_BYTES for _, _, size in FIXTURE_PINS)
                or sum(size for _, _, size in FIXTURE_PINS) > MAX_INPUT_BYTES):
            raise StorageError("fixture bank exceeds pilot limits")
        fd = os.open(self.bank_root, _DIR)
        try:
            info = os.fstat(fd)
            if info.st_uid != _BANK_OWNER or stat.S_IMODE(info.st_mode) != 0o750:
                raise StorageError("fixture bank directory permissions invalid")
            return {name: _read_exact(fd, name, digest, size, bank_group=info.st_gid)
                    for name, digest, size in FIXTURE_PINS}
        finally:
            os.close(fd)

    @contextmanager
    def _project(self, project):
        root = os.open(self.media_root, _DIR)
        try:
            try:
                os.mkdir(project, mode=0o700, dir_fd=root)
                os.fsync(root)
            except FileExistsError:
                pass
            try:
                fd = os.open(project, _DIR, dir_fd=root)
            except OSError:
                raise TransitionError("project fixture directory invalid") from None
            try:
                info = os.fstat(fd)
                if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) not in (0o700, 0o750):
                    raise TransitionError("project fixture directory permissions invalid")
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise TransitionError("project fixture preparation busy") from None
                yield fd
            finally:
                os.close(fd)
        finally:
            os.close(root)

    @staticmethod
    def _copy(directory, name, data):
        # Stage privately, then link atomically without replacing a final name.
        # A crash may leave a private .partial, never an incomplete final.
        temp = ".fixture-" + name + ".partial"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temp, name, src_dir_fd=directory, dst_dir_fd=directory,
                        follow_symlinks=False)
            except FileExistsError:
                raise TransitionError("project fixture name collision") from None
            os.fsync(directory)
        finally:
            os.unlink(temp, dir_fd=directory)
            os.fsync(directory)

    def prepare(self, project):
        validate_id(project)
        try:
            # Read/verify every pinned source before touching the project.
            bank = self._bank()
            with self._project(project) as directory:
                missing, stages = [], []
                # Recover only reserved service-owned stages. A final with two
                # links is admissible solely when the second is its own stage.
                # Reject any other collision before copying/deleting anything.
                for name, digest, size in FIXTURE_PINS:
                    stage = ".fixture-" + name + ".partial"
                    links = 1
                    try:
                        info = os.stat(stage, dir_fd=directory, follow_symlinks=False)
                    except FileNotFoundError:
                        info = None
                    if info is not None:
                        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                                or stat.S_IMODE(info.st_mode) != 0o600
                                or info.st_nlink not in (1, 2) or info.st_size > size):
                            raise TransitionError("invalid reserved fixture stage")
                        if info.st_nlink == 2:
                            final = os.stat(name, dir_fd=directory, follow_symlinks=False)
                            if (info.st_dev, info.st_ino) != (final.st_dev, final.st_ino):
                                raise TransitionError("fixture stage link conflict")
                            links = 2
                        stages.append(stage)
                    try:
                        _read_exact(directory, name, digest, size, links=links)
                    except FileNotFoundError:
                        if links != 1:
                            raise TransitionError("fixture stage final missing") from None
                        missing.append(name)
                if missing:
                    space = os.fstatvfs(directory)
                    if space.f_bavail * space.f_frsize < MIN_FREE_BYTES + sum(len(bank[n]) for n in missing):
                        raise StorageError("insufficient fixture disk space")
                # These are reserved, private temporary files, not originals.
                # A torn pre-link stage may be discarded; a linked stage was
                # verified byte-for-byte above before removing its extra link.
                for stage in stages:
                    os.unlink(stage, dir_fd=directory)
                if stages:
                    os.fsync(directory)
                for name in missing:
                    self._copy(directory, name, bank[name])
                for name, digest, size in FIXTURE_PINS:
                    _read_exact(directory, name, digest, size)
                os.fsync(directory)
        except OSError:
            raise StorageError("fixture preparation unavailable") from None
