"""Endpoints de exámenes: sesión, entrega y reportes.

Iteración 2:
- POST /exams/{id}/sessions: el SERVIDOR emite la sesión y mide el tiempo
  (el temporizador del cliente es solo informativo).
- El envío exige session_id y valida ids/duplicados/orden/estructura.
- Los envíos con respuestas libres quedan pendientes de revisión humana.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .deps import get_current_user, get_service
from ...core.models import ExamSubmission
from ...core.services import LearningService

router = APIRouter(prefix="/courses/{course_id}", tags=["exams"])


@router.get("/exams/{exam_id}")
def get_exam(
    course_id: str, exam_id: str,
    service: LearningService = Depends(get_service),
):
    """Examen listo para presentar: preguntas SIN respuestas ni explicaciones."""
    return service.get_exam_public(course_id, exam_id)


@router.post("/exams/{exam_id}/sessions")
def start_exam_session(
    course_id: str, exam_id: str,
    user_id: str = Depends(get_current_user),
    service: LearningService = Depends(get_service),
):
    """Inicia una sesión de examen (requiere identidad). Devuelve session_id,
    hora del servidor y deadline Unix; el límite se hace cumplir en el
    servidor al enviar."""
    return service.start_exam_session(user_id, course_id, exam_id)


@router.post("/exams/{exam_id}/attempts")
def submit_exam(
    course_id: str, exam_id: str,
    payload: ExamSubmission,
    user_id: str = Depends(get_current_user),
    service: LearningService = Depends(get_service),
):
    """Califica el examen: validación estricta + puntaje provisional +
    pendientes de revisión humana cuando hay respuestas libres."""
    return service.submit_exam(
        user_id, course_id, exam_id,
        session_id=payload.session_id,
        responses=[r.model_dump() for r in payload.responses],
    )


@router.get("/attempts/{attempt_id}/report")
def attempt_report(
    course_id: str, attempt_id: str,
    fmt: str = Query(default="markdown", alias="format", pattern="^(markdown|json)$"),
    user_id: str = Depends(get_current_user),
    service: LearningService = Depends(get_service),
):
    """Reporte del intento (markdown para pegar a tu IA/mentor, o json)."""
    return service.get_attempt_report(user_id, attempt_id, fmt=fmt)
