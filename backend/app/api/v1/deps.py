"""Dependencias compartidas de la API.

Iteración 1: la identidad proviene EXCLUSIVAMENTE del JWT verificado
(Authorization: Bearer). La cabecera X-User-Id fue eliminada: el backend ya
no la lee en ningún punto (ver test_auth.py::test_x_user_id_ya_no_autentica).
"""
from __future__ import annotations

from typing import Optional

from fastapi import Header

from ...core.auth import get_verifier, parse_bearer
from ...core.services import LearningService

_service: Optional[LearningService] = None


def get_service() -> LearningService:
    """Singleton del servicio (stateless: todo estado vive en los stores)."""
    global _service
    if _service is None:
        _service = LearningService()
    return _service


def get_current_user(authorization: Optional[str] = Header(default=None)) -> str:
    """Identidad autenticada (claim `sub` del JWT verificado).

    Lanza:
      - AuthError 401: cabecera ausente/mal formada, token inválido/expirado,
        claims incorrectos, sub fuera de namespace.
      - AuthNotConfiguredError 503: modo supabase sin credenciales (fail-closed).
    """
    token = parse_bearer(authorization)
    claims = get_verifier().verify(token)
    return claims["sub"]


def get_optional_user(authorization: Optional[str] = Header(default=None)) -> Optional[str]:
    """Como get_current_user pero permite ausencia de token (contenido público
    con capa personal opcional). Token presente pero inválido -> 401 igual:
    unas credenciales explícitamente malas no se ignoran en silencio."""
    if not authorization or not authorization.strip():
        return None
    return get_current_user(authorization)
