"""Implementación SQL del ProgressStore (Postgres/Supabase o SQLite).

Activa cuando KIWI_DATABASE_URL está definida; si no, el sistema sigue con
archivos JSON (JsonProgressStore). Mismo contrato (ProgressStore), así que
los servicios/API no cambian — la escalada a multiusuario real es cambiar
una variable de entorno.

Diseño portable:
- SQL estándar, JSON como TEXT (funciona idéntico en SQLite y Postgres).
- Placeholders `?` -> se traducen a `%s` solo para psycopg.
- Conexión por operación fuera de transacciones; dentro de `transaction()`
  todas las operaciones del hilo comparten UNA conexión y UNA transacción.

Iteración 3 — atomicidad:
- transaction(user_id): Postgres = BEGIN + placeholder-row + SELECT…FOR
  UPDATE sobre kiwi_progress (bloqueo de fila; serializa mutaciones del
  usuario); SQLite = BEGIN IMMEDIATE (lock de escritura con busy-timeout).
  Excepción => ROLLBACK completo: cero estados parciales.
- mutate_progress: read-modify-write atómico bajo transaction().
- save_report: guarda de propiedad — nunca sobrescribe/reasigna un reporte
  de otro usuario (devuelve None si se intenta).
- update_attempt / update_attempt_if_status: guards de propiedad en el
  WHERE; el CAS funciona dentro o fuera de una transacción externa.

Supabase: usa la URI de Settings -> Database. Con el pooler (puerto 6543,
modo transaction) funciona bien en serverless; en local puedes usar el
puerto directo 5432.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

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


def _empty_progress(user_id: str) -> dict[str, Any]:
    return {"user_id": user_id, "lessons": {}, "exams": {}, "activity": {}}


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
        self._local = threading.local()   # conexión de la transacción en curso
        self._ensure_schema()

    # ------------------------------------------------------------------
    # plumbing de driver (tx-aware)
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
        conn = sqlite3.connect(self.sqlite_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _sql(self, sql: str) -> str:
        """Traduce placeholders `?` al estilo del driver."""
        return sql.replace("?", "%s") if self.is_postgres else sql

    def _tx_conn(self):
        return getattr(self._local, "conn", None)

    def _execute(self, statements: list[tuple[str, tuple]]) -> list[list[Any]]:
        """Ejecuta (sql, params); si hay transacción activa del hilo, la usa."""
        tx = self._tx_conn()
        conn = tx or self._connect()
        try:
            cur = conn.cursor()
            out = []
            for sql, params in statements:
                cur.execute(self._sql(sql), params)
                out.append(cur.fetchall() if cur.description else [])
            if tx is None:
                conn.commit()
            return out
        finally:
            if tx is None:
                conn.close()

    def _write(self, sql: str, params: tuple) -> int:
        """Una escritura; devuelve rowcount. Tx-aware."""
        tx = self._tx_conn()
        conn = tx or self._connect()
        try:
            cur = conn.cursor()
            cur.execute(self._sql(sql), params)
            rc = cur.rowcount
            if tx is None:
                conn.commit()
            return rc
        finally:
            if tx is None:
                conn.close()

    def _ensure_schema(self) -> None:
        self._execute([(sql, ()) for sql in SCHEMA])

    # ------------------------------------------------------------------
    # Iteración 3: transacciones
    # ------------------------------------------------------------------

    @contextmanager
    def transaction(self, user_id: str) -> Iterator[None]:
        if self._tx_conn() is not None:
            yield            # anidada: se une a la transacción externa
            return

        if self.is_postgres:
            conn = self._connect()
            try:
                cur = conn.cursor()
                # La fila debe existir para que FOR UPDATE la bloquee:
                # placeholder inocuo si el usuario aún no tiene progreso.
                cur.execute(
                    self._sql(
                        "INSERT INTO kiwi_progress (user_id, data, updated_at) "
                        "VALUES (?, ?, ?) ON CONFLICT (user_id) DO NOTHING"
                    ),
                    (user_id, json.dumps(_empty_progress(user_id)), ""),
                )
                cur.execute(
                    self._sql("SELECT data FROM kiwi_progress WHERE user_id = ? FOR UPDATE"),
                    (user_id,),
                )
                self._local.conn = conn
                yield
                conn.commit()
            except BaseException:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                self._local.conn = None
                conn.close()
        else:
            # SQLite: autocommit manual; BEGIN IMMEDIATE toma el lock de
            # escritura al inicio (busy-timeout 30 s reintentando).
            conn = sqlite3.connect(self.sqlite_path, timeout=30, isolation_level=None)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("BEGIN IMMEDIATE")
                self._local.conn = conn
                yield
                conn.execute("COMMIT")
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            finally:
                self._local.conn = None
                conn.close()

    def mutate_progress(self, user_id: str, fn: Callable[[dict[str, Any]], Any]) -> Any:
        with self.transaction(user_id):
            prog = self.load_progress(user_id)
            out = fn(prog)
            self.save_progress(user_id, prog)
            return out

    # ------------------------------------------------------------------
    # progreso
    # ------------------------------------------------------------------

    def load_progress(self, user_id: str) -> dict[str, Any]:
        rows = self._execute([
            ("SELECT data FROM kiwi_progress WHERE user_id = ?", (user_id,)),
        ])[0]
        if not rows:
            return _empty_progress(user_id)
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
        return self._write(sql, (data, submitted, attempt["id"], user_id)) > 0

    @staticmethod
    def _status_of(raw: str) -> Optional[str]:
        try:
            return (json.loads(raw).get("score") or {}).get("status")
        except (ValueError, AttributeError):
            return None

    def update_attempt_if_status(
        self, user_id: str, attempt_id: str,
        expected_status: str, attempt: dict[str, Any],
    ) -> bool:
        """CAS atómico pending->reviewed. Dentro de una transacción externa
        reutiliza su conexión y bloqueos; fuera, abre la suya propia."""
        data = json.dumps(attempt, ensure_ascii=False)
        submitted = str(attempt.get("submitted_at", ""))
        tx = self._tx_conn()

        if tx is not None:
            cur = tx.cursor()
            select_sql = "SELECT data FROM kiwi_attempts WHERE id = ? AND user_id = ?"
            if self.is_postgres:
                select_sql += " FOR UPDATE"   # bloqueo de fila dentro de la tx
            cur.execute(self._sql(select_sql), (attempt_id, user_id))
            row = cur.fetchone()
            if not row or self._status_of(row[0]) != expected_status:
                return False
            cur.execute(
                self._sql("UPDATE kiwi_attempts SET data = ?, submitted_at = ? WHERE id = ? AND user_id = ?"),
                (data, submitted, attempt_id, user_id),
            )
            return True

        if self.is_postgres:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute(
                    self._sql("SELECT data FROM kiwi_attempts WHERE id = ? AND user_id = ? FOR UPDATE"),
                    (attempt_id, user_id),
                )
                row = cur.fetchone()
                if not row or self._status_of(row[0]) != expected_status:
                    conn.rollback()
                    return False
                cur.execute(
                    self._sql("UPDATE kiwi_attempts SET data = ?, submitted_at = ? WHERE id = ? AND user_id = ?"),
                    (data, submitted, attempt_id, user_id),
                )
                conn.commit()
                return True
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                conn.close()

        conn = sqlite3.connect(self.sqlite_path, timeout=30, isolation_level=None)
        cur = conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                "SELECT data FROM kiwi_attempts WHERE id = ? AND user_id = ?",
                (attempt_id, user_id),
            )
            row = cur.fetchone()
            if not row or self._status_of(row[0]) != expected_status:
                cur.execute("ROLLBACK")
                return False
            cur.execute(
                "UPDATE kiwi_attempts SET data = ?, submitted_at = ? WHERE id = ? AND user_id = ?",
                (data, submitted, attempt_id, user_id),
            )
            cur.execute("COMMIT")
            return True
        except Exception:
            try:
                cur.execute("ROLLBACK")
            except Exception:
                pass
            raise
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
    # reportes (con guarda de propiedad — Iteración 3)
    # ------------------------------------------------------------------

    def save_report(self, user_id: str, attempt_id: str, name: str, content: str) -> Optional[str]:
        """Guarda/actualiza el reporte SOLO si no existe o pertenece al mismo
        usuario. Un reporte ajeno jamás se sobrescribe ni se reasigna:
        devuelve None (denegado) sin efectos."""
        tx = self._tx_conn()
        conn = tx or self._connect()
        try:
            cur = conn.cursor()
            # (auditoría Iter 3, punto 3) Propiedad del INTENTO: solo se crea
            # o actualiza un reporte si el intento existe y pertenece a este
            # usuario. Impide vincular reportes a intentos ajenos/inexistentes.
            cur.execute(
                self._sql("SELECT user_id FROM kiwi_attempts WHERE id = ?"),
                (attempt_id,),
            )
            att_row = cur.fetchone()
            if not att_row or att_row[0] != user_id:
                if tx is None:
                    conn.rollback()
                return None
            cur.execute(
                self._sql("SELECT user_id FROM kiwi_reports WHERE attempt_id = ? AND name = ?"),
                (attempt_id, name),
            )
            row = cur.fetchone()
            if row and row[0] != user_id:
                if tx is None:
                    conn.rollback()
                return None    # intento de usurpación de propiedad: denegado
            if row:
                cur.execute(
                    self._sql("UPDATE kiwi_reports SET content = ? WHERE attempt_id = ? AND name = ? AND user_id = ?"),
                    (content, attempt_id, name, user_id),
                )
            else:
                if self.is_postgres:
                    # INSERT .. ON CONFLICT DO NOTHING: si otra transacción lo
                    # creó entre el SELECT y el INSERT, no se pisa al dueño.
                    cur.execute(
                        self._sql(
                            "INSERT INTO kiwi_reports (attempt_id, name, user_id, content) "
                            "VALUES (?, ?, ?, ?) ON CONFLICT (attempt_id, name) DO NOTHING"
                        ),
                        (attempt_id, name, user_id, content),
                    )
                    if cur.rowcount == 0:
                        if tx is None:
                            conn.rollback()
                        return None
                else:
                    cur.execute(
                        "INSERT OR IGNORE INTO kiwi_reports (attempt_id, name, user_id, content) "
                        "VALUES (?, ?, ?, ?)",
                        (attempt_id, name, user_id, content),
                    )
                    if cur.rowcount == 0:
                        if tx is None:
                            conn.rollback()
                        return None
            if tx is None:
                conn.commit()
            return f"sql://{attempt_id}/{name}"
        finally:
            if tx is None:
                conn.close()
