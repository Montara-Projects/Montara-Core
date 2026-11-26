"""Trusted, explicitly invoked pilot worker. No listener or background retry."""
from contextlib import contextmanager
import fcntl
import json
import os
import shutil
import stat
import sys

from montara_core.adapters.zucker_engine import ZuckerEngineAdapter, EngineExecutionError
from montara_core.application.ports import StorageError
from montara_core.bootstrap.core import build_core
from montara_core.domain.jobs import TransitionError
from montara_core.domain.pilot import MIN_FREE_BYTES
from montara_core.domain.projects import InvalidValue, validate_id


class WorkerBusy(RuntimeError):
    pass


@contextmanager
def worker_lock(config):
    """Serialize pilot CLI invocations for this canonical private DB."""
    path = config.sqlite_path.with_name(config.sqlite_path.name + ".worker.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise StorageError("invalid worker lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise WorkerBusy("worker already active") from None
        yield
    finally:
        os.close(fd)


def _report(state, claim=None):
    result = {"worker": state}
    if claim is not None:
        # Trusted operator stdout only; no path, token, raw exception or media.
        result.update(job_id=claim.attempt.job_id, attempt_id=claim.attempt.id,
                      generation=claim.attempt.generation)
    print(json.dumps(result), flush=True)


def _run(core, config, adapter, *, recover=None, interrupt=None):
    active = core.jobs.running_attempt()
    if recover is not None or interrupt is not None:
        wanted = recover if recover is not None else interrupt
        claim = (core.jobs.running_export() if interrupt is not None
                 else core.jobs.current_export(wanted))
        if claim is None or claim.attempt.id != wanted:
            _report("attempt_not_current")
            return 3
        try:
            if interrupt is not None:
                core.jobs.recover(claim.attempt)
                _report("interrupted", claim)
            else:
                core.publication.recover(claim.owner_id, claim.attempt)
                _report("recovered", claim)
            return 0
        except (StorageError, TransitionError, OSError):
            _report("recovery_required", claim)
            return 5
    if active is not None:
        _report("attempt_pending", core.jobs.running_export())
        return 3
    # Conservative admission floor, not a reservation or a proof of peak space.
    if any(shutil.disk_usage(root).free < MIN_FREE_BYTES
           for root in (config.sqlite_path.parent, config.media_root,
                        config.workspace_root, config.artifact_root)):
        _report("insufficient_disk")
        return 1
    claim = core.jobs.claim_export()
    if claim is None:
        if core.jobs.running_attempt() is not None:
            _report("attempt_pending", core.jobs.running_export())
            return 3
        _report("idle")
        return 0
    ref = claim.attempt
    try:
        spec = core.media.prepare_execution(ref)
        candidate = adapter.execute(spec)
    except (EngineExecutionError, StorageError, InvalidValue, TransitionError):
        # Known pre-publication failure; adapter did not return a candidate.
        # Exact fencing remains in Core. Never override a cancelled/stale job.
        try:
            core.jobs.fail(ref)
        except (StorageError, TransitionError):
            _report("recovery_required", claim)
            return 5
        _report("failed", claim)
        return 4
    except Exception:
        _report("recovery_required", claim)
        return 5
    try:
        core.publication.publish(claim.owner_id, ref, candidate)
    except Exception:
        # Publication may already have committed or left a durable intent.
        # Do not mark failed, cancel, retry or re-render on ambiguous result.
        _report("recovery_required", claim)
        return 5
    _report("published", claim)
    return 0


def run_worker(config, *, recover=None, interrupt=None):
    try:
        if sys.platform != "linux" or config.pilot is None:
            _report("invalid_worker_config")
            return 2
        if recover is not None:
            validate_id(recover)
        if interrupt is not None:
            validate_id(interrupt)
        if config.sqlite_path.resolve() != config.sqlite_path:
            raise StorageError("noncanonical database")
        with worker_lock(config):
            core = build_core(config)
            adapter = ZuckerEngineAdapter(config.pilot.renderer_root, config.pilot.engine_root,
                                          timeout=config.pilot.render_timeout_sec)
            return _run(core, config, adapter, recover=recover, interrupt=interrupt)
    except InvalidValue:
        _report("invalid_attempt")
        return 2
    except WorkerBusy:
        _report("worker_busy")
        return 3
    except (OSError, StorageError, TransitionError):
        _report("worker_unavailable")
        return 1
