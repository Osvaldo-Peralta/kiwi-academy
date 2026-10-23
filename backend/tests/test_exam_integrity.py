"""Iteración 2 — QA-03 (evaluación correcta) y QA-04 (integridad del examen).

Cubre:
- Sesiones de examen emitidas por el servidor: obligatorias, de un solo uso,
  no transferibles entre usuarios, reemplazables, expiración por reloj de
  servidor, duración medida por el servidor (campos de cliente ignorados).
- Validación estricta del envío: ids desconocidos, duplicados, faltantes,
  orden incorrecto, tipos de respuesta inválidos por ejercicio → 422
  controlado con detalle.
- Pendiente de revisión: respuestas libres con contenido → status
  pending_review, passed=None, 0 puntos automáticos, cobertura orientativa.
- Revisión humana: allowlist por configuración del servidor (nunca por datos
  del cliente); 403 sin configurar / para no revisores; validación de
  cobertura y rango de puntos; finalización con veredicto definitivo;
  imposibilidad de doble revisión.
"""
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.core.auth import TEST_JWT_SECRET, reset_verifier
from backend.app.core.services import LearningService
from backend.app.main import app
from backend.app.api.v1.deps import get_service
from backend.app.storage.json_store import JsonProgressStore
from backend.app.storage.repositories import ContentRepository, ProgressRepository

COURSE = "soc-n1"
EXAM = "fase-1-examen"
SUBMIT_URL = f"/api/v1/courses/{COURSE}/exams/{EXAM}/attempts"
SESSION_URL = f"/api/v1/courses/{COURSE}/exams/{EXAM}/sessions"


def make_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    return pyjwt.encode(
        {"sub": user_id, "aud": "authenticated", "iss": "kiwi-test",
         "iat": now, "exp": now + timedelta(hours=1)},
        TEST_JWT_SECRET, algorithm="HS256",
    )


def H(user: str) -> dict:
    return {"Authorization": f"Bearer {make_token(user)}"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "test")
    monkeypatch.setattr(settings, "REVIEWER_USERS", "")       # allowlist vacía por defecto
    monkeypatch.setattr(settings, "EXAM_GRACE_SECONDS", 30)
    reset_verifier()
    service = LearningService(
        content=ContentRepository(),
        progress=ProgressRepository(JsonProgressStore(root=tmp_path)),
    )
    app.dependency_overrides[get_service] = lambda: service
    with TestClient(app) as c:
        c.svc = service
        yield c
    app.dependency_overrides.clear()
    reset_verifier()


def full_responses(correct=True) -> list[dict]:
    """Respuestas completas en orden (correctas o todas None)."""
    exam = ContentRepository().get_exam(COURSE, EXAM)
    out = []
    for q in exam.questions:
        if not correct:
            out.append({"exercise_id": q.id, "answer": None})
            continue
        a = q.answer
        if q.type == "free_text":
            out.append({"exercise_id": q.id, "answer": a["model_answer"]})
        elif q.type == "fill_blank" and "blanks" in a:
            out.append({"exercise_id": q.id, "answer": [b["accepted"][0] for b in a["blanks"]]})
        elif q.type == "fill_blank":
            out.append({"exercise_id": q.id, "answer": a["accepted"][0]})
        else:
            out.append({"exercise_id": q.id, "answer": a})
    return out


def start(client, user="dev-student") -> str:
    r = client.post(SESSION_URL, headers=H(user))
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


# ---------------------------------------------------------------------------
# QA-04 · Sesiones y temporizador (EVAL-02)
# ---------------------------------------------------------------------------

def test_envio_sin_sesion_rechazado(client):
    r = client.post(SUBMIT_URL, json={"session_id": "inventada", "responses": full_responses(False)},
                    headers=H("dev-student"))
    assert r.status_code == 409
    assert r.json()["error"]["reason"] == "session_invalid"


def test_envio_sin_campo_sesion_422(client):
    r = client.post(SUBMIT_URL, json={"responses": full_responses(False)}, headers=H("dev-student"))
    assert r.status_code == 422   # session_id obligatorio en el modelo


def test_sesion_ajena_rechazada(client):
    sid = start(client, "dev-student")
    r = client.post(SUBMIT_URL, json={"session_id": sid, "responses": full_responses(False)},
                    headers=H("dev-otro"))
    assert r.status_code == 409 and r.json()["error"]["reason"] == "session_invalid"


