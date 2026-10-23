"""Punto de entrada de la aplicación FastAPI.

- API versionada en /api/v1 (contrato estable para el frontend; mañana
  para una app móvil o múltiples sedes).
- Frontend estático servido por el mismo proceso en local (un solo
  comando para levantar todo). En un despliegue se pueden separar.
- /healthz para monitoreo (expone auth_mode: auditable de un vistazo).
- Iteración 1: warnings explícitos de arranque según el modo de auth.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.v1.router import api_router
from .config import settings
from .core.errors import register_error_handlers

log = logging.getLogger("kiwi")


def _log_auth_banner() -> None:
    mode = settings.AUTH_MODE
    if mode in ("local", "test"):
        log.warning(
            "⚠ KIWI_AUTH_MODE=%s — MODO DE DESARROLLO/TEST. La identidad la "
            "emite un emisor local; NO usar en producción (default: supabase).",
            mode,
        )
    elif mode == "supabase":
        has_secret = bool(settings.SUPABASE_JWT_SECRET)
        has_url = bool(settings.SUPABASE_URL)
        if not has_url:
            log.warning(
                "KIWI_AUTH_MODE=supabase sin SUPABASE_URL: los endpoints "
                "privados responderán 503 auth_not_configured (fail-closed)."
            )
        elif not has_secret:
            log.info(
                "SUPABASE_JWT_SECRET ausente: se usará verificación JWKS "
                "(claves asimétricas) contra %s/auth/v1/.well-known/jwks.json",
                settings.SUPABASE_URL,
            )


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        _log_auth_banner()
        yield

    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="Plataforma de aprendizaje interactivo para analistas SOC (y más).",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)
    app.include_router(api_router)

    @app.get("/healthz", tags=["system"])
    def healthz():
        return {
            "status": "ok",
            "app": settings.app_name,
            "version": settings.version,
            "auth_mode": settings.AUTH_MODE,
        }

    # Asegura que el directorio de datos exista
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Frontend (SPA). Debe ir al final para no shadowear /api/*
    if settings.FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(settings.FRONTEND_DIR), html=True), name="frontend")

    return app


app = create_app()
