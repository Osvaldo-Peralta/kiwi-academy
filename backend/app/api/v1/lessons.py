"""Endpoints de lecciones: contenido, progreso y práctica de ejercicios."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header

from .deps import get_service, get_user_id
from ...core.models import ExerciseAttemptPayload, LessonProgressEvent
from ...core.services import LearningService

router = APIRouter(
    prefix="/courses/{course_id}/modules/{module_id}/lessons",
    tags=["lessons"],
)


@router.get("/{lesson_id}")
def get_lesson(
    course_id: str, module_id: str, lesson_id: str,
    user_id: str = Depends(get_user_id),
    service: LearningService = Depends(get_service),
):
    """Contenido de la lección (teoría, ejemplos, ejercicios SIN respuestas)
    + progreso del usuario."""
    return service.get_lesson_public(user_id, course_id, module_id, lesson_id)


@router.post("/{lesson_id}/progress")
def update_lesson_progress(
    course_id: str, module_id: str, lesson_id: str,
    payload: LessonProgressEvent,
    user_id: str = Depends(get_user_id),
    service: LearningService = Depends(get_service),
):
    """Marca teoría leída / ejemplos vistos."""
    lp = service.mark_lesson_progress(
        user_id, course_id, module_id, lesson_id,
        theory_completed=payload.theory_completed,
        examples_viewed=payload.examples_viewed,
    )
    return {"lesson_progress": lp}


@router.post("/{lesson_id}/exercises/{exercise_id}/attempt")
def attempt_exercise(
    course_id: str, module_id: str, lesson_id: str, exercise_id: str,
    payload: ExerciseAttemptPayload,
    user_id: str = Depends(get_user_id),
    service: LearningService = Depends(get_service),
) -> dict[str, Any]:
    """Evalúa un ejercicio con feedback inmediato (modo práctica)."""
    return service.attempt_lesson_exercise(
        user_id, course_id, module_id, lesson_id, exercise_id, payload.answer
    )
