"""Domain errors with stable CLI exit codes (ecosystem convention)."""

from __future__ import annotations

from typing import Any


class ProofHarnessError(Exception):
    """Expected, actionable error; never shown as a traceback without --debug."""

    exit_code = 2
    error_type = "domain_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationError(ProofHarnessError):
    error_type = "validation_error"


class ConflictError(ProofHarnessError):
    error_type = "conflict"


class DependencyError(ProofHarnessError):
    """A required external dependency (Git checkout, Grafos index) is missing or stale."""

    exit_code = 3
    error_type = "dependency_error"
