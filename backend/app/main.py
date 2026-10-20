"""Punto de entrada de la aplicación FastAPI.

- API versionada en /api/v1 (contrato estable para el frontend; mañana
  para una app móvil o múltiples sedes).
- Frontend estático servido por el mismo proceso en local (un solo
  comando para levantar todo). En un despliegue se pueden separar.
- /healthz para monitoreo.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.v1.router import api_router
from .config import settings
from .core.errors import register_error_handlers


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        description="Plataforma de aprendizaje interactivo para analistas SOC (y más).",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
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
        return {"status": "ok", "app": settings.app_name, "version": settings.version}

    # Asegura que el directorio de datos exista
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Frontend (SPA). Debe ir al final para no shadowear /api/*
    if settings.FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(settings.FRONTEND_DIR), html=True), name="frontend")

    return app


app = create_app()
