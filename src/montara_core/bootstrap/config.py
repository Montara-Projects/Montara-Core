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
    if storage is not None:
        if not isinstance(storage, dict) or set(storage) != {"sqlite_path"}:
            raise ConfigError("storage requires exactly sqlite_path")
        raw_path = storage["sqlite_path"]
        if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
            raise ConfigError("storage.sqlite_path must be a nonempty path")
        sqlite_path = Path(raw_path)
        if not sqlite_path.is_absolute() or not sqlite_path.parent.is_dir():
            raise ConfigError("storage.sqlite_path requires an absolute path and existing directory")
        if sqlite_path.exists() and not sqlite_path.is_file():
            raise ConfigError("storage.sqlite_path must be a file")
    return Config(port=port, sqlite_path=sqlite_path)
