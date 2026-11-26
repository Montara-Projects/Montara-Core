"""H2 private export adapter. Core retains authorization, fencing and publication."""
from __future__ import annotations

from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import wave
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
    if (not segments or len(segments) > 1000 or len(cameras) > 200
            or elapsed > 21_600_000 or any(s.end_ms > 86_400_000 for s in spec.shots)):
        raise EngineExecutionError("Recipe exceeds initial renderer limits")
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

def _probe(path, *args):
    result = subprocess.run(["ffprobe", "-v", "error", *args, "-of", "json", str(path)],
                            capture_output=True, text=True, timeout=180, check=True)
    if result.stderr.strip():
        raise EngineExecutionError("Source probe reported decode errors")
    return json.loads(result.stdout)


def _check_master(path, duration_ms):
    """PCM WAV: require actual sample frames, not a padded container duration."""
    info = _probe(path, "-show_streams", "-show_format")
    streams = info["streams"]
    if (len(streams) != 1 or streams[0]["codec_type"] != "audio"
            or not streams[0]["codec_name"].startswith("pcm_")
            or info["format"]["format_name"] != "wav"):
        raise EngineExecutionError("H2 master requires one PCM WAV audio stream")
    with wave.open(str(path), "rb") as master:
        rate = master.getframerate()
        required = (duration_ms * rate + 999) // 1000
        stride = master.getnchannels() * master.getsampwidth()
        if master.getnframes() < required:
            raise EngineExecutionError("Master does not cover recipe samples")
        remaining = required
        while remaining:
            n = min(remaining, 65536)
            data = master.readframes(n)
            if len(data) != n * stride:
                raise EngineExecutionError("Master PCM is truncated")
            remaining -= n


def _check_camera(path, end_ms):
    """H2 flat CFR30: verify decoded PTS coverage, no 150ms allowance."""
    info = _probe(path, "-show_streams", "-show_format")
    videos = [s for s in info["streams"] if s["codec_type"] == "video"]
    if len(videos) != 1:
        raise EngineExecutionError("H2 camera requires one video stream")
    stream = videos[0]
    if (stream["codec_name"] != "h264" or stream["avg_frame_rate"] != "30/1"
            or stream["r_frame_rate"] != "30/1"
            or Fraction(stream.get("start_time", "0")) != 0
            or stream["width"] < 320 or stream["height"] < 240
            or not {"mov", "mp4"}.intersection(info["format"]["format_name"].split(","))
            or Fraction(195, 100) <= Fraction(stream["width"], stream["height"]) <= Fraction(205, 100)
            or any(marker in json.dumps(info).lower()
                   for marker in ("equirect", "spherical", "360 video", "insta360"))
            or any(s.get("rotation", 0) or "spherical" in s.get("side_data_type", "").lower()
                   for s in stream.get("side_data_list", []))
            or stream.get("tags", {}).get("rotate", "0") != "0"):
        raise EngineExecutionError("Unsupported H2 camera (flat H264 CFR30 required)")
    required = end_ms * 30 // 1000
    timebase = Fraction(stream["time_base"])
    if Fraction(stream["duration_ts"]) * timebase < Fraction(end_ms, 1000):
        raise EngineExecutionError("Camera does not cover recipe interval")
    frames = _probe(path, "-select_streams", "v:0", "-show_frames",
                    "-show_entries", "frame=best_effort_timestamp")["frames"]
    if len(frames) < required:
        raise EngineExecutionError("Camera decoded frames do not cover recipe interval")
    previous = None
    for index, frame in enumerate(frames[:required]):
        timestamp = Fraction(frame["best_effort_timestamp"]) * timebase
        if ((previous is not None and timestamp <= previous)
                or abs(timestamp - Fraction(index, 30)) > min(timebase / 2, Fraction(1, 120))):
            raise EngineExecutionError("Camera timeline is not contiguous CFR30")
        previous = timestamp


