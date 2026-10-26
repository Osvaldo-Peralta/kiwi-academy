"""Iteración 3 — QA-05 (consistencia transaccional) y QA-06 (propiedad e
idempotencia).

Pruebas de fallos inducidos, reintentos, concurrencia y recuperación:
- SQL: fallo a mitad del flujo => ROLLBACK total (cero estados parciales) y
  reintento limpio con la misma sesión.
- JSON (single-proceso): fallo => estado parcial posible; la reparación
  (rebuild desde intentos, fuente de verdad) restaura la consistencia.
- Idempotencia: reenvío con la misma sesión => replay del resultado original,
  CERO duplicados (secuencial y concurrente).
- Concurrencia de progreso: N hilos => contadores exactos (sin pérdidas).
- Propiedad: guardar un informe NUNCA reasigna/sobrescribe el de otro usuario.
"""
import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.core.auth import TEST_JWT_SECRET, reset_verifier
from backend.app.core.services import LearningService, exam_key, lesson_key
from backend.app.main import app
from backend.app.api.v1.deps import get_service
from backend.app.storage.json_store import (
    _lock_for,
    _STORE_LOCK,
    JsonProgressStore,
    StoreDegradedError,
    TransactionRollbackError,
)
from backend.app.storage.repositories import ContentRepository, ProgressRepository
from backend.app.storage.sql_store import SqlProgressStore

COURSE = "soc-n1"
MOD1 = "01-fundamentos"
LESSON1 = "01-modelo-osi-protocolos"
EXAM = "fase-1-examen"
EXAM_KEY = exam_key(COURSE, EXAM)


def make_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    return pyjwt.encode(
        {"sub": user_id, "aud": "authenticated", "iss": "kiwi-test",
         "iat": now, "exp": now + timedelta(hours=1)},
        TEST_JWT_SECRET, algorithm="HS256",
    )


def H(user="dev-student"):
    return {"Authorization": f"Bearer {make_token(user)}"}


def make_store(kind, tmp_path):
    if kind == "json":
        return JsonProgressStore(root=tmp_path)
    return SqlProgressStore(f"sqlite:///{tmp_path}/integrity.db")


def make_service(store):
    return LearningService(content=ContentRepository(), progress=ProgressRepository(store))


def start_session(service, user="dev-student"):
    return service.start_exam_session(user, COURSE, EXAM)["session_id"]


def blank_responses():
    exam = ContentRepository().get_exam(COURSE, EXAM)
    return [{"exercise_id": q.id, "answer": None} for q in exam.questions]


# ---------------------------------------------------------------------------
# QA-05 · Fallos inducidos: SQL rollback total vs JSON reparación
# ---------------------------------------------------------------------------

def test_sql_fallo_a_media_transaccion_rollback_total(tmp_path, monkeypatch):
    store = make_store("sqlite", tmp_path)
    service = make_service(store)
    sid = start_session(service)

    original = store.save_progress
    calls = {"n": 0}

    def flaky_save_progress(user_id, progress):
        calls["n"] += 1
        raise RuntimeError("fallo inducido a mitad de la transacción")

    monkeypatch.setattr(store, "save_progress", flaky_save_progress)
    with pytest.raises(RuntimeError):
        service.submit_exam("dev-student", COURSE, EXAM, sid, blank_responses())

    # ROLLBACK: ni intento, ni agregados, ni sesión consumida
    assert store.list_attempts("dev-student") == []
    prog = store.load_progress("dev-student")
    assert prog.get("exams", {}).get(EXAM_KEY) is None
    session = prog["exam_sessions"][EXAM_KEY]
    assert "consumed_attempt" not in session

    # reintento con la MISMA sesión tras sanar el fallo -> exactamente 1 intento
    monkeypatch.setattr(store, "save_progress", original)
    out = service.submit_exam("dev-student", COURSE, EXAM, sid, blank_responses())
    assert out["idempotent_replay"] is False
    atts = [a for a in store.list_attempts("dev-student") if a["scope"]["type"] == "exam"]
    assert len(atts) == 1


def test_json_fallo_a_media_transaccion_rollback_total(tmp_path, monkeypatch):
    """Corrección de auditoría (punto 1): el store JSON ya NO deja estados
    parciales ante un fallo a mitad de la operación compuesta — el diario
    copy-on-write revierte intento, progreso y reporte."""
    store = make_store("json", tmp_path)
    service = make_service(store)
    sid = start_session(service)

    original = store.save_report

    def flaky_save_report(*a, **k):
        raise RuntimeError("fallo inducido en el último paso del flujo")

    monkeypatch.setattr(store, "save_report", flaky_save_report)
    with pytest.raises(RuntimeError):
        service.submit_exam("dev-student", COURSE, EXAM, sid, blank_responses())

    # ROLLBACK: ni intento, ni agregados, ni sesión consumida
    assert store.list_attempts("dev-student") == []
    prog = store.load_progress("dev-student")
    assert prog.get("exams", {}).get(EXAM_KEY) is None
    session = prog["exam_sessions"][EXAM_KEY]
    assert "consumed_attempt" not in session

    # reintento limpio con la misma sesión tras sanar el fallo -> 1 intento
    monkeypatch.setattr(store, "save_report", original)
    out = service.submit_exam("dev-student", COURSE, EXAM, sid, blank_responses())
    assert out["idempotent_replay"] is False
    atts = [a for a in store.list_attempts("dev-student") if a["scope"]["type"] == "exam"]
    assert len(atts) == 1


def test_json_recuperacion_automatica_no_necesaria_tras_rollback(tmp_path, monkeypatch):
    """El estado tras el fallo es consistente SIN intervención: no hace falta
    repair_progress.py (que queda como mecanismo adicional ante catástrofes
    externas, p. ej. muerte del proceso)."""
    store = make_store("json", tmp_path)
    service = make_service(store)
    sid = start_session(service)

    def flaky(user_id, progress):
        raise RuntimeError("fallo inducido en progreso")

    monkeypatch.setattr(store, "save_progress", flaky)
    with pytest.raises(RuntimeError):
        service.submit_exam("dev-student", COURSE, EXAM, sid, blank_responses())

    # estado inmediatamente consistente: sin intento huérfano
    assert store.list_attempts("dev-student") == []
    # y el flujo completo siguiente funciona sin ninguna reparación manual
    monkeypatch.setattr(store, "save_progress", JsonProgressStore(root=tmp_path).save_progress)
    out = service.submit_exam("dev-student", COURSE, EXAM, sid, blank_responses())
    prog = store.load_progress("dev-student")
    assert prog["exams"][EXAM_KEY]["attempts"] == 1
    assert prog["exams"][EXAM_KEY]["last_attempt_id"] == out["attempt_id"]


# ---------------------------------------------------------------------------
# QA-06 · Idempotencia de envíos repetidos (la sesión es la clave)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["json", "sqlite"])
def test_reenvio_misma_sesion_reproduce_sin_duplicar(kind, tmp_path):
    store = make_store(kind, tmp_path)
    service = make_service(store)
    sid = start_session(service)

    first = service.submit_exam("dev-student", COURSE, EXAM, sid, blank_responses())
    assert first["idempotent_replay"] is False

    second = service.submit_exam("dev-student", COURSE, EXAM, sid, blank_responses())
    assert second["idempotent_replay"] is True
    assert second["attempt_id"] == first["attempt_id"]
    assert second["score"] == first["score"]
    assert second["report_markdown"].startswith("# Reporte")

    atts = [a for a in store.list_attempts("dev-student") if a["scope"]["type"] == "exam"]
    assert len(atts) == 1                                    # CERO duplicados
    prog = store.load_progress("dev-student")
    assert prog["exams"][EXAM_KEY]["attempts"] == 1          # agregado no duplicado


@pytest.mark.parametrize("kind", ["json", "sqlite"])
def test_doble_envio_concurrente_un_solo_intento(kind, tmp_path):
    store = make_store(kind, tmp_path)
    service = make_service(store)
    sid = start_session(service)
    responses = blank_responses()

    resultados: dict = {}
    barrier = threading.Barrier(2, timeout=15)

    def enviar(nombre):
        def run():
            try:
                barrier.wait()
                resultados[nombre] = service.submit_exam("dev-student", COURSE, EXAM, sid, responses)
            except Exception as exc:  # noqa: BLE001
                resultados[nombre] = exc
        return run

    t1 = threading.Thread(target=enviar("A"))
    t2 = threading.Thread(target=enviar("B"))
    t1.start(); t2.start(); t1.join(20); t2.join(20)
    assert not (t1.is_alive() or t2.is_alive())

    # ambos obtienen resultado OK (uno califica, el otro reproduce) o, en el
    # peor orden posible, uno recibe conflicto controlado; NUNCA dos intentos
    oks = [r for r in resultados.values() if isinstance(r, dict)]
    assert len(oks) >= 1
    ids = {r["attempt_id"] for r in oks}
    assert len(ids) == 1, f"un único intento entre ambos envíos: {ids}"
    atts = [a for a in store.list_attempts("dev-student") if a["scope"]["type"] == "exam"]
    assert len(atts) == 1
    prog = store.load_progress("dev-student")
    assert prog["exams"][EXAM_KEY]["attempts"] == 1


