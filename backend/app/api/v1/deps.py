"""Dependencias compartidas de la API."""
from __future__ import annotations

from fastapi import Header

from ...config import settings
from ...core.services import LearningService

_service: LearningService | None = None


def get_service() -> LearningService:
    """Singleton del servicio (stateless: todo estado vive en los stores)."""
    global _service
    if _service is None:
        _service = LearningService()
    return _service


def get_user_id(x_user_id: str | None = Header(default=None)) -> str:
    """Multi-usuario desde el día 1: el frontend manda X-User-Id.

    Hoy: un header simple (uso local, un estudiante).
    Mañana: se reemplaza por JWT/sesión aquí mismo, sin tocar nada más.
    """
    return (x_user_id or settings.DEFAULT_USER_ID).strip() or settings.DEFAULT_USER_ID
