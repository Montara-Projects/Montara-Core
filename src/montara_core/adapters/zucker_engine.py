"""H2 private export adapter. Core retains authorization, fencing and publication."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from montara_core.domain.execution import ExecutionSpec, EngineCandidate

ENGINE_REVISION = "9af275b18535005e36bd289a68ceb4a1d2f6c506"
ENGINE_VERSION = "2.6.20"
RENDERER_REVISION = "1fb15f87eb8df9e1ac08123fb7a8e79ffad1c24d"


class EngineExecutionError(RuntimeError):
    """The attempt did not yield a usable private candidate."""


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _checked_checkout(root, expected):
    if not root.is_absolute() or not root.is_dir():
        raise EngineExecutionError("External checkout unavailable")
    try:
        actual = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True, timeout=15
        ).strip()
        dirty = subprocess.check_output(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            text=True, timeout=15
        ).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise EngineExecutionError("Cannot verify external checkout") from exc
    if actual != expected or dirty:
        raise EngineExecutionError("External checkout revision or tracked contents mismatch")


def _plan(spec):
    p = spec.profile
    if (p.width, p.height, p.fps, p.video_codec, p.audio_codec, p.container) != (
        1920, 1080, 30, "h264", "aac", "mp4"
    ):
        raise EngineExecutionError("Unsupported H2 output profile")
    inputs = {item.representation_id: item for item in spec.inputs}
    if len(inputs) != len(spec.inputs) or spec.master_representation_id not in inputs:
        raise EngineExecutionError("Invalid input identities")
    cameras = {}
    segments = []
    elapsed = 0
    for shot in spec.shots:
        if (shot.representation_id not in inputs
                or shot.representation_id == spec.master_representation_id):
            raise EngineExecutionError("Unknown camera representation")
        a, b = shot.start_ms, shot.end_ms
        if (type(a) is not int or type(b) is not int or a < 0 or b <= a
                or a * 30 % 1000 or b * 30 % 1000):
            raise EngineExecutionError("H2 requires frame-aligned positive intervals")
        key = cameras.setdefault(shot.representation_id, len(cameras))
        segments.append({"source": key, "clip_start_sec": a / 1000,
                         "duration_sec": (b - a) / 1000,
                         "master_start_sec": elapsed / 1000,
                         "playback_speed": 1})
        elapsed += b - a
    if not segments:
        raise EngineExecutionError("Empty recipe")
    recipe = {"version": 1, "engine": ENGINE_REVISION, "platform": "youtube",
              "sources": [{"key": key} for key in cameras.values()],
              "segments": segments}
    return inputs, cameras, recipe, elapsed


def _copy_verified(item, destination):
    source = item.path
    if (not source.is_absolute() or source.is_symlink() or not source.is_file()
            or source.stat().st_size != item.size_bytes or _digest(source) != item.sha256):
        raise EngineExecutionError("Input identity mismatch")
    shutil.copyfile(source, destination)
    if destination.stat().st_size != item.size_bytes or _digest(destination) != item.sha256:
        raise EngineExecutionError("Materialized input changed")


class ZuckerEngineAdapter:
    """Use an authorized, pinned external renderer and Engine checkout on Linux.

    No bundled upstream code; no dependency on V1 runtime or Companion service.
    Paths and interpreter are trusted deployment configuration, not user inputs.
    """
    def __init__(self, renderer_root: Path, engine_root: Path, *, timeout: int = 180):
        self.renderer_root = renderer_root
        self.engine_root = engine_root
        self.timeout = timeout

    def execute(self, spec: ExecutionSpec) -> EngineCandidate:
        # The canonical class is supplied by H2.1; no duplicate domain type.
        from montara_core.domain.execution import EngineCandidate
        return EngineCandidate(**self._render(spec))

    def _render(self, spec):
        """Private implementation, independently testable while H2.1 is in flight."""
        if sys.platform != "linux":
            raise EngineExecutionError("Only isolated Linux is supported in H2")
        inputs, cameras, recipe, duration_ms = _plan(spec)
        _checked_checkout(self.renderer_root, RENDERER_REVISION)
        _checked_checkout(self.engine_root, ENGINE_REVISION)
        workspace = spec.workspace
        if (not workspace.is_absolute() or workspace.is_symlink()
                or not workspace.is_dir() or workspace.resolve() != workspace
                or any(workspace.iterdir())):
            raise EngineExecutionError("Attempt requires an empty private workspace")
        # Core provides exclusive workspace; no retry overwrites prior bytes.
        root = workspace / "engine"
        root.mkdir(mode=0o700)
        layout = root / "runner"
        layout.mkdir(mode=0o700)
        for name in ("local_video.py", "video_recipe.py"):
            shutil.copyfile(self.renderer_root / "companion" / name, layout / name)
        (layout / "engine").symlink_to(self.engine_root, target_is_directory=True)
        (layout / "brand").symlink_to(self.renderer_root / "web" / "brand", target_is_directory=True)
        media = root / "inputs"
        media.mkdir(mode=0o700)
        sources = {}
        for representation, key in cameras.items():
            item = inputs[representation]
            if item.path.suffix.lower() not in (".mp4", ".mov"):
                raise EngineExecutionError("H2 camera must be MP4 or MOV")
            dest = media / ("camera-" + str(key) + item.path.suffix.lower())
            _copy_verified(item, dest)
            sources[str(key)] = str(dest)
        master = media / "master.wav"
        _copy_verified(inputs[spec.master_representation_id], master)
        output = root / "candidate.mp4"
        home = root / "home"
        home.mkdir(mode=0o700)
        temp = root / "tmp"
        temp.mkdir(mode=0o700)
        manifest = root / "request.json"
        manifest.write_text(json.dumps({"recipe": recipe, "source_files": sources,
            "master": str(master), "work": str(root / "work"),
            "progress": str(root / "progress.json"), "output": str(output),
            "branding": "none"}), encoding="utf-8")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home),
               "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"),
               "TMPDIR": str(temp), "PYTHONDONTWRITEBYTECODE": "1",
               "LANG": "C.UTF-8", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
        try:
            with (root / "render.log").open("xb") as log:
                process = subprocess.Popen([sys.executable, str(layout / "local_video.py"),
                    str(manifest)], cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True)
                try:
                    code = process.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise EngineExecutionError("Engine timeout")
            if code or output.is_symlink() or not output.is_file() or not output.stat().st_size:
                raise EngineExecutionError("Engine failed; private diagnostic retained")
            probe = json.loads(subprocess.check_output(
                ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)],
                timeout=30))
            video = [s for s in probe["streams"] if s["codec_type"] == "video"]
            audio = [s for s in probe["streams"] if s["codec_type"] == "audio"]
            if (len(video) != 1 or len(audio) != 1 or video[0]["codec_name"] != "h264"
                    or audio[0]["codec_name"] != "aac"
                    or (video[0]["width"], video[0]["height"]) != (1920, 1080)
                    or video[0]["avg_frame_rate"] != "30/1"
                    or abs(float(probe["format"]["duration"]) - duration_ms / 1000) > 0.15):
                raise EngineExecutionError("Candidate profile or duration mismatch")
            return {"path": output, "sha256": _digest(output), "size_bytes": output.stat().st_size,
                    "engine_version": ENGINE_VERSION, "engine_revision": ENGINE_REVISION}
        except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
            raise EngineExecutionError("Engine execution or candidate verification failed") from exc
