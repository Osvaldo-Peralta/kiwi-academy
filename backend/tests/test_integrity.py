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
import threading
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.core.auth import TEST_JWT_SECRET, reset_verifier
from backend.app.core.services import LearningService, exam_key, lesson_key
from backend.app.main import app
from backend.app.api.v1.deps import get_service
from backend.app.storage.json_store import JsonProgressStore, TransactionRollbackError
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


# ── Caso D: estado transaccional definido tras rollback fallido ──────────
def test_caso_d_estado_transaccional_definido_y_sin_diario_obsoleto(tmp_path, monkeypatch):
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

    # 1) estado local DEFINIDO: diario limpio pese al fallo de restauración
    assert getattr(store._local, "journal", None) is None
    assert len(store.rollback_failures) == 1

    # 2) el store sigue utilizable: sanear el FS y operar de nuevo
    proxy.fail_replace = False
    prog_tx2 = dict(_base_prog(v=3), marker="tx2")
    with store.transaction("dev-u"):
        store.save_progress("dev-u", prog_tx2)
    assert store.load_progress("dev-u")["marker"] == "tx2"

    # 3) nueva transacción fallida con rollback EXITOSO: restaura al estado
    #    inmediatamente anterior (tx2) — el diario es fresco, no se mezcla
    #    con el de la transacción fallida anterior
    with pytest.raises(ValueError):
        with store.transaction("dev-u"):
            store.save_progress("dev-u", dict(prog_tx2, marker="tx3"))
            raise ValueError("fallo nuevo controlado")
    prog = store.load_progress("dev-u")
    assert prog["marker"] == "tx2" and prog["v"] == 3
    # 4) sin nuevos fallos de rollback; diario nuevamente limpio
    assert len(store.rollback_failures) == 1
    assert getattr(store._local, "journal", None) is None
