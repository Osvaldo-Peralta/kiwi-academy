"""Corrección de auditoría (Iteración 2): atomicidad de la revisión humana.

Demuestra que la transición pending_review -> reviewed es atómica en ambos
stores (JSON y SQL/SQLite) bajo concurrencia real de hilos:
- exactamente UNA revisión gana;
- la perdedora recibe un conflicto controlado (ExamStateError, 409);
- no se duplican efectos secundarios (el estado final, los agregados del
  propietario y el reporte corresponden exclusivamente al ganador).
"""
import copy
import threading

import pytest

from backend.app.config import settings
from backend.app.core.errors import ExamStateError
from backend.app.core.services import LearningService
from backend.app.storage.json_store import JsonProgressStore
from backend.app.storage.repositories import ContentRepository, ProgressRepository
from backend.app.storage.sql_store import SqlProgressStore

COURSE = "soc-n1"
EXAM = "fase-1-examen"
FREE_ID = "ex-15"
FREE_MAX = 18


def make_store(kind: str, tmp_path):
    if kind == "json":
        return JsonProgressStore(root=tmp_path)
    return SqlProgressStore(f"sqlite:///{tmp_path}/race.db")


def make_service(store) -> LearningService:
    return LearningService(content=ContentRepository(), progress=ProgressRepository(store))


def create_pending_attempt(service, student: str) -> str:
    """Examen con respuesta libre contestada -> pending_review."""
    sess = service.start_exam_session(student, COURSE, EXAM)
    exam = ContentRepository().get_exam(COURSE, EXAM)
    responses = []
    for q in exam.questions:
        if q.type == "free_text":
            responses.append({"exercise_id": q.id, "answer": q.answer["model_answer"]})
        else:
            responses.append({"exercise_id": q.id, "answer": None})
    out = service.submit_exam(student, COURSE, EXAM, sess["session_id"], responses)
    assert out["score"]["status"] == "pending_review"
    return out["attempt_id"]


# ---------------------------------------------------------------------------
# CAS a nivel de store: 8 hilos, exactamente un True
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["json", "sqlite"])
def test_store_cas_exactly_one_winner(kind, tmp_path):
    store = make_store(kind, tmp_path)
    att = {
        "id": "att_cas", "user_id": "dev-u", "submitted_at": "2026-01-01T00:00:00+00:00",
        "scope": {"type": "exam", "course_id": COURSE, "exam_id": EXAM},
        "score": {"status": "pending_review", "points_total": 10},
    }
    store.save_attempt("dev-u", att)

    n = 8
    barrier = threading.Barrier(n, timeout=15)
    results: list = []
    results_lock = threading.Lock()

    def worker(i: int):
        new = copy.deepcopy(att)
        new["score"] = {"status": "reviewed", "winner": i, "points_total": 10}
        barrier.wait()
        ok = store.update_attempt_if_status("dev-u", "att_cas", "pending_review", new)
        with results_lock:
            results.append((i, ok))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
        assert not t.is_alive(), "hilo colgado (¿deadlock?)"

    wins = [i for i, ok in results if ok]
    assert len(wins) == 1, f"exactamente un ganador, obtenido: {results}"
    stored = store.get_attempt("dev-u", "att_cas")
    assert stored["score"]["status"] == "reviewed"
    assert stored["score"]["winner"] == wins[0]      # el estado es del ganador

    # segunda ronda con estado ya transitionado: nadie gana
    assert store.update_attempt_if_status(
        "dev-u", "att_cas", "pending_review",
        {**att, "score": {"status": "reviewed", "winner": 999}}) is False


# ---------------------------------------------------------------------------
# Carrera a nivel de servicio: dos revisores autorizados simultáneos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["json", "sqlite"])
def test_dos_revisiones_concurrentes_una_gana_sin_efectos_duplicados(kind, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "REVIEWER_USERS", "dev-rev-a,dev-rev-b")
    store = make_store(kind, tmp_path)
    service = make_service(store)

    for ronda in range(3):  # varias rondas para reducir falsos negativos
        student = f"dev-student-r{ronda}"
        att_id = create_pending_attempt(service, student)

        resultados: dict = {}
        barrier = threading.Barrier(2, timeout=15)

        def revisar(nombre: str, puntos: float):
            def run():
                try:
                    barrier.wait()
                    out = service.review_attempt(
                        nombre, att_id,
                        [{"exercise_id": FREE_ID, "points_earned": puntos,
                          "feedback": f"feedback-exclusivo-de-{nombre}"}],
                        comment=f"comentario-de-{nombre}",
                    )
                    resultados[nombre] = ("ok", out)
                except Exception as exc:  # noqa: BLE001 — la prueba clasifica
                    resultados[nombre] = ("err", exc)
            return run

        # Puntos distinguibles: el estado final debe corresponder a UNO de ellos
        t_a = threading.Thread(target=revisar("dev-rev-a", FREE_MAX))   # máximos puntos libres
        t_b = threading.Thread(target=revisar("dev-rev-b", 0))          # cero puntos libres
        t_a.start(); t_b.start()
        t_a.join(timeout=20); t_b.join(timeout=20)
        assert not (t_a.is_alive() or t_b.is_alive())

        ganadores = [n for n, (st, _) in resultados.items() if st == "ok"]
        perdedores = [n for n, (st, _) in resultados.items() if st == "err"]
        assert len(ganadores) == 1, f"ronda {ronda}: exactamente un ganador: {resultados}"
        assert len(perdedores) == 1
        winner = ganadores[0]
        loser_exc = resultados[perdedores[0]][1]

        # 1) el perdedor recibe un conflicto CONTROLADO (409, reason not_pending)
        assert isinstance(loser_exc, ExamStateError)
        assert loser_exc.status_code == 409
        assert loser_exc.details.get("reason") == "not_pending"

        # 2) estado final = linealización del ganador (nunca mezcla)
        _, stored = store.find_attempt_any_user(att_id)
        assert stored["score"]["status"] == "reviewed"
        assert stored["review"]["reviewer"] == winner
        esperado = FREE_MAX if winner == "dev-rev-a" else 0
        assert stored["score"]["points_earned_reviewed"] == esperado
        final_pct = round(esperado / stored["score"]["points_total"] * 100, 1)
        assert stored["score"]["final_percent"] == final_pct
        assert stored["score"]["passed"] is (final_pct >= stored["score"]["pass_score"])

        # 3) agregados del propietario coherentes con el ganador, sin duplicar
        prog = store.load_progress(student)
        ep = prog["exams"][f"{COURSE}:{EXAM}"]
        assert ep["attempts"] == 1                       # la revisión NO suma intentos
        assert ep["best_percent"] == final_pct             # solo el final del ganador
        assert ep["passed"] is stored["score"]["passed"]
        assert ep["last_status"] == "reviewed"

        # 4) el reporte refleja SOLO al ganador (el perdedor no ejecutó efectos)
        report = service.get_attempt_report(student, att_id, fmt="markdown")["report"]
        assert f"feedback-exclusivo-de-{winner}" in report
        assert f"feedback-exclusivo-de-{perdedores[0]}" not in report
        assert f"comentario-de-{winner}" in report
        assert f"comentario-de-{perdedores[0]}" not in report
