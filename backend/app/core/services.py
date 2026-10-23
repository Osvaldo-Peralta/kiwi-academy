"""Capa de servicios: la lógica de negocio del laboratorio.

Orquesta repositorios + motor de evaluación + feedback. La API solo
traduce HTTP <-> servicios; los servicios no saben nada de HTTP.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .errors import NotFoundError, ValidationError
from .evaluation import grade_exercise
from .feedback import (
    build_attempt_feedback,
    render_attempt_json,
    render_report_markdown,
)
from .models import Attempt, Exercise, QuestionResult
from ..storage.repositories import ContentRepository, ProgressRepository


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
        prog = self.progress.get_progress(user_id)
        key = lesson_key(course_id, module_id, lesson_id)
        lp = prog.setdefault("lessons", {}).setdefault(key, self._empty_lesson_progress(lesson))
        self._sync_lesson_totals(lp, lesson)
        if theory_completed is not None:
            lp["theory_completed"] = bool(theory_completed)
        if examples_viewed is not None:
            lp["examples_viewed"] = bool(examples_viewed)
        self._recompute_lesson_status(lp)
        lp["last_activity_at"] = _now().isoformat()
        self._touch_activity(prog)
        self.progress.save_progress(user_id, prog)
        return lp

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
        self.progress.save_attempt(user_id, attempt.model_dump(mode="json"))

        # Actualizar progreso de la lección
        prog = self.progress.get_progress(user_id)
        key = lesson_key(course_id, module_id, lesson_id)
        lp = prog.setdefault("lessons", {}).setdefault(key, self._empty_lesson_progress(lesson))
        self._sync_lesson_totals(lp, lesson)
        lp["attempts"] = lp.get("attempts", 0) + 1
        mastered = lp.setdefault("mastered_exercises", [])
        reviewed = lp.setdefault("reviewed_exercises", [])
        if ex.type == "free_text":
            # respuesta libre enviada y no vacía => queda "respondida" (revisión
            # humana/IA vía reporte); cuenta para completar la lección
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
        self.progress.save_progress(user_id, prog)

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

    def submit_exam(
        self, user_id: str, course_id: str, exam_id: str,
        responses: list[dict[str, Any]],
        started_at: Optional[str] = None,
        duration_seconds: Optional[int] = None,
    ) -> dict[str, Any]:
        exam = self.content.get_exam(course_id, exam_id)
        q_by_id = {q.id: q for q in exam.questions}
        seen: set[str] = set()
        by_id: dict[str, Any] = {}
        for r in responses:
            eid = r.get("exercise_id")
            if eid in q_by_id:
                by_id[eid] = r.get("answer")
                seen.add(eid)

        results: list[QuestionResult] = []
        points_earned = 0.0
        points_total = 0
        pending_review = 0
        for q in exam.questions:
            answer = by_id.get(q.id)
            outcome = grade_exercise(q, answer)
            earned = round(q.points * outcome.ratio, 2)
            points_earned += earned
            points_total += q.points
            if outcome.needs_review and answer not in (None, "", []):
                pending_review += 1
            results.append(QuestionResult(
                exercise_id=q.id, type=q.type, submitted=answer,
                correct=outcome.correct, partial=(0 < outcome.ratio < 1),
                points_earned=earned, points_total=q.points,
                feedback=outcome.feedback, explanation=q.explanation,
                tags=q.tags,
            ))

        percent = round((points_earned / points_total) * 100, 1) if points_total else 0.0
        passed = percent >= exam.pass_score
        tag_map = self.tag_to_lessons(course_id)
        summary, weak, recs = build_attempt_feedback(results, percent, passed, tag_map)

        attempt = Attempt(
            id=self.progress.new_attempt_id(),
            user_id=user_id,
            scope={
                "type": "exam", "course_id": course_id,
                "module_id": exam.module_id, "exam_id": exam_id,
            },
            started_at=datetime.fromisoformat(started_at) if started_at else None,
            submitted_at=_now(),
            duration_seconds=duration_seconds,
            score={
                "points_earned": round(points_earned, 2),
                "points_total": points_total,
                "percent": percent,
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
        self.progress.save_attempt(user_id, attempt_data)

        # Actualizar progreso agregado del examen
        prog = self.progress.get_progress(user_id)
        key = exam_key(course_id, exam_id)
        ep = prog.setdefault("exams", {}).setdefault(
            key, {"attempts": 0, "best_percent": 0.0, "passed": False}
        )
        ep["attempts"] = ep.get("attempts", 0) + 1
        ep["best_percent"] = max(ep.get("best_percent", 0.0), percent)
        ep["passed"] = bool(ep.get("passed") or passed)
        ep["last_attempt_id"] = attempt.id
        ep["last_attempt_at"] = _now().isoformat()
        self._touch_activity(prog)
        self.progress.save_progress(user_id, prog)

        # Reportes exportables (para feedback con IA/mentor)
        course = self.content.get_course(course_id)
        md = render_report_markdown(
            attempt, exam.questions,
            course_title=course.title, scope_title=exam.title,
        )
        self.progress.save_report(user_id, attempt.id, "report.md", md)

        return {
            "attempt_id": attempt.id,
            "score": attempt.score,
            "results": [r.model_dump(mode="json") for r in results],
            "feedback_summary": summary,
            "weak_tags": weak,
            "recommendations": recs,
            "report_markdown": md,
        }

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
