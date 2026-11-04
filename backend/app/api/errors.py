"""One error envelope for every failure: {"error": {"code", "message", "fields"?}}.

Service exceptions map to HTTP statuses here; unexpected exceptions become a
generic 500 that never leaks internals (the details go to the log only).
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services.corrections import CorrectionParseError
from app.services.errors import ServiceError

log = logging.getLogger(__name__)

_STATUS_BY_CODE = {
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "INVALID_SORT": 422,
    "VALIDATION_ERROR": 422,
    "EMBEDDING_UNAVAILABLE": 503,
    "INFRA_ERROR": 503,
}
_CODE_BY_HTTP_STATUS = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "TOO_LARGE",
    422: "VALIDATION_ERROR",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, fields: dict | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.fields = status, code, message, fields


def envelope(status: int, code: str, message: str, fields: dict | None = None,
             headers: dict | None = None) -> JSONResponse:
    body: dict = {"code": code, "message": message}
    if fields:
        body["fields"] = fields
    return JSONResponse({"error": body}, status_code=status, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, exc: ApiError):
        return envelope(exc.status, exc.code, exc.message, exc.fields)

    @app.exception_handler(CorrectionParseError)
    async def _correction(_: Request, exc: CorrectionParseError):
        return envelope(422, "VALIDATION_ERROR", "Some values could not be parsed", exc.errors)

    @app.exception_handler(ServiceError)
    async def _service(_: Request, exc: ServiceError):
        return envelope(_STATUS_BY_CODE.get(exc.code, 400), exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        fields = {".".join(str(p) for p in e["loc"][1:]) or "body": e["msg"] for e in exc.errors()}
        return envelope(422, "VALIDATION_ERROR", "Request validation failed", fields)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = _CODE_BY_HTTP_STATUS.get(exc.status_code, "HTTP_ERROR")
        return envelope(exc.status_code, code, str(exc.detail), headers=getattr(exc, "headers", None))

    @app.exception_handler(OperationalError)
    @app.exception_handler(InterfaceError)
    async def _db_down(_: Request, exc: DBAPIError):
        log.error("Database unavailable: %s", type(exc).__name__)
        return envelope(503, "DB_UNAVAILABLE", "Database temporarily unavailable")

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        log.exception("Unhandled error")
        return envelope(500, "INTERNAL_ERROR", "Internal server error")
