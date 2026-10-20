"""Endpoints de progreso del estudiante y dashboard."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .deps import get_service, get_user_id
from ...core.services import LearningService

router = APIRouter(tags=["progress"])


@router.get("/progress")
def dashboard(
    user_id: str = Depends(get_user_id),
    service: LearningService = Depends(get_service),
):
    """Payload único del dashboard: avance por curso/módulo/lección,
    estadísticas, actividad diaria, intentos recientes y próximas acciones."""
    return service.dashboard(user_id)


@router.get("/attempts")
def attempts_history(
    limit: int = Query(default=30, ge=1, le=200),
    user_id: str = Depends(get_user_id),
    service: LearningService = Depends(get_service),
):
    """Historial completo de intentos (ejercicios y exámenes)."""
    return {"attempts": service.progress.list_attempts(user_id, limit=limit)}