# ---------------------------------------------------------------------------
# QA-05/06 · Concurrencia de progreso: sin actualizaciones perdidas
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["json", "sqlite"])
def test_progreso_concurrente_sin_perdidas(kind, tmp_path, monkeypatch):
    import time as _time
    store = make_store(kind, tmp_path)
    service = make_service(store)

    # Ensanchar la ventana de carrera: si la política de actualización no es
    # atómica, la pérdida se manifiesta de forma determinista. Con el lock
    # correcto (JSON: _STORE_LOCK; SQL: transacción con bloqueo), la pausa es
    # inocua: solo serializa.
    original_load = store.load_progress

    def slow_load(user_id):
        data = original_load(user_id)
        _time.sleep(0.01)
        return data

    monkeypatch.setattr(store, "load_progress", slow_load)

    n_threads, per_thread = 6, 4
    barrier = threading.Barrier(n_threads, timeout=30)
    errors = []

    def worker():
        def run():
            try:
                barrier.wait()
                for _ in range(per_thread):
                    service.attempt_lesson_exercise(
                        "dev-student", COURSE, MOD1, LESSON1, "l1-e1", "b")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        return run

    threads = [threading.Thread(target=worker()) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
        assert not t.is_alive()
    assert not errors, f"errores en hilos: {errors}"

    prog = store.load_progress("dev-student")
    lp = prog["lessons"][lesson_key(COURSE, MOD1, LESSON1)]
    total = n_threads * per_thread
    assert lp["attempts"] == total                 # sin actualizaciones perdidas
    assert lp["exercises_mastered"] == 1           # dominio deduplicado
    atts = store.list_attempts("dev-student")
    assert len(atts) == total                      # cada intento persistido


# ---------------------------------------------------------------------------
# QA-06 · Propiedad de informes ante solicitudes manipuladas
# ---------------------------------------------------------------------------

def test_sql_report_no_reasigna_propiedad(tmp_path):
    store = make_store("sqlite", tmp_path)
    att = {"id": "att_prop", "user_id": "dev-ana", "submitted_at": "2026-01-01T00:00:00+00:00",
           "scope": {"type": "exam"}, "score": {}}
    store.save_attempt("dev-ana", att)
    ref = store.save_report("dev-ana", "att_prop", "report.md", "contenido de ana")
    assert ref is not None

    # atacante intenta sobrescribir el informe de otra usuaria
    denied = store.save_report("dev-mallory", "att_prop", "report.md", "contenido usurpado")
    assert denied is None

    rows = store._execute([
        ("SELECT user_id, content FROM kiwi_reports WHERE attempt_id = ? AND name = ?",
         ("att_prop", "report.md")),
    ])[0]
    assert rows[0][0] == "dev-ana"                  # propiedad intacta
    assert rows[0][1] == "contenido de ana"         # contenido intacto

    # la dueña legítima SÍ puede actualizar su informe
    ok = store.save_report("dev-ana", "att_prop", "report.md", "v2 de ana")
    assert ok is not None


def test_json_report_propiedad_reforzada(tmp_path):
    """Corrección de auditoría (punto 3): no se puede CREAR un reporte sobre
    un intento ajeno o inexistente (antes solo se protegía la sobrescritura)."""
    store = make_store("json", tmp_path)
    att = {"id": "att_prop", "user_id": "dev-ana", "submitted_at": "2026-01-01T00:00:00+00:00",
           "scope": {"type": "exam"}, "score": {}}
    store.save_attempt("dev-ana", att)

    # atacante: el intento no existe en SU directorio -> denegado, nada creado
    assert store.save_report("dev-mallory", "att_prop", "report.md", "usurpado") is None
    reports_mal = store.root / "dev-mallory" / "reports"
    assert not reports_mal.exists() or not list(reports_mal.glob("*"))

    # intento inexistente -> denegado incluso para usuario legítimo
    assert store.save_report("dev-ana", "att_ghost", "report.md", "x") is None

    # la dueña sí puede
    assert store.save_report("dev-ana", "att_prop", "report.md", "contenido de ana") is not None
    assert (store.root / "dev-ana" / "reports" / "att_prop.report.md").read_text(
        encoding="utf-8") == "contenido de ana"


def test_sql_report_no_se_crea_sobre_intento_ajeno(tmp_path):
    """Corrección de auditoría (punto 3, SQL): crear un reporte vinculado a
    un intento ajeno cuando AÚN no existe reporte -> denegado sin efectos."""
    store = make_store("sqlite", tmp_path)
    att = {"id": "att_prop", "user_id": "dev-ana", "submitted_at": "2026-01-01T00:00:00+00:00",
           "scope": {"type": "exam"}, "score": {}}
    store.save_attempt("dev-ana", att)

    assert store.save_report("dev-mallory", "att_prop", "report.md", "usurpado") is None
    rows = store._execute([("SELECT COUNT(*) FROM kiwi_reports", ())])[0]
    assert rows[0][0] == 0                        # ni fila creó

    assert store.save_report("dev-ana", "att_ghost", "report.md", "x") is None   # inexistente
    assert store.save_report("dev-ana", "att_prop", "report.md", "legítimo") is not None
    rows = store._execute([("SELECT user_id, content FROM kiwi_reports", ())])[0]
    assert rows[0][0] == "dev-ana" and rows[0][1] == "legítimo"


# ---------------------------------------------------------------------------
# Recuperación (DATA-06): rebuild completo y preservación de no derivados
# ---------------------------------------------------------------------------

def test_rebuild_restaura_y_preserva_flags(tmp_path):
    store = make_store("json", tmp_path)
    service = make_service(store)

    # actividad real: teoría marcada + ejercicio + examen
    service.mark_lesson_progress("dev-student", COURSE, MOD1, LESSON1, theory_completed=True)
    service.attempt_lesson_exercise("dev-student", COURSE, MOD1, LESSON1, "l1-e1", "b")
    sid = start_session(service)
    service.submit_exam("dev-student", COURSE, EXAM, sid, blank_responses())

    # corrupción inducida de los agregados derivados
    prog = store.load_progress("dev-student")
    prog["exams"] = {}
    lk = lesson_key(COURSE, MOD1, LESSON1)
    prog["lessons"][lk]["mastered_exercises"] = []
    prog["lessons"][lk]["exercises_mastered"] = 0
    prog["lessons"][lk]["attempts"] = 0
    store.save_progress("dev-student", prog)

    service.rebuild_progress_from_attempts("dev-student")
    prog = store.load_progress("dev-student")

    # derivados restaurados con exactitud
    assert prog["exams"][EXAM_KEY]["attempts"] == 1
    lp = prog["lessons"][lk]
    assert lp["exercises_mastered"] == 1 and lp["mastered_exercises"] == ["l1-e1"]
    assert lp["attempts"] == 1
    # NO derivados preservados
    assert lp["theory_completed"] is True
    assert prog.get("activity"), "la actividad se preserva"

    # idempotente
    service.rebuild_progress_from_attempts("dev-student")
    prog2 = store.load_progress("dev-student")
    assert prog2["exams"] == prog["exams"]
    assert prog2["lessons"][lk]["exercises_mastered"] == 1


# ---------------------------------------------------------------------------
# Corrección de auditoría (punto 2): revisión = UNA unidad transaccional
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["json", "sqlite"])
def test_review_fallo_en_efectos_hace_rollback_del_cas(kind, tmp_path, monkeypatch):
    """Si un efecto posterior al CAS falla (agregados/reporte), la transición
    pending->reviewed se REVIERTE: el intento sigue pendiente y la revisión
    puede reintentarse sin reparación manual."""
    monkeypatch.setattr(settings, "REVIEWER_USERS", "dev-reviewer")
    store = make_store(kind, tmp_path)
    service = make_service(store)

    sess = service.start_exam_session("dev-student", COURSE, EXAM)
    exam = ContentRepository().get_exam(COURSE, EXAM)
    responses = [{"exercise_id": q.id,
                  "answer": q.answer["model_answer"] if q.type == "free_text" else None}
                 for q in exam.questions]
    sub = service.submit_exam("dev-student", COURSE, EXAM, sess["session_id"], responses)
    att_id = sub["attempt_id"]
    assert sub["score"]["status"] == "pending_review"

    original = store.save_report

    def flaky(*a, **k):
        raise RuntimeError("fallo inducido en el reporte (último efecto)")

    monkeypatch.setattr(store, "save_report", flaky)
    with pytest.raises(RuntimeError):
        service.review_attempt("dev-reviewer", att_id,
                               [{"exercise_id": "ex-15", "points_earned": 18}])

    # CAS revertido: intento SIGUE pendiente, sin review, agregados intactos
    _, stored = store.find_attempt_any_user(att_id)
    assert stored["score"]["status"] == "pending_review"
    assert stored.get("review") is None
    prog = store.load_progress("dev-student")
    ep = prog["exams"][EXAM_KEY]
    assert ep["last_status"] == "pending_review"
    assert ep.get("passed") is not True

    # recuperación automática: re-revisar funciona sin repair manual
    monkeypatch.setattr(store, "save_report", original)
    out = service.review_attempt("dev-reviewer", att_id,
                                 [{"exercise_id": "ex-15", "points_earned": 18}])
    assert out["score"]["status"] == "reviewed"
    expected_final = round(18 / out["score"]["points_total"] * 100, 1)
    assert out["score"]["final_percent"] == expected_final
    assert out["score"]["passed"] is (expected_final >= 70)
    prog = store.load_progress("dev-student")
    assert prog["exams"][EXAM_KEY]["best_percent"] == expected_final
    assert prog["exams"][EXAM_KEY]["last_status"] == "reviewed"


# ---------------------------------------------------------------------------
# Nivel API: idempotencia visible por HTTP + regresión de autorización
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "test")
    reset_verifier()
    service = make_service(JsonProgressStore(root=tmp_path))
    app.dependency_overrides[get_service] = lambda: service
    with TestClient(app) as c:
        c.svc = service
        yield c
    app.dependency_overrides.clear()
    reset_verifier()


