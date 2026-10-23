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


class AuthError(AppError):
    """Credenciales ausentes/inválidas/expiradas. El subcódigo viaja en
    `details['reason']`: missing | malformed | invalid | expired |
    invalid_subject | invalid_subject_namespace | not_available."""
    status_code = 401
    code = "auth_error"


class AuthNotConfiguredError(AppError):
    """El servidor exige auth (modo supabase) pero no tiene credenciales de
    verificación configuradas. Fail-closed: 503, nunca identidad implícita."""
    status_code = 503
    code = "auth_not_configured"


class PermissionDeniedError(AppError):
    """Identidad válida pero sin permiso para el recurso/acción (p. ej. revisión
    humana sin estar en la lista de revisores). 403."""
    status_code = 403
    code = "permission_denied"


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError):  # noqa: ARG001
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {"code": exc.code, "message": exc.message, **exc.details},
            },
        )
