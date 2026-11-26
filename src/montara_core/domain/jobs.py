"""Minimal execution authority values, independent of storage and workers."""
from dataclasses import dataclass
from enum import StrEnum

from montara_core.domain.projects import InvalidValue, validate_id


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobNotFound(LookupError):
    """Missing or inaccessible job."""


class TransitionError(RuntimeError):
    """Illegal transition, conflicting replay or stale attempt."""


@dataclass(frozen=True)
class Job:
    id: str
    project_id: str
    state: JobState
    generation: int
    result: str | None
    attempt_id: str | None
    task_id: str | None = None


@dataclass(frozen=True)
class Task:
    id: str
    job_id: str
    kind: str
    recipe_id: str | None
    state: JobState


@dataclass(frozen=True)
class AttemptRef:
    job_id: str
    id: str
    generation: int
    task_id: str | None = None

    def __post_init__(self) -> None:
        validate_id(self.job_id)
        validate_id(self.id)
        if self.task_id is not None:
            validate_id(self.task_id)
        if type(self.generation) is not int or self.generation < 1:
            raise InvalidValue("invalid attempt generation")


def validate_key(key: str) -> None:
    if (not isinstance(key, str) or not 1 <= len(key) <= 120
            or key != key.strip() or any(ord(c) < 32 or ord(c) == 127 for c in key)):
        raise InvalidValue("invalid request key")


def validate_result(result: str) -> None:
    if (not isinstance(result, str) or not 1 <= len(result) <= 256
            or any(ord(c) < 32 or ord(c) == 127 for c in result)):
        raise InvalidValue("invalid synthetic result")


@dataclass(frozen=True)
class ExportClaim:
    attempt: AttemptRef
    owner_id: str
