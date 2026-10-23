"""Tests del ProgressStore SQL (driver SQLite) y de la fábrica de stores.

El driver Postgres comparte exactamente el mismo SQL (solo cambian los
placeholders ?->%s), por lo que la lógica queda cubierta con SQLite.
"""
import json
import os

import pytest

from backend.app.storage.sql_store import SqlProgressStore
from backend.app.storage.json_store import JsonProgressStore
from backend.app.storage.factory import make_progress_store
from backend.app.core.services import LearningService
from backend.app.storage.repositories import ContentRepository, ProgressRepository


@pytest.fixture()
def store(tmp_path):
    return SqlProgressStore(f"sqlite:///{tmp_path / 'kiwi_test.db'}")


# ------------------------------------------------------------------ progreso
def test_progress_roundtrip(store):
    default = store.load_progress("alice")
    assert default == {"user_id": "alice", "lessons": {}, "exams": {}, "activity": {}}

    store.save_progress("alice", {"user_id": "alice", "lessons": {"x": {"status": "completed"}},
                                  "exams": {}, "activity": {"2026-10-20": 3},
                                  "updated_at": "2026-10-20T00:00:00Z"})
    loaded = store.load_progress("alice")
    assert loaded["lessons"]["x"]["status"] == "completed"
    assert loaded["activity"]["2026-10-20"] == 3

    # upsert: guardar de nuevo sobrescribe
    store.save_progress("alice", {"user_id": "alice", "lessons": {}, "exams": {},
                                  "activity": {}, "updated_at": "2026-10-21T00:00:00Z"})
    assert store.load_progress("alice")["lessons"] == {}


# ------------------------------------------------------------------ intentos
def _attempt(att_id, submitted, scope_type="exam"):
    return {"id": att_id, "user_id": "bob", "scope": {"type": scope_type, "course_id": "soc-n1"},
            "submitted_at": submitted, "score": {"percent": 50}, "results": []}


def test_attempts_save_get_list_order_and_isolation(store):
    store.save_attempt("bob", _attempt("att_1", "2026-10-01T10:00:00Z"))
    store.save_attempt("bob", _attempt("att_2", "2026-10-02T10:00:00Z"))
    store.save_attempt("bob", _attempt("att_3", "2026-10-03T10:00:00Z", scope_type="lesson"))
    store.save_attempt("carol", _attempt("att_4", "2026-10-03T11:00:00Z"))

    got = store.get_attempt("bob", "att_2")
    assert got and got["score"]["percent"] == 50

    # aislamiento por usuario
    assert store.get_attempt("bob", "att_4") is None

    # orden descendente por fecha
    ids = [a["id"] for a in store.list_attempts("bob")]
    assert ids == ["att_3", "att_2", "att_1"]
    assert len(store.list_attempts("bob", limit=2)) == 2

    # idempotencia: re-guardar el mismo intento no duplica ni explota
    store.save_attempt("bob", _attempt("att_1", "2026-10-01T10:00:00Z"))
    assert len(store.list_attempts("bob")) == 3


# ------------------------------------------------------------------ reportes
def test_reports_upsert(store):
    ref = store.save_report("bob", "att_1", "report.md", "# v1")
    assert ref.startswith("sql://")
    store.save_report("bob", "att_1", "report.md", "# v2")  # upsert sin error


# ------------------------------------------- Iteración 2: update/find cross-user
def test_update_attempt_guarda_propiedad(store):
    store.save_attempt("bob", _attempt("att_u1", "2026-10-01T10:00:00Z"))
    att = store.get_attempt("bob", "att_u1")
    att["score"]["percent"] = 99
    assert store.update_attempt("bob", att) is True
    assert store.get_attempt("bob", "att_u1")["score"]["percent"] == 99
    # otro usuario NO puede actualizarlo (guarda de propiedad en el WHERE)
    assert store.update_attempt("mallory", att) is False
    # inexistente -> False (nunca crea)
    assert store.update_attempt("bob", {"id": "att_ghost", "submitted_at": "x"}) is False
    assert store.get_attempt("bob", "att_ghost") is None


def test_find_attempt_any_user(store):
    store.save_attempt("bob", _attempt("att_f1", "2026-10-01T10:00:00Z"))
    owner, data = store.find_attempt_any_user("att_f1")
    assert owner == "bob" and data["id"] == "att_f1"
    assert store.find_attempt_any_user("att_inexistente") == (None, None)


