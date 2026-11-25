"""Minimal owner and project model; no I/O or provider dependencies."""
from dataclasses import dataclass
from uuid import UUID


class InvalidValue(ValueError):
    """Invalid domain value."""


class UnknownAccount(LookupError):
    """Actor has no provisioned internal account."""


class ProjectNotFound(LookupError):
    """Missing or inaccessible project (same outward error)."""


def validate_id(value: str) -> None:
    if not isinstance(value, str):
        raise InvalidValue("invalid identifier")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise InvalidValue("invalid identifier") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise InvalidValue("invalid identifier")


@dataclass(frozen=True)
class Account:
    id: str

    def __post_init__(self) -> None:
        validate_id(self.id)


@dataclass(frozen=True)
class Project:
    id: str
    owner_id: str
    name: str

    def __post_init__(self) -> None:
        validate_id(self.id)
        validate_id(self.owner_id)
        if (not isinstance(self.name, str) or not self.name
                or len(self.name) > 120 or self.name != self.name.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in self.name)):
            raise InvalidValue("project name must be trimmed, 1–120 characters, without controls")
