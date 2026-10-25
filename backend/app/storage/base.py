"""Interfaces de almacenamiento (puertos).

La lógica de negocio depende SOLO de estas interfaces abstractas.
Hoy las implementa `json_store` (archivos); mañana una implementación
SQL/NoSQL puede reemplazarla sin tocar ni una línea de la API ni del
motor de evaluación. Este es el punto de costura para escalar.

Iteración 3: el puerto incluye `transaction()` (agrupación atómica de
operaciones relacionadas) y `mutate_progress()` (read-modify-write atómico
del progreso, política contra actualizaciones perdidas).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional


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
    def update_attempt_if_status(
        self, user_id: str, attempt_id: str,
        expected_status: str, attempt: dict[str, Any],
    ) -> bool:
        """Compare-And-Swap atómico: actualiza el intento SOLO si su
        score.status actual == expected_status. Devuelve True si esta llamada
        ganó la transición; False si el intento no existe, no pertenece al
        usuario, u otra operación ya cambió el estado (condición de carrera
        resuelta: exactamente un ganador).

        Garantías por implementación:
        - JSON: lock reentrante por ruta (serializa lectores/escritores del
          proceso; el store JSON es single-proceso por diseño — ver docs).
        - SQL: transacción con bloqueo de fila (Postgres FOR UPDATE) o
          BEGIN IMMEDIATE (SQLite), commit único.
        """
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
    def save_report(self, user_id: str, attempt_id: str, name: str, content: str) -> Optional[str]:
        """Guarda un reporte con validación de propiedad reforzada:
        - el intento DEBE existir y pertenecer a `user_id` (no se pueden
          vincular reportes a intentos ajenos ni inexistentes);
        - un reporte existente de OTRO usuario jamás se sobrescribe ni se
          reasigna.
        Devuelve None (denegado, sin efectos) si no se cumple."""

    # -- Iteración 3: atomicidad ---------------------------------------------

    @abstractmethod
    @contextmanager
    def transaction(self, user_id: str) -> Iterator[None]:
        """Contexto transaccional para el usuario: las operaciones del store
        ejecutadas dentro comparten una única unidad atómica.

        - SQL: transacción real (Postgres BEGIN + bloqueo de fila
          SELECT…FOR UPDATE sobre kiwi_progress; SQLite BEGIN IMMEDIATE con
          busy-timeout). Excepción => ROLLBACK completo.
        - JSON: lock global de store (serialización) + diario copy-on-write:
          excepción => los archivos escritos durante la transacción se
          restauran/borran (rollback en proceso; el store JSON es
          single-proceso por diseño — la garantía multi-worker la da SQL).
          Si la restauración queda incompleta, se lanza
          `json_store.TransactionRollbackError` (encadenada al error
          original, con detalle de archivos no restaurados): el fallo de
          recuperación NUNCA se silencia ni se presenta como recuperado, y
          el store queda DEGRADADO: rechaza TODAS sus operaciones públicas
          de escritura —transaccionales y directas— (`StoreDegradedError`,
          comprobación centralizada) hasta una recuperación explícita y
          verificada (`verify_recovery()`) o la reconstrucción de la
          instancia tras reparar los archivos.

        Anidable: una transacción interna se une a la externa.
        """

    @abstractmethod
    def mutate_progress(self, user_id: str, fn: Callable[[dict[str, Any]], Any]) -> Any:
        """Read-modify-write ATÓMICO del progreso (política definida contra
        actualizaciones perdidas): load -> fn(prog) -> save bajo transacción.
        `fn` muta el dict en lugar y devuelve un valor arbitrario."""
