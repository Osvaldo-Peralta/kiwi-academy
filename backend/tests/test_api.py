"""Tests de API de extremo a extremo con datos en directorio temporal.

Verifican el flujo completo del estudiante y, sobre todo, que NUNCA se
filtran respuestas al frontend.
"""
import json

import pytest
from fastapi.testclient import TestClient

from backend.app.core.services import LearningService
from backend.app.main import app
from backend.app.api.v1.deps import get_service
from backend.app.storage.json_store import JsonProgressStore
from backend.app.storage.repositories import ContentRepository, ProgressRepository


@pytest.fixture()
def client(tmp_path):
    service = LearningService(
        content=ContentRepository(),                       # contenido real del repo
        progress=ProgressRepository(JsonProgressStore(root=tmp_path)),  # datos aislados
    )
    app.dependency_overrides[get_service] = lambda: service
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


COURSE = "soc-n1"
MOD1 = "01-fundamentos"
LESSON1 = "01-modelo-osi-protocolos"


# ------------------------------------------------------------------ catálogo
def test_list_courses(client):
    data = client.get("/api/v1/courses").json()
    assert any(c["id"] == COURSE for c in data["courses"])


def test_course_detail_has_4_modules(client):
    data = client.get(f"/api/v1/courses/{COURSE}").json()
    assert len(data["modules"]) == 4