def test_api_reintento_idempotente_y_propiedad(client):
    sess_url = f"/api/v1/courses/{COURSE}/exams/{EXAM}/sessions"
    sub_url = f"/api/v1/courses/{COURSE}/exams/{EXAM}/attempts"
    sid = client.post(sess_url, headers=H()).json()["session_id"]
    body = {"session_id": sid, "responses": blank_responses()}

    r1 = client.post(sub_url, json=body, headers=H())
    assert r1.status_code == 200 and r1.json()["idempotent_replay"] is False
    r2 = client.post(sub_url, json=body, headers=H())
    assert r2.status_code == 200
    assert r2.json()["idempotent_replay"] is True
    assert r2.json()["attempt_id"] == r1.json()["attempt_id"]

    hist = client.get("/api/v1/attempts", headers=H()).json()["attempts"]
    assert len([a for a in hist if a["scope"]["type"] == "exam"]) == 1

    # otro usuario no ve ni replayea el intento ajeno
    r3 = client.post(sub_url, json=body, headers=H("dev-otro"))
    assert r3.status_code == 409                       # sesión ajena -> conflicto
    assert r3.json()["error"]["reason"] == "session_invalid"


# ---------------------------------------------------------------------------
# Corrección PR #4: manejo explícito de fallo de rollback JSON (casos A–D)
# Inyección controlada de fallos (proxy de os.replace acotado al módulo
# json_store): determinista, sin depender de errores reales del filesystem.
# ---------------------------------------------------------------------------

import os as _real_os
import backend.app.storage.json_store as _jsmod


class _OsProxy:
    """Delega todo en el os real salvo replace(), que puede fallar a orden."""
    def __init__(self, real):
        self._real = real
        self.fail_replace = False

    def __getattr__(self, name):
        return getattr(self._real, name)

    def replace(self, *a, **k):
        if self.fail_replace:
            raise OSError(28, "No space left on device (fallo inyectado)")
        return self._real.replace(*a, **k)


def _base_prog(v=1):
    return {"user_id": "dev-u", "lessons": {}, "exams": {}, "activity": {}, "v": v}


def _att(att_id, **extra):
    return {"id": att_id, "user_id": "dev-u", "submitted_at": "2026-01-01T00:00:00+00:00",
            "scope": {"type": "lesson"}, "score": {}, **extra}


# ── Caso A: fallo de operación con rollback EXITOSO ──────────────────────
def test_caso_a_rollback_exitoso_restaura_y_propaga_error_original(tmp_path):
    store = JsonProgressStore(root=tmp_path)
    store.save_progress("dev-u", _base_prog(v=1))

    with pytest.raises(RuntimeError) as ei:
        with store.transaction("dev-u"):
            store.save_attempt("dev-u", _att("att_a"))            # archivo nuevo
            store.save_progress("dev-u", _base_prog(v=2))         # archivo modificado
            raise RuntimeError("fallo original")

    # el error ORIGINAL se propaga tal cual (no envuelto)
    assert type(ei.value) is RuntimeError
    assert str(ei.value) == "fallo original"
    # archivos originales restaurados y nuevos eliminados
    assert store.get_attempt("dev-u", "att_a") is None
    assert store.load_progress("dev-u")["v"] == 1
    # sin fallos de recuperación registrados
    assert store.rollback_failures == []


# ── Caso B: fallo DURANTE la restauración → no se silencia ───────────────
def test_caso_b_fallo_de_restauracion_se_comunica_explicitamente(tmp_path, monkeypatch):
    store = JsonProgressStore(root=tmp_path)
    store.save_progress("dev-u", _base_prog(v=1))

    proxy = _OsProxy(_real_os)
    monkeypatch.setattr(_jsmod, "os", proxy)

    def flaky_report(*a, **k):
        proxy.fail_replace = True    # la restauración de modificados fallará
        raise RuntimeError("fallo original en reporte")

    monkeypatch.setattr(store, "save_report", flaky_report)

    with pytest.raises(TransactionRollbackError) as ei:
        with store.transaction("dev-u"):
            store.save_attempt("dev-u", _att("att_b"))            # nuevo (unlink: restaurable)
            store.save_progress("dev-u", _base_prog(v=2))         # modificado (os.replace: fallará)
            store.save_report("dev-u", "att_b", "report.md", "x")

    err = ei.value
    # 1) conserva la excepción original (encadenada)
    assert isinstance(err.__cause__, RuntimeError)
    assert str(err.__cause__) == "fallo original en reporte"
    # 2) identifica con claridad la recuperación incompleta
    assert "INCOMPLETO" in str(err)
    assert len(err.failures) == 1
    failed_path = err.failures[0][0]
    assert failed_path.endswith("progress.json")
    assert "No space left" in err.failures[0][1]
    # 3) lo restaurable SÍ se restauró (el archivo nuevo se eliminó)
    assert err.restored == [str(store.root / "dev-u" / "attempts" / "att_b.json")]
    assert store.get_attempt("dev-u", "att_b") is None
    # 4) el estado NO se presenta como recuperado: progress sigue en v2 (parcial)
    assert store.load_progress("dev-u")["v"] == 2
    # 5) evidencia registrada para diagnóstico
    assert len(store.rollback_failures) == 1
    rec = store.rollback_failures[0]
    assert rec["user_id"] == "dev-u"
    assert "fallo original en reporte" in rec["original"]
    assert rec["failures"] == err.failures
    # 6) el .rbak queda como evidencia forense/material de recuperación
    assert (store.root / "dev-u" / "progress.json.rbak").exists()


# ── Caso C: varios archivos modificados ──────────────────────────────────
def test_caso_c_varios_archivos_restaurables_y_fallidos(tmp_path, monkeypatch):
    store = JsonProgressStore(root=tmp_path)
    store.save_progress("dev-u", _base_prog(v=1))
    store.save_attempt("dev-u", _att("att_viejo"))     # preexistente -> será modificado

    proxy = _OsProxy(_real_os)
    monkeypatch.setattr(_jsmod, "os", proxy)

    def flaky_report(*a, **k):
        proxy.fail_replace = True
        raise RuntimeError("fallo original")

    monkeypatch.setattr(store, "save_report", flaky_report)

    with pytest.raises(TransactionRollbackError) as ei:
        with store.transaction("dev-u"):
            store.save_attempt("dev-u", _att("att_nuevo"))                  # nuevo
            store.update_attempt("dev-u", _att("att_viejo", score={"x": 1}))  # modificado
            store.save_progress("dev-u", _base_prog(v=2))                   # modificado
            store.save_report("dev-u", "att_nuevo", "report.md", "x")

    err = ei.value
    failed_paths = {p for p, _ in err.failures}
    assert failed_paths == {
        str(store.root / "dev-u" / "progress.json"),
        str(store.root / "dev-u" / "attempts" / "att_viejo.json"),
    }
    # todo lo restaurable quedó restaurado (el archivo nuevo, eliminado)
    assert err.restored == [str(store.root / "dev-u" / "attempts" / "att_nuevo.json")]
    assert store.get_attempt("dev-u", "att_nuevo") is None
    # cada restauración fallida quedó EXPLÍCITAMENTE registrada
    assert len(store.rollback_failures) == 1
    assert {p for p, _ in store.rollback_failures[0]["failures"]} == failed_paths


# ── Caso D (actualizado por auditoría): store DEGRADADO tras rollback
#    incompleto -> escrituras rechazadas hasta recuperación verificada ──────
def test_caso_d_store_degradado_bloquea_hasta_recuperacion_verificada(tmp_path, monkeypatch):
    store = JsonProgressStore(root=tmp_path)
    store.save_progress("dev-u", _base_prog(v=1))

    proxy = _OsProxy(_real_os)
    monkeypatch.setattr(_jsmod, "os", proxy)

    def flaky_report(*a, **k):
        proxy.fail_replace = True
        raise RuntimeError("fallo original")

    monkeypatch.setattr(store, "save_report", flaky_report)

    with pytest.raises(TransactionRollbackError):
        with store.transaction("dev-u"):
            store.save_attempt("dev-u", _att("att_d"))
            store.save_progress("dev-u", _base_prog(v=2))
            store.save_report("dev-u", "att_d", "report.md", "x")

    # 1) estado transaccional definido Y store marcado como degradado
    assert getattr(store._local, "journal", None) is None
    assert store.degraded is not None
    assert store.degraded["user_id"] == "dev-u"
    assert len(store.rollback_failures) == 1

    # 2) mientras está degradado: TODA escritura transaccional se rechaza
    with pytest.raises(StoreDegradedError):
        with store.transaction("dev-u"):
            store.save_progress("dev-u", _base_prog(v=99))
    with pytest.raises(StoreDegradedError):
        store.mutate_progress("dev-u", lambda prog: prog.update(v=99))
    assert store.load_progress("dev-u")["v"] == 2        # nada fue escrito
    # las lecturas siguen disponibles para diagnosticar
    assert store.list_attempts("dev-u") is not None

    # 3) recuperación NO satisfactoria: si el archivo pendiente queda
    #    corrupto, verify_recovery lo detecta y el store SIGUE degradado
    prog_path = store.root / "dev-u" / "progress.json"
    prog_path.write_text("{ json roto a propósito", encoding="utf-8")
    ok, problems = store.verify_recovery()
    assert ok is False
    assert any("progress.json" in pr["path"] for pr in problems)
    assert store.degraded is not None
    with pytest.raises(StoreDegradedError):
        with store.transaction("dev-u"):
            pass

    # 4) recuperación satisfactoria: el operador restaura BYTE A BYTE el
    #    archivo pendiente (desde el .rbak conservado como material de
    #    recuperación). Corrección de auditoría PR #4: la recuperación exacta
    #    se verifica contra el sha256 del contenido original registrado
    #    antes del rollback — un JSON válido con estado parcial no basta —,
    #    y el archivo creado por la transacción fallida debe seguir ausente.
    proxy.fail_replace = False
    rbak = store.root / "dev-u" / "progress.json.rbak"
    assert rbak.exists(), "el .rbak se conserva como material de recuperación"
    prog_path.write_bytes(rbak.read_bytes())             # contenido original (v1)
    assert not (store.root / "dev-u" / "attempts" / "att_d.json").exists()
    ok, problems = store.verify_recovery()
    assert ok is True and problems == []
    assert store.degraded is None                        # escrituras reanudadas

    # 5) tras la recuperación: el store opera con diario fresco (sin mezcla
    #    con la transacción fallida) y el rollback exitoso sigue funcionando
    with store.transaction("dev-u"):
        store.save_progress("dev-u", dict(_base_prog(v=3), marker="tx2"))
    assert store.load_progress("dev-u")["marker"] == "tx2"

    with pytest.raises(ValueError):
        with store.transaction("dev-u"):
            store.save_progress("dev-u", dict(_base_prog(v=3), marker="tx3"))
            raise ValueError("fallo nuevo controlado")
    prog = store.load_progress("dev-u")
    assert prog["marker"] == "tx2" and prog["v"] == 3    # restauró a SU pre-estado
    assert len(store.rollback_failures) == 1             # sin fallos nuevos
    assert getattr(store._local, "journal", None) is None

    # 6) vía documentada alternativa: una instancia nueva (tras reparar los
    #    archivos) no hereda el estado degradado (es estado de instancia)
    store2 = JsonProgressStore(root=tmp_path)
    assert store2.degraded is None