def test_sesion_de_un_solo_uso(client):
    """Iteración 3: reenviar con la sesión consumida NO crea un segundo
    intento — reproduce el resultado original (idempotencia). Antes de la
    Iteración 3 este caso devolvía 409; el cambio de contrato es parte del
    alcance autorizado (DATA-02)."""
    sid = start(client)
    ok = client.post(SUBMIT_URL, json={"session_id": sid, "responses": full_responses(False)},
                     headers=H("dev-student"))
    assert ok.status_code == 200
    assert ok.json()["idempotent_replay"] is False
    again = client.post(SUBMIT_URL, json={"session_id": sid, "responses": full_responses(False)},
                        headers=H("dev-student"))
    assert again.status_code == 200
    assert again.json()["idempotent_replay"] is True
    assert again.json()["attempt_id"] == ok.json()["attempt_id"]


def test_nueva_sesion_reemplaza_anterior(client):
    s1 = start(client)
    s2 = start(client)
    r1 = client.post(SUBMIT_URL, json={"session_id": s1, "responses": full_responses(False)},
                     headers=H("dev-student"))
    assert r1.status_code == 409           # s1 fue reemplazada
    r2 = client.post(SUBMIT_URL, json={"session_id": s2, "responses": full_responses(False)},
                     headers=H("dev-student"))
    assert r2.status_code == 200           # s2 es la vigente


def test_expiracion_por_reloj_de_servidor(client, monkeypatch):
    # gracia negativa => deadline ya pasado en el momento de iniciar
    monkeypatch.setattr(settings, "EXAM_GRACE_SECONDS", -100_000)
    sid = start(client)
    r = client.post(SUBMIT_URL, json={"session_id": sid, "responses": full_responses(False)},
                    headers=H("dev-student"))
    assert r.status_code == 409
    assert r.json()["error"]["reason"] == "exam_expired"
    # la sesión expirada quedó invalidada: ni reintentando funciona
    r2 = client.post(SUBMIT_URL, json={"session_id": sid, "responses": full_responses(False)},
                     headers=H("dev-student"))
    assert r2.status_code == 409 and r2.json()["error"]["reason"] == "session_invalid"


def test_tiempo_medido_por_servidor_y_campos_cliente_ignorados(client):
    sid = start(client)
    payload = {
        "session_id": sid,
        "responses": full_responses(False),
        # campos del contrato antiguo: deben IGNORARSE (pydantic extra=ignore)
        "started_at": "1999-01-01T00:00:00+00:00",
        "duration_seconds": -999999,
    }
    r = client.post(SUBMIT_URL, json=payload, headers=H("dev-student"))
    assert r.status_code == 200
    att_id = r.json()["attempt_id"]
    stored = client.svc.progress.store.get_attempt("dev-student", att_id)
    # duración real medida por el servidor (segundos, no el -999999 del cliente)
    assert 0 <= stored["duration_seconds"] < 120
    # started_at del servidor es "ahora", no 1999
    assert stored["started_at"].startswith(str(datetime.now(timezone.utc).year))


# ---------------------------------------------------------------------------
# QA-04 · Validación estricta del envío (EVAL-03)
# ---------------------------------------------------------------------------

def _submit(client, responses, user="dev-student"):
    sid = start(client, user)
    return client.post(SUBMIT_URL, json={"session_id": sid, "responses": responses},
                       headers=H(user))


def test_ids_desconocidos_422(client):
    resp = full_responses(False) + [{"exercise_id": "ex-999-falso", "answer": None}]
    r = _submit(client, resp)
    assert r.status_code == 422
    problems = r.json()["error"]["problems"]
    assert any(p["problem"] == "unknown_ids" for p in problems)


def test_ids_duplicados_422(client):
    resp = full_responses(False)
    resp.append(dict(resp[0]))
    r = _submit(client, resp)
    assert r.status_code == 422
    assert any(p["problem"] == "duplicate_ids" for p in r.json()["error"]["problems"])


def test_respuestas_faltantes_422(client):
    resp = full_responses(False)[:-1]     # falta la última
    r = _submit(client, resp)
    assert r.status_code == 422
    assert any(p["problem"] == "missing_ids" for p in r.json()["error"]["problems"])