def _verify_manifest(root, recipe, sources, master):
    """Treat inherited omissions/proxy fallback as a failed candidate."""
    project = root / "work" / "edit.zuckervid"
    report = json.loads((project / "artifacts" / "export_manifest.json").read_text())
    effective = Path(report["effective_plan_path"])
    if not effective.resolve().is_relative_to(project.resolve()):
        raise EngineExecutionError("Engine manifest escaped workspace")
    plan = json.loads(effective.read_text())
    actual = plan["segments"]
    if (report.get("optional_omissions") or report["audio_source"] != "master"
            or plan["inputs"]["master"]["path"] != str(master)
            or len(actual) != len(recipe["segments"])):
        raise EngineExecutionError("Engine changed required recipe inputs")
    for wanted, got in zip(recipe["segments"], actual):
        if (got.get("placeholder") or got.get("source_path") != sources[str(wanted["source"])]
                or got.get("clip_path") != sources[str(wanted["source"])]
                or any(abs(float(got.get(key, -1)) - wanted[key]) > 1e-6
                       for key in ("clip_start_sec", "duration_sec", "master_start_sec", "playback_speed"))):
            raise EngineExecutionError("Engine changed required shot")
    stats = report["performance"]["segments"]
    if (len(stats) != len(actual)
            or any(s["rendered_from"] not in ("original", "stream_copy") or s["cached"] for s in stats)
            or any("proxy fallback" in w.lower() for w in report.get("warnings", []))):
        raise EngineExecutionError("Engine used unapproved fallback or cache")


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
        try:
            return self._render_checked(spec)
        except EngineExecutionError:
            raise
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, EOFError, wave.Error) as exc:
            raise EngineExecutionError("Engine input, execution or candidate verification failed") from exc

    def _render_checked(self, spec):
        """Execute the canonical staged attempt, without publishing it."""
        if sys.platform != "linux":
            raise EngineExecutionError("Only isolated Linux is supported in H2")
        inputs, cameras, recipe, duration_ms = _plan(spec)
        _checked_checkout(self.renderer_root, RENDERER_REVISION)
        _checked_checkout(self.engine_root, ENGINE_REVISION)
        workspace = spec.workspace
        if (not workspace.is_absolute() or workspace.is_symlink()
                or not workspace.is_dir() or workspace.resolve() != workspace):
            raise EngineExecutionError("Attempt requires a private workspace")
        paths = {item.path for item in inputs.values()}
        if (len(paths) != len(inputs) or set(workspace.iterdir()) != paths
                or any(p.parent != workspace or p.is_symlink() or not p.is_file() for p in paths)):
            raise EngineExecutionError("Attempt workspace must contain only staged Core inputs")
        # Core provides exclusive workspace; no retry overwrites prior bytes.
        root = workspace / "engine"
        try:
            root.mkdir(mode=0o700)
        except FileExistsError:
            raise EngineExecutionError("Attempt engine workspace already reserved") from None
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
        master_input = inputs[spec.master_representation_id]
        if master_input.path.suffix.lower() != ".wav":
            raise EngineExecutionError("H2 master must be PCM WAV")
        _copy_verified(master_input, master)
        _check_master(master, duration_ms)
        for representation, key in cameras.items():
            required_end = max(s.end_ms for s in spec.shots if s.representation_id == representation)
            _check_camera(Path(sources[str(key)]), required_end)
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
        # Refuse probing one toolchain and rendering with another: legacy
        # resolution also searches fixed locations before PATH.
        actual_tools = json.loads(subprocess.check_output(
            [sys.executable, "-I", "-B", "-c",
             "import json,sys; sys.path.insert(0,sys.argv[1]); "
             "from core.ffmpeg import tool_status; print(json.dumps(tool_status()))",
             str(self.engine_root)], cwd=root, env=env, timeout=15))
        for name in ("ffmpeg", "ffprobe"):
            selected = shutil.which(name, path=env["PATH"])
            if (not selected or not actual_tools.get(name + "_path")
                    or Path(selected).resolve() != Path(actual_tools[name + "_path"]).resolve()):
                raise EngineExecutionError("Engine and adapter media tools differ")
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
                ["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", str(output)],
                timeout=60))
            video = [s for s in probe["streams"] if s["codec_type"] == "video"]
            audio = [s for s in probe["streams"] if s["codec_type"] == "audio"]
            if (len(video) != 1 or len(audio) != 1 or video[0]["codec_name"] != "h264"
                    or audio[0]["codec_name"] != "aac"
                    or (video[0]["width"], video[0]["height"]) != (1920, 1080)
                    or video[0]["avg_frame_rate"] != "30/1"
                    or int(video[0]["nb_read_frames"]) != duration_ms * 30 // 1000
                    or abs(Fraction(audio[0]["duration_ts"]) * Fraction(audio[0]["time_base"])
                           - Fraction(duration_ms, 1000)) > Fraction(1, 30)
                    or abs(float(probe["format"]["duration"]) - duration_ms / 1000) > 1 / 30):
                raise EngineExecutionError("Candidate profile or duration mismatch")
            _verify_manifest(root, recipe, sources, master)
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(output),
                            "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
                           check=True, capture_output=True, timeout=180)
            for item in inputs.values():
                if item.path.stat().st_size != item.size_bytes or _digest(item.path) != item.sha256:
                    raise EngineExecutionError("Staged Core input changed")
            return {"path": output, "sha256": _digest(output), "size_bytes": output.stat().st_size,
                    "engine_version": ENGINE_VERSION, "engine_revision": ENGINE_REVISION}
        except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
            raise EngineExecutionError("Engine execution or candidate verification failed") from exc
