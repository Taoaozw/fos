"""Enumerable exit codes and structured error types for FlowOS."""

from __future__ import annotations

from enum import IntEnum
from typing import Any


class ExitCode(IntEnum):
    """Deterministic CLI exit codes per PRD & fops conventions."""

    OK = 0
    ERROR = 1
    USAGE = 2
    NOT_FOUND = 3
    GATE_FAILED = 4
    CANCELED = 130


class FosError(Exception):
    """Base structured error with deterministic exit code, machine code, and recovery hint."""

    exit_code: ExitCode = ExitCode.ERROR
    error_code: str = "error"

    def __init__(
        self,
        message: str,
        *,
        exit_code: ExitCode | None = None,
        error_code: str | None = None,
        hint: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if exit_code is not None:
            self.exit_code = exit_code
        if error_code is not None:
            self.error_code = error_code
        self.hint = hint
        self.details = details or {}


class UsageError(FosError):
    exit_code = ExitCode.USAGE
    error_code = "invalid_usage"


class NotFoundError(FosError):
    exit_code = ExitCode.NOT_FOUND
    error_code = "not_found"


class GateFailedError(FosError):
    exit_code = ExitCode.GATE_FAILED
    error_code = "gate_failed"


class AuthError(FosError):
    exit_code = ExitCode.ERROR
    error_code = "auth_error"


class OfflineError(FosError):
    exit_code = ExitCode.ERROR
    error_code = "offline"