def test_store_degradado_rechaza_escrituras_del_servicio(tmp_path):
    """Regresión a nivel de servicio: con el store degradado, los flujos de
    negocio que escriben (marcar progreso, iniciar sesión de examen) se
    rechazan con StoreDegradedError; tras verify_recovery se reanudan."""
    store = make_store("json", tmp_path)
    service = make_service(store)
    store._degraded = {
        "when": "2026-10-24T00:00:00+00:00", "user_id": "dev-student",
        "original": "RuntimeError: simulado", "restored": [],
        "failures": [("ruta", "OSError: simulado")], "checks": [],
    }
    with pytest.raises(StoreDegradedError):
        service.mark_lesson_progress("dev-student", COURSE, MOD1, LESSON1,
                                     theory_completed=True)
    with pytest.raises(StoreDegradedError):
        service.start_exam_session("dev-student", COURSE, EXAM)

    ok, problems = store.verify_recovery()      # checks vacíos (simulado)
    assert ok is True and problems == []
    lp = service.mark_lesson_progress("dev-student", COURSE, MOD1, LESSON1,
                                      theory_completed=True)
    assert lp["theory_completed"] is True       # servicio reanudado


# ---------------------------------------------------------------------------
# Corrección FINAL de auditoría: ninguna ruta pública de escritura elude el
# estado degradado (comprobación centralizada _ensure_not_degraded)
# ---------------------------------------------------------------------------

def _degradar_store_real(store, monkeypatch):
    """Provoca una degradación REAL (rollback incompleto) con la inyección
    determinista habitual: proxy de os.replace + fallo original dentro de la
    transacción (sin parchear métodos del store)."""
    proxy = _OsProxy(_real_os)
    monkeypatch.setattr(_jsmod, "os", proxy)
    with pytest.raises(TransactionRollbackError):
        with store.transaction("dev-u"):
            store.save_attempt("dev-u", _att("att_degradado"))
            store.save_progress("dev-u", _base_prog(v=2))
            proxy.fail_replace = True     # la restauración fallará desde aquí
            raise RuntimeError("fallo original")
    assert store.degraded is not None
    proxy.fail_replace = False            # "FS saneado": escrituras delegan al os real
    return proxy


def test_todas_las_rutas_publicas_de_escritura_rechazan_en_degradado(tmp_path, monkeypatch):
    store = JsonProgressStore(root=tmp_path)
    store.save_progress("dev-u", _base_prog(v=1))
    store.save_attempt("dev-u", _att("att_pre"))       # intento preexistente

    _degradar_store_real(store, monkeypatch)

    # instantáneas de integridad (bytes) de los archivos existentes
    prog_path = store.root / "dev-u" / "progress.json"
    att_path = store.root / "dev-u" / "attempts" / "att_pre.json"
    prog_bytes_antes = prog_path.read_bytes()
    att_bytes_antes = att_path.read_bytes()

    # 1) save_progress
    with pytest.raises(StoreDegradedError):
        store.save_progress("dev-u", _base_prog(v=99))
    # 2) save_attempt
    with pytest.raises(StoreDegradedError):
        store.save_attempt("dev-u", _att("att_nuevo"))
    # 3) update_attempt
    with pytest.raises(StoreDegradedError):
        store.update_attempt("dev-u", _att("att_pre", score={"manipulado": True}))
    # 4) update_attempt_if_status (CAS)
    with pytest.raises(StoreDegradedError):
        store.update_attempt_if_status("dev-u", "att_pre", "pending_review",
                                       _att("att_pre", score={"status": "reviewed"}))
    # 5) save_report (sobre intento propio existente)
    with pytest.raises(StoreDegradedError):
        store.save_report("dev-u", "att_pre", "report.md", "contenido nuevo")
    # 6) transaction() y 7) mutate_progress (se reafirman con la guarda central)
    with pytest.raises(StoreDegradedError):
        with store.transaction("dev-u"):
            pass
    with pytest.raises(StoreDegradedError):
        store.mutate_progress("dev-u", lambda prog: None)

    # NINGÚN archivo fue modificado/creado: integridad byte a byte
    assert prog_path.read_bytes() == prog_bytes_antes
    assert att_path.read_bytes() == att_bytes_antes
    assert not (store.root / "dev-u" / "attempts" / "att_nuevo.json").exists()
    assert not (store.root / "dev-u" / "reports" / "att_pre.report.md").exists()
    # lecturas y recuperación explícita siguen operativas
    assert store.load_progress("dev-u") is not None


def test_escrituras_se_reanudan_tras_recuperacion_verificada(tmp_path, monkeypatch):
    """Corrección de auditoría PR #4 (recuperación verificable): mientras
    progress.json conserve el contenido PARCIAL de la transacción fallida —
    aunque sea JSON sintácticamente válido—, la recuperación DEBE FALLAR y
    el store seguir degradado. Las escrituras solo se reanudan después de
    restaurar byte a byte el estado original y verificarlo realmente
    (sha256 registrado en el diario antes del rollback)."""
    store = JsonProgressStore(root=tmp_path)
    store.save_progress("dev-u", _base_prog(v=1))
    store.save_attempt("dev-u", _att("att_pre"))
    _degradar_store_real(store, monkeypatch)

    prog_path = store.root / "dev-u" / "progress.json"
    bytes_parciales = prog_path.read_bytes()
    assert json.loads(bytes_parciales)["v"] == 2      # estado parcial (JSON válido)

    # 1) la recuperación FALLA: el contenido difiere del estado original
    #    esperado (no basta con que sea JSON válido ni con que exista)
    ok, problems = store.verify_recovery()
    assert ok is False
    assert any("progress.json" in pr["path"] and "DIFIERE" in pr["problem"]
               for pr in problems)
    assert store.degraded is not None
    with pytest.raises(StoreDegradedError):
        store.save_progress("dev-u", _base_prog(v=99))
    assert prog_path.read_bytes() == bytes_parciales  # nada fue escrito

    # 2) el operador restaura el contenido original BYTE A BYTE (desde el
    #    .rbak conservado) -> la recuperación exacta sí se verifica
    rbak = store.root / "dev-u" / "progress.json.rbak"
    assert rbak.exists(), "el .rbak se conserva como material de recuperación"
    prog_path.write_bytes(rbak.read_bytes())
    ok, problems = store.verify_recovery()
    assert ok is True and problems == [] and store.degraded is None
    assert json.loads(prog_path.read_text(encoding="utf-8"))["v"] == 1

    # 3) las 5 rutas públicas + la transaccional vuelven a funcionar
    store.save_progress("dev-u", _base_prog(v=5))
    assert store.load_progress("dev-u")["v"] == 5
    store.save_attempt("dev-u", _att("att_ok"))
    assert store.get_attempt("dev-u", "att_ok") is not None
    assert store.update_attempt("dev-u", _att("att_ok", score={"x": 1})) is True
    assert store.update_attempt_if_status(
        "dev-u", "att_pre", "pending_review",
        _att("att_pre", score={"status": "reviewed"})) is False  # estado no coincide: False normal
    assert store.save_report("dev-u", "att_ok", "report.md", "r") is not None
    with store.transaction("dev-u"):
        store.save_progress("dev-u", _base_prog(v=6))
    assert store.load_progress("dev-u")["v"] == 6


# ---------------------------------------------------------------------------
# Corrección de auditoría PR #4 (recuperación verificable): pruebas negativas
# y ruta semántica explícita
# ---------------------------------------------------------------------------

