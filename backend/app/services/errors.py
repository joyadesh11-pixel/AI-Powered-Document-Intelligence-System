"""Service-layer exceptions. The API maps each to an HTTP status and error code."""
from __future__ import annotations


class ServiceError(Exception):
    code = "SERVICE_ERROR"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class NotFound(ServiceError):
    code = "NOT_FOUND"


class Conflict(ServiceError):
    code = "CONFLICT"


class IngestError(ServiceError):
    """A file failed the upload checks (size, extension, magic bytes)."""


class InfraError(ServiceError):
    """A transient infrastructure failure (DB connection, broker). Safe to retry."""
    code = "INFRA_ERROR"
