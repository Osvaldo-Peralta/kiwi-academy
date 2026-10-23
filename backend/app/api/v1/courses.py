"""Endpoints de catálogo: cursos y módulos."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .deps import get_service
from ...core.services import LearningService

router = APIRouter(prefix="/courses", tags=["courses"])


@router.get("")
def list_courses(service: LearningService = Depends(get_service)):
    """Lista de cursos disponibles (hoy SOC N1; mañana N2, otros)."""
    return {
        "courses": [
            {
                "id": c.id,
                "title": c.title,
                "description": c.description,
                "level": c.level,
                "language": c.language,
                "estimated_weeks": c.estimated_weeks,
                "tags": c.tags,
                "modules": len(c.modules),
            }
            for c in service.content.list_courses()
        ]
    }


@router.get("/{course_id}")
def get_course(course_id: str, service: LearningService = Depends(get_service)):
    """Curso + módulos con su metadata (sin contenido pesado de lecciones)."""
    course = service.content.get_course(course_id)
    modules = [
        {
            "id": m.id,
            "title": m.title,
            "phase": m.phase,
            "subtitle": m.subtitle,
            "duration": m.duration,
            "outcome": m.outcome,
            "lessons": m.lessons,
            "exam": m.exam,
            "resources": [r.model_dump(exclude_none=True) for r in m.resources],
        }
        for m in service.content.list_modules(course_id)
    ]
    return {"course": course.model_dump(), "modules": modules}


@router.get("/{course_id}/catalog")
def get_catalog(course_id: str, service: LearningService = Depends(get_service)):
    """Catálogo PÚBLICO del curso: metadata de módulos/lecciones/exámenes.

    Pensado para invitados (modo lectura sin sesión): títulos, tipos, estado
    (published/draft), duraciones y conteo de ejercicios. NO incluye contenido
    de lecciones, preguntas de examen ni datos de usuario.
    """
    return service.course_catalog(course_id)
