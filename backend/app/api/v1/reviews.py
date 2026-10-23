"""Endpoints de revisión humana (Iteración 2).

Autorización: SOLO usuarios autenticados cuyo `sub` (del JWT verificado)
esté en KIWI_REVIEWER_USERS. La lista por defecto está VACÍA: nadie puede
revisar hasta que se configure explícitamente en el servidor. Nunca se
confía en roles/identidades declarados por el cliente.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .deps import get_current_user, get_service
from ...core.models import ExamReviewPayload
from ...core.services import LearningService

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("/attempts/{attempt_id}")
def review_data(
    attempt_id: str,
    reviewer: str = Depends(get_current_user),
    service: LearningService = Depends(get_service),
):
    """Datos para revisar: intento + respuestas libres pendientes con su
    respuesta modelo y cobertura orientativa."""
    return service.review_data(reviewer, attempt_id)


@router.post("/attempts/{attempt_id}")
def submit_review(
    attempt_id: str,
    payload: ExamReviewPayload,
    reviewer: str = Depends(get_current_user),
    service: LearningService = Depends(get_service),
):
    """Finaliza la revisión: asigna puntos a cada respuesta libre pendiente
    y calcula el resultado definitivo (aprobado/reprobado)."""
    return service.review_attempt(
        reviewer, attempt_id,
        reviews=[r.model_dump() for r in payload.reviews],
        comment=payload.comment,
    )
