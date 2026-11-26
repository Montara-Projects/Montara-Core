"""A one-shot deterministic worker, with no external side effects."""
from montara_core.application.jobs import JobService
from montara_core.domain.jobs import Job


def run_once(service: JobService) -> Job | None:
    attempt = service.claim(synthetic_only=True)
    if attempt is None:
        return None
    # Only the authority publishes the synthetic result; no file or media.
    return service.succeed(attempt, "synthetic:" + attempt.job_id)
