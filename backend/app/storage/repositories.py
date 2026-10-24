"""Repositorios: envuelven los stores con validación Pydantic y caché.

- ContentRepository: parsea y valida el contenido (falla rápido si un JSON
  de contenido está mal formado, con mensaje claro), y cachea por mtime
  para no re-leer disco en cada request.
- ProgressRepository: acceso a progreso/intentos del usuario con IDs y
  fechas gestionados aquí (reloj en un solo lugar).
"""
from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..config import settings
from ..core.errors import NotFoundError
from ..core.models import Course, Exam, Lesson, Module
from .base import ContentStore, ProgressStore
from .factory import make_content_store, make_progress_store


class ContentRepository:
    def __init__(self, store: Optional[ContentStore] = None):
        self.store = store or make_content_store()
        self._cache: dict[str, tuple[float, Any]] = {}

    def _cache_get(self, key: str) -> Optional[Any]:
        entry = self._cache.get(key)
        if not entry:
            return None
        cached_at, value = entry
        # TTL corta (5s): suficiente para desarrollo sin reiniciar el server
        if time.time() - cached_at > 5:
            self._cache.pop(key, None)
            return None
        return value

    def _cache_set(self, key: str, value: Any) -> None:
        self._cache[key] = (time.time(), value)

    # -- cursos ---------------------------------------------------------------

    def list_courses(self) -> list[Course]:
        cached = self._cache_get("courses")
        if cached is not None:
            return cached
        courses = []
        for raw in self.store.list_courses():
            courses.append(Course.model_validate(raw))
        self._cache_set("courses", courses)
        return courses

    def get_course(self, course_id: str) -> Course:
        raw = self.store.get_course(course_id)
        if not raw:
            raise NotFoundError(f"Curso no encontrado: {course_id}")
        return Course.model_validate(raw)

    # -- módulos -------------------------------------------------------------------

    def list_modules(self, course_id: str) -> list[Module]:
        key = f"modules:{course_id}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        mods = [Module.model_validate(m) for m in self.store.list_modules(course_id)]
        self._cache_set(key, mods)
        return mods

    def get_module(self, course_id: str, module_id: str) -> Module:
        raw = self.store.get_module(course_id, module_id)
        if not raw:
            raise NotFoundError(f"Módulo no encontrado: {course_id}/{module_id}")
        return Module.model_validate(raw)

    # -- lecciones ---------------------------------------------------------------

    def get_lesson(self, course_id: str, module_id: str, lesson_id: str) -> Lesson:
        raw = self.store.get_lesson(course_id, module_id, lesson_id)
        if not raw:
            raise NotFoundError(
                f"Lección no encontrada: {course_id}/{module_id}/{lesson_id}"
            )
        return Lesson.model_validate(raw)

    def all_lessons(self, course_id: str) -> list[Lesson]:
        key = f"lessons:{course_id}"
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        lessons = [Lesson.model_validate(l) for l in self.store.all_lessons(course_id)]
        self._cache_set(key, lessons)
        return lessons

    # -- exámenes -----------------------------------------------------------------

    def get_exam(self, course_id: str, exam_id: str) -> Exam:
        raw = self.store.get_exam(course_id, exam_id)
        if not raw:
            raise NotFoundError(f"Examen no encontrado: {course_id}/{exam_id}")
        return Exam.model_validate(raw)

    def all_exams(self, course_id: str) -> list[Exam]:
        return [Exam.model_validate(e) for e in self.store.all_exams(course_id)]


class ProgressRepository:
    def __init__(self, store: Optional[ProgressStore] = None):
        self.store = store or make_progress_store()

    def get_progress(self, user_id: str) -> dict[str, Any]:
        return self.store.load_progress(user_id)

    def save_progress(self, user_id: str, progress: dict[str, Any]) -> None:
        progress["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.store.save_progress(user_id, progress)

    # -- Iteración 3: atomicidad -------------------------------------------------

    def transaction(self, user_id: str):
        """Contexto transaccional del store (ver ProgressStore.transaction)."""
        return self.store.transaction(user_id)

    def mutate_progress(self, user_id: str, fn):
        """Read-modify-write atómico; sella updated_at como save_progress."""
        from datetime import datetime, timezone

        def wrapped(prog):
            out = fn(prog)
            prog["updated_at"] = datetime.now(timezone.utc).isoformat()
            return out

        return self.store.mutate_progress(user_id, wrapped)

    def new_attempt_id(self) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        digest = hashlib.sha1(os.urandom(8)).hexdigest()[:6]
        return f"att_{ts}_{digest}"

    def save_attempt(self, user_id: str, attempt: dict[str, Any]) -> None:
        self.store.save_attempt(user_id, attempt)

    def update_attempt(self, user_id: str, attempt: dict[str, Any]) -> bool:
        return self.store.update_attempt(user_id, attempt)

    def update_attempt_if_status(
        self, user_id: str, attempt_id: str,
        expected_status: str, attempt: dict[str, Any],
    ) -> bool:
        return self.store.update_attempt_if_status(
            user_id, attempt_id, expected_status, attempt)

    def get_attempt(self, user_id: str, attempt_id: str) -> dict[str, Any]:
        data = self.store.get_attempt(user_id, attempt_id)
        if not data:
            raise NotFoundError(f"Intento no encontrado: {attempt_id}")
        return data

    def find_attempt_any_user(self, attempt_id: str) -> tuple[Optional[str], Optional[dict[str, Any]]]:
        return self.store.find_attempt_any_user(attempt_id)

    def list_attempts(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_attempts(user_id, limit=limit)

    def save_report(self, user_id: str, attempt_id: str, name: str, content: str) -> str:
        return self.store.save_report(user_id, attempt_id, name, content)