def test_orden_incorrecto_422(client):
    resp = full_responses(False)
    resp[0], resp[1] = resp[1], resp[0]
    r = _submit(client, resp)
    assert r.status_code == 422
    assert any(p["problem"] == "wrong_order" for p in r.json()["error"]["problems"])


def test_tipos_de_respuesta_invalidos_422(client):
    exam = ContentRepository().get_exam(COURSE, EXAM)
    q_by_id = {q.id: q for q in exam.questions}
    mc = next(q.id for q in exam.questions if q.type == "multiple_choice")
    ms = next(q.id for q in exam.questions if q.type == "multiple_select")
    ft = next(q.id for q in exam.questions if q.type == "free_text")
    mt = next(q.id for q in exam.questions if q.type == "matching")
    od = next(q.id for q in exam.questions if q.type == "ordering")

    casos = [
        (mc, ["a"], "mc como lista"),
        (mc, "zzz", "mc opción inexistente"),
        (ms, "a", "ms como string"),
        (ms, ["a", "a"], "ms duplicados"),
        (ms, ["no-existe"], "ms id inválido"),
        (ft, {"respuesta": "dict"}, "ft no string"),
        (ft, "x" * 20001, "ft excede máximo"),
        (mt, {list(q_by_id[mt].pairs)[0].left_id: "right-inexistente"}, "mt right inválido"),
        (mt, {"left-inexistente": list(q_by_id[mt].pairs)[0].right_id}, "mt left inválido"),
        (od, [q_by_id[od].items[0].id, q_by_id[od].items[0].id], "od duplicados"),
    ]
    for qid, bad, etiqueta in casos:
        resp = full_responses(False)
        idx = next(i for i, r in enumerate(resp) if r["exercise_id"] == qid)
        resp[idx]["answer"] = bad
        r = _submit(client, resp)
        assert r.status_code == 422, f"{etiqueta}: esperado 422, obtenido {r.status_code}"
        assert any(p.get("exercise_id") == qid for p in r.json()["error"]["problems"]), etiqueta


def test_respuestas_validas_parciales_aceptadas(client):
    # matching {} y listas vacías = "sin responder", no malformado
    exam = ContentRepository().get_exam(COURSE, EXAM)
    mt = next(q.id for q in exam.questions if q.type == "matching")
    ms = next(q.id for q in exam.questions if q.type == "multiple_select")
    resp = full_responses(False)
    for r in resp:
        if r["exercise_id"] == mt:
            r["answer"] = {}
        if r["exercise_id"] == ms:
            r["answer"] = []
    out = _submit(client, resp)
    assert out.status_code == 200


# ---------------------------------------------------------------------------
# QA-03 · Pendiente de revisión humana (EVAL-01)
# ---------------------------------------------------------------------------

def test_respuesta_libre_deja_el_examen_pendiente(client):
    resp = full_responses(correct=True)   # incluye free_text con model_answer
    r = _submit(client, resp)
    assert r.status_code == 200
    s = r.json()["score"]
    assert s["status"] == "pending_review"
    assert s["passed"] is None                     # NI aprobado NI reprobado
    assert s["final_percent"] is None
    assert s["pending_review"] == 1
    assert s["pending_points_total"] == 18         # puntos de ex-15
    assert s["points_earned_auto"] == s["points_total"] - 18  # automática perfecta
    fr = next(x for x in r.json()["results"] if x["review_status"] == "pending")
    assert fr["points_earned"] == 0.0              # keywords NO suman puntos
    assert "ORIENTATIVA" in fr["feedback"]
    assert "no es prueba de comprensión" in fr["feedback"].lower() or "NO es prueba" in fr["feedback"]
    assert "PROVISIONAL" in r.json()["feedback_summary"]


def test_examen_sin_respuestas_libres_contestadas_se_autocalifica(client):
    r = _submit(client, full_responses(correct=False))
    s = r.json()["score"]
    assert s["status"] == "auto_graded"
    assert s["passed"] is False                    # veredicto definitivo inmediato
    assert s["final_percent"] == s["provisional_percent"]


