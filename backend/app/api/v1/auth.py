"""Endpoints públicos de autenticación: configuración y emisor dev.

- GET  /api/v1/config      -> configuración pública de auth (sin secretos)
- POST /api/v1/dev/token   -> emisor de tokens de desarrollo. SOLO existe en
                              modos local/test; en modo supabase devuelve 404
                              (el endpoint no existe en producción).
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from ...core.auth import get_verifier, public_auth_config
from ...core.errors import NotFoundError

router = APIRouter(tags=["auth"])


class DevTokenRequest(BaseModel):
    user_id: str


@router.get("/config")
def auth_config():
    """Configuración pública para el frontend.

    Nunca incluye secretos de firma (JWT secret, service key). La anon key de
    Supabase es pública por diseño (está pensada para el navegador).
    """
    return public_auth_config()


@router.post("/dev/token")
def dev_token(payload: DevTokenRequest):
    """Emite un JWT de desarrollo (HS256, iss=kiwi-local-dev|kiwi-test,
    sub en namespace dev-*). Solo modos local/test; 404 en supabase."""
    verifier = get_verifier()
    if verifier.mode == "supabase":
        # En producción este endpoint no existe (404, no 403: no confirmar)
        raise NotFoundError("Recurso no encontrado")
    return verifier.issue_dev_token(payload.user_id.strip())
