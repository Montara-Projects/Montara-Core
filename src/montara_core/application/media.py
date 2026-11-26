"""Trusted internal media operations; no public upload or identity API."""
from montara_core.application.media_ports import MediaStore
from montara_core.domain.execution import ExecutionSpec, OutputProfile, Shot
from montara_core.domain.jobs import AttemptRef
from montara_core.domain.media import validate_locator, validate_recipe
from montara_core.domain.projects import InvalidValue, validate_id


class MediaService:
    def __init__(self, store: MediaStore) -> None:
        self._store = store

    def register_original(self, actor: str, project: str, kind: str, locator: str):
        validate_id(actor)
        validate_id(project)
        if kind not in ("video", "audio"):
            raise InvalidValue("invalid media kind")
        validate_locator(locator)
        return self._store.register(actor, project, kind, locator)

    def revise_original(self, actor: str, asset: str, expected_revision: str, locator: str):
        for value in (actor, asset, expected_revision):
            validate_id(value)
        validate_locator(locator)
        return self._store.revise(actor, asset, expected_revision, locator)

    def add_location(self, actor: str, representation: str, locator: str):
        validate_id(actor)
        validate_id(representation)
        validate_locator(locator)
        return self._store.add_location(actor, representation, locator)

    def get_representation(self, actor: str, representation: str):
        validate_id(actor)
        validate_id(representation)
        return self._store.get_representation(actor, representation)

    def save_recipe(self, actor: str, project: str, expected_revision: str | None,
                    master: str, shots: tuple[Shot, ...], profile: OutputProfile = OutputProfile()):
        validate_id(actor)
        validate_id(project)
        if expected_revision is not None:
            validate_id(expected_revision)
        validate_recipe(master, shots, profile)
        return self._store.save_recipe(actor, project, expected_revision, master, shots, profile)

    def get_recipe(self, actor: str, recipe: str):
        validate_id(actor)
        validate_id(recipe)
        return self._store.get_recipe(actor, recipe)

    def prepare_execution(self, attempt: AttemptRef) -> ExecutionSpec:
        """Worker-only: stage verified copies for the exact active attempt."""
        return self._store.prepare_execution(attempt)
