"""Endpoints de exámenes: entrega, envío y reportes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .deps import get_service, get_user_id
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


@router.post("/exams/{exam_id}/attempts")
def submit_exam(
    course_id: str, exam_id: str,
    payload: ExamSubmission,
    user_id: str = Depends(get_user_id),
    service: LearningService = Depends(get_service),
):
    """Califica el examen completo, genera feedback y reporte exportable."""
    return service.submit_exam(
        user_id, course_id, exam_id,
        responses=[r.model_dump() for r in payload.responses],
        started_at=payload.started_at.isoformat() if payload.started_at else None,
        duration_seconds=payload.duration_seconds,
    )


@router.get("/attempts/{attempt_id}/report")
def attempt_report(
    course_id: str, attempt_id: str,
    fmt: str = Query(default="markdown", alias="format", pattern="^(markdown|json)$"),
    user_id: str = Depends(get_user_id),
    service: LearningService = Depends(get_service),
):
    """Reporte del intento (markdown para pegar a tu IA/mentor, o json)."""
    return service.get_attempt_report(user_id, attempt_id, fmt=fmt)