# ------------------------------------------------------------------ esquema
def test_schema_idempotent(tmp_path):
    url = f"sqlite:///{tmp_path / 'x.db'}"
    SqlProgressStore(url)
    SqlProgressStore(url)  # segunda vez: CREATE IF NOT EXISTS no falla


def test_url_invalida(tmp_path):
    with pytest.raises(ValueError):
        SqlProgressStore("mysql://nope")


# ------------------------------------------------------------------ fábrica
def test_factory_default_json(monkeypatch):
    from backend.app.config import settings
    monkeypatch.setattr(settings, "DATABASE_URL", "")
    assert isinstance(make_progress_store(), JsonProgressStore)


def test_factory_sqlite(monkeypatch, tmp_path):
    from backend.app.config import settings
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite:///{tmp_path / 'f.db'}")
    assert isinstance(make_progress_store(), SqlProgressStore)


# ------------------------------------------------------------------ integración
def test_service_integration_with_sql_store(store):
    """Flujo real (ejercicio + examen) sobre el store SQL."""
    service = LearningService(content=ContentRepository(), progress=ProgressRepository(store))

    r = service.attempt_lesson_exercise(
        "dana", "soc-n1", "01-fundamentos", "01-modelo-osi-protocolos", "l1-e1", "b")
    assert r["correct"] is True

    prog = store.load_progress("dana")
    key = "soc-n1:01-fundamentos:01-modelo-osi-protocolos"
    assert prog["lessons"][key]["exercises_mastered"] == 1
    assert len(store.list_attempts("dana")) == 1

    # examen completo (respuestas desde el contenido)
    repo = ContentRepository()
    exam = repo.get_exam("soc-n1", "fase-1-examen")
    responses = []
    for q in exam.questions:
        a = q.answer
        if q.type == "free_text":
            ans = a["model_answer"]
        elif q.type == "fill_blank":
            ans = [b["accepted"][0] for b in a["blanks"]] if "blanks" in a else a["accepted"][0]
        elif q.type in ("multiple_select", "ordering"):
            ans = list(a)
        elif q.type == "matching":
            ans = dict(a)
        else:
            ans = a
        responses.append({"exercise_id": q.id, "answer": ans})

    sess = service.start_exam_session("dana", "soc-n1", "fase-1-examen")
    out = service.submit_exam("dana", "soc-n1", "fase-1-examen",
                              sess["session_id"], responses)
    # Iteración 2: respuestas perfectas + free_text contestada -> PENDIENTE de
    # revisión humana (nunca aprobado automáticamente)
    assert out["score"]["status"] == "pending_review"
    assert out["score"]["passed"] is None
    assert out["score"]["provisional_percent"] == 100.0

    # persistido en SQL y recuperable
    attempts = store.list_attempts("dana")
    exam_att = next(a for a in attempts if a["scope"]["type"] == "exam")
    report = service.get_attempt_report("dana", exam_att["id"], fmt="markdown")
    assert "Reporte de evaluación" in report["report"]

    # y el JSON sigue siendo idéntico en estructura (contrato del store)
    # Iteración 2: el examen perfecto con respuesta libre NO se aprueba solo
    assert json.loads(json.dumps(exam_att))["score"]["passed"] is None
    assert json.loads(json.dumps(exam_att))["score"]["status"] == "pending_review"


def test_env_switch_via_variable(monkeypatch, tmp_path):
    """KIWI_DATABASE_URL activa SQL sin tocar código (comportamiento de producción)."""
    db = tmp_path / "env.db"
    monkeypatch.setenv("KIWI_DATABASE_URL", f"sqlite:///{db}")
    # recargar settings para que tome la variable (en producción se define antes del arranque)
    import importlib
    from backend.app import config as cfg
    importlib.reload(cfg)
    importlib.reload(__import__("backend.app.storage.factory", fromlist=["factory"]))
    from backend.app.storage.factory import make_progress_store as fresh
    assert isinstance(fresh(), SqlProgressStore)
    # restaurar
    monkeypatch.delenv("KIWI_DATABASE_URL")
    importlib.reload(cfg)
    importlib.reload(__import__("backend.app.storage.factory", fromlist=["factory"]))
