"""Fábrica de stores: elige implementación según configuración.

Regla simple y explícita:
- KIWI_DATABASE_URL definida  -> SqlProgressStore (Postgres/Supabase o SQLite)
- KIWI_DATABASE_URL vacía     -> JsonProgressStore (archivos, default local)

El contenido (cursos/lecciones) siempre vive en archivos versionados en git:
es solo-lectura en runtime y así se beneficia de deploys, PRs y revisiones.
"""
from __future__ import annotations

from ..config import settings
from .base import ContentStore, ProgressStore


def make_progress_store() -> ProgressStore:
    if settings.DATABASE_URL:
        from .sql_store import SqlProgressStore

        return SqlProgressStore(settings.DATABASE_URL)
    from .json_store import JsonProgressStore

    return JsonProgressStore()


def make_content_store() -> ContentStore:
    from .json_store import JsonContentStore

    return JsonContentStore()
