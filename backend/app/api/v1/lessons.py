"""Endpoints de lecciones: contenido, progreso y práctica de ejercicios.

Iteración 1 (auth):
- GET de lección: público SOLO si está publicada (draft -> 404, AUTH-04).
  Si hay token válido, se adjunta el progreso del usuario; sin token, el
  progreso va en ceros (invitado en modo lectura).
- POST (progreso, intentos de ejercicio): requieren identidad autenticada.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends

from .deps import get_current_user, get_optional_user, get_service
from ...core.models import ExerciseAttemptPayload, LessonProgressEvent
from ...core.services import LearningService

router = APIRouter(
    prefix="/courses/{course_id}/modules/{module_id}/lessons",
    tags=["lessons"],
)


@router.get("/{lesson_id}")
def get_lesson(
    course_id: str, module_id: str, lesson_id: str,
    user_id: Optional[str] = Depends(get_optional_user),
    service: LearningService = Depends(get_service),
):
    """Contenido de la lección publicada (teoría, ejemplos, ejercicios SIN
    respuestas). Las lecciones draft devuelven 404 para todo el mundo."""
    return service.get_lesson_public(user_id, course_id, module_id, lesson_id)


@router.post("/{lesson_id}/progress")
def update_lesson_progress(
    course_id: str, module_id: str, lesson_id: str,
    payload: LessonProgressEvent,
    user_id: str = Depends(get_current_user),
    service: LearningService = Depends(get_service),
):
    """Marca teoría leída / ejemplos vistos (requiere sesión)."""
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
    user_id: str = Depends(get_current_user),
    service: LearningService = Depends(get_service),
) -> dict[str, Any]:
    """Evalúa un ejercicio con feedback inmediato (requiere sesión)."""
    return service.attempt_lesson_exercise(
        user_id, course_id, module_id, lesson_id, exercise_id, payload.answer
    )
