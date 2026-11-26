"""Trusted in-process publication boundary. Engine remains an executor."""
from typing import Protocol

from montara_core.application.engine_port import EngineAdapter
from montara_core.application.media import MediaService
from montara_core.domain.execution import EngineCandidate
from montara_core.domain.jobs import AttemptRef
from montara_core.domain.projects import validate_id
from montara_core.domain.publication import ArtifactReceipt


class PublicationStore(Protocol):
    def authorize_export(self, actor: str, attempt: AttemptRef) -> None: ...
    def publish(self, actor: str, attempt: AttemptRef, candidate: EngineCandidate) -> ArtifactReceipt: ...
    def recover_publication(self, actor: str, attempt: AttemptRef) -> ArtifactReceipt: ...
    def get_artifact(self, actor: str, job_id: str) -> ArtifactReceipt: ...


class PublicationService:
    def __init__(self, store: PublicationStore, media: MediaService):
        self._store = store
        self._media = media

    def execute(self, actor: str, attempt: AttemptRef, adapter: EngineAdapter) -> ArtifactReceipt:
        """Execute a claimed attempt once. On interruption use recover, not execute again."""
        validate_id(actor)
        self._store.authorize_export(actor, attempt)
        spec = self._media.prepare_execution(attempt)
        candidate = adapter.execute(spec)
        return self._store.publish(actor, attempt, candidate)

    def publish(self, actor: str, attempt: AttemptRef, candidate: EngineCandidate) -> ArtifactReceipt:
        validate_id(actor)
        return self._store.publish(actor, attempt, candidate)

    def recover(self, actor: str, attempt: AttemptRef) -> ArtifactReceipt:
        """Operator/worker recovery after confirming the renderer is quiescent."""
        validate_id(actor)
        return self._store.recover_publication(actor, attempt)

    def get_artifact(self, actor: str, job_id: str) -> ArtifactReceipt:
        validate_id(actor)
        validate_id(job_id)
        return self._store.get_artifact(actor, job_id)
