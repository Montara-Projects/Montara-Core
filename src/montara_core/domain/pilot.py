"""Conservative engineering-pilot limits, not general product capabilities."""
from dataclasses import dataclass
from pathlib import Path

from montara_core.domain.projects import InvalidValue

FIXTURES = {"camera-a.mp4": "video", "camera-b.mp4": "video", "master.wav": "audio"}
# Immutable H2.10 synthetic bank, verified in BRAIN #32 / 7230075212.
# No caller-supplied manifests, no generation or decoding in the HTTP path.
FIXTURE_PINS = (
    ("camera-a.mp4", "f290175c1fb7f2587baf6e535b0f6adde0ad78b2d31ec250ae027a0590abfbc7", 63410),
    ("camera-b.mp4", "2817a6ab4b4833f40a5eb9534a90c2c173d3affe408045594f54daa09d87674d", 63194),
    ("master.wav", "8ae3abdb2fef54830f15e0177af8daa92cfb8481564fc9822a9acee950eaf33e", 576078),
)
MAX_PROJECTS = 4
MAX_JOBS = 8
MAX_RECIPES = 8
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_INPUT_BYTES = 512 * 1024 * 1024
MAX_DURATION_MS = 60_000
MIN_FREE_BYTES = 2 * 1024**3


@dataclass(frozen=True)
class PilotSettings:
    issuer: str
    allowed_subjects: tuple[str, ...]
    renderer_root: Path
    engine_root: Path
    render_timeout_sec: int = 180
    fixture_bank_root: Path | None = None


def validate_pilot_recipe(shots):
    if (len(shots) > 30 or any(s.end_ms > MAX_DURATION_MS for s in shots)
            or sum(s.end_ms - s.start_ms for s in shots) > MAX_DURATION_MS):
        raise InvalidValue("pilot recipe exceeds engineering limit")


def validate_pilot_snapshot(document):
    shots = document["shots"]
    if (len(shots) > 30 or any(s["end_ms"] > MAX_DURATION_MS for s in shots)
            or sum(s["end_ms"] - s["start_ms"] for s in shots) > MAX_DURATION_MS
            or len(document["inputs"]) > 3
            or any(i["size_bytes"] > MAX_FILE_BYTES for i in document["inputs"])
            or sum(i["size_bytes"] for i in document["inputs"]) > MAX_INPUT_BYTES):
        raise InvalidValue("pilot snapshot exceeds engineering limit")
