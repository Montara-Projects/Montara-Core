"""Trusted internal job and worker operations; no public authentication."""
from montara_core.application.job_ports import JobStore
from montara_core.domain.jobs import AttemptRef, ExportClaim, Job, Task, validate_key, validate_result
from montara_core.domain.projects import InvalidValue, validate_id


class JobService:
    def __init__(self, store: JobStore) -> None:
        self._store = store

    def create_job(self, actor: str, project: str, key: str, recipe_id: str | None = None) -> Job:
        validate_id(actor)
        validate_id(project)
        validate_key(key)
        if recipe_id is not None:
            validate_id(recipe_id)
        return self._store.create(actor, project, key, recipe_id)

    def get_job(self, actor: str, job_id: str) -> Job:
        validate_id(actor)
        validate_id(job_id)
        return self._store.get(actor, job_id)

    def get_task(self, actor: str, job_id: str) -> Task:
        validate_id(actor)
        validate_id(job_id)
        return self._store.get_task(actor, job_id)

    def claim(self, synthetic_only: bool = False) -> AttemptRef | None:
        """Trusted worker only; at most one running job globally."""
        return self._store.claim(synthetic_only)

    def claim_export(self) -> ExportClaim | None:
        """Trusted pilot worker only; owner is resolved atomically by Core."""
        return self._store.claim_export()

    def running_export(self) -> ExportClaim | None:
        return self._store.running_export()

    def current_export(self, attempt_id: str) -> ExportClaim | None:
        validate_id(attempt_id)
        return self._store.current_export(attempt_id)

    def running_attempt(self) -> AttemptRef | None:
        """Trusted recovery lookup, including a claim whose response was lost."""
        return self._store.running_attempt()

    def succeed(self, attempt: AttemptRef, result: str) -> Job:
        validate_result(result)
        return self._store.finish(attempt, "succeeded", result)

    def fail(self, attempt: AttemptRef) -> Job:
        return self._store.finish(attempt, "failed", None)

    def recover(self, attempt: AttemptRef) -> Job:
        """Trusted operator/worker declares exact interrupted attempt lost."""
        return self._store.finish(attempt, "interrupted", None)

    def retry(self, actor: str, attempt: AttemptRef) -> Job:
        validate_id(actor)
        return self._store.retry(actor, attempt)

    def cancel(self, actor: str, job_id: str, generation: int,
               attempt_id: str | None = None) -> Job:
        validate_id(actor)
        validate_id(job_id)
        if type(generation) is not int or generation < 0:
            raise InvalidValue("invalid generation")
        if attempt_id is not None:
            validate_id(attempt_id)
        return self._store.cancel(actor, job_id, generation, attempt_id)
