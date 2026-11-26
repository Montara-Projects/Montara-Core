"""Compatibility composition helper for the internal project service."""
from montara_core.application.projects import ProjectService
from montara_core.bootstrap.config import Config
from montara_core.bootstrap.core import build_core


def build_project_service(config: Config) -> ProjectService:
    return build_core(config).projects