def test_dashboard_no_cuenta_pendiente_como_aprobado(client):
    _submit(client, full_responses(correct=True))    # pending_review
    dash = client.get("/api/v1/progress", headers=H("dev-student")).json()
    course = next(c for c in dash["courses"] if c["id"] == COURSE)
    mod1 = next(m for m in course["modules"] if m["id"] == "01-fundamentos")
    assert mod1["exam"]["passed"] is False           # aún no aprobado
    assert mod1["exam"]["last_status"] == "pending_review"
    assert dash["overall"]["exams_passed"] == 0


# ---------------------------------------------------------------------------
# QA-03 · Revisión humana autorizada
# ---------------------------------------------------------------------------

def _pending_attempt(client) -> str:
    r = _submit(client, full_responses(correct=True))
    return r.json()["attempt_id"]


def test_revision_bloqueada_sin_allowlist(client):
    att = _pending_attempt(client)
    r = client.post(f"/api/v1/reviews/attempts/{att}",
                    json={"reviews": [{"exercise_id": "ex-15", "points_earned": 10}]},
                    headers=H("dev-student"))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "permission_denied"


def test_revision_solo_por_allowlist_del_servidor(client, monkeypatch):
    att = _pending_attempt(client)
    monkeypatch.setattr(settings, "REVIEWER_USERS", "dev-reviewer")
    # propietario (identidad válida, no revisor) -> 403
    r = client.post(f"/api/v1/reviews/attempts/{att}",
                    json={"reviews": [{"exercise_id": "ex-15", "points_earned": 10}]},
                    headers=H("dev-student"))
    assert r.status_code == 403
    # tercero -> 403
    r = client.post(f"/api/v1/reviews/attempts/{att}",
                    json={"reviews": [{"exercise_id": "ex-15", "points_earned": 10}]},
                    headers=H("dev-intruso"))
    assert r.status_code == 403
    # el claim del cliente no otorga permisos: cabecera X-User-Id ignorada
    r = client.post(f"/api/v1/reviews/attempts/{att}",
                    json={"reviews": [{"exercise_id": "ex-15", "points_earned": 10}]},
                    headers={**H("dev-intruso"), "X-User-Id": "dev-reviewer"})
    assert r.status_code == 403
    # revisor autorizado -> 200
    r = client.post(f"/api/v1/reviews/attempts/{att}",
                    json={"reviews": [{"exercise_id": "ex-15", "points_earned": 10}],
                          "comment": "Bien."},
                    headers=H("dev-reviewer"))
    assert r.status_code == 200
    assert r.json()["score"]["status"] == "reviewed"


def test_revision_valida_cobertura_y_rango(client, monkeypatch):
    att = _pending_attempt(client)
    monkeypatch.setattr(settings, "REVIEWER_USERS", "dev-reviewer")
    rh = H("dev-reviewer")
    # cobertura incompleta (falta la pendiente) -> 422
    r = client.post(f"/api/v1/reviews/attempts/{att}", json={"reviews": []}, headers=rh)
    assert r.status_code == 422
    # id que no está pendiente -> 422
    r = client.post(f"/api/v1/reviews/attempts/{att}",
                    json={"reviews": [{"exercise_id": "ex-01", "points_earned": 5}]}, headers=rh)
    assert r.status_code == 422
    # puntos fuera de rango -> 422
    r = client.post(f"/api/v1/reviews/attempts/{att}",
                    json={"reviews": [{"exercise_id": "ex-15", "points_earned": 99}]}, headers=rh)
    assert r.status_code == 422
    r = client.post(f"/api/v1/reviews/attempts/{att}",
                    json={"reviews": [{"exercise_id": "ex-15", "points_earned": -1}]}, headers=rh)
    assert r.status_code == 422
    # el intento SIGUE pendiente tras revisiones rechazadas
    stored = client.svc.progress.store.get_attempt("dev-student", att)
    assert stored["score"]["status"] == "pending_review"
    assert stored["score"]["passed"] is None


