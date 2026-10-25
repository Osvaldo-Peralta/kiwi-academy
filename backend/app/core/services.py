"""Capa de servicios: la lógica de negocio del laboratorio.

Orquesta repositorios + motor de evaluación + feedback. La API solo
traduce HTTP <-> servicios; los servicios no saben nada de HTTP.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from .errors import (
    ExamStateError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from .evaluation import grade_exercise, validate_exam_responses
from .feedback import (
    build_attempt_feedback,
    render_attempt_json,
    render_report_markdown,
)
from .models import Attempt, Exercise, QuestionResult
from ..config import settings
from ..storage.repositories import ContentRepository, ProgressRepository


SESSIONS_KEY = "exam_sessions"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def lesson_key(course_id: str, module_id: str, lesson_id: str) -> str:
    return f"{course_id}:{module_id}:{lesson_id}"


def exam_key(course_id: str, exam_id: str) -> str:
    return f"{course_id}:{exam_id}"


class LearningService:
    def __init__(
        self,
        content: Optional[ContentRepository] = None,
        progress: Optional[ProgressRepository] = None,
    ):
        self.content = content or ContentRepository()
        self.progress = progress or ProgressRepository()

    # ------------------------------------------------------------------
    # Catálogo
    # ------------------------------------------------------------------

    def tag_to_lessons(self, course_id: str) -> dict[str, list[str]]:
        """Mapa tag -> [títulos de lección] para recomendaciones de repaso."""
        out: dict[str, list[str]] = {}
        for lesson in self.content.all_lessons(course_id):
            for ex in lesson.exercises:
                for tag in ex.tags:
                    out.setdefault(tag, [])
                    if lesson.title not in out[tag]:
                        out[tag].append(lesson.title)
        return out

    # ------------------------------------------------------------------
    # Lecciones
    # ------------------------------------------------------------------

    def get_lesson_public(
        self, user_id: Optional[str], course_id: str, module_id: str, lesson_id: str,
    ) -> dict[str, Any]:
        lesson = self.content.get_lesson(course_id, module_id, lesson_id)
        # AUTH-04: las lecciones no publicadas no se sirven a nadie (404).
        # Su metadata (título/estado) sigue visible en el catálogo del curso.
        if lesson.status != "published":
            raise NotFoundError(
                f"Lección no encontrada: {course_id}/{module_id}/{lesson_id}"
            )
        data = lesson.public()
        if user_id:
            prog = self.progress.get_progress(user_id)
            data["progress"] = prog.get("lessons", {}).get(
                lesson_key(course_id, module_id, lesson_id),
                {"status": "not_started", "exercises_mastered": 0,
                 "exercises_reviewed": 0, "exercises_done": 0,
                 "exercises_total": len(lesson.exercises)},
            )
        else:
            # invitado sin sesión: progreso en ceros (modo lectura)
            data["progress"] = {
                "status": "not_started", "exercises_mastered": 0,
                "exercises_reviewed": 0, "exercises_done": 0,
                "exercises_total": len(lesson.exercises),
            }
        data["exercise_count"] = len(lesson.exercises)
        return data

    def course_catalog(self, course_id: str) -> dict[str, Any]:
        """Catálogo público (invitados): metadata de módulos/lecciones/examen."""
        course = self.content.get_course(course_id)
        modules_out = []
        for mod in self.content.list_modules(course_id):
            lessons_out = []
            for lid in mod.lessons:
                lesson = self.content.get_lesson(course_id, mod.id, lid)
                lessons_out.append({
                    "id": lid,
                    "title": lesson.title,
                    "kind": lesson.kind,
                    "status": lesson.status,
                    "summary": lesson.summary,
                    "estimated_minutes": lesson.estimated_minutes,
                    "exercise_count": len(lesson.exercises),
                })
            exam_out = None
            if mod.exam:
                try:
                    exam = self.content.get_exam(course_id, mod.exam)
                    exam_out = {
                        "id": exam.id, "title": exam.title,
                        "pass_score": exam.pass_score,
                        "time_limit_minutes": exam.time_limit_minutes,
                        "question_count": len(exam.questions),
                    }
                except NotFoundError:
                    exam_out = None
            modules_out.append({
                "id": mod.id, "title": mod.title, "phase": mod.phase,
                "subtitle": mod.subtitle, "duration": mod.duration,
                "outcome": mod.outcome, "lessons": lessons_out, "exam": exam_out,
                "resources": [r.model_dump(exclude_none=True) for r in mod.resources],
            })
        return {"course": course.model_dump(), "modules": modules_out}

    def mark_lesson_progress(
        self,
        user_id: str,
        course_id: str,
        module_id: str,
        lesson_id: str,
        theory_completed: Optional[bool] = None,
        examples_viewed: Optional[bool] = None,
    ) -> dict[str, Any]:
        lesson = self.content.get_lesson(course_id, module_id, lesson_id)
        key = lesson_key(course_id, module_id, lesson_id)

        def _mut(prog: dict[str, Any]) -> dict[str, Any]:
            lp = prog.setdefault("lessons", {}).setdefault(key, self._empty_lesson_progress(lesson))
            self._sync_lesson_totals(lp, lesson)
            if theory_completed is not None:
                lp["theory_completed"] = bool(theory_completed)
            if examples_viewed is not None:
                lp["examples_viewed"] = bool(examples_viewed)
            self._recompute_lesson_status(lp)
            lp["last_activity_at"] = _now().isoformat()
            self._touch_activity(prog)
            return lp

        # Iteración 3 (DATA-03): read-modify-write atómico
        return dict(self.progress.mutate_progress(user_id, _mut))

    def attempt_lesson_exercise(
        self, user_id: str, course_id: str, module_id: str, lesson_id: str,
        exercise_id: str, answer: Any,
    ) -> dict[str, Any]:
        """Evalúa UN ejercicio de lección (modo práctica con feedback inmediato)."""
        lesson = self.content.get_lesson(course_id, module_id, lesson_id)
        ex = next((e for e in lesson.exercises if e.id == exercise_id), None)
        if ex is None:
            raise NotFoundError(f"Ejercicio no encontrado: {exercise_id}")

        outcome = grade_exercise(ex, answer)

        # Guardar intento individual (historial completo)
        result = QuestionResult(
            exercise_id=ex.id, type=ex.type, submitted=answer,
            correct=outcome.correct, partial=(0 < outcome.ratio < 1),
            points_earned=round(ex.points * outcome.ratio, 2),
            points_total=ex.points,
            feedback=outcome.feedback, explanation=ex.explanation,
            tags=ex.tags,
        )
        attempt = Attempt(
            id=self.progress.new_attempt_id(),
            user_id=user_id,
            scope={
                "type": "lesson", "course_id": course_id,
                "module_id": module_id, "lesson_id": lesson_id,
                "exercise_id": exercise_id,
            },
            submitted_at=_now(),
            score={
                "points_earned": result.points_earned,
                "points_total": result.points_total,
                "percent": round(outcome.ratio * 100, 1),
                "passed": bool(outcome.correct),
            },
            results=[result],
        )
        key = lesson_key(course_id, module_id, lesson_id)

        def _mut(prog: dict[str, Any]) -> dict[str, Any]:
            lp = prog.setdefault("lessons", {}).setdefault(key, self._empty_lesson_progress(lesson))
            self._sync_lesson_totals(lp, lesson)
            lp["attempts"] = lp.get("attempts", 0) + 1
            mastered = lp.setdefault("mastered_exercises", [])
            reviewed = lp.setdefault("reviewed_exercises", [])
            if ex.type == "free_text":
                # respuesta libre enviada y no vacía => queda "respondida"
                # (revisión humana/IA vía reporte); cuenta para completar
                if outcome.needs_review and ex.id not in reviewed:
                    reviewed.append(ex.id)
            elif outcome.correct and ex.id not in mastered:
                mastered.append(ex.id)
            lp["exercises_mastered"] = len(mastered)
            # accuracy histórica sobre ejercicios autocorregibles
            acc = lp.get("best_accuracy", 0.0)
            lp["best_accuracy"] = max(acc, outcome.ratio) if outcome.correct is not None else acc
            self._recompute_lesson_status(lp)
            lp["last_activity_at"] = _now().isoformat()
            self._touch_activity(prog)
            return lp

        # Iteración 3 (DATA-01/03): intento + progreso en UNA transacción;
        # si algo falla, no quedan registros en estados incompatibles.
        with self.progress.transaction(user_id):
            self.progress.save_attempt(user_id, attempt.model_dump(mode="json"))
            lp = self.progress.mutate_progress(user_id, _mut)

        return {
            "exercise_id": ex.id,
            "correct": outcome.correct,
            "ratio": round(outcome.ratio, 3),
            "points_earned": result.points_earned,
            "points_total": ex.points,
            "feedback": outcome.feedback,
            "explanation": ex.explanation if not outcome.correct else "",
            "needs_review": outcome.needs_review,
            "hint": None if outcome.correct else ex.hint,
            "lesson_progress": lp,
        }

    # ------------------------------------------------------------------
    # Exámenes
    # ------------------------------------------------------------------

    def get_exam_public(self, course_id: str, exam_id: str) -> dict[str, Any]:
        exam = self.content.get_exam(course_id, exam_id)
        return exam.public()

    def start_exam_session(self, user_id: str, course_id: str, exam_id: str) -> dict[str, Any]:
        """Iteración 2 (EVAL-02): el servidor emite la sesión del examen y
        mide el tiempo. El temporizador del cliente es solo informativo:
        la expiración se valida SIEMPRE aquí con reloj de servidor."""
        exam = self.content.get_exam(course_id, exam_id)
        now = _now()
        session_id = secrets.token_urlsafe(16)
        deadline_unix: Optional[float] = None
        if exam.time_limit_minutes:
            deadline_unix = now.timestamp() + exam.time_limit_minutes * 60 + settings.EXAM_GRACE_SECONDS

        key = exam_key(course_id, exam_id)

        def _mut(prog: dict[str, Any]) -> None:
            sessions = prog.setdefault(SESSIONS_KEY, {})
            sessions[key] = {
                "session_id": session_id,
                "started_at": now.isoformat(),
                "deadline_unix": deadline_unix,
            }

        self.progress.mutate_progress(user_id, _mut)
        return {
            "exam_id": exam_id,
            "session_id": session_id,
            "server_time_unix": now.timestamp(),
            "deadline_unix": deadline_unix,
            "time_limit_minutes": exam.time_limit_minutes,
            "grace_seconds": settings.EXAM_GRACE_SECONDS if exam.time_limit_minutes else 0,
        }

    def _replay_exam_response(self, attempt_data: dict[str, Any], exam, course) -> dict[str, Any]:
        """Iteración 3 (DATA-02): reproducción del resultado original para un
        reintento con la misma sesión (idempotencia exacta: mismo attempt_id,
        ningún efecto duplicado)."""
        attempt = Attempt.model_validate(attempt_data)
        md = render_report_markdown(
            attempt, exam.questions,
            course_title=course.title, scope_title=exam.title,
        )
        return {
            "attempt_id": attempt.id,
            "score": attempt.score,
            "results": [r.model_dump(mode="json") for r in attempt.results],
            "feedback_summary": attempt.feedback_summary,
            "weak_tags": attempt.weak_tags,
            "recommendations": attempt.recommendations,
            "report_markdown": md,
            "idempotent_replay": True,
        }

    def submit_exam(
        self, user_id: str, course_id: str, exam_id: str,
        session_id: str,
        responses: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Corrige el examen completo.

        Iteración 2: sesión de servidor (tiempo), validación estricta,
        puntaje provisional y respuestas libres pendientes de revisión.
        Iteración 3 (DATA-01/02/03):
        - TODO el flujo (chequeos + intento + progreso + reporte) corre en
          UNA transacción del store: fallo => rollback, cero estados parciales
          (SQL) o reparación derivable desde los intentos (JSON, single-proceso).
        - La sesión es la clave de idempotencia: reenviar una sesión ya
          consumida NO duplica el intento; reproduce el resultado original
          (`idempotent_replay: true`). Dos envíos concurrentes con la misma
          sesión se serializan: uno califica y el otro recibe el replay.
        """
        exam = self.content.get_exam(course_id, exam_id)
        course = self.content.get_course(course_id)
        key = exam_key(course_id, exam_id)

        # Validación estructural (pura; antes de tocar estado)
        by_id = validate_exam_responses(exam, responses)

        outcome_kind = "ok"
        payload: Any = None

        with self.progress.transaction(user_id):
            now = _now()
            prog = self.progress.get_progress(user_id)
            sessions = prog.get(SESSIONS_KEY, {}) or {}
            session = sessions.get(key)

            if not session or session.get("session_id") != session_id:
                outcome_kind, payload = "invalid", None
            elif session.get("consumed_attempt"):
                # (a) reintento idempotente: reproducir el resultado original
                original = self.progress.store.get_attempt(user_id, session["consumed_attempt"])
                if original:
                    outcome_kind, payload = "replay", self._replay_exam_response(original, exam, course)
                else:
                    outcome_kind, payload = "invalid", None
            else:
                deadline = session.get("deadline_unix")
                if deadline is not None and now.timestamp() > float(deadline):
                    # (b) expirada según reloj de servidor: invalidar y rechazar
                    sessions.pop(key, None)
                    prog[SESSIONS_KEY] = sessions
                    self.progress.save_progress(user_id, prog)
                    outcome_kind, payload = "expired", None
                else:
                    # (c) corrección (CPU pura dentro de la transacción)
                    results: list[QuestionResult] = []
                    points_total = 0
                    auto_points_total = 0
                    pending_points_total = 0
                    points_earned_auto = 0.0
                    pending_review = 0
                    for q in exam.questions:
                        answer = by_id.get(q.id)
                        g = grade_exercise(q, answer)
                        review_status = "auto"
                        if q.type == "free_text":
                            review_status = (g.detail or {}).get(
                                "review_status", "pending" if g.needs_review else "not_answered")
                        earned = round(q.points * g.ratio, 2)
                        points_total += q.points
                        if review_status == "pending":
                            pending_points_total += q.points
                            pending_review += 1
                            earned = 0.0
                        else:
                            auto_points_total += q.points
                            points_earned_auto += earned
                        results.append(QuestionResult(
                            exercise_id=q.id, type=q.type, submitted=answer,
                            correct=g.correct, partial=(0 < g.ratio < 1),
                            points_earned=earned, points_total=q.points,
                            feedback=g.feedback, explanation=q.explanation,
                            tags=q.tags, review_status=review_status,
                        ))

                    status = "pending_review" if pending_review else "auto_graded"
                    provisional_percent = (
                        round((points_earned_auto / auto_points_total) * 100, 1)
                        if auto_points_total else 0.0
                    )
                    passed = None if status == "pending_review" else (provisional_percent >= exam.pass_score)
                    final_percent = None if status == "pending_review" else provisional_percent

                    tag_map = self.tag_to_lessons(course_id)
                    summary, weak, recs = build_attempt_feedback(results, provisional_percent, passed, tag_map)
                    if status == "pending_review":
                        summary = (
                            f"⏳ Resultado PROVISIONAL sobre la parte automática "
                            f"({pending_review} respuesta(s) libre(s) pendientes de revisión humana; "
                            f"el examen no está aprobado ni reprobado hasta completarla). " + summary
                        )
                        recs.insert(0, (
                            "Espera la revisión humana de tus respuestas libres: hasta entonces "
                            "el veredicto definitivo queda suspendido (los puntos libres no se "
                            "otorgan automáticamente)."
                        ))

                    started_iso = session.get("started_at")
                    started_dt = datetime.fromisoformat(started_iso) if started_iso else None
                    duration = int((now - started_dt).total_seconds()) if started_dt else None

                    attempt = Attempt(
                        id=self.progress.new_attempt_id(),
                        user_id=user_id,
                        scope={
                            "type": "exam", "course_id": course_id,
                            "module_id": exam.module_id, "exam_id": exam_id,
                        },
                        started_at=started_dt,
                        submitted_at=now,
                        duration_seconds=duration,
                        score={
                            "points_total": points_total,
                            "auto_points_total": auto_points_total,
                            "pending_points_total": pending_points_total,
                            "points_earned_auto": round(points_earned_auto, 2),
                            "points_earned_reviewed": 0.0,
                            "points_earned": round(points_earned_auto, 2),
                            "provisional_percent": provisional_percent,
                            "percent": provisional_percent,
                            "final_percent": final_percent,
                            "status": status,
                            "passed": passed,
                            "pass_score": exam.pass_score,
                            "pending_review": pending_review,
                        },
                        results=results,
                        weak_tags=weak,
                        feedback_summary=summary,
                        recommendations=recs,
                    )
                    attempt_data = attempt.model_dump(mode="json")

                    # (d) persistencia atómica: intento + sesión consumida +
                    # agregados + reporte, todo dentro de la transacción
                    self.progress.save_attempt(user_id, attempt_data)

                    def _mut(prog: dict[str, Any]) -> None:
                        sess = prog.setdefault(SESSIONS_KEY, {})
                        rec = dict(sess.get(key) or {})
                        rec["consumed_attempt"] = attempt.id
                        rec["consumed_at"] = now.isoformat()
                        sess[key] = rec
                        ep = prog.setdefault("exams", {}).setdefault(
                            key, {"attempts": 0, "best_percent": 0.0, "passed": False}
                        )
                        ep["attempts"] = ep.get("attempts", 0) + 1
                        ep["last_attempt_id"] = attempt.id
                        ep["last_attempt_at"] = now.isoformat()
                        ep["last_status"] = status
                        if status == "auto_graded":
                            ep["best_percent"] = max(ep.get("best_percent", 0.0), provisional_percent)
                            ep["passed"] = bool(ep.get("passed") or passed)
                        self._touch_activity(prog)

                    self.progress.mutate_progress(user_id, _mut)
                    md = render_report_markdown(
                        attempt, exam.questions,
                        course_title=course.title, scope_title=exam.title,
                    )
                    self.progress.save_report(user_id, attempt.id, "report.md", md)
                    outcome_kind = "ok"
                    payload = {
                        "attempt_id": attempt.id,
                        "score": attempt.score,
                        "results": [r.model_dump(mode="json") for r in results],
                        "feedback_summary": summary,
                        "weak_tags": weak,
                        "recommendations": recs,
                        "report_markdown": md,
                        "idempotent_replay": False,
                    }

        # Errores de estado se lanzan FUERA de la transacción (lo escrito en
        # cada rama —p.ej. invalidación por expiración— ya se commiteó).
        if outcome_kind == "invalid":
            raise ExamStateError(
                "Sesión de examen inválida, inexistente o ya consumida sin "
                "registro original. Inicia el examen para que el servidor "
                "emita una sesión.",
                reason="session_invalid",
            )
        if outcome_kind == "expired":
            raise ExamStateError(
                "El tiempo límite autorizado ya expiró (verificación de servidor). "
                "La sesión fue invalidada; inicia el examen de nuevo.",
                reason="exam_expired",
            )
        return payload

    # ------------------------------------------------------------------
    # Revisión humana (Iteración 2)
    # ------------------------------------------------------------------

    def _require_reviewer(self, reviewer_sub: str) -> None:
        allowed = {s.strip() for s in (settings.REVIEWER_USERS or "").split(",") if s.strip()}
        if reviewer_sub not in allowed:
            raise PermissionDeniedError(
                "Tu usuario no está autorizado para revisar exámenes "
                "(KIWI_REVIEWER_USERS)."
            )

    def review_data(self, reviewer_sub: str, attempt_id: str) -> dict[str, Any]:
        """Datos para revisar: intento + preguntas libres pendientes con su
        respuesta modelo (solo revisores autorizados)."""
        self._require_reviewer(reviewer_sub)
        owner_id, data = self.progress.find_attempt_any_user(attempt_id)
        if not data:
            raise NotFoundError(f"Intento no encontrado: {attempt_id}")
        attempt = Attempt.model_validate(data)
        if attempt.scope.type != "exam":
            raise ValidationError("Solo los exámenes admiten revisión humana.")
        exam = self.content.get_exam(attempt.scope.course_id, attempt.scope.exam_id or "")
        q_by_id = {q.id: q for q in exam.questions}
        pending = []
        for r in attempt.results:
            if r.review_status not in ("pending", "reviewed"):
                continue
            q = q_by_id.get(r.exercise_id)
            item: dict[str, Any] = {
                "exercise_id": r.exercise_id,
                "prompt": q.prompt if q else "",
                "points_total": r.points_total,
                "points_earned": r.points_earned,
                "review_status": r.review_status,
                "submitted": r.submitted,
                "auto_feedback": r.feedback,
            }
            if q and isinstance(q.answer, dict):
                item["model_answer"] = q.answer.get("model_answer", "")
                item["keywords"] = q.answer.get("keywords", [])
            pending.append(item)
        return {
            "attempt": {
                "id": attempt.id,
                "owner": owner_id,
                "submitted_at": attempt.submitted_at.isoformat(),
                "duration_seconds": attempt.duration_seconds,
                "score": attempt.score,
                "review": attempt.review,
            },
            "exam_title": exam.title,
            "free_text_questions": pending,
        }

    def review_attempt(
        self, reviewer_sub: str, attempt_id: str,
        reviews: list[dict[str, Any]],
        comment: Optional[str] = None,
    ) -> dict[str, Any]:
        """Finaliza un examen pendiente: un revisor autorizado asigna los
        puntos de cada respuesta libre y se calcula el resultado definitivo."""
        self._require_reviewer(reviewer_sub)
        owner_id, data = self.progress.find_attempt_any_user(attempt_id)
        if not data:
            raise NotFoundError(f"Intento no encontrado: {attempt_id}")
        attempt = Attempt.model_validate(data)
        if attempt.scope.type != "exam":
            raise ValidationError("Solo los exámenes admiten revisión humana.")
        if (attempt.score or {}).get("status") != "pending_review":
            raise ExamStateError(
                f"El intento no está pendiente de revisión (status="
                f"{(attempt.score or {}).get('status')}).",
                reason="not_pending",
            )

        pending = {r.exercise_id: r for r in attempt.results if r.review_status == "pending"}
        got_ids = [str(rv.get("exercise_id")) for rv in reviews]
        if set(got_ids) != set(pending.keys()) or len(got_ids) != len(set(got_ids)):
            raise ValidationError(
                "La revisión debe cubrir exactamente una vez cada respuesta "
                "libre pendiente.",
                problems=[{
                    "problem": "review_coverage",
                    "pending_ids": sorted(pending.keys()),
                    "got_ids": sorted(set(got_ids)),
                }],
            )
        for rv in reviews:
            r = pending[str(rv["exercise_id"])]
            pts = rv.get("points_earned")
            if not isinstance(pts, (int, float)) or pts < 0 or pts > r.points_total:
                raise ValidationError(
                    f"Puntos inválidos para {r.exercise_id}: debe ser un número "
                    f"entre 0 y {r.points_total:g}.",
                    problems=[{"problem": "points_out_of_range", "exercise_id": r.exercise_id}],
                )
            fb = rv.get("feedback")
            if fb is not None and (not isinstance(fb, str) or len(fb) > 2000):
                raise ValidationError(
                    f"Feedback de {r.exercise_id} debe ser texto de ≤2000 caracteres.",
                    problems=[{"problem": "feedback_invalid", "exercise_id": r.exercise_id}],
                )

        # Aplica la revisión
        reviewed_points = 0.0
        question_feedback: dict[str, str] = {}
        for rv in reviews:
            r = pending[str(rv["exercise_id"])]
            r.points_earned = round(float(rv["points_earned"]), 2)
            r.review_status = "reviewed"
            if rv.get("feedback"):
                r.feedback = f"{r.feedback} · Revisor: {rv['feedback']}"
                question_feedback[r.exercise_id] = rv["feedback"]
            reviewed_points += r.points_earned

        s = dict(attempt.score or {})
        s["points_earned_reviewed"] = round(reviewed_points, 2)
        s["points_earned"] = round(s.get("points_earned_auto", 0.0) + reviewed_points, 2)
        final_percent = (
            round((s["points_earned"] / s["points_total"]) * 100, 1)
            if s.get("points_total") else 0.0
        )
        s["final_percent"] = final_percent
        s["percent"] = final_percent
        s["provisional_percent"] = s.get("provisional_percent", final_percent)
        s["passed"] = final_percent >= s.get("pass_score", 70)
        s["status"] = "reviewed"
        attempt.score = s
        attempt.review = {
            "reviewer": reviewer_sub,
            "reviewed_at": _now().isoformat(),
            "comment": comment,
            "question_feedback": question_feedback,
        }

        # Corrección de auditoría (Iter 3, punto 2): CAS + agregados + reporte
        # en UNA unidad transaccional cuando el adaptador lo permite:
        # - SQL: transacción real (la fila de progreso del propietario se
        #   bloquea con FOR UPDATE; el CAS usa la misma conexión; excepción =>
        #   ROLLBACK completo: el intento NO queda revisado si un efecto falla).
        # - JSON: serialización + diario copy-on-write => garantía equivalente
        #   en proceso (excepción => se restauran intento/progreso/reporte).
        # La consistencia normal ya NO depende de reparación manual;
        # repair_progress.py queda como mecanismo adicional ante catástrofes
        # externas (p. ej. muerte del proceso a mitad de una escritura).
        key = exam_key(attempt.scope.course_id, attempt.scope.exam_id or "")
        exam = self.content.get_exam(attempt.scope.course_id, attempt.scope.exam_id or "")
        course_title = ""
        try:
            course_title = self.content.get_course(attempt.scope.course_id).title
        except NotFoundError:
            pass
        md = render_report_markdown(
            attempt, exam.questions, course_title=course_title, scope_title=exam.title,
        )

        with self.progress.transaction(owner_id or ""):
            won = self.progress.update_attempt_if_status(
                owner_id or "", attempt.id, "pending_review",
                attempt.model_dump(mode="json"),
            )
            if not won:
                _, current = self.progress.find_attempt_any_user(attempt_id)
                if not current:
                    raise NotFoundError(f"Intento no encontrado: {attempt_id}")
                raise ExamStateError(
                    "El intento ya no está pendiente de revisión: otra revisión "
                    "completó la transición primero (conflicto controlado; esta "
                    "solicitud no tuvo efectos).",
                    reason="not_pending",
                )

            if owner_id:
                def _mut(prog: dict[str, Any]) -> None:
                    ep = prog.setdefault("exams", {}).setdefault(
                        key, {"attempts": 0, "best_percent": 0.0, "passed": False}
                    )
                    ep["best_percent"] = max(ep.get("best_percent", 0.0), final_percent)
                    ep["passed"] = bool(ep.get("passed") or s["passed"])
                    if ep.get("last_attempt_id") == attempt.id:
                        ep["last_status"] = "reviewed"

                self.progress.mutate_progress(owner_id, _mut)
                self.progress.save_report(owner_id, attempt.id, "report.md", md)

        return {
            "attempt_id": attempt.id,
            "score": attempt.score,
            "review": attempt.review,
            "report_markdown": md,
        }

    # ------------------------------------------------------------------
    # Recuperación (Iteración 3, DATA-06): los intentos (append-only) son la
    # fuente de verdad; los agregados del progreso son datos derivados y
    # pueden reconstruirse tras un fallo parcial.
    # ------------------------------------------------------------------

    def rebuild_progress_from_attempts(self, user_id: str) -> dict[str, Any]:
        """Reconstruye los agregados derivados (exams y dominio de ejercicios
        por lección) desde el historial de intentos. Preserva datos no
        derivados (theory_completed, examples_viewed, activity, sesiones).

        Uso: recuperación tras estado parcial (store JSON/crash) y herramienta
        de consistencia (tools/repair_progress.py). Idempotente.

        Contrapartes de solo lectura (corrección de auditoría PR #4, store
        degradado): `compute_repaired_progress` (calcula el progreso reparado
        SIN escribir) y `validate_progress_repair` (acredita que el progreso
        en disco es consistente con los intentos; se usa como validador de la
        ruta explícita de recuperación semántica del store,
        `verify_semantic_recovery`).
        """
        return self.progress.mutate_progress(
            user_id, lambda prog: self._apply_rebuilt_aggregates(user_id, prog))

    def _apply_rebuilt_aggregates(self, user_id: str, prog: dict[str, Any]) -> dict[str, Any]:
        """Reconstruye los agregados derivados desde los intentos y los aplica
        IN PLACE sobre `prog` (sin escribir en el store). Devuelve el resumen
        {"exams": n, "lessons": m}."""
        attempts = self.progress.list_attempts(user_id, limit=1_000_000)

        exams_agg: dict[str, dict[str, Any]] = {}
        lessons_agg: dict[str, dict[str, Any]] = {}
        for a in sorted(attempts, key=lambda x: str(x.get("submitted_at", ""))):
            scope = a.get("scope") or {}
            score = a.get("score") or {}
            if scope.get("type") == "exam" and scope.get("exam_id"):
                key = exam_key(scope.get("course_id", ""), scope["exam_id"])
                ep = exams_agg.setdefault(
                    key, {"attempts": 0, "best_percent": 0.0, "passed": False})
                ep["attempts"] += 1
                ep["last_attempt_id"] = a.get("id")
                ep["last_attempt_at"] = a.get("submitted_at")
                st = score.get("status", "auto_graded")
                ep["last_status"] = st
                if st in ("auto_graded", "reviewed"):
                    pct = score.get("percent")
                    if pct is None:
                        pct = score.get("final_percent") or score.get("provisional_percent") or 0.0
                    ep["best_percent"] = max(ep.get("best_percent", 0.0), pct or 0.0)
                    ep["passed"] = bool(ep.get("passed") or score.get("passed"))
            elif scope.get("type") == "lesson" and scope.get("lesson_id"):
                key = lesson_key(scope.get("course_id", ""), scope.get("module_id", ""),
                                 scope["lesson_id"])
                la = lessons_agg.setdefault(
                    key, {"mastered": set(), "reviewed": set(), "attempts": 0,
                          "best_accuracy": 0.0})
                la["attempts"] += 1
                for r in a.get("results", []):
                    rs = r.get("review_status")
                    if rs == "pending" or (r.get("type") == "free_text" and r.get("submitted")):
                        la["reviewed"].add(r.get("exercise_id"))
                    elif r.get("correct") is True:
                        la["mastered"].add(r.get("exercise_id"))
                    ratio = (r.get("points_earned") or 0) / (r.get("points_total") or 1)
                    if r.get("correct") is not None:
                        la["best_accuracy"] = max(la["best_accuracy"], ratio)

        # exámenes: agregados 100% derivados -> se reemplazan
        prog["exams"] = {k: dict(v) for k, v in exams_agg.items()}
        # lecciones: se conservan flags no derivados (teoría/ejemplos)
        lessons = prog.setdefault("lessons", {})
        for key, la in lessons_agg.items():
            lp = lessons.get(key)
            course_id, module_id, lesson_id = key.split(":", 2)
            try:
                lesson = self.content.get_lesson(course_id, module_id, lesson_id)
            except NotFoundError:
                continue
            if lp is None:
                lp = self._empty_lesson_progress(lesson)
            lp["mastered_exercises"] = sorted(x for x in la["mastered"] if x)
            lp["reviewed_exercises"] = sorted(x for x in la["reviewed"] if x)
            lp["exercises_mastered"] = len(lp["mastered_exercises"])
            lp["exercises_reviewed"] = len(lp["reviewed_exercises"])
            lp["attempts"] = la["attempts"]
            lp["best_accuracy"] = la["best_accuracy"]
            self._sync_lesson_totals(lp, lesson)
            self._recompute_lesson_status(lp)
            lessons[key] = lp
        return {"exams": len(exams_agg), "lessons": len(lessons_agg)}

    def compute_repaired_progress(self, user_id: str) -> dict[str, Any]:
        """Calcula (SOLO LECTURA, sin escribir) el dict de progreso tal como
        debería quedar tras reconstruir los agregados derivados desde los
        intentos.

        Motivación (corrección de auditoría PR #4): un store JSON degradado
        rechaza TODA escritura vía API (`StoreDegradedError`), incluido este
        rebuild. La reparación a nivel de archivo —equivalente a lo que hace
        tools/repair_progress.py sobre una instancia no degradada— puede
        usar este método para obtener el contenido reparado, escribirlo
        directamente y acreditarlo después con la ruta explícita de
        recuperación semántica del store:
        `store.verify_semantic_recovery(
            lambda s: service.validate_progress_repair(user_id)[0])`."""
        prog = self.progress.get_progress(user_id)
        self._apply_rebuilt_aggregates(user_id, prog)
        return prog

    def validate_progress_repair(self, user_id: str) -> tuple[bool, dict[str, Any]]:
        """Valida (SOLO LECTURA) una reparación: el progreso actual en disco
        debe ser IGUAL a la reconstrucción desde el historial de intentos
        (los intentos son la fuente de verdad; los agregados, datos
        derivados).

        Destinado a servir de validador en la ruta explícita de recuperación
        semántica del store (`JsonProgressStore.verify_semantic_recovery`)
        tras una reparación con contenido legítimamente distinto del
        original (p. ej. tools/repair_progress.py). Devuelve (ok, detalle)."""
        actual = self.progress.get_progress(user_id)
        expected = self.compute_repaired_progress(user_id)
        ok = actual == expected
        detail: dict[str, Any] = {"consistent_with_attempts": ok}
        if not ok:
            detail["detalle"] = ("el progreso actual no coincide con la "
                                 "reconstrucción desde los intentos "
                                 "(reparación incompleta o agregados alterados)")
        return ok, detail

    # ------------------------------------------------------------------
    # Reportes / historial
    # ------------------------------------------------------------------

    def get_attempt_report(self, user_id: str, attempt_id: str, fmt: str = "markdown") -> dict[str, Any]:
        attempt_data = self.progress.get_attempt(user_id, attempt_id)
        attempt = Attempt.model_validate(attempt_data)
        exercises = self._exercises_for_attempt(attempt)
        scope = attempt.scope
        title = scope.exam_id or scope.lesson_id or "Evaluación"
        course_title = ""
        try:
            course_title = self.content.get_course(scope.course_id).title
        except NotFoundError:
            pass
        if fmt == "json":
            return {"format": "json", "attempt_id": attempt_id,
                    "report": render_attempt_json(attempt, exercises)}
        md = render_report_markdown(attempt, exercises, course_title=course_title, scope_title=title)
        return {"format": "markdown", "attempt_id": attempt_id, "report": md}

    def _exercises_for_attempt(self, attempt: Attempt) -> list[Exercise]:
        scope = attempt.scope
        if scope.type == "exam" and scope.exam_id:
            exam = self.content.get_exam(scope.course_id, scope.exam_id)
            return list(exam.questions)
        if scope.type == "lesson" and scope.lesson_id and scope.module_id:
            lesson = self.content.get_lesson(scope.course_id, scope.module_id, scope.lesson_id)
            return list(lesson.exercises)
        return []

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    def dashboard(self, user_id: str) -> dict[str, Any]:
        prog = self.progress.get_progress(user_id)
        courses_out = []
        overall = {"lessons_total": 0, "lessons_completed": 0, "exercises_mastered": 0,
                   "exams_taken": 0, "exams_passed": 0}
        for course in self.content.list_courses():
            modules_out = []
            course_stats = {"lessons_total": 0, "lessons_completed": 0,
                            "exercises_mastered": 0, "exercises_total": 0,
                            "published_lessons": 0}
            for mod in self.content.list_modules(course.id):
                lessons_out = []
                for lid in mod.lessons:
                    lesson = self.content.get_lesson(course.id, mod.id, lid)
                    key = lesson_key(course.id, mod.id, lid)
                    lp = prog.get("lessons", {}).get(key, {})
                    status = lp.get("status", "not_started") if lp else "not_started"
                    course_stats["lessons_total"] += 1
                    if lesson.status == "published":
                        course_stats["published_lessons"] += 1
                        course_stats["exercises_total"] += len(lesson.exercises)
                        if status == "completed":
                            course_stats["lessons_completed"] += 1
                        course_stats["exercises_mastered"] += lp.get("exercises_mastered", 0)
                    lessons_out.append({
                        "id": lid, "title": lesson.title, "kind": lesson.kind,
                        "status": lesson.status, "progress_status": status,
                        "exercises_mastered": lp.get("exercises_mastered", 0),
                        "exercises_done": lp.get("exercises_done", lp.get("exercises_mastered", 0)),
                        "exercises_total": len(lesson.exercises),
                        "estimated_minutes": lesson.estimated_minutes,
                    })
                exam_out = None
                if mod.exam:
                    try:
                        exam = self.content.get_exam(course.id, mod.exam)
                        ep = prog.get("exams", {}).get(exam_key(course.id, mod.exam), {})
                        exam_out = {
                            "id": exam.id, "title": exam.title,
                            "pass_score": exam.pass_score,
                            "attempts": ep.get("attempts", 0),
                            "best_percent": ep.get("best_percent", 0),
                            "passed": ep.get("passed", False),
                            "last_status": ep.get("last_status"),
                        }
                        if ep.get("attempts"):
                            overall["exams_taken"] += 1
                            if ep.get("passed"):
                                overall["exams_passed"] += 1
                    except NotFoundError:
                        exam_out = None
                modules_out.append({
                    "id": mod.id, "title": mod.title, "phase": mod.phase,
                    "subtitle": mod.subtitle, "duration": mod.duration,
                    "outcome": mod.outcome, "exam": exam_out,
                    "lessons": lessons_out,
                    "resources": [r.model_dump() for r in mod.resources],
                })
            pct = 0.0
            if course_stats["published_lessons"]:
                pct = round(100 * course_stats["lessons_completed"] / course_stats["lessons_total"], 1)
            courses_out.append({
                "id": course.id, "title": course.title,
                "description": course.description, "level": course.level,
                "estimated_weeks": course.estimated_weeks, "tags": course.tags,
                "meta": course.meta, "modules": modules_out,
                "stats": {**course_stats, "completion_percent": pct},
            })
            overall["lessons_total"] += course_stats["lessons_total"]
            overall["lessons_completed"] += course_stats["lessons_completed"]
            overall["exercises_mastered"] += course_stats["exercises_mastered"]

        # próximas acciones sugeridas
        next_actions = self._next_actions(courses_out)
        return {
            "user_id": user_id,
            "overall": overall,
            "courses": courses_out,
            "activity": prog.get("activity", {}),
            "recent_attempts": [
                self._attempt_summary(a) for a in self.progress.list_attempts(user_id, limit=8)
            ],
            "next_actions": next_actions,
        }

    def _attempt_summary(self, a: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": a.get("id"),
            "type": (a.get("scope") or {}).get("type"),
            "ref": (a.get("scope") or {}).get("exam_id")
                   or (a.get("scope") or {}).get("lesson_id")
                   or (a.get("scope") or {}).get("exercise_id"),
            "percent": (a.get("score") or {}).get("percent"),
            "passed": (a.get("score") or {}).get("passed"),
            "status": (a.get("score") or {}).get("status"),
            "submitted_at": a.get("submitted_at"),
        }

    def _next_actions(self, courses_out: list[dict[str, Any]]) -> list[dict[str, Any]]:
        actions = []
        for c in courses_out:
            for m in c["modules"]:
                for l in m["lessons"]:
                    if l["status"] != "published":
                        continue
                    if l["progress_status"] == "in_progress":
                        actions.append({"kind": "continue_lesson", "course_id": c["id"],
                                        "module_id": m["id"], "lesson_id": l["id"],
                                        "label": f"Continuar: {l['title']}"})
                    elif l["progress_status"] == "not_started":
                        actions.append({"kind": "start_lesson", "course_id": c["id"],
                                        "module_id": m["id"], "lesson_id": l["id"],
                                        "label": f"Empezar: {l['title']}"})
                if (m["exam"] and not m["exam"]["passed"]
                        and all(l["progress_status"] == "completed" or l["status"] != "published"
                                for l in m["lessons"])):
                    actions.append({"kind": "take_exam", "course_id": c["id"],
                                    "exam_id": m["exam"]["id"],
                                    "label": f"Presentar examen: {m['exam']['title']}"})
        return actions[:3]

    # ------------------------------------------------------------------
    # Helpers de progreso
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_lesson_progress(lesson) -> dict[str, Any]:
        autos = [e for e in lesson.exercises if e.type != "free_text"]
        frees = [e for e in lesson.exercises if e.type == "free_text"]
        return {
            "status": "not_started", "theory_completed": False,
            "examples_viewed": False,
            "exercises_total": len(lesson.exercises),
            "auto_total": len(autos), "free_total": len(frees),
            "exercises_mastered": 0, "mastered_exercises": [],
            "exercises_reviewed": 0, "reviewed_exercises": [],
            "best_accuracy": 0.0, "attempts": 0,
        }

    @staticmethod
    def _sync_lesson_totals(lp: dict[str, Any], lesson) -> None:
        """Mantiene los conteos al día si el contenido de la lección creció."""
        autos = sum(1 for e in lesson.exercises if e.type != "free_text")
        frees = sum(1 for e in lesson.exercises if e.type == "free_text")
        lp["exercises_total"] = len(lesson.exercises)
        lp["auto_total"] = autos
        lp["free_total"] = frees
        lp["exercises_reviewed"] = len(lp.get("reviewed_exercises", []))

    @staticmethod
    def _recompute_lesson_status(lp: dict[str, Any]) -> None:
        """Lección completa = teoría leída + autocorregibles dominados +
        respuestas libres enviadas (quedan para revisión humana/IA)."""
        lp["exercises_reviewed"] = len(lp.get("reviewed_exercises", []))
        lp["exercises_done"] = lp.get("exercises_mastered", 0) + lp["exercises_reviewed"]
        autos_done = lp.get("exercises_mastered", 0) >= lp.get("auto_total", 0)
        frees_done = lp["exercises_reviewed"] >= lp.get("free_total", 0)
        has_exercises = lp.get("exercises_total", 0) > 0
        started = lp.get("theory_completed") or lp.get("attempts", 0) > 0 or lp.get("exercises_done", 0) > 0
        if lp.get("theory_completed") and has_exercises and autos_done and frees_done:
            lp["status"] = "completed"
        elif started:
            lp["status"] = "in_progress"
        else:
            lp["status"] = "not_started"

    @staticmethod
    def _touch_activity(prog: dict[str, Any]) -> None:
        today = _now().date().isoformat()
        activity = prog.setdefault("activity", {})
        activity[today] = activity.get(today, 0) + 1