def test_404_curso_inexistente(client):
    r = client.get("/api/v1/courses/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


# ------------------------------------------------------------------ anti-fugas
def test_lesson_no_leaks_answers(client):
    data = client.get(f"/api/v1/courses/{COURSE}/modules/{MOD1}/lessons/{LESSON1}").json()
    raw = json.dumps(data)
    for forbidden in ('"answer"', '"explanation"', "accepted", "model_answer"):
        assert forbidden not in raw
    matching = [e for e in data["exercises"] if e["type"] == "matching"]
    for m in matching:
        assert "pairs" not in m           # pares crudo delataría la solución
        assert "left_items" in m and "right_items" in m


def test_exam_no_leaks_answers(client):
    data = client.get(f"/api/v1/courses/{COURSE}/exams/fase-1-examen").json()
    raw = json.dumps(data)
    assert '"answer"' not in raw
    assert "explanation" not in raw


# ------------------------------------------------------------------ práctica
def test_exercise_attempt_flow(client):
    url = f"/api/v1/courses/{COURSE}/modules/{MOD1}/lessons/{LESSON1}/exercises/l1-e1/attempt"

    bad = client.post(url, json={"answer": "a"}).json()
    assert bad["correct"] is False
    assert bad["hint"]                     # pista solo cuando falla
    assert bad["explanation"]              # explicación solo cuando falla

    good = client.post(url, json={"answer": "b"}).json()
    assert good["correct"] is True
    assert good["lesson_progress"]["exercises_mastered"] == 1
    assert good["lesson_progress"]["status"] == "in_progress"


def test_mark_theory_and_complete_lesson(client):
    base = f"/api/v1/courses/{COURSE}/modules/{MOD1}/lessons/{LESSON1}"
    client.post(f"{base}/progress", json={"theory_completed": True})
    lesson = client.get(base).json()
    assert lesson["progress"]["theory_completed"] is True

    # dominar TODOS los ejercicios -> lección completa
    # (los free_text cuentan como "respondidos" al enviarlos)
    from backend.app.storage.repositories import ContentRepository as CR
    repo = CR()
    full = repo.get_lesson(COURSE, MOD1, LESSON1)
    for ex in full.exercises:
        answer = ex.answer
        if ex.type == "free_text":
            payload = answer["model_answer"]
        elif ex.type == "multiple_choice":
            payload = answer
        elif ex.type == "multiple_select":
            payload = list(answer)
        elif ex.type == "fill_blank":
            if "blanks" in answer:
                payload = [b["accepted"][0] for b in answer["blanks"]]
            else:
                payload = answer["accepted"][0]
        elif ex.type == "ordering":
            payload = list(answer)
        elif ex.type == "matching":
            payload = dict(answer)
        r = client.post(f"{base}/exercises/{ex.id}/attempt", json={"answer": payload}).json()
        if ex.type == "free_text":
            assert r["correct"] is None and r["needs_review"] is True
        else:
            assert r["correct"] is True, f"{ex.id} debería ser correcto"

    prog = client.get(base).json()["progress"]
    assert prog["status"] == "completed"
    assert prog["exercises_done"] == len(full.exercises)


# ------------------------------------------------------------------ examen
def test_exam_full_submission_and_report(client):
    exam = client.get(f"/api/v1/courses/{COURSE}/exams/fase-1-examen").json()
    from backend.app.storage.repositories import ContentRepository as CR
    full_exam = CR().get_exam(COURSE, "fase-1-examen")
    correct = {q.id: q.answer for q in full_exam.questions}

    # envío perfecto menos una mal a propósito (free_text con keywords)
    responses = []
    for q in full_exam.questions:
        if q.type == "free_text":
            responses.append({"exercise_id": q.id, "answer": q.answer["model_answer"]})
        elif q.type == "fill_blank" and "blanks" in q.answer:
            responses.append({"exercise_id": q.id, "answer": [b["accepted"][0] for b in q.answer["blanks"]]})
        elif q.type == "fill_blank":
            responses.append({"exercise_id": q.id, "answer": q.answer["accepted"][0]})
        else:
            responses.append({"exercise_id": q.id, "answer": correct[q.id]})

    r = client.post(
        f"/api/v1/courses/{COURSE}/exams/fase-1-examen/attempts",
        json={"responses": responses, "duration_seconds": 900},
    ).json()

    assert r["score"]["passed"] is True
    assert r["score"]["percent"] >= 90
    assert r["score"]["pending_review"] == 1        # la free_text
    assert r["report_markdown"].startswith("# Reporte")
    assert "Solicitud de feedback" in r["report_markdown"]

    # reporte recuperable por ID, en ambos formatos
    att_id = r["attempt_id"]
    md = client.get(f"/api/v1/courses/{COURSE}/attempts/{att_id}/report?format=markdown").json()
    assert md["format"] == "markdown" and "Respuesta correcta" in md["report"]
    js = client.get(f"/api/v1/courses/{COURSE}/attempts/{att_id}/report?format=json").json()
    assert "exercise_solutions" in js["report"]


def test_exam_zero_score_fails_and_recommends(client):
    exam = client.get(f"/api/v1/courses/{COURSE}/exams/fase-1-examen").json()
    responses = [{"exercise_id": q["id"], "answer": None} for q in exam["questions"]]
    r = client.post(
        f"/api/v1/courses/{COURSE}/exams/fase-1-examen/attempts",
        json={"responses": responses},
    ).json()
    assert r["score"]["passed"] is False
    assert r["score"]["percent"] == 0.0
    assert r["weak_tags"], "debe detectar áreas débiles"
    assert any("examen" in rec.lower() for rec in r["recommendations"])


# ------------------------------------------------------------------ progreso
def test_dashboard_reflects_activity(client):
    url = f"/api/v1/courses/{COURSE}/modules/{MOD1}/lessons/{LESSON1}/exercises/l1-e1/attempt"
    client.post(url, json={"answer": "b"})
    dash = client.get("/api/v1/progress").json()
    course = next(c for c in dash["courses"] if c["id"] == COURSE)
    l1 = next(l for m in course["modules"] if m["id"] == MOD1 for l in m["lessons"] if l["id"] == LESSON1)
    assert l1["progress_status"] == "in_progress"
    assert dash["overall"]["exercises_mastered"] >= 1
    assert dash["next_actions"], "el dashboard sugiere próximas acciones"


def test_multiuser_isolation(client):
    url = f"/api/v1/courses/{COURSE}/modules/{MOD1}/lessons/{LESSON1}/exercises/l1-e1/attempt"
    client.post(url, json={"answer": "b"}, headers={"X-User-Id": "alice"})
    dash_alice = client.get("/api/v1/progress", headers={"X-User-Id": "alice"}).json()
    dash_bob = client.get("/api/v1/progress", headers={"X-User-Id": "bob"}).json()
    assert dash_alice["overall"]["exercises_mastered"] == 1
    assert dash_bob["overall"]["exercises_mastered"] == 0
