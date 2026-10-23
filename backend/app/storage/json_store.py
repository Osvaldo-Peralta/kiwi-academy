"""Implementación del almacenamiento con archivos JSON.

- Escrituras atómicas (tmp + os.replace) para no corromper datos si el
  proceso muere a mitad de escritura.
- Lock por ruta para concurrencia básica en local (varios workers/hilos).
- Los intentos se guardan append-only (un archivo por intento): historial
  auditable completo, fácil de migrar a DB o analizar con pandas después.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

from ..config import settings
from ..core.errors import ContentError
from .base import ContentStore, ProgressStore

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(path, threading.Lock())


CONTENT_EXTENSIONS = (".yaml", ".yml", ".json")


def _read_data(path: Path) -> Optional[dict[str, Any]]:
    """Lee un archivo de datos (YAML o JSON según extensión)."""
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            if path.suffix in (".yaml", ".yml"):
                import yaml
                return yaml.safe_load(f)
            return json.load(f)
    except Exception as exc:  # YAML/JSON malformado -> error claro, no 500 críptico
        raise ContentError(f"Archivo de contenido inválido en {path}: {exc}") from exc


def _resolve(stem: Path) -> Path:
    """Dado un path sin extensión, devuelve el primero que exista (.yaml/.yml/.json)."""
    for ext in CONTENT_EXTENSIONS:
        candidate = stem.with_suffix(ext)
        if candidate.exists():
            return candidate
    return stem.with_suffix(".json")  # no existe; _read_data devolverá None


# alias de compatibilidad
_read_json = _read_data


def _write_json(path: Path, data: Any) -> None:
    with _lock_for(str(path)):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)


class JsonContentStore(ContentStore):
    """Lee el contenido desde content/courses/<course_id>/...

    Estructura esperada:
        course.json
        modules/<module_id>/module.json
        modules/<module_id>/lessons/<lesson_id>.json
        exams/<exam_id>.json
    """

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root or settings.CONTENT_DIR) / "courses"

    def _course_dir(self, course_id: str) -> Path:
        return self.root / course_id

    def list_courses(self) -> list[dict[str, Any]]:
        out = []
        if not self.root.exists():
            return out
        for entry in sorted(self.root.iterdir()):
            meta = _read_data(_resolve(entry / "course"))
            if meta:
                out.append(meta)
        return out

    def get_course(self, course_id: str) -> Optional[dict[str, Any]]:
        return _read_data(_resolve(self._course_dir(course_id) / "course"))

    def list_modules(self, course_id: str) -> list[dict[str, Any]]:
        course = self.get_course(course_id)
        if not course:
            return []
        out = []
        for mid in course.get("modules", []):
            mod = _read_data(_resolve(self._course_dir(course_id) / "modules" / mid / "module"))
            if mod:
                out.append(mod)
        return out

    def get_module(self, course_id: str, module_id: str) -> Optional[dict[str, Any]]:
        return _read_data(_resolve(self._course_dir(course_id) / "modules" / module_id / "module"))

    def get_lesson(self, course_id: str, module_id: str, lesson_id: str) -> Optional[dict[str, Any]]:
        stem = self._course_dir(course_id) / "modules" / module_id / "lessons" / lesson_id
        return _read_data(_resolve(stem))

    def get_exam(self, course_id: str, exam_id: str) -> Optional[dict[str, Any]]:
        return _read_data(_resolve(self._course_dir(course_id) / "exams" / exam_id))

    def all_lessons(self, course_id: str) -> list[dict[str, Any]]:
        out = []
        for mod in self.list_modules(course_id):
            for lid in mod.get("lessons", []):
                lesson = self.get_lesson(course_id, mod["id"], lid)
                if lesson:
                    out.append(lesson)
        return out

    def all_exams(self, course_id: str) -> list[dict[str, Any]]:
        out = []
        exams_dir = self._course_dir(course_id) / "exams"
        if exams_dir.exists():
            files = set()
            for ext in CONTENT_EXTENSIONS:
                files.update(exams_dir.glob(f"*{ext}"))
            for f in sorted(files):
                data = _read_data(f)
                if data:
                    out.append(data)
        return out


class JsonProgressStore(ProgressStore):
    """Datos por usuario en data/users/<user_id>/."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root or settings.DATA_DIR) / "users"

    # -- progreso -----------------------------------------------------------

    def _progress_path(self, user_id: str) -> Path:
        base = self.root / user_id
        base.mkdir(parents=True, exist_ok=True)
        return base / "progress.json"

    def load_progress(self, user_id: str) -> dict[str, Any]:
        data = _read_json(self._progress_path(user_id))
        return data or {"user_id": user_id, "lessons": {}, "exams": {}, "activity": {}}

    def save_progress(self, user_id: str, progress: dict[str, Any]) -> None:
        _write_json(self._progress_path(user_id), progress)

    # -- intentos (append-only) ---------------------------------------------

    def _attempts_dir(self, user_id: str) -> Path:
        d = self.root / user_id / "attempts"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_attempt(self, user_id: str, attempt: dict[str, Any]) -> None:
        _write_json(self._attempts_dir(user_id) / f"{attempt['id']}.json", attempt)

    def update_attempt(self, user_id: str, attempt: dict[str, Any]) -> bool:
        path = self._attempts_dir(user_id) / f"{attempt['id']}.json"
        if not path.exists():
            return False  # guarda de propiedad: solo actualiza lo existente
        _write_json(path, attempt)
        return True

    def get_attempt(self, user_id: str, attempt_id: str) -> Optional[dict[str, Any]]:
        safe = "".join(c for c in attempt_id if c.isalnum() or c in "-_")
        return _read_json(self._attempts_dir(user_id) / f"{safe}.json")

    def find_attempt_any_user(self, attempt_id: str) -> tuple[Optional[str], Optional[dict[str, Any]]]:
        safe = "".join(c for c in attempt_id if c.isalnum() or c in "-_")
        if not self.root.exists():
            return None, None
        for user_dir in sorted(self.root.iterdir()):
            if not user_dir.is_dir():
                continue
            data = _read_json(user_dir / "attempts" / f"{safe}.json")
            if data:
                return user_dir.name, data
        return None, None

    def list_attempts(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        files = sorted(self._attempts_dir(user_id).glob("*.json"), reverse=True)[:limit]
        out = []
        for f in files:
            data = _read_json(f)
            if data:
                out.append(data)
        return out

    # -- reportes exportados ----------------------------------------------------------------

    def save_report(self, user_id: str, attempt_id: str, name: str, content: str) -> str:
        d = self.root / user_id / "reports"
        d.mkdir(parents=True, exist_ok=True)
        safe_attempt = "".join(c for c in attempt_id if c.isalnum() or c in "-_")
        safe_name = "".join(c for c in name if c.isalnum() or c in "-_.")
        path = d / f"{safe_attempt}.{safe_name}"
        with _lock_for(str(path)):
            path.write_text(content, encoding="utf-8")
        return str(path)
