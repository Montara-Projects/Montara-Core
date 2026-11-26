"""Minimal immutable original media and recipe revisions (no codec inference)."""
from dataclasses import dataclass
from pathlib import PurePosixPath

from montara_core.domain.execution import OutputProfile, Shot
from montara_core.domain.projects import InvalidValue, validate_id


class MediaNotFound(LookupError):
    """Missing or inaccessible media/recipe."""


@dataclass(frozen=True)
class Asset:
    id: str
    project_id: str
    kind: str


@dataclass(frozen=True)
class Representation:
    id: str
    asset_id: str
    revision: int
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class MediaLocation:
    id: str
    representation_id: str
    locator: str


@dataclass(frozen=True)
class RecipeRevision:
    id: str
    project_id: str
    revision: int
    parent_id: str | None
    master_representation_id: str
    shots: tuple[Shot, ...]
    profile: OutputProfile


def validate_locator(locator: str) -> None:
    if (not isinstance(locator, str) or not locator or len(locator) > 1024
            or "\\" in locator or any(ord(c) < 32 or ord(c) == 127 for c in locator)
            or any(p in ("", ".", "..") for p in locator.split("/"))
            or PurePosixPath(locator).is_absolute()):
        raise InvalidValue("invalid project-relative media locator")


# Narrow contract of the pinned H2 renderer, not general media support.
MAX_SHOTS = 1000
MAX_CAMERAS = 200
MAX_DURATION_MS = 21_600_000
MAX_ENDPOINT_MS = 86_400_000
FRAME_GRID_MS = 100


def supports_export_location(kind: str, locator: str) -> bool:
    """Nominal eligibility only; Engine must verify the actual streams."""
    suffix = PurePosixPath(locator).suffix.lower()
    return suffix in {"video": (".mp4", ".mov"), "audio": (".wav",)}.get(kind, ())


def validate_recipe(master: str, shots: tuple[Shot, ...], profile: OutputProfile) -> None:
    validate_id(master)
    if not isinstance(shots, tuple) or not 1 <= len(shots) <= MAX_SHOTS:
        raise InvalidValue("recipe requires ordered immutable shots")
    cameras = set()
    duration = 0
    for shot in shots:
        if not isinstance(shot, Shot):
            raise InvalidValue("invalid shot")
        validate_id(shot.representation_id)
        if (type(shot.start_ms) is not int or type(shot.end_ms) is not int
                or not 0 <= shot.start_ms < shot.end_ms <= MAX_ENDPOINT_MS):
            raise InvalidValue("invalid half-open millisecond interval")
        if shot.start_ms % FRAME_GRID_MS or shot.end_ms % FRAME_GRID_MS:
            raise InvalidValue("H2 requires endpoints aligned to 100 ms at 30 fps")
        length = shot.end_ms - shot.start_ms
        if length > MAX_DURATION_MS:
            raise InvalidValue("H2 shot exceeds six hours")
        duration += length
        cameras.add(shot.representation_id)
    if len(cameras) > MAX_CAMERAS:
        raise InvalidValue("H2 recipe exceeds 200 camera representations")
    if duration > MAX_DURATION_MS:
        raise InvalidValue("H2 recipe exceeds six hours total")
    if not isinstance(profile, OutputProfile) or profile != OutputProfile() or any(
        type(v) is not int for v in (profile.width, profile.height, profile.fps)
    ):
        raise InvalidValue("only the initial 1080p30 H264/AAC MP4 profile is supported")
