"""Engine executes a private specification, never owns publication or state."""
from typing import Protocol

from montara_core.domain.execution import EngineCandidate, ExecutionSpec


class EngineAdapter(Protocol):
    def execute(self, spec: ExecutionSpec) -> EngineCandidate: ...
