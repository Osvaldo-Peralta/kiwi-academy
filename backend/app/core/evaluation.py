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

from .models import Exercise


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
    """No se auto-califica de forma definitiva: calcula cobertura de keywords
    como orientación y lo marca para revisión (humana o IA vía reporte)."""
    ans = ex.answer or {}
    keywords = [str(k) for k in ans.get("keywords", [])]
    min_kw = int(ans.get("min_keywords", max(1, len(keywords) // 2)))
    text = _normalize(submitted)
    if not text:
        return GradeOutcome(
            None, 0.0, "No enviaste respuesta.", needs_review=False
        )
    covered = [k for k in keywords if _normalize(k) in text]
    ratio_hint = len(covered) / len(keywords) if keywords else 0.0
    pending = len(covered) >= min_kw
    fb = (
        f"Respuesta registrada ({len(covered)}/{len(keywords)} conceptos clave detectados). "
        + ("Queda pendiente de revisión detallada — está incluida en tu reporte exportable para feedback con IA."
           if pending else
           "Cobertura baja de conceptos clave; igualmente se incluye en tu reporte para feedback.")
    )
    return GradeOutcome(
        None, ratio_hint, fb,
        needs_review=True,
        detail={"keywords_covered": covered, "keywords_missing": [k for k in keywords if k not in covered]},
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
