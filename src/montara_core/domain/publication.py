"""Immutable receipt of an authorized, verified local MP4 publication."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArtifactReceipt:
    id: str
    job_id: str
    task_id: str
    attempt_id: str
    generation: int
    recipe_revision_id: str
    snapshot_sha256: str
    sha256: str
    size_bytes: int
    stage_profile: str
    engine_revision: str
    engine_version: str
    renderer_revision: str
    path: Path
