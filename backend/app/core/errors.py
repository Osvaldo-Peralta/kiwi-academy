"""Errores de dominio y su traducción a respuestas HTTP."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Error de negocio con código estable para que el frontend lo entienda."""

    status_code = 400
    code = "app_error"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ContentError(AppError):
    status_code = 500
    code = "content_error"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class ExamStateError(AppError):
    status_code = 409
    code = "exam_state_error"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError):  # noqa: ARG001
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {"code": exc.code, "message": exc.message, **exc.details},
            },
        )
