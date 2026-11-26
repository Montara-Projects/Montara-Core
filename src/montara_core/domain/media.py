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


def validate_recipe(master: str, shots: tuple[Shot, ...], profile: OutputProfile) -> None:
    validate_id(master)
    if not isinstance(shots, tuple) or not 1 <= len(shots) <= 1000:
        raise InvalidValue("recipe requires ordered immutable shots")
    for shot in shots:
        if not isinstance(shot, Shot):
            raise InvalidValue("invalid shot")
        validate_id(shot.representation_id)
        if (type(shot.start_ms) is not int or type(shot.end_ms) is not int
                or not 0 <= shot.start_ms < shot.end_ms <= 86_400_000):
            raise InvalidValue("invalid half-open millisecond interval")
    if not isinstance(profile, OutputProfile) or profile != OutputProfile() or any(
        type(v) is not int for v in (profile.width, profile.height, profile.fps)
    ):
        raise InvalidValue("only the initial 1080p30 H264/AAC MP4 profile is supported")
