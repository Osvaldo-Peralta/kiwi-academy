"""Tests de autenticación y autorización — Iteración 1 (QA-01, QA-02, AUTH-04).

Cubren, con emisor simulado (sin servicios externos):
- JWT válido / expirado / firma inválida / alg=none / alg confusion (HS512)
- iss / aud / claims requeridos / sub inválido o fuera de namespace
- Cabecera ausente o mal formada
- X-User-Id YA NO autentica ni influye (evidencia anti-suplantación)
- Aislamiento entre usuarios (lectura/escritura/reportes)
- Modo supabase sin configurar -> 503 fail-closed (públicos siguen vivos)
- Modos local/test: emisor dev, namespace dev-*, rechazo cruzado entre modos
- JWKS ES256 real: clave EC generada en el test, servida por http local
- Lecciones draft -> 404 incluso con token válido (AUTH-04)
"""
import base64
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from backend.app.api.v1.deps import get_service
from backend.app.config import settings
from backend.app.core.auth import TEST_JWT_SECRET, reset_verifier
from backend.app.core.services import LearningService
from backend.app.main import app
from backend.app.storage.json_store import JsonProgressStore
from backend.app.storage.repositories import ContentRepository, ProgressRepository

SB_URL = "https://refprueba.supabase.co"
SB_SECRET = "super-secret-de-prueba-hs256"
USER_A = "5b6f1a2e-aaaa-4bbb-8ccc-000000000001"
USER_B = "5b6f1a2e-aaaa-4bbb-8ccc-000000000002"
PROGRESS = "/api/v1/progress"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def sb_token(sub=USER_A, secret=SB_SECRET, iss=None, aud="authenticated",
             exp_delta=3600, iat_delta=0, alg="HS256", url=SB_URL, drop=()):
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub, "aud": aud, "iss": iss or f"{url}/auth/v1",
        "iat": now + timedelta(seconds=iat_delta),
        "exp": now + timedelta(seconds=exp_delta),
        "role": "authenticated",
    }
    for k in drop:
        payload.pop(k, None)
    return jwt.encode(payload, secret, algorithm=alg)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def b64url(obj: dict) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def none_alg_token(sub=USER_A):
    """Token alg=none fabricado a mano (ataque clásico)."""
    header = b64url({"alg": "none", "typ": "JWT"})
    now = int(datetime.now(timezone.utc).timestamp())
    payload = b64url({
        "sub": sub, "aud": "authenticated", "iss": f"{SB_URL}/auth/v1",
        "iat": now, "exp": now + 3600,
    })
    return f"{header}.{payload}."


def configure_supabase(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "supabase")
    monkeypatch.setattr(settings, "SUPABASE_URL", SB_URL)
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", SB_SECRET)
    monkeypatch.setattr(settings, "JWT_MODE", "auto")
    reset_verifier()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    service = LearningService(
        content=ContentRepository(),
        progress=ProgressRepository(JsonProgressStore(root=tmp_path)),
    )
    app.dependency_overrides[get_service] = lambda: service
    configure_supabase(monkeypatch)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    reset_verifier()


def err(resp):
    return resp.json()["error"]


# ---------------------------------------------------------------------------
# QA-01 · JWT válido / inválido / ausente (modo supabase, HS256)
# ---------------------------------------------------------------------------

def test_jwt_valido_da_acceso_con_identidad_del_sub(client):
    r = client.get(PROGRESS, headers=bearer(sb_token(sub=USER_A)))
    assert r.status_code == 200
    assert r.json()["user_id"] == USER_A          # identidad = sub, nada más


def test_jwt_ausente_401(client):
    r = client.get(PROGRESS)
    assert r.status_code == 401
    assert err(r)["reason"] == "missing"


def test_cabecera_mal_formada_401(client):
    for bad in ["Token abc", "Bearer", "Bearer ", "basic dXNlcjpwYXNz"]:
        r = client.get(PROGRESS, headers={"Authorization": bad})
        assert r.status_code == 401, bad
        assert err(r)["reason"] == "malformed", bad


def test_jwt_basura_401(client):
    r = client.get(PROGRESS, headers=bearer("esto.no.es.un.jwt"))
    assert r.status_code == 401 and err(r)["reason"] == "invalid"


