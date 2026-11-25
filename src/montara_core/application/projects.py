"""Internal project use cases. Actor IDs must come from a trusted caller.

This is not authentication. No HTTP route or user-supplied identity mapping
exists in H1.2; provision_account is an internal provisioning primitive.
"""
from uuid import uuid4

from montara_core.application.ports import ProjectStore
from montara_core.domain.projects import (
    Account, Project, ProjectNotFound, UnknownAccount, validate_id,
)


class ProjectService:
    def __init__(self, store: ProjectStore) -> None:
        self._store = store

    def provision_account(self) -> Account:
        account = Account(str(uuid4()))
        self._store.add_account(account)
        return account

    def _require_actor(self, actor_id: str) -> None:
        validate_id(actor_id)
        if not self._store.account_exists(actor_id):
            raise UnknownAccount("unknown account")

    def create_project(self, actor_id: str, name: str) -> Project:
        self._require_actor(actor_id)
        project = Project(str(uuid4()), actor_id, name)
        self._store.add_project(project)
        return project

    def get_project(self, actor_id: str, project_id: str) -> Project:
        self._require_actor(actor_id)
        validate_id(project_id)
        project = self._store.find_project(actor_id, project_id)
        if project is None:
            raise ProjectNotFound("project not found")
        return project

    def list_projects(self, actor_id: str) -> list[Project]:
        self._require_actor(actor_id)
        return self._store.list_projects(actor_id)
