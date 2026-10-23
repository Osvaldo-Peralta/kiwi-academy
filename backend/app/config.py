"""Configuración central de la aplicación.

Todas las rutas se resuelven relativas a la raíz del proyecto para que el
backend funcione igual en local, en CI y en un despliegue (Vercel/Render).
Para producción, los valores pueden sobreescribirse con variables de
entorno (KIWI_*), sin tocar código.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# backend/app/config.py -> raíz del proyecto = 2 niveles arriba
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Soporte opcional de .env (python-dotenv). Las variables de entorno reales
# siempre tienen prioridad sobre el archivo.
try:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:  # dotenv no instalado -> sin .env, todo por variables de entorno
    pass


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).resolve() if raw else default


def _writable_dir(path: Path, fallback_name: str) -> Path:
    """Garantiza un directorio de datos escribible.

    En serverless (Vercel/Lambda) el filesystem del despliegue es de solo
    lectura: ahí caemos a /tmp (efímero — el progreso se reinicia en cada
    arranque en frío; ver docs/deployment.md para persistencia real).
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".writable-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return path
    except OSError:
        fallback = Path(tempfile.gettempdir()) / fallback_name
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


class Settings:
    app_name: str = "Kiwi Academy"
    version: str = "0.1.0"

    # Contenido versionado (cursos, lecciones, exámenes) — solo lectura en runtime
    CONTENT_DIR: Path = _env_path("KIWI_CONTENT_DIR", PROJECT_ROOT / "content")

    # Datos mutables (progreso de usuarios, intentos, reportes).
    # Si el destino no es escribible (serverless), cae a /tmp automáticamente.
    DATA_DIR: Path = _writable_dir(
        _env_path("KIWI_DATA_DIR", PROJECT_ROOT / "data"),
        "kiwi-academy-data",
    )

    # Frontend estático
    FRONTEND_DIR: Path = _env_path("KIWI_FRONTEND_DIR", PROJECT_ROOT / "frontend")

    # Usuario por defecto: ELIMINADO en Iteración 1. La identidad deriva
    # exclusivamente del JWT verificado (claim sub). No existe usuario
    # implícito ni cabecera de identidad aceptada.

    # ── Autenticación (Iteración 1) ─────────────────────────────────────
    # Modo de auth: supabase (DEFAULT/producción) | local (desarrollo) | test
    AUTH_MODE: str = os.environ.get("KIWI_AUTH_MODE", "supabase").strip().lower()

    # Supabase (modo supabase)
    SUPABASE_URL: str = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    # Secreto JWT legacy HS256 (SECRETO: nunca exponer al frontend ni loguear)
    SUPABASE_JWT_SECRET: str = os.environ.get("SUPABASE_JWT_SECRET", "")
    # Anon key: pública por diseño (cliente navegador). Se sirve vía /api/v1/config
    SUPABASE_ANON_KEY: str = os.environ.get("SUPABASE_ANON_KEY", "")
    # Verificación supabase: auto | secret (HS256) | jwks (ES256/RS256)
    JWT_MODE: str = os.environ.get("KIWI_JWT_MODE", "auto").strip().lower()

    # Desarrollo local (modo local): secreto del emisor dev; si falta, efímero
    DEV_JWT_SECRET: str = os.environ.get("KIWI_DEV_JWT_SECRET", "")

    # ── Iteración 2: evaluación y revisión humana ──────────────────────────
    # Subs (JWT `sub`) autorizados a revisar respuestas libres pendientes.
    # CSV; VACÍO por defecto => nadie puede revisar => los exámenes con
    # respuestas libres pendientes permanecen pendientes (sin veredicto).
    # La autorización se resuelve SIEMPRE contra el sub del token verificado,
    # nunca contra datos enviados por el cliente.
    REVIEWER_USERS: str = os.environ.get("KIWI_REVIEWER_USERS", "")

    # Margen de red/reloj sobre el límite del examen (segundos)
    EXAM_GRACE_SECONDS: int = int(os.environ.get("KIWI_EXAM_GRACE_SECONDS", "30"))

    # Persistencia SQL opcional (Supabase/Postgres o SQLite). Vacío = archivos JSON.
    #   postgresql://user:pass@host:port/db   (Supabase: Settings -> Database -> URI)
    #   sqlite:///ruta/kiwi.db
    DATABASE_URL: str = os.environ.get("KIWI_DATABASE_URL", "").strip()

    # CORS: en local el frontend lo sirve el propio backend (mismo origen),
    # pero dejamos abierto para desarrollo; en producción se restringe.
    CORS_ORIGINS: list[str] = os.environ.get(
        "KIWI_CORS_ORIGINS", "*"
    ).split(",")

    @property
    def users_dir(self) -> Path:
        return self.DATA_DIR / "users"

    def user_dir(self, user_id: str) -> Path:
        # Sanitización básica del user_id (evita path traversal)
        safe = "".join(c for c in user_id if c.isalnum() or c in ("-", "_"))[:64]
        return self.users_dir / (safe or "default")


settings = Settings()

# Validación fail-fast de la configuración de auth: mejor un error claro al
# arrancar que un comportamiento indefinido en runtime.
if settings.AUTH_MODE not in ("supabase", "local", "test"):
    raise ValueError(
        f"KIWI_AUTH_MODE inválido: {settings.AUTH_MODE!r} "
        "(valores admitidos: supabase | local | test; default: supabase)"
    )
if settings.JWT_MODE not in ("auto", "secret", "jwks"):
    raise ValueError(
        f"KIWI_JWT_MODE inválido: {settings.JWT_MODE!r} (auto | secret | jwks)"
    )
