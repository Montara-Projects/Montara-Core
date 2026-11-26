"""Immutable v0 Engine boundary; paths are assigned by trusted Core."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InputFile:
    representation_id: str
    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class Shot:
    representation_id: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class OutputProfile:
    width: int = 1920
    height: int = 1080
    fps: int = 30
    video_codec: str = "h264"
    audio_codec: str = "aac"
    container: str = "mp4"


@dataclass(frozen=True)
class ExecutionSpec:
    job_id: str
    task_id: str
    attempt_id: str
    generation: int
    recipe_revision_id: str
    inputs: tuple[InputFile, ...]
    master_representation_id: str
    shots: tuple[Shot, ...]
    profile: OutputProfile
    workspace: Path


@dataclass(frozen=True)
class EngineCandidate:
    path: Path
    sha256: str
    size_bytes: int
    engine_version: str
    engine_revision: str
