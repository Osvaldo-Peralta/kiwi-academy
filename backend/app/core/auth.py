"""Autenticación: verificación estricta de JWT (fail-closed) y emisión dev.

Modos (KIWI_AUTH_MODE):
- "supabase" (DEFAULT y obligatorio en producción): verifica JWT emitidos por
  Supabase Auth. Dos variantes de verificación:
    * secret (HS256): con SUPABASE_JWT_SECRET (proyectos con JWT secret legacy)
    * jwks  (ES256/RS256): con SUPABASE_URL (proyectos con signing keys
      asimétricas); las claves públicas se obtienen del endpoint JWKS oficial.
  La identidad es ÚNICAMENTE el claim `sub` del token verificado.
- "local": desarrollo local. Emisor propio HS256 (iss=kiwi-local-dev) con
  secreto KIWI_DEV_JWT_SECRET (o efímero por proceso). La validación JWT NO se
  omite: cambia el emisor, no el camino de verificación. Ids obligatorios en
  el namespace "dev-*" (imposible colisionar con UUIDs de Supabase).
- "test": igual que local pero con secreto fijo conocido SOLO para fixtures
  (TEST_JWT_SECRET) y sin warnings. Desplegar en modo test es un error grave
  de configuración: el modo por defecto es supabase, el modo se ve en
  /healthz y el arranque loguea WARN explícito en local/test.

Reglas de validación (todos los modos):
- Algoritmo FIJADO por configuración (defensa contra algorithm confusion y
  alg=none). Nunca se acepta el `alg` del token sin cotejarlo con la lista.
- Se exigen claims: exp, iat, iss, aud, sub. aud debe ser "authenticated".
- `iat` no puede estar en el futuro fuera del margen (defensa contra tokens
  pre-fechados / relojes manipulados).
- iss esperado según modo. Leeway de reloj: 30 s.
- `sub` debe ser un identificador seguro: [A-Za-z0-9_-]{1,64} (defensa en
  profundidad contra path traversal / inyección en stores) y, en modos
  local/test, pertenecer al namespace dev-*.
- Fallos -> AuthError (401). Modo supabase sin credenciales configuradas ->
  AuthNotConfiguredError (503): fail-closed, nunca identidad implícita.
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from jwt import PyJWKClient

from ..config import settings
from .errors import AuthError, AuthNotConfiguredError

log = logging.getLogger("kiwi.auth")

ISSUER_SUPABASE_FMT = "{url}/auth/v1"
ISSUER_LOCAL = "kiwi-local-dev"
ISSUER_TEST = "kiwi-test"
AUDIENCE = "authenticated"
LEEWAY_SECONDS = 30
SUB_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DEV_SUB_PATTERN = re.compile(r"^dev-[A-Za-z0-9_-]{1,32}$")
DEV_TOKEN_TTL_SECONDS = 12 * 3600

# H-02: contrato documentado == contrato implementado. `iat` es claim
# obligatorio (como exp/iss/aud/sub) y se valida además que no esté en el
# futuro fuera del margen permitido.
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub"]
_DECODE_OPTIONS = {"require": REQUIRED_CLAIMS}

# Secreto fijo exclusivo para fixtures de prueba (público por diseño: solo
# tiene sentido con KIWI_AUTH_MODE=test, que NUNCA debe usarse desplegado).
TEST_JWT_SECRET = "kiwi-academy-fixed-secret-ONLY-for-pytest-fixtures"

_ephemeral_dev_secret: Optional[str] = None
_ephemeral_lock = threading.Lock()


def _dev_secret() -> str:
    """Secreto del emisor local: env si existe; si no, efímero por proceso."""
    global _ephemeral_dev_secret
    if settings.DEV_JWT_SECRET:
        return settings.DEV_JWT_SECRET
    with _ephemeral_lock:
        if _ephemeral_dev_secret is None:
            _ephemeral_dev_secret = secrets.token_urlsafe(32)
            log.warning(
                "KIWI_DEV_JWT_SECRET no definido: se generó un secreto efímero "
                "para este proceso (los tokens dev expiran al reiniciar)."
            )
        return _ephemeral_dev_secret


def expected_issuer(mode: str) -> str:
    if mode == "supabase":
        return ISSUER_SUPABASE_FMT.format(url=settings.SUPABASE_URL.rstrip("/"))
    if mode == "local":
        return ISSUER_LOCAL
    return ISSUER_TEST


def _validate_sub(sub: Any, mode: str) -> str:
    if not isinstance(sub, str) or not SUB_PATTERN.match(sub):
        raise AuthError("Claim 'sub' inválido", reason="invalid_subject")
    if mode in ("local", "test") and not DEV_SUB_PATTERN.match(sub):
        raise AuthError(
            "En modo local/test el usuario debe vivir en el namespace dev-*",
            reason="invalid_subject_namespace",
        )
    return sub


# ---------------------------------------------------------------------------
# Verificadores
# ---------------------------------------------------------------------------

class Verifier:
    """Verifica tokens según el modo configurado. Inmutable por instancia."""

    def __init__(self, mode: str, supabase_url: str, supabase_secret: str,
                 jwt_mode: str, dev_secret_value: Optional[str]):
        self.mode = mode
        self.supabase_url = supabase_url.rstrip("/")
        self.supabase_secret = supabase_secret
        self.jwt_mode = jwt_mode  # "secret" | "jwks" | "auto" (solo supabase)
        self._dev_secret = dev_secret_value
        self._jwks_client: Optional[PyJWKClient] = None

    # -- resolución de modo efectivo ---------------------------------------

    def _supabase_verification(self) -> str:
        if self.jwt_mode in ("secret", "jwks"):
            return self.jwt_mode
        # auto: secreto presente -> HS256; si no -> JWKS (requiere URL)
        return "secret" if self.supabase_secret else "jwks"

    def configured(self) -> bool:
        if self.mode == "supabase":
            if self._supabase_verification() == "secret":
                return bool(self.supabase_secret and self.supabase_url)
            return bool(self.supabase_url)
        return True  # local/test siempre tienen emisor/verificador propio

    # -- verificación --------------------------------------------------------

    def verify(self, token: str) -> dict[str, Any]:
        """Devuelve claims verificados o lanza AuthError (401) / 503."""
        if not self.configured():
            raise AuthNotConfiguredError(
                "Autenticación requerida pero no configurada en el servidor "
                "(modo supabase sin SUPABASE_URL/SECRET). Fail-closed: no se "
                "aceeptan identidades."
            )
        try:
            if self.mode == "supabase":
                if self._supabase_verification() == "secret":
                    claims = jwt.decode(
                        token, self.supabase_secret,
                        algorithms=["HS256"],
                        audience=AUDIENCE,
                        issuer=expected_issuer("supabase"),
                        leeway=timedelta(seconds=LEEWAY_SECONDS),
                        options=_DECODE_OPTIONS,
                    )
                else:
                    if self._jwks_client is None:
                        self._jwks_client = PyJWKClient(
                            f"{self.supabase_url}/auth/v1/.well-known/jwks.json"
                        )
                    signing_key = self._jwks_client.get_signing_key_from_jwt(token)
                    claims = jwt.decode(
                        token, signing_key.key,
                        algorithms=["ES256", "RS256"],  # solo asimétricos: nunca HS* vía JWKS
                        audience=AUDIENCE,
                        issuer=expected_issuer("supabase"),
                        leeway=timedelta(seconds=LEEWAY_SECONDS),
                        options=_DECODE_OPTIONS,
                    )
            else:
                secret = TEST_JWT_SECRET if self.mode == "test" else (
                    self._dev_secret or _dev_secret())
                claims = jwt.decode(
                    token, secret,
                    algorithms=["HS256"],
                    audience=AUDIENCE,
                    issuer=expected_issuer(self.mode),
                    leeway=timedelta(seconds=LEEWAY_SECONDS),
                    options=_DECODE_OPTIONS,
                )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("Token expirado", reason="expired") from exc
        except jwt.ImmatureSignatureError as exc:
            # H-02: iat en el futuro fuera del margen (PyJWT lo detecta en
            # decode); se mapea a un reason explícito y auditable.
            raise AuthError(
                "Token emitido en el futuro (iat fuera del margen permitido)",
                reason="iat_in_future",
            ) from exc
        except jwt.PyJWKClientError as exc:
            # JWKS: kid ausente/desconocido, endpoint inaccesible, etc.
            # Fail-closed: 401, nunca 500.
            raise AuthError("Token inválido (clave de firma no resoluble)",
                            reason="invalid") from exc
        except jwt.InvalidTokenError as exc:
            # cubre firma inválida, iss/aud incorrectos, alg no permitido,
            # claims ausentes (incluido iat, H-02), token malformado,
            # alg=none, etc.
            raise AuthError("Token inválido", reason="invalid") from exc

        self._check_iat(claims)
        sub = _validate_sub(claims.get("sub"), self.mode)
        claims["sub"] = sub
        return claims

    @staticmethod
    def _check_iat(claims: dict[str, Any]) -> None:
        """H-02: `iat` obligatorio (vía options require) y no futuro fuera
        del margen. PyJWT valida exp/nbf pero NO un iat adelantado."""
        iat = claims.get("iat")
        if not isinstance(iat, (int, float)):
            raise AuthError("Claim 'iat' inválido", reason="invalid")
        if iat > time.time() + LEEWAY_SECONDS:
            raise AuthError(
                "Token emitido en el futuro (iat fuera del margen permitido)",
                reason="iat_in_future",
            )

    # -- emisión (solo modos local/test) --------------------------------------

    def issue_dev_token(self, user_id: str) -> dict[str, Any]:
        if self.mode == "supabase":
            raise AuthError("El emisor dev no existe en modo supabase",
                            reason="not_available")
        if not DEV_SUB_PATTERN.match(user_id or ""):
            raise AuthError(
                "user_id debe cumplir dev-[A-Za-z0-9_-]{1,32}",
                reason="invalid_user_id",
            )
        now = datetime.now(timezone.utc)
        secret = TEST_JWT_SECRET if self.mode == "test" else (
            self._dev_secret or _dev_secret())
        payload = {
            "sub": user_id,
            "aud": AUDIENCE,
            "iss": expected_issuer(self.mode),
            "iat": now,
            "exp": now + timedelta(seconds=DEV_TOKEN_TTL_SECONDS),
        }
        token = jwt.encode(payload, secret, algorithm="HS256")
        return {
            "access_token": token,
            "token_type": "Bearer",
            "user_id": user_id,
            "expires_in": DEV_TOKEN_TTL_SECONDS,
        }


# ---------------------------------------------------------------------------
# Singleton perezoso, reconstruido si cambia la configuración relevante
# (permite monkeypatching en tests sin estado residual).
# ---------------------------------------------------------------------------

_verifier: Optional[Verifier] = None
_verifier_key: Optional[tuple] = None
_verifier_lock = threading.Lock()


def _config_key() -> tuple:
    dev = settings.DEV_JWT_SECRET or ""
    return (
        settings.AUTH_MODE,
        settings.SUPABASE_URL,
        hashlib.sha256(settings.SUPABASE_JWT_SECRET.encode()).hexdigest()[:16],
        settings.JWT_MODE,
        hashlib.sha256(dev.encode()).hexdigest()[:16],
    )


def get_verifier() -> Verifier:
    global _verifier, _verifier_key
    key = _config_key()
    with _verifier_lock:
        if _verifier is None or _verifier_key != key:
            mode = settings.AUTH_MODE
            _verifier = Verifier(
                mode=mode,
                supabase_url=settings.SUPABASE_URL,
                supabase_secret=settings.SUPABASE_JWT_SECRET,
                jwt_mode=settings.JWT_MODE,
                dev_secret_value=settings.DEV_JWT_SECRET or None,
            )
            _verifier_key = key
        return _verifier


def reset_verifier() -> None:
    """Solo para tests."""
    global _verifier, _verifier_key, _ephemeral_dev_secret
    with _verifier_lock:
        _verifier = None
        _verifier_key = None
        _ephemeral_dev_secret = None


# ---------------------------------------------------------------------------
# Utilidades de cabecera
# ---------------------------------------------------------------------------

def parse_bearer(authorization: Optional[str]) -> str:
    if not authorization or not authorization.strip():
        raise AuthError(
            "Falta la cabecera Authorization: Bearer <token>",
            reason="missing",
        )
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise AuthError(
            "Cabecera Authorization mal formada (se espera 'Bearer <token>')",
            reason="malformed",
        )
    return parts[1].strip()


def public_auth_config() -> dict[str, Any]:
    """Config pública para el frontend. NUNCA incluye secretos de firma."""
    mode = settings.AUTH_MODE
    out: dict[str, Any] = {"auth_mode": mode}
    if mode == "supabase" and settings.SUPABASE_URL:
        out["supabase"] = {
            "url": settings.SUPABASE_URL,
            # La anon key es pública por diseño en Supabase (cliente navegador);
            # la seguridad reside en el JWT de sesión y las políticas RLS.
            "anon_key": settings.SUPABASE_ANON_KEY or None,
        }
    if mode in ("local", "test"):
        out["dev_token_url"] = "/api/v1/dev/token"
    return out
