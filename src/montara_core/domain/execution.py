"""Immutable v0 Engine boundary; paths are assigned by trusted Core."""
from dataclasses import asdict, dataclass
import hashlib
import json
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
    snapshot_sha256: str = ""


@dataclass(frozen=True)
class EngineCandidate:
    path: Path
    sha256: str
    size_bytes: int
    engine_version: str
    engine_revision: str


ENGINE_REVISION = "9af275b18535005e36bd289a68ceb4a1d2f6c506"
ENGINE_VERSION = "2.6.20"
RENDERER_REVISION = "1fb15f87eb8df9e1ac08123fb7a8e79ffad1c24d"
STAGE_SEMANTICS = "stage-engineering-v0"


def stage_semantics():
    """Private attestation of the fixed engineering profile, not a public recipe schema."""
    return {"id": STAGE_SEMANTICS, "renderer_revision": RENDERER_REVISION,
            "engine_revision": ENGINE_REVISION, "engine_version": ENGINE_VERSION,
            "platform": "youtube", "branding": "none",
            "presentation": "in-content-stage-defaults",
            "video_color": "stage-default-matching", "video_light": "disabled",
            "edge_fades": "stage-default-audio-video", "extra_bookends": False,
            "manual_effects": False, "neutral": False,
            "master": "continuous-from-zero-full-content-coverage",
            "padding": "preserve-mux-policy-no-missing-content"}



def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def snapshot_document(recipe_id, master_id, shots, profile, inputs):
    """Paths are not part of a portable execution snapshot."""
    return {"schema": 1, "recipe_revision_id": recipe_id,
            "master_representation_id": master_id, "shots": [asdict(s) for s in shots],
            "profile": asdict(profile), "inputs": sorted(inputs, key=lambda i: i["representation_id"]),
            "stage_semantics": stage_semantics()}


def snapshot_digest(document) -> str:
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


def spec_document(spec: ExecutionSpec):
    return snapshot_document(spec.recipe_revision_id, spec.master_representation_id,
                             spec.shots, spec.profile,
                             [{"representation_id": i.representation_id, "sha256": i.sha256,
                               "size_bytes": i.size_bytes} for i in spec.inputs])
