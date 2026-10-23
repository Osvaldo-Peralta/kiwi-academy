"""Implementación SQL del ProgressStore (Postgres/Supabase o SQLite).

Activa cuando KIWI_DATABASE_URL está definida; si no, el sistema sigue con
archivos JSON (JsonProgressStore). Mismo contrato (ProgressStore), así que
los servicios/API no cambian — la escalada a multiusuario real es cambiar
una variable de entorno.

Diseño portable:
- SQL estándar, JSON como TEXT (funciona idéntico en SQLite y Postgres).
- Placeholders `?` -> se traducen a `%s` solo para psycopg.
- Conexión por operación: patrón correcto en serverless (Vercel) y simple
  en local; pooling queda para cuando haga falta.

Supabase: usa la URI de Settings -> Database. Con el pooler (puerto 6543,
modo transaction) funciona bien en serverless; en local puedes usar el
puerto directo 5432.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from .base import ProgressStore

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS kiwi_progress (
        user_id    TEXT PRIMARY KEY,
        data       TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS kiwi_attempts (
        id           TEXT PRIMARY KEY,
        user_id      TEXT NOT NULL,
        submitted_at TEXT NOT NULL,
        scope_type   TEXT NOT NULL DEFAULT '',
        data         TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS kiwi_attempts_user ON kiwi_attempts (user_id, submitted_at)",
    """
    CREATE TABLE IF NOT EXISTS kiwi_reports (
        attempt_id TEXT NOT NULL,
        name       TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        content    TEXT NOT NULL,
        PRIMARY KEY (attempt_id, name)
    )
    """,
]


class SqlProgressStore(ProgressStore):
    """ProgressStore sobre Postgres (psycopg) o SQLite (stdlib)."""

    def __init__(self, url: str):
        self.url = url.strip()
        self.is_postgres = self.url.startswith(("postgres://", "postgresql://"))
        if self.url.startswith("sqlite:///"):
            self.sqlite_path = self.url[len("sqlite:///"):]
            # relativo -> se resuelve contra el cwd del proceso
            Path(self.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        elif not self.is_postgres:
            raise ValueError(
                "KIWI_DATABASE_URL debe ser postgresql://... o sqlite:///ruta.db "
                f"(recibido: {self.url[:40]}...)"
            )
        self._ensure_schema()

    # ------------------------------------------------------------------
    # plumbing de driver
    # ------------------------------------------------------------------

    def _connect(self):
        if self.is_postgres:
            try:
                import psycopg
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "KIWI_DATABASE_URL apunta a Postgres pero falta el driver. "
                    "Instálalo con:  pip install -r requirements-postgres.txt"
                ) from exc
            url = self.url.replace("postgres://", "postgresql://", 1)
            return psycopg.connect(url, connect_timeout=10)
        conn = sqlite3.connect(self.sqlite_path, timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _sql(self, sql: str) -> str:
        """Traduce placeholders `?` al estilo del driver."""
        return sql.replace("?", "%s") if self.is_postgres else sql

    def _execute(self, statements: list[tuple[str, tuple]]) -> list[list[Any]]:
        """Ejecuta (sql, params) en una conexión; devuelve filas por sentencia."""
        conn = self._connect()
        try:
            cur = conn.cursor()
            out = []
            for sql, params in statements:
                cur.execute(self._sql(sql), params)
                out.append(cur.fetchall() if cur.description else [])
            conn.commit()
            return out
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        self._execute([(sql, ()) for sql in SCHEMA])

    # ------------------------------------------------------------------
    # progreso
    # ------------------------------------------------------------------

    def load_progress(self, user_id: str) -> dict[str, Any]:
        rows = self._execute([
            ("SELECT data FROM kiwi_progress WHERE user_id = ?", (user_id,)),
        ])[0]
        if not rows:
            return {"user_id": user_id, "lessons": {}, "exams": {}, "activity": {}}
        return json.loads(rows[0][0])

    def save_progress(self, user_id: str, progress: dict[str, Any]) -> None:
        data = json.dumps(progress, ensure_ascii=False)
        updated = progress.get("updated_at", "")
        if self.is_postgres:
            sql = (
                "INSERT INTO kiwi_progress (user_id, data, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT (user_id) DO UPDATE SET data = EXCLUDED.data, "
                "updated_at = EXCLUDED.updated_at"
            )
        else:
            sql = "INSERT OR REPLACE INTO kiwi_progress (user_id, data, updated_at) VALUES (?, ?, ?)"
        self._execute([(sql, (user_id, data, updated))])

    # ------------------------------------------------------------------
    # intentos
    # ------------------------------------------------------------------

    def save_attempt(self, user_id: str, attempt: dict[str, Any]) -> None:
        data = json.dumps(attempt, ensure_ascii=False)
        submitted = str(attempt.get("submitted_at", ""))
        scope_type = str((attempt.get("scope") or {}).get("type", ""))
        if self.is_postgres:
            sql = "INSERT INTO kiwi_attempts (id, user_id, submitted_at, scope_type, data) VALUES (?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING"
        else:
            sql = "INSERT OR IGNORE INTO kiwi_attempts (id, user_id, submitted_at, scope_type, data) VALUES (?, ?, ?, ?, ?)"
        self._execute([(sql, (attempt["id"], user_id, submitted, scope_type, data))])

    def update_attempt(self, user_id: str, attempt: dict[str, Any]) -> bool:
        # Guarda de propiedad en el WHERE: solo actualiza si el intento existe
        # Y pertenece a user_id. Nunca reasigna propiedad ni crea filas.
        data = json.dumps(attempt, ensure_ascii=False)
        submitted = str(attempt.get("submitted_at", ""))
        sql = "UPDATE kiwi_attempts SET data = ?, submitted_at = ? WHERE id = ? AND user_id = ?"
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(self._sql(sql), (data, submitted, attempt["id"], user_id))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get_attempt(self, user_id: str, attempt_id: str) -> Optional[dict[str, Any]]:
        rows = self._execute([
            ("SELECT data FROM kiwi_attempts WHERE id = ? AND user_id = ?",
             (attempt_id, user_id)),
        ])[0]
        return json.loads(rows[0][0]) if rows else None

    def find_attempt_any_user(self, attempt_id: str) -> tuple[Optional[str], Optional[dict[str, Any]]]:
        rows = self._execute([
            ("SELECT user_id, data FROM kiwi_attempts WHERE id = ?", (attempt_id,)),
        ])[0]
        if not rows:
            return None, None
        return rows[0][0], json.loads(rows[0][1])

    def list_attempts(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._execute([
            ("SELECT data FROM kiwi_attempts WHERE user_id = ? "
             "ORDER BY submitted_at DESC, id DESC LIMIT ?", (user_id, limit)),
        ])[0]
        return [json.loads(r[0]) for r in rows]

    # ------------------------------------------------------------------
    # reportes
    # ------------------------------------------------------------------

    def save_report(self, user_id: str, attempt_id: str, name: str, content: str) -> str:
        if self.is_postgres:
            sql = (
                "INSERT INTO kiwi_reports (attempt_id, name, user_id, content) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (attempt_id, name) DO UPDATE SET content = EXCLUDED.content, "
                "user_id = EXCLUDED.user_id"
            )
        else:
            sql = "INSERT OR REPLACE INTO kiwi_reports (attempt_id, name, user_id, content) VALUES (?, ?, ?, ?)"
        self._execute([(sql, (attempt_id, name, user_id, content))])
        return f"sql://{attempt_id}/{name}"