def test_verify_recovery_rechaza_json_valido_semanticamente_distinto(tmp_path, monkeypatch):
    """Prueba negativa exigida por la auditoría: un JSON sintácticamente
    VÁLIDO pero semánticamente distinto del estado esperado NO debe superar
    la recuperación exacta; el store sigue degradado y rechaza escrituras.
    Cubre también la regla de los archivos que debían eliminarse: si vuelven
    a existir, la recuperación falla aunque el resto esté restaurado."""
    store = JsonProgressStore(root=tmp_path)
    store.save_progress("dev-u", _base_prog(v=1))
    bytes_originales = (store.root / "dev-u" / "progress.json").read_bytes()
    store.save_attempt("dev-u", _att("att_pre"))
    _degradar_store_real(store, monkeypatch)

    prog_path = store.root / "dev-u" / "progress.json"
    att_degradado = store.root / "dev-u" / "attempts" / "att_degradado.json"
    assert not att_degradado.exists()       # el rollback sí pudo borrar el archivo nuevo

    # JSON válido, pero distinto del estado original esperado
    prog_path.write_text(json.dumps(
        {"user_id": "dev-u", "lessons": {}, "exams": {}, "activity": {}, "v": 1,
         "extra": "contenido legítimo pero distinto del original"},
        ensure_ascii=False, indent=2), encoding="utf-8")
    json.loads(prog_path.read_text(encoding="utf-8"))   # constancia: es JSON válido

    ok, problems = store.verify_recovery()
    assert ok is False
    assert any("progress.json" in pr["path"] for pr in problems)
    assert store.degraded is not None
    with pytest.raises(StoreDegradedError):
        store.save_progress("dev-u", _base_prog(v=99))

    # progreso restaurado con exactitud, pero el archivo que debía estar
    # ELIMINADO vuelve a existir -> la recuperación exacta sigue fallando
    prog_path.write_bytes(bytes_originales)
    att_degradado.write_text(json.dumps(_att("att_degradado")), encoding="utf-8")
    ok, problems = store.verify_recovery()
    assert ok is False
    assert any("att_degradado" in pr["path"] and "sigue existiendo" in pr["problem"]
               for pr in problems)
    assert store.degraded is not None

    # eliminado de nuevo: recuperación exacta verificada y escrituras reanudadas
    att_degradado.unlink()
    ok, problems = store.verify_recovery()
    assert ok is True and problems == [] and store.degraded is None
    store.save_progress("dev-u", _base_prog(v=7))
    assert store.load_progress("dev-u")["v"] == 7


