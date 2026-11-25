"""Validated configuration, with no implicit environment or file reads."""
from dataclasses import dataclass
from pathlib import Path
import tomllib


class ConfigError(ValueError):
    """Invalid or unreadable external configuration."""


@dataclass(frozen=True)
class Config:
    port: int = 8080


def load_config(path: Path | None = None) -> Config:
    if path is None:
        return Config()
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, ValueError) as exc:
        raise ConfigError("configuration is unreadable or invalid TOML") from exc
    if set(data) - {"http"}:
        raise ConfigError("unknown configuration section")
    http = data.get("http", {})
    if not isinstance(http, dict) or set(http) - {"port"}:
        raise ConfigError("invalid http configuration")
    port = http.get("port", 8080)
    if type(port) is not int or not 0 <= port <= 65535:
        raise ConfigError("http.port must be an integer from 0 to 65535")
    return Config(port=port)