def test_revision_finaliza_con_veredicto_definitivo(client, monkeypatch):
    att = _pending_attempt(client)
    monkeypatch.setattr(settings, "REVIEWER_USERS", "dev-reviewer")
    out = client.post(f"/api/v1/reviews/attempts/{att}", json={
        "reviews": [{"exercise_id": "ex-15", "points_earned": 18,
                     "feedback": "Reconstrucción completa del ataque."}],
        "comment": "Excelente desempeño.",
    }, headers=H("dev-reviewer")).json()
    s = out["score"]
    assert s["status"] == "reviewed"
    assert s["points_earned"] == s["points_total"]          # automática perfecta + 18 revisados
    assert s["final_percent"] == 100.0
    assert s["passed"] is True
    assert out["review"]["reviewer"] == "dev-reviewer"
    assert out["review"]["question_feedback"]["ex-15"]

    # agregados del propietario actualizados
    dash = client.get("/api/v1/progress", headers=H("dev-student")).json()
    course = next(c for c in dash["courses"] if c["id"] == COURSE)
    mod1 = next(m for m in course["modules"] if m["id"] == "01-fundamentos")
    assert mod1["exam"]["passed"] is True
    assert mod1["exam"]["best_percent"] == 100.0

    # doble revisión -> 409
    r = client.post(f"/api/v1/reviews/attempts/{att}", json={
        "reviews": [{"exercise_id": "ex-15", "points_earned": 0}]}, headers=H("dev-reviewer"))
    assert r.status_code == 409 and r.json()["error"]["reason"] == "not_pending"


def test_revision_con_puntos_bajos_reprueba(client, monkeypatch):
    """La revisión humana puede REPROBAR: el veredicto definitivo respeta sus puntos."""
    resp = full_responses(correct=True)
    # además fallamos una automática para bajar el provisional
    exam = ContentRepository().get_exam(COURSE, EXAM)
    mc = next(q.id for q in exam.questions if q.type == "multiple_choice")
    for r in resp:
        if r["exercise_id"] == mc:
            opts = [o.id for o in next(q for q in exam.questions if q.id == mc).options]
            r["answer"] = next(o for o in opts if o != next(q for q in exam.questions if q.id == mc).answer)
    sub = _submit(client, resp).json()
    assert sub["score"]["status"] == "pending_review"

    monkeypatch.setattr(settings, "REVIEWER_USERS", "dev-reviewer")
    out = client.post(f"/api/v1/reviews/attempts/{sub['attempt_id']}", json={
        "reviews": [{"exercise_id": "ex-15", "points_earned": 0,
                     "feedback": "Lista acciones pero no identifica la fase de C2."}],
    }, headers=H("dev-reviewer")).json()
    s = out["score"]
    assert s["status"] == "reviewed"
    assert s["final_percent"] < 100.0
    # con automática casi perfecta (-8 pts mc) y libre en 0: ¿pasa el umbral 70?
    assert s["passed"] is (s["final_percent"] >= 70)


def test_review_data_solo_revisores(client, monkeypatch):
    att = _pending_attempt(client)
    # sin allowlist: ni el propietario
    assert client.get(f"/api/v1/reviews/attempts/{att}", headers=H("dev-student")).status_code == 403
    monkeypatch.setattr(settings, "REVIEWER_USERS", "dev-reviewer")
    assert client.get(f"/api/v1/reviews/attempts/{att}", headers=H("dev-student")).status_code == 403
    data = client.get(f"/api/v1/reviews/attempts/{att}", headers=H("dev-reviewer")).json()
    fq = data["free_text_questions"][0]
    assert fq["exercise_id"] == "ex-15"
    assert fq["model_answer"] and fq["keywords"]        # el revisor ve la respuesta modelo
    assert fq["submitted"]                              # y la respuesta del estudiante
    # intento inexistente -> 404
    assert client.get("/api/v1/reviews/attempts/att_nope", headers=H("dev-reviewer")).status_code == 404


def test_review_data_no_explota_con_intentos_de_leccion(client, monkeypatch):
    # un intento de ejercicio de lección no es revisable (solo exámenes)
    monkeypatch.setattr(settings, "REVIEWER_USERS", "dev-reviewer")
    client.post(f"/api/v1/courses/{COURSE}/modules/01-fundamentos/lessons/01-modelo-osi-protocolos/exercises/l1-e8/attempt",
                json={"answer": "dns tunneling: aislar y bloquear"}, headers=H("dev-student"))
    atts = client.get("/api/v1/attempts", headers=H("dev-student")).json()["attempts"]
    lesson_att = next(a["id"] for a in atts if a["scope"]["type"] == "lesson")
    r = client.get(f"/api/v1/reviews/attempts/{lesson_att}", headers=H("dev-reviewer"))
    assert r.status_code == 422