def test_ruta_semantica_explicita_para_reparacion_con_contenido_distinto(tmp_path, monkeypatch):
    """Corrección de auditoría (PR #4, ruta semántica): si el operador repara
    los agregados con contenido LEGÍTIMAMENTE distinto del original (flujo de
    tools/repair_progress.py), la recuperación exacta debe rechazarlo —no se
    confunde con la restauración del rollback— y solo la ruta semántica
    EXPLÍCITA levanta la degradación. Actualizada por la corrección de
    auditoría de recuperación semántica (alcance por archivo): un veredicto
    genérico (incluso `True`) NO acredita los archivos modificados; una
    reparación basada en el estado PARCIAL de la transacción fallida se
    rechaza aunque sus agregados sean auto-coherentes; los agregados
    alterados se rechazan; las invariantes estructurales se conservan."""
    store = JsonProgressStore(root=tmp_path)
    service = make_service(store)

    # estado real: sesión de examen + envío (agregados derivados en progreso)
    sid = start_session(service, user="dev-u")
    service.submit_exam("dev-u", COURSE, EXAM, sid, blank_responses())
    progress_path = store.root / "dev-u" / "progress.json"
    contenido_original = json.loads(progress_path.read_text(encoding="utf-8"))
    assert contenido_original.get("exams"), "precondición: hay agregados derivados"

    _degradar_store_real(store, monkeypatch)
    assert store.degraded is not None
    # la transacción fallida dejó progress.json en estado parcial (_base_prog v2)
    assert json.loads(progress_path.read_text(encoding="utf-8")) == _base_prog(v=2)
    rbak = Path(str(progress_path) + ".rbak")
    assert rbak.exists(), "el rollback conserva el .rbak como material de recuperación"

    # 1) "reparación" con base BASURA (el estado parcial de la transacción
    #    fallida) y agregados auto-coherentes con esa base: el diagnóstico
    #    genérico la ve consistente — ese es exactamente el defecto auditado:
    #    no distingue una reparación legítima del estado parcial.
    reparado_basura = service.compute_repaired_progress("dev-u")
    progress_path.write_text(json.dumps(reparado_basura, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    assert service.validate_progress_repair("dev-u")[0] is True, \
        "precondición del defecto: la comprobación genérica es auto-coherente"

    # 1a) la recuperación EXACTA la rechaza (no se confunde con la reparación)
    ok, problems = store.verify_recovery()
    assert ok is False
    assert any("progress.json" in pr["path"] and "DIFIERE" in pr["problem"]
               for pr in problems)
    assert store.degraded is not None

    # 1b) ruta semántica SIN validador callable: rechazada (TypeError)
    with pytest.raises(TypeError):
        store.verify_semantic_recovery(None)
    assert store.degraded is not None

    # 1c) validador que no acredita (False) o que lanza excepción: degradado
    ok, problems = store.verify_semantic_recovery(lambda ctx: False)
    assert ok is False and store.degraded is not None
    assert any("NO acreditó" in pr["problem"] for pr in problems)

    def validador_roto(ctx):
        raise ValueError("fallo del validador")
    ok, problems = store.verify_semantic_recovery(validador_roto)
    assert ok is False and store.degraded is not None
    assert any("excepción" in pr["problem"] for pr in problems)

    # 1d) veredicto True GENÉRICO sin acreditación por archivo: rechazado
    #     (núcleo del defecto auditado: el store exige cobertura explícita
    #     de cada archivo modificado)
    ok, problems = store.verify_semantic_recovery(lambda ctx: True)
    assert ok is False and store.degraded is not None
    assert any(pr["path"] == str(progress_path)
               and "SIN verificación suficiente" in pr["problem"]
               for pr in problems)

    # 1e) validador CON ALCANCE (semantic_recovery_validator): rechaza la
    #     reparación con base basura — los campos no derivados no provienen
    #     del estado original aunque los agregados se reconstruyan bien
    ok, problems = store.verify_semantic_recovery(
        service.semantic_recovery_validator())
    assert ok is False and store.degraded is not None
    assert any("NO acreditó" in pr["problem"] for pr in problems)

    # 2) reparación correcta: base ORIGINAL (.rbak) + agregados reconstruidos
    #    desde los intentos; si se altera un agregado, no se acredita
    progress_path.write_bytes(rbak.read_bytes())
    reparado = service.compute_repaired_progress("dev-u")
    alterado = json.loads(json.dumps(reparado))
    if alterado.get("exams"):
        clave_examen = next(iter(alterado["exams"]))
        alterado["exams"][clave_examen]["best_percent"] = 999.0
    else:
        alterado["extra_ilegitimo"] = True
    progress_path.write_text(json.dumps(alterado, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    ok, problems = store.verify_semantic_recovery(
        service.semantic_recovery_validator())
    assert ok is False and store.degraded is not None

    # 3) invariante estructural: el archivo que debía estar ELIMINADO no
    #    puede resucitar ni siquiera por la ruta semántica
    progress_path.write_text(json.dumps(reparado, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    att_degradado = store.root / "dev-u" / "attempts" / "att_degradado.json"
    att_degradado.write_text(json.dumps(_att("att_degradado")), encoding="utf-8")
    ok, problems = store.verify_semantic_recovery(
        service.semantic_recovery_validator())
    assert ok is False and store.degraded is not None
    assert any("att_degradado" in (pr["path"] or "")
               and "sigue existiendo" in pr["problem"] for pr in problems)
    att_degradado.unlink()

    # 4) reparación correcta + validador con alcance: recuperación semántica
    #    ACREDITADA; escrituras reanudadas
    ok, problems = store.verify_semantic_recovery(
        service.semantic_recovery_validator())
    assert ok is True and problems == [] and store.degraded is None
    assert service.validate_progress_repair("dev-u")[0] is True
    store.save_attempt("dev-u", _att("att_post"))
    assert store.get_attempt("dev-u", "att_post") is not None


# ---------------------------------------------------------------------------
# Corrección de auditoría PR #4 (recuperación semántica con alcance por
# archivo): pruebas negativas y de regresión exigidas
# ---------------------------------------------------------------------------

def _att_ok(att_id, user="dev-u", **extra):
    """Intento VÁLIDO según el contrato Attempt (Pydantic): id, user_id,
    scope completo y score. Para pruebas de la ruta semántica con alcance."""
    base = {"id": att_id, "user_id": user,
            "submitted_at": "2026-01-01T00:00:00+00:00",
            "scope": {"type": "lesson", "course_id": COURSE,
                      "module_id": MOD1, "lesson_id": LESSON1},
            "score": {"status": "auto_graded", "percent": 50.0, "passed": False},
            "results": []}
    base.update(extra)
    return base


def _degradar_con_intento_modificado(store, monkeypatch, intento_modificado):
    """Degradación REAL en la que un intento PREEXISTENTE y el progreso
    quedan modificados (ambas restauraciones fallan; los .rbak conservan el
    estado original). Inyección determinista habitual (proxy de os.replace)."""
    proxy = _OsProxy(_real_os)
    monkeypatch.setattr(_jsmod, "os", proxy)
    with pytest.raises(TransactionRollbackError):
        with store.transaction("dev-u"):
            assert store.update_attempt("dev-u", intento_modificado) is True
            store.save_progress("dev-u", _base_prog(v=2))
            proxy.fail_replace = True     # la restauración fallará desde aquí
            raise RuntimeError("fallo original")
    assert store.degraded is not None
    proxy.fail_replace = False            # "FS saneado"
    return proxy


def test_semantica_rechaza_intento_preexistente_modificado_sin_acreditar(tmp_path, monkeypatch):
    """Prueba negativa exigida (auditoría): un archivo de intento
    preexistente queda MODIFICADO tras el rollback incompleto pero sigue
    siendo JSON válido. La recuperación semántica debe rechazarlo mientras su
    contenido no se haya validado y reparado: el veredicto genérico `True` no
    lo cubre (falta de acreditación explícita) y el validador con alcance lo
    rechaza si viola identidad/pertenencia/contrato. Solo se libera cuando el
    intento queda reparado (cambio legítimo que satisface el contrato) y se
    acredita por archivo."""
    store = JsonProgressStore(root=tmp_path)
    service = make_service(store)
    store.save_progress("dev-u", _base_prog(v=1))
    store.save_attempt("dev-u", _att_ok("att_pre"))
    att_path = store.root / "dev-u" / "attempts" / "att_pre.json"
    prog_path = store.root / "dev-u" / "progress.json"

    adulterado = _att_ok("att_pre", score={"status": "reviewed",
                                           "percent": 100.0, "passed": True})
    _degradar_con_intento_modificado(store, monkeypatch, adulterado)
    # ambos archivos quedan en estado de la transacción fallida; los .rbak
    # conservan el estado original
    assert json.loads(att_path.read_text(encoding="utf-8"))["score"]["passed"] is True
    assert json.loads(prog_path.read_text(encoding="utf-8")) == _base_prog(v=2)
    assert Path(str(att_path) + ".rbak").exists()
    assert Path(str(prog_path) + ".rbak").exists()

    # 1) validador GENÉRICO que devuelve True: rechazado — el intento
    #    modificado y el progreso parcial quedan SIN acreditación explícita
    ok, problems = store.verify_semantic_recovery(lambda ctx: True)
    assert ok is False and store.degraded is not None
    assert any(pr["path"] == str(att_path)
               and "SIN verificación suficiente" in pr["problem"]
               for pr in problems)
    assert any(pr["path"] == str(prog_path)
               and "SIN verificación suficiente" in pr["problem"]
               for pr in problems)
    with pytest.raises(StoreDegradedError):
        store.save_progress("dev-u", _base_prog(v=99))

    # 2) progreso restaurado con exactitud, pero el intento viola la
    #    IDENTIDAD (JSON válido; el id no corresponde al archivo): el
    #    validador con alcance NO acredita
    prog_path.write_bytes(Path(str(prog_path) + ".rbak").read_bytes())
    roto = json.loads(att_path.read_text(encoding="utf-8"))
    roto["id"] = "otro_id"
    att_path.write_text(json.dumps(roto, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    json.loads(att_path.read_text(encoding="utf-8"))   # constancia: JSON válido
    ok, problems = store.verify_semantic_recovery(
        service.semantic_recovery_validator())
    assert ok is False and store.degraded is not None
    assert any("NO acreditó" in pr["problem"] for pr in problems)
    with pytest.raises(StoreDegradedError):
        store.save_attempt("dev-u", _att_ok("att_bloqueado"))

    # 2b) pertenencia violada (user_id de otro usuario): tampoco se acredita
    ajeno = json.loads(att_path.read_text(encoding="utf-8"))
    ajeno["id"] = "att_pre"
    ajeno["user_id"] = "otro-usuario"
    att_path.write_text(json.dumps(ajeno, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    ok, problems = store.verify_semantic_recovery(
        service.semantic_recovery_validator())
    assert ok is False and store.degraded is not None

    # 3) intento reparado con cambio LEGÍTIMO que satisface contrato,
    #    identidad y pertenencia: la ruta con alcance lo acredita
    #    explícitamente y el store se libera
    legitimo = _att_ok("att_pre", feedback_summary="corregido tras revisión manual")
    att_path.write_text(json.dumps(legitimo, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    ok, problems = store.verify_semantic_recovery(
        service.semantic_recovery_validator())
    assert ok is True and problems == [] and store.degraded is None
    store.save_progress("dev-u", _base_prog(v=3))
    assert store.load_progress("dev-u")["v"] == 3


def test_semantica_rechaza_reporte_alterado_que_sigue_existiendo(tmp_path, monkeypatch):
    """Prueba negativa exigida (auditoría): un reporte preexistente queda
    ALTERADO tras el rollback incompleto pero sigue existiendo. No debe
    considerarse íntegro solo por existir: el veredicto genérico no lo cubre
    y el validador con alcance exige que el contenido coincida con la
    regeneración determinista desde el intento vinculado (contrato de
    formato)."""
    store = JsonProgressStore(root=tmp_path)
    service = make_service(store)
    sid = start_session(service, user="dev-u")
    service.submit_exam("dev-u", COURSE, EXAM, sid, blank_responses())
    att_id = sorted((store.root / "dev-u" / "attempts").glob("*.json"))[0].stem
    att_path = store.root / "dev-u" / "attempts" / f"{att_id}.json"
    prog_path = store.root / "dev-u" / "progress.json"
    md = service.get_attempt_report("dev-u", att_id, fmt="markdown")["report"]
    assert store.save_report("dev-u", att_id, "report.md", md) is not None
    report_path = store.root / "dev-u" / "reports" / f"{att_id}.report.md"

    # transacción de revisión que falla a mitad: modifica intento, reporte y
    # progreso; las tres restauraciones fallan
    att0 = json.loads(att_path.read_text(encoding="utf-8"))
    revisado = json.loads(json.dumps(att0))
    revisado["review"] = {"reviewer": "mentor-1",
                          "reviewed_at": "2026-01-02T00:00:00+00:00",
                          "comment": "corrección manual"}
    revisado["score"] = dict(revisado.get("score") or {})
    revisado["score"]["status"] = "reviewed"

    proxy = _OsProxy(_real_os)
    monkeypatch.setattr(_jsmod, "os", proxy)
    with pytest.raises(TransactionRollbackError):
        with store.transaction("dev-u"):
            assert store.update_attempt("dev-u", revisado) is True
            store.save_report("dev-u", att_id, "report.md",
                              md + "\n# ESTADO PARCIAL DE LA TX FALLIDA")
            store.save_progress("dev-u", _base_prog(v=2))
            proxy.fail_replace = True
            raise RuntimeError("fallo original")
    assert store.degraded is not None
    proxy.fail_replace = False
    assert "ESTADO PARCIAL" in report_path.read_text(encoding="utf-8")

    # 1) el reporte alterado SIGUE EXISTIENDO: un validador genérico True no
    #    lo acredita (cobertura por archivo) y el store sigue degradado
    ok, problems = store.verify_semantic_recovery(lambda ctx: True)
    assert ok is False and store.degraded is not None
    assert any(pr["path"] == str(report_path)
               and "SIN verificación suficiente" in pr["problem"]
               for pr in problems)

    # 2) progreso restaurado con exactitud, pero el reporte sigue alterado:
    #    el validador con alcance lo rechaza (no coincide con la
    #    regeneración desde el intento) — existir NO basta
    prog_path.write_bytes(Path(str(prog_path) + ".rbak").read_bytes())
    ok, problems = store.verify_semantic_recovery(
        service.semantic_recovery_validator())
    assert ok is False and store.degraded is not None

    # 2b) reporte con "pinta" de markdown (original + basura añadida): sigue
    #     rechazado — la integridad la exige el contrato, no el formato externo
    report_path.write_text(md + "\nbasura añadida manualmente", encoding="utf-8")
    ok, problems = store.verify_semantic_recovery(
        service.semantic_recovery_validator())
    assert ok is False and store.degraded is not None

    # 3) reporte REGENERADO de forma determinista desde el intento actual
    #    (revisado): la recuperación semántica se acredita y las escrituras
    #    se reanudan
    md_reparado = service.get_attempt_report("dev-u", att_id,
                                             fmt="markdown")["report"]
    assert md_reparado != md, "el intento revisado produce un reporte distinto"
    report_path.write_text(md_reparado, encoding="utf-8")
    ok, problems = store.verify_semantic_recovery(
        service.semantic_recovery_validator())
    assert ok is True and problems == [] and store.degraded is None
    store.save_progress("dev-u", _base_prog(v=3))
    assert store.load_progress("dev-u")["v"] == 3


def test_semantica_no_libera_con_archivo_sin_cobertura_aunque_validador_devuelva_true(
        tmp_path, monkeypatch):
    """Prueba negativa exigida (auditoría): el progreso reparado es coherente
    con los intentos, pero OTRO archivo afectado (un intento) continúa
    inconsistente. Un validador que devuelve True tras acreditar SOLO el
    progreso no puede liberar el store: la cobertura por archivo lo rechaza y
    señala el archivo no validado."""
    store = JsonProgressStore(root=tmp_path)
    service = make_service(store)
    store.save_progress("dev-u", _base_prog(v=1))
    store.save_attempt("dev-u", _att_ok("att_pre"))
    att_path = store.root / "dev-u" / "attempts" / "att_pre.json"
    prog_path = store.root / "dev-u" / "progress.json"

    adulterado = _att_ok("att_pre", score={"status": "reviewed",
                                           "percent": 100.0, "passed": True})
    _degradar_con_intento_modificado(store, monkeypatch, adulterado)

    # el operador repara SOLO el progreso (base original + agregados
    # reconstruidos): contenido legítimamente distinto del original
    prog_path.write_bytes(Path(str(prog_path) + ".rbak").read_bytes())
    reparado = service.compute_repaired_progress("dev-u")
    prog_path.write_text(json.dumps(reparado, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    assert reparado != json.loads(Path(str(prog_path) + ".rbak").read_text(
        encoding="utf-8")), "precondición: el progreso reparado difiere del original"

    def validador_parcial(ctx):
        # acredita SOLO el progreso (agregados coherentes con los intentos)
        # y devuelve True: el intento adulterado queda sin cobertura
        for f in ctx.modified_files():
            if f.kind == "progress":
                ctx.mark_validated(f.path,
                                   "agregados reconstruidos desde los intentos")
        return True

    ok, problems = store.verify_semantic_recovery(validador_parcial)
    assert ok is False and store.degraded is not None
    assert any(pr["path"] == str(att_path)
               and "SIN verificación suficiente" in pr["problem"]
               for pr in problems)
    assert not any(pr["path"] == str(prog_path) for pr in problems), \
        "el progreso SÍ fue acreditado explícitamente: no debe aparecer"
    with pytest.raises(StoreDegradedError):
        store.save_attempt("dev-u", _att_ok("att_bloqueado"))

    # acreditando también el intento (reparado con cambio legítimo), la
    # recuperación sí pasa: la cobertura por archivo es lo único que faltaba
    att_path.write_text(json.dumps(
        _att_ok("att_pre", feedback_summary="revisado"),
        ensure_ascii=False, indent=2), encoding="utf-8")
    ok, problems = store.verify_semantic_recovery(
        service.semantic_recovery_validator())
    assert ok is True and problems == [] and store.degraded is None


@pytest.mark.parametrize("verdicto", [False, None, 0, 1, "True", [], {}, (True,)])
def test_semantica_rechaza_veredictos_invalidos_del_validador(
        tmp_path, monkeypatch, verdicto):
    """Prueba negativa exigida (auditoría): el validador devuelve False,
    None, un valor distinto de True (incluidos truthy como 1, 'True', (True,))
    => la ruta semántica NO libera el store (se exige exactamente `True`) y
    las escrituras siguen rechazadas."""
    store = JsonProgressStore(root=tmp_path)
    store.save_progress("dev-u", _base_prog(v=1))
    _degradar_store_real(store, monkeypatch)

    ok, problems = store.verify_semantic_recovery(lambda ctx: verdicto)
    assert ok is False and store.degraded is not None
    assert any("NO acreditó" in pr["problem"] for pr in problems)
    with pytest.raises(StoreDegradedError):
        store.save_progress("dev-u", _base_prog(v=99))


def test_semantica_rechaza_validador_que_lanza_excepcion(tmp_path, monkeypatch):
    """Prueba negativa exigida (auditoría): un validador que lanza excepción
    no libera el store; el fallo se reporta y se conserva el estado degradado
    (fallo cerrado)."""
    store = JsonProgressStore(root=tmp_path)
    store.save_progress("dev-u", _base_prog(v=1))
    _degradar_store_real(store, monkeypatch)

    def validador_roto(ctx):
        raise ValueError("fallo del validador")

    ok, problems = store.verify_semantic_recovery(validador_roto)
    assert ok is False and store.degraded is not None
    assert any("excepción" in pr["problem"] for pr in problems)
    with pytest.raises(StoreDegradedError):
        store.save_progress("dev-u", _base_prog(v=99))


def test_semantica_falla_cerrado_referencias_incompletas_e_ilegibles(
        tmp_path, monkeypatch):
    """Prueba negativa exigida (auditoría): falta una referencia fiable en el
    diario, un archivo requerido no puede leerse, existe el archivo que debía
    estar ausente, o un archivo de tipo NO reconocido quedó modificado: la
    ruta semántica falla de forma CERRADA y conserva el estado degradado en
    todos los casos."""
    store = JsonProgressStore(root=tmp_path)
    store.save_progress("dev-u", _base_prog(v=1))
    _degradar_store_real(store, monkeypatch)
    prog_path = store.root / "dev-u" / "progress.json"
    att_degradado = store.root / "dev-u" / "attempts" / "att_degradado.json"
    service = make_service(store)
    validador = service.semantic_recovery_validator()
    checks = store.degraded["checks"]
    entrada = next(c for c in checks if c["path"] == str(prog_path))

    # (a) el archivo que debía estar AUSENTE vuelve a existir: rechazo
    #     estructural, incluso con un validador perfecto
    att_degradado.write_text(json.dumps(_att("att_degradado")), encoding="utf-8")
    ok, problems = store.verify_semantic_recovery(validador)
    assert ok is False and store.degraded is not None
    assert any("att_degradado" in (pr["path"] or "")
               and "sigue existiendo" in pr["problem"] for pr in problems)
    att_degradado.unlink()

    # (b) referencia incompleta en el diario (sin sha256 original): fallo
    #     cerrado por diseño en la ruta semántica…
    sha_original = entrada.pop("sha256")
    ok, problems = store.verify_semantic_recovery(validador)
    assert ok is False and store.degraded is not None
    assert any("sin referencia fiable" in pr["problem"] for pr in problems)
    entrada["sha256"] = sha_original

    #     …y también en la ruta exacta
    sha_2 = entrada.pop("sha256")
    ok, problems = store.verify_recovery()
    assert ok is False and store.degraded is not None
    assert any("sin referencia fiable" in pr["problem"] for pr in problems)
    entrada["sha256"] = sha_2

    # (b2) entrada sin "expect": sin referencia fiable (fallo cerrado)
    expect_original = entrada.pop("expect")
    ok, problems = store.verify_semantic_recovery(validador)
    assert ok is False and store.degraded is not None
    assert any("sin referencia fiable" in pr["problem"] for pr in problems)
    entrada["expect"] = expect_original

    # (c) archivo requerido que NO PUEDE LEERSE (un directorio ocupa su
    #     lugar): fallo cerrado
    prog_path.unlink()
    prog_path.mkdir()
    ok, problems = store.verify_semantic_recovery(validador)
    assert ok is False and store.degraded is not None
    assert any("no se pudo leer" in pr["problem"] for pr in problems)
    prog_path.rmdir()
    prog_path.write_bytes(Path(str(prog_path) + ".rbak").read_bytes())

    # (d) archivo MODIFICADO de tipo NO reconocido por el layout del store:
    #     la ruta semántica no puede verificar su finalidad (fallo cerrado)
    notas = store.root / "dev-u" / "notas.txt"
    notas.write_text("contenido cualquiera", encoding="utf-8")
    checks.append({"path": str(notas), "expect": "sha256",
                   "sha256": hashlib.sha256(b"otro contenido").hexdigest()})
    ok, problems = store.verify_semantic_recovery(validador)
    assert ok is False and store.degraded is not None
    assert any("tipo NO reconocido" in pr["problem"] for pr in problems)

    # tras sanear el diario y el archivo, la recuperación exacta verifica
    checks.pop()
    notas.unlink()
    ok, problems = store.verify_recovery()
    assert ok is True and problems == [] and store.degraded is None


def test_semantica_solo_reanuda_escrituras_con_cobertura_completa_por_archivo(
        tmp_path, monkeypatch):
    """Prueba positiva exigida (auditoría): la recuperación semántica solo
    reanuda las escrituras cuando TODOS los archivos afectados pasan su
    verificación específica —intento modificado legítimo (contrato,
    identidad, pertenencia), reporte regenerado desde el intento, progreso
    reconstruido sobre el estado original— y el archivo creado por la
    transacción fallida está ausente. Mientras falta cobertura, el store
    sigue degradado; tras acreditarla, las siete rutas públicas de escritura
    funcionan de nuevo."""
    store = JsonProgressStore(root=tmp_path)
    service = make_service(store)
    sid = start_session(service, user="dev-u")
    service.submit_exam("dev-u", COURSE, EXAM, sid, blank_responses())
    att_id = sorted((store.root / "dev-u" / "attempts").glob("*.json"))[0].stem
    att_path = store.root / "dev-u" / "attempts" / f"{att_id}.json"
    prog_path = store.root / "dev-u" / "progress.json"
    md_original = service.get_attempt_report("dev-u", att_id,
                                             fmt="markdown")["report"]
    assert store.save_report("dev-u", att_id, "report.md", md_original) is not None
    report_path = store.root / "dev-u" / "reports" / f"{att_id}.report.md"

    # transacción de revisión que falla a mitad: modifica intento, reporte y
    # progreso, y crea un intento nuevo (que el rollback sí logra borrar)
    att0 = json.loads(att_path.read_text(encoding="utf-8"))
    revisado = json.loads(json.dumps(att0))
    revisado["review"] = {"reviewer": "mentor-1",
                          "reviewed_at": "2026-01-02T00:00:00+00:00",
                          "comment": "corrección manual"}
    revisado["score"] = dict(revisado.get("score") or {})
    revisado["score"]["status"] = "reviewed"

    proxy = _OsProxy(_real_os)
    monkeypatch.setattr(_jsmod, "os", proxy)
    with pytest.raises(TransactionRollbackError):
        with store.transaction("dev-u"):
            assert store.update_attempt("dev-u", revisado) is True
            store.save_report("dev-u", att_id, "report.md",
                              md_original + "\n# ESTADO PARCIAL DE LA TX")
            store.save_attempt("dev-u", _att_ok("att_nuevo"))
            store.save_progress("dev-u", _base_prog(v=2))
            proxy.fail_replace = True
            raise RuntimeError("fallo original")
    assert store.degraded is not None
    proxy.fail_replace = False
    assert not (store.root / "dev-u" / "attempts" / "att_nuevo.json").exists(), \
        "el rollback borró el archivo creado (expect absent verificado)"

    # 0) sin reparar nada: el veredicto True genérico NO libera el store —
    #    faltan las tres acreditaciones por archivo
    ok, problems = store.verify_semantic_recovery(lambda ctx: True)
    assert ok is False and store.degraded is not None
    sin_cobertura = {pr["path"] for pr in problems
                     if "SIN verificación suficiente" in pr["problem"]}
    assert sin_cobertura == {str(att_path), str(report_path), str(prog_path)}
    with pytest.raises(StoreDegradedError):
        store.save_progress("dev-u", _base_prog(v=99))

    # 1) reparación correcta de cada archivo afectado:
    #    - intento: queda el estado revisado legítimo (contrato/identidad/
    #      pertenencia verificables)
    #    - reporte: regeneración determinista desde el intento actual
    md_reparado = service.get_attempt_report("dev-u", att_id,
                                             fmt="markdown")["report"]
    assert md_reparado != md_original
    report_path.write_text(md_reparado, encoding="utf-8")
    #    - progreso: base ORIGINAL (.rbak) + agregados reconstruidos desde
    #      los intentos (fuente de verdad)
    prog_path.write_bytes(Path(str(prog_path) + ".rbak").read_bytes())
    reparado = service.compute_repaired_progress("dev-u")
    prog_path.write_text(json.dumps(reparado, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    # 2) validador con alcance: TODOS los archivos modificados acreditados
    ok, problems = store.verify_semantic_recovery(
        service.semantic_recovery_validator())
    assert ok is True and problems == [] and store.degraded is None

    # 3) las siete rutas públicas de escritura vuelven a funcionar
    store.save_progress("dev-u", _base_prog(v=3))                       # 1
    assert store.load_progress("dev-u")["v"] == 3
    store.save_attempt("dev-u", _att_ok("att_post"))                    # 2
    assert store.get_attempt("dev-u", "att_post") is not None
    assert store.update_attempt(                                        # 3
        "dev-u", _att_ok("att_post", feedback_summary="x")) is True
    assert store.update_attempt_if_status(                              # 4
        "dev-u", "att_post", "estado_que_no_existe",
        _att_ok("att_post")) is False
    assert store.save_report(                                           # 5
        "dev-u", "att_post", "report.md", "r") is not None
    with store.transaction("dev-u"):                                    # 6
        store.save_progress("dev-u", _base_prog(v=4))
    assert store.load_progress("dev-u")["v"] == 4
    store.mutate_progress("dev-u", lambda prog: prog.update(v=5))       # 7
    assert store.load_progress("dev-u")["v"] == 5


def test_escritura_directa_y_verificaciones_se_serializan_con_lock_global(tmp_path):
    """Endurecimiento de hilos (punto 7 de la auditoría): la comprobación de
    degradación y las escrituras directas forman UNA sola sección crítica
    bajo el lock global del store (_STORE_LOCK), igual que las
    verificaciones de recuperación. Mientras otra sección crítica lo tiene
    (p. ej. una transacción en curso), TODOS deben esperar: no existe
    ventana entre comprobar y escribir."""
    store = JsonProgressStore(root=tmp_path)
    store.save_progress("dev-u", _base_prog(v=1))
    resultados: dict = {}

    def lanzar(nombre, fn):
        def run():
            try:
                resultados[nombre] = ("ok", fn())
            except BaseException as exc:      # evidencia de la excepción
                resultados[nombre] = ("error", exc)
        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t

    with _STORE_LOCK:               # el hilo principal simula una transacción larga
        t1 = lanzar("escritura", lambda: store.save_progress("dev-u", _base_prog(v=2)))
        t2 = lanzar("exacta", lambda: store.verify_recovery())
        t3 = lanzar("semantica", lambda: store.verify_semantic_recovery(lambda s: True))
        for t in (t1, t2, t3):
            t.join(timeout=0.4)
            assert t.is_alive(), "debe bloquear mientras el lock global esté tomado"
        assert resultados == {}
    for t in (t1, t2, t3):
        t.join(timeout=3)
        assert not t.is_alive()
    assert resultados["escritura"][0] == "ok"
    assert resultados["exacta"] == ("ok", (True, []))
    assert resultados["semantica"] == ("ok", (True, []))
    assert store.load_progress("dev-u")["v"] == 2


def test_carrera_degradacion_vs_escritura_directa_sin_ventana(tmp_path):
    """Reproducción determinista de la carrera señalada por la auditoría: un
    hilo intenta una escritura directa mientras el store se degrada en el
    peor momento posible. Como la comprobación de degradación ya NO se hace
    fuera del lock global —comprobación + escritura es una única sección
    crítica bajo _STORE_LOCK, y la degradación por rollback incompleto
    también se marca bajo ese lock (en transaction())—, la escritura en
    espera NUNCA puede materializarse tras la degradación."""
    store = JsonProgressStore(root=tmp_path)
    store.save_progress("dev-u", _base_prog(v=1))
    prog_path = store.root / "dev-u" / "progress.json"
    bytes_antes = prog_path.read_bytes()
    resultado: dict = {}

    def writer():
        try:
            store.save_progress("dev-u", _base_prog(v=99))
            resultado["escritura"] = "aceptada"
        except StoreDegradedError as exc:
            resultado["escritura"] = exc

    with _STORE_LOCK:
        t = threading.Thread(target=writer, daemon=True)
        t.start()
        t.join(timeout=0.4)
        assert t.is_alive(), "la escritura directa debe esperar al lock global"
        # peor momento: el store se degrada mientras el escritor aguarda
        # (la degradación real por rollback incompleto ocurre bajo este
        # mismo lock; la inyección directa emula ese instante)
        store._degraded = {
            "when": "2026-10-25T00:00:00+00:00", "user_id": "dev-u",
            "original": "RuntimeError: simulación de carrera", "restored": [],
            "failures": [("ruta", "OSError: simulado")], "checks": [],
        }
    t.join(timeout=3)
    assert not t.is_alive()
    assert isinstance(resultado["escritura"], StoreDegradedError)
    assert prog_path.read_bytes() == bytes_antes    # nada se escribió tras degradar


class _OsProxyRollback(_OsProxy):
    """Falla os.replace SOLO cuando el origen es un .rbak (la firma del
    rollback): las escrituras normales (origen .tmp), incluidas las de la
    propia transacción que se degradará, no se ven afectadas."""

    def replace(self, src, dst, *a, **k):
        if self.fail_replace and str(src).endswith(".rbak"):
            raise OSError(28, "No space left on device (fallo inyectado)")
        return self._real.replace(src, dst, *a, **k)


def test_ventana_entre_comprobacion_y_escritura_cerrada_bajo_lock_global(tmp_path, monkeypatch):
    """Carrera determinista (punto 7 de la auditoría): un hilo que ejecuta una
    escritura directa queda pausado ENTRE la comprobación de degradación y la
    materialización de la escritura (el test retiene el lock de ruta que
    `_write_json` necesita). Mientras tanto, otro hilo intenta una degradación
    REAL (transacción fallida con rollback saboteado).

    En el código corregido la escritura directa mantiene `_STORE_LOCK` durante
    toda la sección "comprobación + escritura", y la degradación por rollback
    incompleto también se marca bajo ese lock (dentro de `transaction()`): la
    degradación NO puede intercalarse en la ventana — el hilo degradador
    espera. Sin esa atomicidad (comprobación fuera del lock global), la
    degradación se completaría mientras el escritor está pausado y la
    escritura aterrizaría DESPUÉS de la degradación: esa es la ventana que
    este test cierra."""
    store = JsonProgressStore(root=tmp_path)
    store.save_progress("dev-u", _base_prog(v=1))
    store.save_attempt("dev-u", _att("att_pre"))
    prog_path = store.root / "dev-u" / "progress.json"

    proxy = _OsProxyRollback(_real_os)
    proxy.fail_replace = True               # solo afecta al .rbak del rollback
    monkeypatch.setattr(_jsmod, "os", proxy)

    eventos: dict = {}

    def writer():
        try:
            store.save_progress("dev-u", _base_prog(v=99))
            eventos["escritura"] = "aceptada"
        except BaseException as exc:        # evidencia de la excepción
            eventos["escritura"] = exc

    def degradador():
        try:
            with store.transaction("dev-u"):
                store.save_attempt("dev-u", _att("att_pre", score={"parcial": True}))
                raise RuntimeError("fallo original")
        except TransactionRollbackError:
            eventos["degradado"] = True
        except BaseException as exc:
            eventos["degradador_error"] = exc

    with _lock_for(str(prog_path)):         # punto de pausa DENTRO de la escritura
        t_writer = threading.Thread(target=writer, daemon=True)
        t_writer.start()
        time.sleep(0.3)                     # el escritor superó la comprobación y aguarda el lock de ruta
        t_degr = threading.Thread(target=degradador, daemon=True)
        t_degr.start()
        t_degr.join(timeout=0.5)
        # código corregido: el escritor tiene el lock global desde la
        # comprobación hasta la escritura; la degradación (que también se
        # marca bajo ese lock) NO puede intercalarse en la ventana.
        assert "degradado" not in eventos, (
            "la degradación se intercaló ENTRE la comprobación y la escritura: "
            "la ventana sigue abierta")
        assert t_degr.is_alive(), "el degradador debe esperar al lock global"

    # se libera la pausa: la escritura se materializa (comprobada con el store
    # sano y bajo el mismo lock) y la degradación real procede a continuación
    t_degr.join(timeout=3)
    t_writer.join(timeout=3)
    assert not t_writer.is_alive() and not t_degr.is_alive()
    assert eventos.get("degradado") is True
    assert eventos["escritura"] == "aceptada"      # serializada ANTES de la degradación
    assert json.loads(prog_path.read_text(encoding="utf-8"))["v"] == 99
    assert store.degraded is not None
    assert len(store.rollback_failures) == 1
    # tras la degradación, nuevas escrituras: rechazadas
    with pytest.raises(StoreDegradedError):
        store.save_progress("dev-u", _base_prog(v=100))
    assert json.loads(prog_path.read_text(encoding="utf-8"))["v"] == 99
