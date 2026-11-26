"""Validated configuration, with no implicit environment or file reads."""
from dataclasses import dataclass
from pathlib import Path
import tomllib
from urllib.parse import urlsplit
from montara_core.application.identity import OIDCSettings
from montara_core.domain.pilot import PilotSettings


class ConfigError(ValueError):
    """Invalid or unreadable external configuration."""


@dataclass(frozen=True)
class Config:
    port: int = 8080
    sqlite_path: Path | None = None
    media_root: Path | None = None
    workspace_root: Path | None = None
    artifact_root: Path | None = None
    oidc: OIDCSettings | None = None
    pilot: PilotSettings | None = None


def load_config(path: Path | None = None) -> Config:
    if path is None:
        return Config()
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, ValueError) as exc:
        raise ConfigError("configuration is unreadable or invalid TOML") from exc
    if set(data) - {"http", "storage", "oidc", "pilot"}:
        raise ConfigError("unknown configuration section")
    http = data.get("http", {})
    if not isinstance(http, dict) or set(http) - {"port"}:
        raise ConfigError("invalid http configuration")
    port = http.get("port", 8080)
    if type(port) is not int or not 0 <= port <= 65535:
        raise ConfigError("http.port must be an integer from 0 to 65535")
    storage = data.get("storage")
    sqlite_path = None
    media_root = workspace_root = artifact_root = None
    if storage is not None:
        if not isinstance(storage, dict) or "sqlite_path" not in storage or set(storage) - {"sqlite_path", "media_root", "workspace_root", "artifact_root"}:
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
        if "artifact_root" in storage:
            if media_root is None:
                raise ConfigError("artifact_root requires media and workspace roots")
            artifact_root = _root(storage["artifact_root"])
            validate_artifact_root(sqlite_path, media_root, workspace_root, artifact_root)
    oidc = None
    if "oidc" in data:
        if not all((sqlite_path, media_root, workspace_root, artifact_root)):
            raise ConfigError("OIDC requires configured database and all private roots")
        oidc = _oidc(data["oidc"])
    pilot = None
    if "pilot" in data:
        if oidc is None:
            raise ConfigError("pilot requires OIDC")
        pilot = _pilot(data["pilot"], oidc.issuer)
        if sqlite_path.resolve() != sqlite_path:
            raise ConfigError("pilot database must have a canonical path")
        for source in (pilot.renderer_root, pilot.engine_root):
            if any(source.is_relative_to(root) or root.is_relative_to(source)
                   for root in (media_root, workspace_root, artifact_root)) or sqlite_path.is_relative_to(source):
                raise ConfigError("pilot checkouts must be separate from mutable state")
        if (pilot.renderer_root.is_relative_to(pilot.engine_root)
                or pilot.engine_root.is_relative_to(pilot.renderer_root)):
            raise ConfigError("pilot checkouts must be separate")
    return Config(port=port, sqlite_path=sqlite_path, media_root=media_root,
                  workspace_root=workspace_root, artifact_root=artifact_root, oidc=oidc, pilot=pilot)


def _pilot(data, issuer):
    required = {"allowed_subjects", "renderer_root", "engine_root"}
    if not isinstance(data, dict) or not required <= set(data) or set(data) - required - {"render_timeout_sec"}:
        raise ConfigError("invalid pilot configuration")
    subjects = data["allowed_subjects"]
    if (not isinstance(subjects, list) or not 1 <= len(subjects) <= 4
            or any(not isinstance(s, str) or not 1 <= len(s) <= 255
                   or any(ord(c) < 33 or ord(c) > 126 for c in s) for s in subjects)
            or len(set(subjects)) != len(subjects)):
        raise ConfigError("pilot requires one to four distinct admitted subjects")
    timeout = data.get("render_timeout_sec", 180)
    if type(timeout) is not int or not 30 <= timeout <= 300:
        raise ConfigError("pilot render timeout must be 30–300 seconds")
    return PilotSettings(issuer, tuple(subjects), _root(data["renderer_root"]),
                         _root(data["engine_root"]), timeout)



def _https(value, origin=False):
    if (not isinstance(value, str) or len(value) > 2048 or not value
            or any(ord(c) < 33 or ord(c) > 126 for c in value) or "\\" in value):
        raise ConfigError("invalid OIDC HTTPS URL")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or (origin and (parsed.path or value != "https://" + parsed.netloc))):
            raise ConfigError("invalid OIDC HTTPS URL")
        _ = parsed.port
    except ValueError:
        raise ConfigError("invalid OIDC HTTPS URL") from None
    return value


def _oidc(data):
    keys = {"issuer", "authorization_endpoint", "token_endpoint", "jwks_uri",
            "client_id", "client_secret_file", "public_origin"}
    if not isinstance(data, dict) or set(data) != keys:
        raise ConfigError("OIDC requires explicit issuer, endpoints, client and public origin")
    client = data["client_id"]
    if (not isinstance(client, str) or not client or len(client) > 512
            or any(ord(ch) < 33 or ord(ch) > 126 for ch in client)):
        raise ConfigError("invalid OIDC client ID")
    secret = data["client_secret_file"]
    if not isinstance(secret, str) or not secret or "\x00" in secret or not Path(secret).is_absolute():
        raise ConfigError("OIDC secret requires an absolute file path")
    return OIDCSettings(_https(data["issuer"]), _https(data["authorization_endpoint"]),
                        _https(data["token_endpoint"]), _https(data["jwks_uri"]),
                        client, Path(secret), _https(data["public_origin"], origin=True))



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



def validate_artifact_root(sqlite_path: Path, media_root: Path, workspace_root: Path,
                           artifact_root: Path) -> None:
    artifact_root = _root(str(artifact_root))
    if (sqlite_path.resolve().is_relative_to(artifact_root) or
            any(artifact_root.is_relative_to(p) or p.is_relative_to(artifact_root)
                for p in (media_root, workspace_root))):
        raise ConfigError("artifact root must be separate from database, originals and workspaces")
