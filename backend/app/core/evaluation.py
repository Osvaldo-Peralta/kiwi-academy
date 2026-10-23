"""Motor de evaluación.

Diseño: un *grader* por tipo de ejercicio, registrados en GRADERS.
Para añadir un tipo nuevo (p.ej. "query_siem" o "pcap_analysis"):
  1. Crear la función grader(exercise, submitted) -> GradeOutcome
  2. Registrarla en GRADERS
Nada más toca: la API, el progreso y los reportes funcionan igual.

Todos los graders devuelven GradeOutcome con:
  - correct: True/False/None (None = requiere revisión humana/IA)
  - ratio: 0.0–1.0 (crédito parcial permitido)
  - feedback: texto específico para el estudiante
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .errors import ValidationError
from .models import Exam, Exercise


@dataclass
class GradeOutcome:
    correct: Optional[bool]
    ratio: float
    feedback: str
    needs_review: bool = False
    detail: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Utilidades de normalización
# ---------------------------------------------------------------------------

def _normalize(text: Any) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text).strip().lower())


def _normalize_strict(text: Any) -> str:
    """Normalización agresiva para respuestas de comando/tecleo."""
    s = _normalize(text)
    s = s.replace("  ", " ").strip(" .;,\"'")
    # tolera flags en otro orden: "ss -tulpn" == "ss -pntlu"? no automático,
    # pero sí ignoramos múltiples espacios y mayúsculas.
    return s


# ---------------------------------------------------------------------------
# Graders por tipo
# ---------------------------------------------------------------------------

def grade_multiple_choice(ex: Exercise, submitted: Any) -> GradeOutcome:
    expected = str(ex.answer or "")
    got = str(submitted or "")
    correct = got.strip().lower() == expected.strip().lower()
    if correct:
        return GradeOutcome(True, 1.0, "¡Correcto!")
    chosen = next((o.text for o in (ex.options or []) if o.id == got), got or "—")
    return GradeOutcome(
        False, 0.0,
        f"Incorrecto. Respondiste: «{chosen}».",
        detail={"expected_option": expected},
    )


def grade_multiple_select(ex: Exercise, submitted: Any) -> GradeOutcome:
    expected = {str(a).lower() for a in (ex.answer or [])}
    got = {str(a).lower() for a in (submitted or []) if str(a).strip()}
    if not expected:
        return GradeOutcome(False, 0.0, "Pregunta mal configurada.")
    if got == expected:
        return GradeOutcome(True, 1.0, "¡Correcto! Selección exacta.")
    missing = expected - got
    extra = got - expected
    # Crédito parcial: aciertos - fallos, suelo 0
    hits = len(expected & got)
    ratio = max(0.0, (hits - len(extra)) / len(expected))
    parts = []
    if missing:
        opts = {o.id.lower(): o.text for o in (ex.options or [])}
        parts.append("te faltó seleccionar: " + "; ".join(
            f"«{opts.get(m, m)}»" for m in sorted(missing)))
    if extra:
        opts = {o.id.lower(): o.text for o in (ex.options or [])}
        parts.append("sobraba: " + "; ".join(
            f"«{opts.get(e, e)}»" for e in sorted(extra)))
    correct = ratio >= 0.999
    return GradeOutcome(
        correct, ratio,
        ("¡Correcto!" if correct else "Selección incompleta/incorrecta: ") + " y ".join(parts) if parts else "",
    )


def grade_fill_blank(ex: Exercise, submitted: Any) -> GradeOutcome:
    """Soporta blank único y multi-blank.

    answer = {"accepted": [...], "case_sensitive": bool, "regex": "..."}
    answer = {"blanks": [{"accepted": [...]}, {"regex": "..."}]}
    submitted = "texto"  |  ["texto1", "texto2"]
    """
    ans = ex.answer or {}
    blank_specs = ans.get("blanks") or [ans]
    subs = submitted if isinstance(submitted, list) else [submitted]

    n = len(blank_specs)
    hits = 0
    wrong_idx = []
    for i, spec in enumerate(blank_specs):
        given = subs[i] if i < len(subs) else ""
        case_sensitive = bool(spec.get("case_sensitive", False))
        accepted = spec.get("accepted") or []
        regex = spec.get("regex")

        ok = False
        for acc in accepted:
            if case_sensitive:
                ok = str(given).strip() == str(acc)
            else:
                ok = _normalize_strict(given) == _normalize_strict(acc)
            if ok:
                break
        if not ok and regex:
            ok = bool(re.fullmatch(regex, str(given or "").strip(), re.IGNORECASE))
        if ok:
            hits += 1
        else:
            wrong_idx.append(i + 1)

    ratio = hits / n if n else 0.0
    if hits == n:
        return GradeOutcome(True, 1.0, "¡Correcto!")
    msg = f"{hits}/{n} espacios correctos."
    if wrong_idx:
        msg += f" Revisa el/los espacio(s): {', '.join(map(str, wrong_idx))}."
    return GradeOutcome(hits == n, ratio, msg, detail={"wrong_blanks": wrong_idx})


def grade_ordering(ex: Exercise, submitted: Any) -> GradeOutcome:
    expected = [str(a) for a in (ex.answer or [])]
    got = [str(a) for a in (submitted or [])]
    if got == expected:
        return GradeOutcome(True, 1.0, "¡Orden correcto!")
    if len(got) != len(expected):
        return GradeOutcome(False, 0.0, "La secuencia está incompleta o tiene elementos extra.")
    hits = sum(1 for a, b in zip(got, expected) if a == b)
    ratio = hits / len(expected)
    items = {i.id: i.text for i in (ex.items or [])}
    misplaced = [items.get(got[i], got[i]) for i in range(len(expected)) if got[i] != expected[i]]
    return GradeOutcome(
        False, ratio,
        f"{hits}/{len(expected)} elementos en su posición. Fuera de lugar: "
        + "; ".join(f"«{m}»" for m in misplaced),
    )


def grade_matching(ex: Exercise, submitted: Any) -> GradeOutcome:
    expected = {str(k): str(v) for k, v in (ex.answer or {}).items()}
    got = {str(k): str(v) for k, v in (submitted or {}).items()} if isinstance(submitted, dict) else {}
    if not expected:
        return GradeOutcome(False, 0.0, "Pregunta mal configurada.")
    hits = sum(1 for k, v in expected.items() if got.get(k) == v)
    ratio = hits / len(expected)
    pairs = {p.left_id: p for p in (ex.pairs or [])}
    wrong = []
    for k, v in expected.items():
        if got.get(k) != v:
            p = pairs.get(k)
            wrong.append(f"«{p.left if p else k}»")
    if hits == len(expected):
        return GradeOutcome(True, 1.0, "¡Todas las asociaciones son correctas!")
    return GradeOutcome(
        False, ratio,
        f"{hits}/{len(expected)} pares correctos. Revisa: " + "; ".join(wrong),
    )


def grade_free_text(ex: Exercise, submitted: Any) -> GradeOutcome:
    """Iteración 2: la evaluación automática NO puntúa respuestas libres.

    Una coincidencia de palabras clave no es prueba de comprensión: la
    cobertura se reporta como señal ORIENTATIVA y la puntuación definitiva la
    asigna un revisor humano. Sin respuesta => nada que revisar (0 pts auto).
    """
    ans = ex.answer or {}
    keywords = [str(k) for k in ans.get("keywords", [])]
    text = _normalize(submitted)
    if not text:
        return GradeOutcome(
            None, 0.0,
            "Sin respuesta: no hay nada que revisar; puntúa 0 en la parte automática.",
            needs_review=False,
            detail={"review_status": "not_answered"},
        )
    covered = [k for k in keywords if _normalize(k) in text]
    missing = [k for k in keywords if k not in covered]
    coverage = len(covered) / len(keywords) if keywords else 0.0
    fb = (
        f"Respuesta registrada para revisión humana. "
        f"Cobertura ORIENTATIVA de conceptos clave: {len(covered)}/{len(keywords)} "
        f"(la coincidencia de palabras NO es prueba de comprensión: no suma "
        f"puntos automáticos; un revisor autorizado asignará la puntuación "
        f"definitiva). Incluida en tu reporte exportable."
    )
    return GradeOutcome(
        None, 0.0, fb,
        needs_review=True,
        detail={
            "review_status": "pending",
            "keyword_coverage": round(coverage, 3),
            "keywords_covered": covered,
            "keywords_missing": missing,
        },
    )


GRADERS: dict[str, Callable[[Exercise, Any], GradeOutcome]] = {
    "multiple_choice": grade_multiple_choice,
    "multiple_select": grade_multiple_select,
    "fill_blank": grade_fill_blank,
    "ordering": grade_ordering,
    "matching": grade_matching,
    "free_text": grade_free_text,
}


def grade_exercise(ex: Exercise, submitted: Any) -> GradeOutcome:
    grader = GRADERS.get(ex.type)
    if grader is None:
        return GradeOutcome(False, 0.0, f"Tipo de ejercicio no soportado: {ex.type}")
    outcome = grader(ex, submitted)
    # Añade la explicación pedagógica del contenido (si existe)
    return outcome


# ---------------------------------------------------------------------------
# Iteración 2 (QA-04): validación estructural estricta de respuestas de examen
# ---------------------------------------------------------------------------

FREE_TEXT_MAX_CHARS = 20000


def _answer_type_error(qid: str, expected: str, got: Any) -> dict[str, Any]:
    return {
        "exercise_id": qid,
        "problem": "answer_type",
        "expected": expected,
        "got_type": type(got).__name__,
    }


def validate_exam_responses(exam, responses: list[dict[str, Any]]) -> dict[str, Any]:
    """Valida la estructura completa del envío de un examen.

    Reglas (rechazo controlado 422 con detalle preciso, nunca silencio):
    - Deben estar TODAS las preguntas del examen, exactamente una vez cada
      una y EN EL ORDEN del examen (ids y orden validados; duplicados y
      desconocidos se listan en el error).
    - `answer` puede ser null (sin responder) — puntúa 0, no es malformado.
    - Por tipo, la respuesta debe tener la estructura exacta del contrato:
      * multiple_choice: str ∈ ids de opciones
      * multiple_select: list[str] ⊆ ids, sin duplicados
      * fill_blank: str (1 blank) o list[str] con longitud == nº de blanks
      * ordering: list[str] ⊆ ids de items, sin duplicados
      * matching: dict {left_id: right_id} con ids válidos y derechos únicos
      * free_text: str de longitud ≤ FREE_TEXT_MAX_CHARS

    Devuelve {exercise_id: answer} normalizado.
    Lanza ValidationError (422) con details["problems"] exhaustivo.
    """
    problems: list[dict[str, Any]] = []
    q_ids = [q.id for q in exam.questions]
    got_ids = [str(r.get("exercise_id", "")) for r in responses]

    # 1) cobertura exacta: mismos ids, sin duplicados ni desconocidos
    unknown = [g for g in got_ids if g not in set(q_ids)]
    seen: set[str] = set()
    duplicates = sorted({g for g in got_ids if g in seen or seen.add(g)})  # type: ignore
    missing = [q for q in q_ids if q not in set(got_ids)]
    if unknown:
        problems.append({"problem": "unknown_ids", "ids": sorted(set(unknown))})
    if duplicates:
        problems.append({"problem": "duplicate_ids", "ids": duplicates})
    if missing:
        problems.append({"problem": "missing_ids", "ids": missing})
    # 2) orden exacto (solo tiene sentido si la cobertura es correcta)
    if not (unknown or duplicates or missing) and got_ids != q_ids:
        problems.append({"problem": "wrong_order", "expected": q_ids, "got": got_ids})

    if problems:
        raise ValidationError(
            "Envío de examen malformado: identificadores incompletos, "
            "duplicados, desconocidos o fuera de orden.",
            problems=problems,
        )

    # 3) validación por tipo de cada respuesta
    by_id = {str(r.get("exercise_id")): r.get("answer") for r in responses}
    for q in exam.questions:
        a = by_id.get(q.id)
        if a is None:
            continue  # sin responder: permitido
        if q.type == "multiple_choice":
            valid = {o.id for o in (q.options or [])}
            if not isinstance(a, str) or a not in valid:
                problems.append(_answer_type_error(q.id, f"str ∈ {sorted(valid)}", a))
        elif q.type == "multiple_select":
            valid = {o.id for o in (q.options or [])}
            if not isinstance(a, list) or any(not isinstance(x, str) or x not in valid for x in a) \
                    or len(set(a)) != len(a):
                problems.append(_answer_type_error(q.id, f"list[str] ⊆ {sorted(valid)} sin duplicados", a))
        elif q.type == "fill_blank":
            n_blanks = len((q.answer or {}).get("blanks") or [None])
            if n_blanks == 1:
                if not isinstance(a, (str, list)) or (isinstance(a, list) and (len(a) != 1 or not isinstance(a[0], str))):
                    problems.append(_answer_type_error(q.id, "str (o list[str] de 1)", a))
            else:
                if not isinstance(a, list) or len(a) != n_blanks or any(not isinstance(x, str) for x in a):
                    problems.append(_answer_type_error(q.id, f"list[str] de longitud {n_blanks}", a))
        elif q.type == "ordering":
            valid = {i.id for i in (q.items or [])}
            if not isinstance(a, list) or any(not isinstance(x, str) or x not in valid for x in a) \
                    or len(set(a)) != len(a):
                problems.append(_answer_type_error(q.id, f"list[str] ⊆ {sorted(valid)} sin duplicados", a))
        elif q.type == "matching":
            lefts = {p.left_id for p in (q.pairs or [])}
            rights = {p.right_id for p in (q.pairs or [])}
            # dict vacío = "sin responder" (el widget no tocado envía {}): permitido
            ok = isinstance(a, dict) and all(
                isinstance(k, str) and k in lefts and isinstance(v, str) and v in rights
                for k, v in a.items()
            )
            if ok and len(set(a.values())) != len(a):
                ok = False  # dos izquierdos al mismo derecho: malformado
            if not ok:
                problems.append(_answer_type_error(q.id, "dict {left_id: right_id} con ids válidos y únicos", a))
        elif q.type == "free_text":
            if not isinstance(a, str) or len(a) > FREE_TEXT_MAX_CHARS:
                problems.append(_answer_type_error(
                    q.id, f"str de ≤ {FREE_TEXT_MAX_CHARS} caracteres", a if not isinstance(a, str) else f"str({len(a)})"))
        else:
            problems.append(_answer_type_error(q.id, "tipo de ejercicio desconocido", a))

    if problems:
        raise ValidationError(
            "Respuestas con estructura inválida para su tipo de ejercicio.",
            problems=problems,
        )
    return by_id