def test_jwt_expirado_401(client):
    r = client.get(PROGRESS, headers=bearer(sb_token(exp_delta=-3600)))
    assert r.status_code == 401 and err(r)["reason"] == "expired"


def test_firma_invalida_401(client):
    r = client.get(PROGRESS, headers=bearer(sb_token(secret="otro-secreto")))
    assert r.status_code == 401 and err(r)["reason"] == "invalid"


def test_alg_none_rechazado(client):
    r = client.get(PROGRESS, headers=bearer(none_alg_token()))
    assert r.status_code == 401 and err(r)["reason"] == "invalid"


def test_alg_confusion_hs512_rechazado(client):
    # mismo secreto, algoritmo distinto al fijado (HS256): debe rechazarse
    tok = sb_token(alg="HS512")
    r = client.get(PROGRESS, headers=bearer(tok))
    assert r.status_code == 401 and err(r)["reason"] == "invalid"


def test_iss_incorrecto_401(client):
    r = client.get(PROGRESS, headers=bearer(sb_token(iss="https://otro.supabase.co/auth/v1")))
    assert r.status_code == 401 and err(r)["reason"] == "invalid"


def test_aud_anon_401(client):
    # la anon key (aud=anon) NO es identidad de usuario
    r = client.get(PROGRESS, headers=bearer(sb_token(aud="anon")))
    assert r.status_code == 401 and err(r)["reason"] == "invalid"


@pytest.mark.parametrize("claim", ["exp", "iat", "iss", "aud", "sub"])
def test_claims_requeridos(client, claim):
    # H-02: iat incluido — el contrato documentado exige los 5 claims
    r = client.get(PROGRESS, headers=bearer(sb_token(drop=(claim,))))
    assert r.status_code == 401


def test_iat_futuro_fuera_de_margen_401(client):
    # H-02: iat muy adelantado (reloj manipulado / token pre-fechado)
    r = client.get(PROGRESS, headers=bearer(sb_token(iat_delta=3600)))
    assert r.status_code == 401
    assert err(r)["reason"] == "iat_in_future"


def test_iat_futuro_dentro_de_margen_ok(client):
    # H-02: skew de reloj dentro del leeway (30 s) -> aceptado
    r = client.get(PROGRESS, headers=bearer(sb_token(iat_delta=10)))
    assert r.status_code == 200


def test_sub_peligroso_rechazado(client):
    # firma válida pero sub con caracteres de path traversal -> 401
    for bad_sub in ["../etc/passwd", "con espacios", "a" * 65, ""]:
        r = client.get(PROGRESS, headers=bearer(sb_token(sub=bad_sub)))
        assert r.status_code == 401, bad_sub
        assert err(r)["reason"] == "invalid_subject", bad_sub


def test_endpoints_publicos_no_exigen_token(client):
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/v1/config").status_code == 200
    assert client.get("/api/v1/courses").status_code == 200
    r = client.get("/api/v1/courses/soc-n1/modules/01-fundamentos/lessons/01-modelo-osi-protocolos")
    assert r.status_code == 200                       # lección publicada: pública
    assert r.json()["progress"]["status"] == "not_started"  # sin sesión: ceros


def test_todos_los_privados_exigen_token(client):
    privados_get = [PROGRESS, "/api/v1/attempts",
                    "/api/v1/courses/soc-n1/attempts/att_inexistente/report"]
    for url in privados_get:
        assert client.get(url).status_code == 401, url
    privados_post = [
        ("/api/v1/courses/soc-n1/modules/01-fundamentos/lessons/01-modelo-osi-protocolos/progress",
         {"theory_completed": True}),
        ("/api/v1/courses/soc-n1/modules/01-fundamentos/lessons/01-modelo-osi-protocolos/exercises/l1-e1/attempt",
         {"answer": "b"}),
        ("/api/v1/courses/soc-n1/exams/fase-1-examen/attempts", {"responses": []}),
    ]
    for url, body in privados_post:
        assert client.post(url, json=body).status_code == 401, url


