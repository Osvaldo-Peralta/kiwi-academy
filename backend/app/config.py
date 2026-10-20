"""Configuración central de la aplicación.

Todas las rutas se resuelven relativas a la raíz del proyecto para que el
backend funcione igual en local, en CI y en un despliegue (Vercel/Render).
Para producción, los valores pueden sobreescribirse con variables de
entorno (SOCACADEMY_*), sin tocar código.
"""
from __future__ import annotations

import os
from pathlib import Path

# backend/app/config.py -> raíz del proyecto = 2 niveles arriba
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).resolve() if raw else default


class Settings:
    app_name: str = "SOC Academy"
    version: str = "0.1.0"

    # Contenido versionado (cursos, lecciones, exámenes) — solo lectura en runtime
    CONTENT_DIR: Path = _env_path("SOCACADEMY_CONTENT_DIR", PROJECT_ROOT / "content")

    # Datos mutables (progreso de usuarios, intentos, reportes)
    DATA_DIR: Path = _env_path("SOCACADEMY_DATA_DIR", PROJECT_ROOT / "data")

    # Frontend estático
    FRONTEND_DIR: Path = _env_path("SOCACADEMY_FRONTEND_DIR", PROJECT_ROOT / "frontend")

    # Usuario por defecto cuando no llega cabecera X-User-Id (uso local)
    DEFAULT_USER_ID: str = os.environ.get("SOCACADEMY_DEFAULT_USER", "default")

    # CORS: en local el frontend lo sirve el propio backend (mismo origen),
    # pero dejamos abierto para desarrollo; en producción se restringe.
    CORS_ORIGINS: list[str] = os.environ.get(
        "SOCACADEMY_CORS_ORIGINS", "*"
    ).split(",")

    @property
    def users_dir(self) -> Path:
        return self.DATA_DIR / "users"

    def user_dir(self, user_id: str) -> Path:
        # Sanitización básica del user_id (evita path traversal)
        safe = "".join(c for c in user_id if c.isalnum() or c in ("-", "_"))[:64]
        return self.users_dir / (safe or "default")


settings = Settings()
