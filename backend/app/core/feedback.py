"""Generación de feedback pedagógico y reportes exportables.

Dos salidas:
1. Feedback automático inmediato (por pregunta, por intento, áreas débiles
   por tag, recomendaciones de qué repasar).
2. Reporte exportable (Markdown/JSON) pensado para pegarlo en un chat con
   un mentor/IA y recibir feedback humano profundo.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from .models import Attempt, Exercise, QuestionResult


# ---------------------------------------------------------------------------
# Feedback de intento (examen o batería de ejercicios de una lección)
# ---------------------------------------------------------------------------

BANDS = [
    (90, "¡Excelente! Dominas el material. Estás listo/a para avanzar."),
    (75, "Muy bien. Tienes la base sólida; pule los detalles señalados abajo."),
    (60, "Vas por buen camino, pero hay lagunas concretas que cerrar antes del examen."),
    (0, "Este tema necesita una segunda pasada completa de teoría + ejercicios."),
]


def score_band(percent: float) -> str:
    for threshold, msg in BANDS:
        if percent >= threshold:
            return msg
    return BANDS[-1][1]


def compute_weak_tags(results: list[QuestionResult], threshold: float = 0.7) -> list[dict[str, Any]]:
    """Agrega aciertos por tag; devuelve tags bajo el umbral, peor primero."""
    agg: dict[str, list[float]] = defaultdict(list)
    for r in results:
        if r.correct is None:  # free_text pendiente: no penaliza ni puntúa
            continue
        ratio = (r.points_earned / r.points_total) if r.points_total else 0.0
        for t in r.tags or ["general"]:
            agg[t].append(ratio)
    weak = []
    for tag, ratios in agg.items():
        acc = sum(ratios) / len(ratios)
        if acc < threshold:
            weak.append({"tag": tag, "accuracy": round(acc, 2), "questions": len(ratios)})
    weak.sort(key=lambda w: w["accuracy"])
    return weak


def build_recommendations(
    weak_tags: list[dict[str, Any]],
    tag_to_lessons: dict[str, list[str]],
    passed: Optional[bool],
    percent: float,
) -> list[str]:
    recs: list[str] = []
    for w in weak_tags[:4]:
        lessons = tag_to_lessons.get(w["tag"], [])
        where = f" (repasa: {', '.join(lessons)})" if lessons else ""
        recs.append(
            f"Refuerza el tema «{w['tag']}» — accuracy {int(w['accuracy']*100)}%{where}"
        )
    if passed is False:
        recs.append(
            "No alcanzaste la nota de aprobación: vuelve a la teoría de las áreas débiles "
            "y reintenta el examen (los intentos quedan registrados para medir tu mejora)."
        )
    elif passed is True and percent >= 90:
        recs.append("Puedes avanzar al siguiente módulo con confianza.")
    if not recs:
        recs.append("Sigue con el siguiente ejercicio/lección del roadmap.")
    return recs


def build_attempt_feedback(
    results: list[QuestionResult],
    percent: float,
    passed: Optional[bool],
    tag_to_lessons: dict[str, list[str]],
) -> tuple[str, list[dict[str, Any]], list[str]]:
    weak = compute_weak_tags(results)
    summary = score_band(percent)
    recs = build_recommendations(weak, tag_to_lessons, passed, percent)
    return summary, weak, recs


# ---------------------------------------------------------------------------
# Reporte exportable (para feedback con IA / mentor)
# ---------------------------------------------------------------------------

def _fmt_answer(value: Any) -> str:
    if value is None:
        return "—(sin respuesta)—"
    if isinstance(value, (dict, list)):
        import json
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _fmt_expected(ex: Exercise) -> str:
    """Formatea la respuesta correcta de forma legible según el tipo."""
    ans = ex.answer
    if ans is None:
        return "—"
    if ex.type == "multiple_choice":
        opt = next((o for o in (ex.options or []) if o.id == ans), None)
        return f"`{ans}`) {opt.text}" if opt else str(ans)
    if ex.type == "multiple_select":
        opts = {o.id: o.text for o in (ex.options or [])}
        return " · ".join(f"`{a}`) {opts.get(a, '?')}" for a in ans)
    if ex.type == "fill_blank":
        if "blanks" in (ans or {}):
            parts = []
            for i, spec in enumerate(ans["blanks"], 1):
                accepted = spec.get("accepted") or ([spec.get("regex")] if spec.get("regex") else [])
                parts.append(f"[{i}] {' / '.join(map(str, accepted))}")
            return " · ".join(parts)
        accepted = ans.get("accepted") or ([ans.get("regex")] if ans.get("regex") else [])
        return " / ".join(map(str, accepted))
    if ex.type == "ordering":
        items = {i.id: i.text for i in (ex.items or [])}
        return " → ".join(items.get(a, str(a)) for a in ans)
    if ex.type == "matching":
        pairs = {p.left_id: p for p in (ex.pairs or [])}
        rights = {p.right_id: p.right for p in (ex.pairs or [])}
        return " · ".join(
            f"{pairs[k].left} → {rights.get(v, v)}" for k, v in ans.items() if k in pairs
        )
    if ex.type == "free_text":
        return f"(respuesta modelo) {ans.get('model_answer', '—')}"
    return _fmt_answer(ans)


def _fmt_submitted(ex: Exercise, value: Any) -> str:
    """Formatea la respuesta del estudiante de forma legible según el tipo."""
    if value in (None, "", []):
        return "—(sin respuesta)—"
    if ex.type == "multiple_choice":
        opt = next((o for o in (ex.options or []) if o.id == str(value)), None)
        return f"`{value}`) {opt.text}" if opt else _fmt_answer(value)
    if ex.type == "multiple_select":
        opts = {o.id: o.text for o in (ex.options or [])}
        return " · ".join(f"`{v}`) {opts.get(str(v), str(v))}" for v in value) or "—"
    if ex.type == "ordering":
        items = {i.id: i.text for i in (ex.items or [])}
        return " → ".join(items.get(str(v), str(v)) for v in value)
    if ex.type == "fill_blank":
        vals = value if isinstance(value, list) else [value]
        return " · ".join(f"[{i}] {v}" for i, v in enumerate(vals, 1))
    if ex.type == "matching" and isinstance(value, dict):
        lefts = {p.left_id: p.left for p in (ex.pairs or [])}
        rights = {p.right_id: p.right for p in (ex.pairs or [])}
        return " · ".join(
            f"{lefts.get(str(k), str(k))} → {rights.get(str(v), str(v))}"
            for k, v in value.items()
        )
    return _fmt_answer(value)


def render_report_markdown(
    attempt: Attempt,
    exercises: list[Exercise],
    course_title: str = "",
    scope_title: str = "",
) -> str:
    """Reporte completo con respuestas del estudiante vs. las correctas.

    Está pensado para copiar/pegar en un chat con una IA (o mentor) y
    recibir feedback cualitativo profundo.

    Iteración 2: distingue resultado provisional (parte automática) del
    definitivo; las respuestas libres pendientes se marcan explícitamente y
    su cobertura de palabras clave se presenta como ORIENTATIVA (nunca como
    prueba de comprensión).
    """
    ex_by_id = {e.id: e for e in exercises}
    score = attempt.score or {}
    status = score.get("status", "auto_graded")
    lines: list[str] = []
    lines.append(f"# Reporte de evaluación — {scope_title or attempt.scope.type}")
    lines.append("")
    if course_title:
        lines.append(f"**Curso:** {course_title}")
    lines.append(f"**Intento:** `{attempt.id}`")
    lines.append(f"**Fecha:** {attempt.submitted_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    if attempt.duration_seconds:
        lines.append(f"**Duración:** {attempt.duration_seconds // 60} min {attempt.duration_seconds % 60} s")

    if status == "pending_review":
        lines.append(
            f"**Puntaje automático (PROVISIONAL):** {score.get('points_earned_auto', 0):g}/"
            f"{score.get('auto_points_total', 0):g} de la parte automática "
            f"({score.get('provisional_percent', 0):.0f}%) — total del examen "
            f"{score.get('points_total', 0):g} pts"
        )
        lines.append(
            f"**Estado:** ⏳ PENDIENTE DE REVISIÓN HUMANA — "
            f"{score.get('pending_review', 0)} respuesta(s) libre(s) por valor de "
            f"{score.get('pending_points_total', 0):g} pts. El examen NO está "
            f"aprobado ni reprobado hasta que un revisor autorizado las puntúe."
        )
    else:
        verdict = "APROBADO" if score.get("passed") else "NO APROBADO"
        prefix = ("**Resultado definitivo (revisión humana completada):**"
                  if status == "reviewed" else "**Puntaje:**")
        lines.append(
            f"{prefix} {score.get('points_earned', 0):g}/{score.get('points_total', 0):g} "
            f"({score.get('percent', 0):.0f}%) — {verdict}"
        )

    if attempt.review:
        rv = attempt.review
        lines.append("")
        lines.append(f"**Revisión humana:** por `{rv.get('reviewer')}` el {rv.get('reviewed_at')}")
        if rv.get("comment"):
            lines.append(f"> Comentario del revisor: {rv['comment']}")

    lines.append("")
    lines.append(f"> Resumen automático: {attempt.feedback_summary}")
    if attempt.recommendations:
        lines.append(">")
        for r in attempt.recommendations:
            lines.append(f"> - {r}")
    lines.append("")
    lines.append("## Detalle pregunta por pregunta")
    for i, r in enumerate(attempt.results, start=1):
        ex = ex_by_id.get(r.exercise_id)
        rs = getattr(r, "review_status", "auto")
        if rs == "pending":
            icon = "🕵️ pendiente de revisión humana — 0 pts automáticos"
        elif rs == "reviewed":
            icon = f"✍️ revisada por humano — {r.points_earned:g} pts"
        elif rs == "not_answered":
            icon = "⬜ sin respuesta"
        else:
            icon = "✅" if r.correct else "❌"
        lines.append("")
        lines.append(f"### {i}. [{icon}] {r.exercise_id} · {r.points_earned:g}/{r.points_total:g} pts · tags: {', '.join(r.tags) or '—'}")
        if ex:
            lines.append("")
            lines.append(f"**Pregunta:** {ex.prompt}")
            if ex.context and ex.context.body:
                fmt = ex.context.format
                fence = "```"
                lines.append("")
                lines.append(f"**Contexto ({fmt}):**")
                lines.append(f"{fence}")
                lines.append(ex.context.body.strip())
                lines.append(f"{fence}")
            if ex.options:
                lines.append("")
                lines.append("**Opciones:**")
                for o in ex.options:
                    lines.append(f"- `{o.id}`) {o.text}")
            lines.append("")
            lines.append(f"**Tu respuesta:** {_fmt_submitted(ex, r.submitted)}")
            lines.append(f"**Respuesta correcta:** {_fmt_expected(ex)}")
            if ex.explanation:
                lines.append("")
                lines.append(f"**Explicación:** {ex.explanation}")
            lines.append("")
            lines.append(f"**Feedback del sistema:** {r.feedback}")
            if rs in ("pending", "reviewed"):
                lines.append("")
                lines.append(
                    "> Nota: la cobertura de palabras clave es ORIENTATIVA y no "
                    "constituye prueba de comprensión; la puntuación de esta "
                    "respuesta la asigna (o asignó) un revisor humano."
                )
    lines.append("")
    lines.append("---")
    lines.append(
        "## Solicitud de feedback (para pegar a tu mentor/IA)\n\n"
        "Actúa como analista SOC senior y mentor. Con base en este reporte:\n"
        "1. Diagnostica mis errores más importantes y el concepto de fondo detrás de cada uno.\n"
        "2. Señala patrones (¿leo mal los logs? ¿confundo capas? ¿me falta vocabulario técnico?).\n"
        "3. Proponme 3 ejercicios prácticos nuevos para mis áreas débiles.\n"
        "4. Evalúa cómo respondería esto en una entrevista real de SOC N1.\n"
        + ("5. Para las respuestas libres pendientes: valora su calidad real más allá de las palabras clave y sugiere una puntuación justa (0–máx) con criterios.\n"
           if status == "pending_review" else "")
    )
    return "\n".join(lines)


def render_attempt_json(attempt: Attempt, exercises: list[Exercise]) -> dict[str, Any]:
    ex_by_id = {e.id: e.public_solution() for e in exercises}
    data = attempt.model_dump(mode="json")
    data["exercise_solutions"] = ex_by_id
    return data


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