# ---------------------------------------------------------------------------
# QA-01 · fail-closed: modo supabase sin credenciales -> 503
# ---------------------------------------------------------------------------

def test_supabase_sin_configurar_503_fail_closed(client, monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", "")
    monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", "")
    reset_verifier()
    r = client.get(PROGRESS, headers=bearer(sb_token()))
    assert r.status_code == 503
    assert err(r)["code"] == "auth_not_configured"
    # los públicos siguen funcionando
    assert client.get("/api/v1/courses").status_code == 200
    assert client.get("/healthz").json()["auth_mode"] == "supabase"


def test_config_publico_no_filtra_secretos(client, monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_ANON_KEY", "anon-key-publica")
    r = client.get("/api/v1/config")
    body = r.json()
    assert body["auth_mode"] == "supabase"
    assert body["supabase"]["url"] == SB_URL
    assert body["supabase"]["anon_key"] == "anon-key-publica"
    raw = json.dumps(body)
    assert SB_SECRET not in raw           # el secreto de firma jamás sale
    assert "dev_token_url" not in body    # emisor dev inexistente en supabase


# ---------------------------------------------------------------------------
# QA-02 · suplantación: X-User-Id eliminado + aislamiento entre usuarios
# ---------------------------------------------------------------------------

def test_x_user_id_ya_no_autentica(client):
    # La cabecera que antes daba identidad ya no sirve de nada:
    r = client.get(PROGRESS, headers={"X-User-Id": "victim-user"})
    assert r.status_code == 401 and err(r)["reason"] == "missing"


def test_x_user_id_no_permite_suplantar_con_token_valido(client):
    # Token legítimo de A + cabecera X-User-Id: B -> todo opera sobre A
    headers = {**bearer(sb_token(sub=USER_A)), "X-User-Id": USER_B}
    r = client.post(
        "/api/v1/courses/soc-n1/modules/01-fundamentos/lessons/01-modelo-osi-protocolos/exercises/l1-e1/attempt",
        json={"answer": "b"}, headers=headers)
    assert r.status_code == 200
    prog_b = client.get(PROGRESS, headers=bearer(sb_token(sub=USER_B))).json()
    assert prog_b["overall"]["exercises_mastered"] == 0     # B intacto
    prog_a = client.get(PROGRESS, headers=bearer(sb_token(sub=USER_A))).json()
    assert prog_a["overall"]["exercises_mastered"] == 1     # el intento es de A


def test_aislamiento_lectura_entre_usuarios(client):
    ha, hb = bearer(sb_token(sub=USER_A)), bearer(sb_token(sub=USER_B))
    # A genera actividad
    client.post("/api/v1/courses/soc-n1/modules/01-fundamentos/lessons/01-modelo-osi-protocolos/progress",
                json={"theory_completed": True}, headers=ha)
    client.post("/api/v1/courses/soc-n1/modules/01-fundamentos/lessons/01-modelo-osi-protocolos/exercises/l1-e1/attempt",
                json={"answer": "b"}, headers=ha)
    exam = client.get("/api/v1/courses/soc-n1/exams/fase-1-examen").json()
    sess = client.post("/api/v1/courses/soc-n1/exams/fase-1-examen/sessions", headers=ha).json()
    sub = client.post("/api/v1/courses/soc-n1/exams/fase-1-examen/attempts",
                      json={"session_id": sess["session_id"],
                            "responses": [{"exercise_id": q["id"], "answer": None}
                                          for q in exam["questions"]]}, headers=ha).json()
    # B no ve nada de A
    dash_b = client.get(PROGRESS, headers=hb).json()
    assert dash_b["user_id"] == USER_B
    assert dash_b["overall"]["exams_taken"] == 0
    assert dash_b["recent_attempts"] == []
    att_b = client.get("/api/v1/attempts", headers=hb).json()
    assert att_b["attempts"] == []
    # B no puede leer el reporte del intento de A
    r = client.get(f"/api/v1/courses/soc-n1/attempts/{sub['attempt_id']}/report", headers=hb)
    assert r.status_code == 404
    # A sí puede
    r = client.get(f"/api/v1/courses/soc-n1/attempts/{sub['attempt_id']}/report", headers=ha)
    assert r.status_code == 200


def test_aislamiento_lectura_leccion(client):
    ha = bearer(sb_token(sub=USER_A))
    client.post("/api/v1/courses/soc-n1/modules/01-fundamentos/lessons/01-modelo-osi-protocolos/exercises/l1-e1/attempt",
                json={"answer": "b"}, headers=ha)
    lesson_a = client.get("/api/v1/courses/soc-n1/modules/01-fundamentos/lessons/01-modelo-osi-protocolos",
                          headers=ha).json()
    lesson_b = client.get("/api/v1/courses/soc-n1/modules/01-fundamentos/lessons/01-modelo-osi-protocolos",
                          headers=bearer(sb_token(sub=USER_B))).json()
    assert lesson_a["progress"]["exercises_mastered"] == 1
    assert lesson_b["progress"]["exercises_mastered"] == 0


# ---------------------------------------------------------------------------
# AUTH-04 · lecciones no publicadas
# ---------------------------------------------------------------------------

def test_leccion_draft_404_con_y_sin_token(client):
    url = "/api/v1/courses/soc-n1/modules/02-deteccion/lessons/01-lectura-logs"
    assert client.get(url).status_code == 404                          # anónimo
    assert client.get(url, headers=bearer(sb_token())).status_code == 404  # autenticado
    # la metadata del catálogo sí la muestra (status draft), sin contenido
    cat = client.get("/api/v1/courses/soc-n1/catalog").json()
    mod2 = next(m for m in cat["modules"] if m["id"] == "02-deteccion")
    assert mod2["lessons"][0]["status"] == "draft"
    assert "exercises" not in json.dumps(mod2["lessons"][0])


def test_catalogo_publico_completo(client):
    cat = client.get("/api/v1/courses/soc-n1/catalog").json()
    assert cat["course"]["id"] == "soc-n1"
    assert len(cat["modules"]) == 4
    mod1 = next(m for m in cat["modules"] if m["id"] == "01-fundamentos")
    assert mod1["exam"]["id"] == "fase-1-examen"
    assert mod1["lessons"][0]["exercise_count"] == 8


# ---------------------------------------------------------------------------
# Modos local / test: emisor dev, namespace, rechazo cruzado
# ---------------------------------------------------------------------------

def test_modo_local_flujo_dev_completo(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "local")
    monkeypatch.setattr(settings, "DEV_JWT_SECRET", "dev-secret-local-x")
    reset_verifier()

    cfg = client.get("/api/v1/config").json()
    assert cfg["auth_mode"] == "local" and cfg["dev_token_url"] == "/api/v1/dev/token"

    r = client.post("/api/v1/dev/token", json={"user_id": "dev-osvaldo"})
    assert r.status_code == 200
    tok = r.json()["access_token"]

    me = client.get(PROGRESS, headers=bearer(tok)).json()
    assert me["user_id"] == "dev-osvaldo"

    # user_id fuera del namespace dev- -> rechazo controlado
    r = client.post("/api/v1/dev/token", json={"user_id": "osvaldo"})
    assert r.status_code == 401 and err(r)["reason"] == "invalid_user_id"
    r = client.post("/api/v1/dev/token", json={"user_id": "dev-../../etc"})
    assert r.status_code == 401


def test_dev_token_no_existe_en_modo_supabase(client):
    r = client.post("/api/v1/dev/token", json={"user_id": "dev-hacker"})
    assert r.status_code == 404        # en producción el endpoint no existe


def test_rechazo_cruzado_entre_modos(client, monkeypatch):
    # token del emisor local no vale en modo supabase (aunque el secreto coincidiera)
    tok_local = jwt.encode(
        {"sub": "dev-1", "aud": "authenticated", "iss": "kiwi-local-dev",
         "iat": datetime.now(timezone.utc),
         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        SB_SECRET, algorithm="HS256")   # incluso firmado con el secreto supabase
    r = client.get(PROGRESS, headers=bearer(tok_local))
    assert r.status_code == 401 and err(r)["reason"] == "invalid"

    # token supabase no vale en modo local
    tok_sb = sb_token()
    monkeypatch.setattr(settings, "AUTH_MODE", "local")
    monkeypatch.setattr(settings, "DEV_JWT_SECRET", "dev-secret-local-x")
    reset_verifier()
    r = client.get(PROGRESS, headers=bearer(tok_sb))
    assert r.status_code == 401


def test_modo_local_exige_namespace_dev(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "local")
    monkeypatch.setattr(settings, "DEV_JWT_SECRET", "dev-secret-local-x")
    reset_verifier()
    # token bien firmado para local pero con sub sin prefijo dev-
    tok = jwt.encode(
        {"sub": "5b6f1a2e-uuid-real", "aud": "authenticated", "iss": "kiwi-local-dev",
         "iat": datetime.now(timezone.utc),
         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        "dev-secret-local-x", algorithm="HS256")
    r = client.get(PROGRESS, headers=bearer(tok))
    assert r.status_code == 401 and err(r)["reason"] == "invalid_subject_namespace"


def test_modo_test_con_secreto_fijo(client, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "test")
    reset_verifier()
    tok = jwt.encode(
        {"sub": "dev-fixture", "aud": "authenticated", "iss": "kiwi-test",
         "iat": datetime.now(timezone.utc),
         "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        TEST_JWT_SECRET, algorithm="HS256")
    r = client.get(PROGRESS, headers=bearer(tok))
    assert r.status_code == 200 and r.json()["user_id"] == "dev-fixture"


def test_modo_por_defecto_es_supabase():
    # el default de Settings (sin env) debe ser producción-seguro
    from backend.app.config import Settings
    import os
    sin_env = {k: v for k, v in os.environ.items() if k != "KIWI_AUTH_MODE"}
    old = dict(os.environ)
    try:
        os.environ.clear(); os.environ.update(sin_env)
        s = Settings()
        assert s.AUTH_MODE == "supabase"
    finally:
        os.environ.clear(); os.environ.update(old)


# ---------------------------------------------------------------------------
# JWKS ES256 (camino asimétrico real, sin servicios externos)
# ---------------------------------------------------------------------------

def _jwks_server():
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_numbers()

    def b64(n):
        return base64.urlsafe_b64encode(n.to_bytes(32, "big")).decode().rstrip("=")

    jwks = {"keys": [{"kty": "EC", "crv": "P-256", "kid": "test-key-1",
                      "use": "sig", "alg": "ES256",
                      "x": b64(pub.x), "y": b64(pub.y)}]}
    payload = json.dumps(jwks).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/auth/v1/.well-known/jwks.json":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)
            else:
                self.send_response(404); self.end_headers()

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, key


def test_jwks_es256_valido_y_clave_ajena_rechazada(client, monkeypatch):
    srv, key = _jwks_server()
    try:
        port = srv.server_address[1]
        url = f"http://127.0.0.1:{port}"
        monkeypatch.setattr(settings, "SUPABASE_URL", url)
        monkeypatch.setattr(settings, "SUPABASE_JWT_SECRET", "")   # fuerza modo jwks
        monkeypatch.setattr(settings, "JWT_MODE", "auto")
        reset_verifier()

        now = datetime.now(timezone.utc)
        claims = {"sub": USER_A, "aud": "authenticated", "iss": f"{url}/auth/v1",
                  "iat": now, "exp": now + timedelta(hours=1)}
        tok = jwt.encode(claims, key, algorithm="ES256",
                         headers={"kid": "test-key-1"})
        r = client.get(PROGRESS, headers=bearer(tok))
        assert r.status_code == 200 and r.json()["user_id"] == USER_A

        # token firmado con OTRA clave EC (kid existente) -> 401
        other = ec.generate_private_key(ec.SECP256R1())
        tok_malo = jwt.encode(claims, other, algorithm="ES256",
                              headers={"kid": "test-key-1"})
        r = client.get(PROGRESS, headers=bearer(tok_malo))
        assert r.status_code == 401

        # HS256 NO se acepta en modo jwks (aunque el "secrito" sea la clave pública)
        tok_hs = jwt.encode(claims, "cualquier-cosa", algorithm="HS512")
        assert client.get(PROGRESS, headers=bearer(tok_hs)).status_code == 401
    finally:
        srv.shutdown()
        reset_verifier()
