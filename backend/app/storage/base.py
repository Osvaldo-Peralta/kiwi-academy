"""Interfaces de almacenamiento (puertos).

La lógica de negocio depende SOLO de estas interfaces abstractas.
Hoy las implementa `json_store` (archivos); mañana una implementación
SQL/NoSQL puede reemplazarla sin tocar ni una línea de la API ni del
motor de evaluación. Este es el punto de costura para escalar.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class ContentStore(ABC):
    """Acceso de solo-lectura al contenido versionado (cursos/lecciones/exámenes)."""

    @abstractmethod
    def list_courses(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_course(self, course_id: str) -> Optional[dict[str, Any]]: ...

    @abstractmethod
    def list_modules(self, course_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def get_module(self, course_id: str, module_id: str) -> Optional[dict[str, Any]]: ...

    @abstractmethod
    def get_lesson(self, course_id: str, module_id: str, lesson_id: str) -> Optional[dict[str, Any]]: ...

    @abstractmethod
    def get_exam(self, course_id: str, exam_id: str) -> Optional[dict[str, Any]]: ...

    @abstractmethod
    def all_lessons(self, course_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def all_exams(self, course_id: str) -> list[dict[str, Any]]: ...


class ProgressStore(ABC):
    """Acceso de lectura/escritura a datos del estudiante: progreso, intentos, reportes."""

    @abstractmethod
    def load_progress(self, user_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def save_progress(self, user_id: str, progress: dict[str, Any]) -> None: ...

    @abstractmethod
    def save_attempt(self, user_id: str, attempt: dict[str, Any]) -> None: ...

    @abstractmethod
    def update_attempt(self, user_id: str, attempt: dict[str, Any]) -> bool:
        """Actualiza un intento EXISTENTE propiedad de user_id (p. ej. tras
        revisión humana). Devuelve False si no existe el intento para ese
        usuario (guarda de propiedad: nunca crea ni reasigna)."""
        ...

    @abstractmethod
    def get_attempt(self, user_id: str, attempt_id: str) -> Optional[dict[str, Any]]: ...

    @abstractmethod
    def find_attempt_any_user(self, attempt_id: str) -> tuple[Optional[str], Optional[dict[str, Any]]]:
        """Busca un intento por id sin filtro de usuario (uso exclusivo del
        flujo de revisión humana autorizado). Devuelve (user_id, attempt) o
        (None, None)."""
        ...

    @abstractmethod
    def list_attempts(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]: ...

    @abstractmethod
    def save_report(self, user_id: str, attempt_id: str, name: str, content: str) -> str: ...
