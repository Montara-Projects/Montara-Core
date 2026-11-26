"""Validated configuration, with no implicit environment or file reads."""
from dataclasses import dataclass
from pathlib import Path
import tomllib


class ConfigError(ValueError):
    """Invalid or unreadable external configuration."""


@dataclass(frozen=True)
class Config:
    port: int = 8080
    sqlite_path: Path | None = None
    media_root: Path | None = None
    workspace_root: Path | None = None


def load_config(path: Path | None = None) -> Config:
    if path is None:
        return Config()
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, ValueError) as exc:
        raise ConfigError("configuration is unreadable or invalid TOML") from exc
    if set(data) - {"http", "storage"}:
        raise ConfigError("unknown configuration section")
    http = data.get("http", {})
    if not isinstance(http, dict) or set(http) - {"port"}:
        raise ConfigError("invalid http configuration")
    port = http.get("port", 8080)
    if type(port) is not int or not 0 <= port <= 65535:
        raise ConfigError("http.port must be an integer from 0 to 65535")
    storage = data.get("storage")
    sqlite_path = None
    media_root = workspace_root = None
    if storage is not None:
        if not isinstance(storage, dict) or "sqlite_path" not in storage or set(storage) - {"sqlite_path", "media_root", "workspace_root"}:
            raise ConfigError("storage requires sqlite_path and optional paired media roots")
        raw_path = storage["sqlite_path"]
        if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
            raise ConfigError("storage.sqlite_path must be a nonempty path")
        sqlite_path = Path(raw_path)
        if not sqlite_path.is_absolute() or not sqlite_path.parent.is_dir():
            raise ConfigError("storage.sqlite_path requires an absolute path and existing directory")
        if sqlite_path.exists() and not sqlite_path.is_file():
            raise ConfigError("storage.sqlite_path must be a file")
        if ("media_root" in storage) != ("workspace_root" in storage):
            raise ConfigError("media_root and workspace_root must be configured together")
        if "media_root" in storage:
            media_root = _root(storage["media_root"])
            workspace_root = _root(storage["workspace_root"])
            validate_roots(sqlite_path, media_root, workspace_root)
    return Config(port=port, sqlite_path=sqlite_path, media_root=media_root,
                  workspace_root=workspace_root)


def _root(value) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ConfigError("invalid local media root")
    path = Path(value)
    if not path.is_absolute() or not path.is_dir() or path.resolve() != path:
        raise ConfigError("local media roots must be existing canonical absolute directories")
    return path


def validate_roots(sqlite_path: Path, media_root: Path, workspace_root: Path) -> None:
    media_root = _root(str(media_root))
    workspace_root = _root(str(workspace_root))
    if (media_root.is_relative_to(workspace_root) or workspace_root.is_relative_to(media_root)
            or sqlite_path.resolve().is_relative_to(media_root)
            or sqlite_path.resolve().is_relative_to(workspace_root)):
        raise ConfigError("database, media and workspace roots must be separate")

