"""Compatibility composition helper for the internal job service."""
from montara_core.application.jobs import JobService
from montara_core.bootstrap.config import Config
from montara_core.bootstrap.core import build_core


def build_job_service(config: Config) -> JobService:
    return build_core(config).jobs
