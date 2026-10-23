"""Modelos de dominio (Pydantic v2).

Dos familias:
1. Modelos de CONTENIDO: curso, módulo, lección, ejercicio, examen.
   Se cargan desde los JSON de `content/`. Incluyen las respuestas
   correctas (solo viven en el servidor).
2. Modelos de API: payloads de entrega (sin respuestas) y de intento.

Regla de oro: ningún objeto con `answer` sale jamás al frontend.
`Exercise.public()` devuelve la versión entregable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Bloques de contenido (teoría / ejemplos / contextos)
# ---------------------------------------------------------------------------


class ContentBlock(BaseModel):
    """Sección de texto enriquecido (markdown ligero renderizado en el front)."""

    id: Optional[str] = None
    section: Optional[str] = None          # título de la sección
    format: Literal["markdown", "log", "code", "command"] = "markdown"
    language: Optional[str] = None         # p.ej. "bash", "python", "splunk"
    body: str = ""


class ExerciseContext(ContentBlock):
    """Contexto adjunto a un ejercicio (log, captura, snippet...)."""
    pass


# ---------------------------------------------------------------------------
# Ejercicios — envelope común + discriminación por tipo en `type`
# ---------------------------------------------------------------------------

ExerciseType = Literal[
    "multiple_choice",
    "multiple_select",
    "fill_blank",
    "ordering",
    "matching",
    "free_text",
]


class Option(BaseModel):
    id: str
    text: str


class MatchPair(BaseModel):
    left_id: str
    left: str
    right_id: str
    right: str


class Exercise(BaseModel):
    """Ejercicio/pregunta. El campo `answer` cambia de forma según `type`:

    - multiple_choice: "b"                       (id de opción)
    - multiple_select: ["a", "c"]                (ids de opciones)
    - fill_blank:      {"accepted": ["tcp"], "case_sensitive": false,
                        "regex": null}  — o multi-blank:
                       {"blanks": [{"accepted": [...]}, ...]}
    - ordering:        ["s3", "s1", "s2"]        (ids en orden correcto)
    - matching:        {"l1": "r2", "l2": "r1"}  (left_id -> right_id)
    - free_text:       {"model_answer": "...", "keywords": [...],
                        "min_keywords": 2}
    """

    id: str
    type: ExerciseType
    prompt: str
    context: Optional[ExerciseContext] = None
    tags: list[str] = Field(default_factory=list)
    difficulty: int = Field(default=1, ge=1, le=3)
    points: int = Field(default=10, ge=1)

    # según tipo
    options: Optional[list[Option]] = None
    text: Optional[str] = None          # fill_blank: texto con [___1], [___2]
    items: Optional[list[Option]] = None  # ordering: elementos a ordenar
    pairs: Optional[list[MatchPair]] = None  # matching
    hint: Optional[str] = None

    # ⚠ SOLO SERVIDOR — nunca se serializa al frontend
    answer: Any = None
    explanation: str = ""

    def public(self) -> dict[str, Any]:
        """Versión entregable: sin respuesta, sin explicación.

        Además re-ordena aleatoriamente los elementos de `ordering` y los
        lados de `matching`, para que ni el orden de llegada delate la
        solución (las respuestas se corrigen por ID, no por posición).
        """
        import random

        data = self.model_dump(exclude={"answer", "explanation"}, exclude_none=True)

        if self.type == "ordering" and self.items:
            items = [i.model_dump() for i in self.items]
            random.shuffle(items)
            data["items"] = items

        if self.type == "matching" and self.pairs:
            left = [{"id": p.left_id, "text": p.left} for p in self.pairs]
            right = [{"id": p.right_id, "text": p.right} for p in self.pairs]
            random.shuffle(right)
            data.pop("pairs", None)
            data["left_items"] = left
            data["right_items"] = right

        return data

    def public_solution(self) -> dict[str, Any]:
        """Versión completa (para reportes de feedback del propio usuario)."""
        return self.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# Estructura del curso
# ---------------------------------------------------------------------------


class Resource(BaseModel):
    title: str
    url: Optional[str] = None
    type: Optional[str] = None  # free | paid | video | lab ...
    note: Optional[str] = None


class Lesson(BaseModel):
    id: str
    module_id: str
    course_id: str
    title: str
    kind: str = "teoria"                    # teoria | practica | herramienta
    estimated_minutes: int = 60
    status: Literal["published", "draft"] = "published"
    summary: str = ""
    objectives: list[str] = Field(default_factory=list)
    theory: list[ContentBlock] = Field(default_factory=list)
    examples: list[ContentBlock] = Field(default_factory=list)
    exercises: list[Exercise] = Field(default_factory=list)

    def public(self) -> dict[str, Any]:
        data = self.model_dump(exclude_none=True)
        data["exercises"] = [e.public() for e in self.exercises]
        return data


class Module(BaseModel):
    id: str
    course_id: str
    title: str
    phase: int = 1
    subtitle: str = ""
    duration: str = ""
    outcome: str = ""
    lessons: list[str] = Field(default_factory=list)   # ids de lección
    exam: Optional[str] = None                        # id de examen
    resources: list[Resource] = Field(default_factory=list)


class Course(BaseModel):
    id: str
    title: str
    description: str = ""
    language: str = "es"
    level: str = "beginner"
    estimated_weeks: str = ""
    tags: list[str] = Field(default_factory=list)
    modules: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class Exam(BaseModel):
    id: str
    course_id: str
    module_id: Optional[str] = None
    title: str
    description: str = ""
    time_limit_minutes: Optional[int] = None
    pass_score: int = Field(default=70, ge=0, le=100)   # porcentaje
    attempts_allowed: Optional[int] = None               # None = ilimitados
    questions: list[Exercise] = Field(default_factory=list)

    def public(self) -> dict[str, Any]:
        data = self.model_dump(
            exclude={"questions"}, exclude_none=True
        )
        data["question_count"] = len(self.questions)
        data["total_points"] = sum(q.points for q in self.questions)
        data["questions"] = [q.public() for q in self.questions]
        return data


# ---------------------------------------------------------------------------
# Intentos y progreso (API / almacenamiento de datos de usuario)
# ---------------------------------------------------------------------------


class QuestionResult(BaseModel):
    exercise_id: str
    type: ExerciseType
    submitted: Any = None
    correct: Optional[bool] = None       # None = pendiente de revisión (free_text)
    partial: bool = False
    points_earned: float = 0.0
    points_total: float = 0.0
    feedback: str = ""
    explanation: str = ""
    tags: list[str] = Field(default_factory=list)
    # Iteración 2: estado de revisión de la pregunta
    #   auto         -> corregida automáticamente (definitiva)
    #   pending      -> respuesta libre enviada, espera revisor humano (0 pts auto)
    #   reviewed     -> puntuada por revisor humano (definitiva)
    #   not_answered -> respuesta libre vacía (0 pts, nada que revisar)
    review_status: Literal["auto", "pending", "reviewed", "not_answered"] = "auto"


class AttemptScope(BaseModel):
    type: Literal["lesson", "exam"]
    course_id: str
    module_id: Optional[str] = None
    lesson_id: Optional[str] = None
    exam_id: Optional[str] = None
    exercise_id: Optional[str] = None


class Attempt(BaseModel):
    id: str
    user_id: str
    scope: AttemptScope
    started_at: Optional[datetime] = None
    submitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_seconds: Optional[int] = None
    score: dict[str, Any] = Field(default_factory=dict)
    results: list[QuestionResult] = Field(default_factory=list)
    weak_tags: list[dict[str, Any]] = Field(default_factory=list)
    feedback_summary: str = ""
    recommendations: list[str] = Field(default_factory=list)
    # Iteración 2: revisión humana (None hasta que un revisor autorizado actúa)
    # {reviewer: <sub>, reviewed_at: iso, comment: str, question_feedback: {qid: str}}
    review: Optional[dict[str, Any]] = None


class LessonProgress(BaseModel):
    status: Literal["not_started", "in_progress", "completed"] = "not_started"
    theory_completed: bool = False
    examples_viewed: bool = False
    exercises_total: int = 0
    exercises_mastered: int = 0
    best_accuracy: float = 0.0
    attempts: int = 0
    last_activity_at: Optional[datetime] = None


class LessonProgressEvent(BaseModel):
    """Payload para marcar teoría leída / ejemplos vistos."""
    theory_completed: Optional[bool] = None
    examples_viewed: Optional[bool] = None


class ExerciseAttemptPayload(BaseModel):
    answer: Any


class ExamResponseItem(BaseModel):
    exercise_id: str
    answer: Any = None


class ExamSubmission(BaseModel):
    """Iteración 2: el envío exige una sesión de examen emitida por el
    servidor (control de tiempo server-side). `started_at`/`duration_seconds`
    del cliente fueron eliminados del contrato: el servidor mide el tiempo.
    Campos extra en el payload se ignoran (no son de confianza)."""
    session_id: str
    responses: list[ExamResponseItem]


class ExamReviewItem(BaseModel):
    """Puntuación de un revisor humano para una respuesta libre pendiente."""
    exercise_id: str
    points_earned: float = Field(ge=0)
    feedback: Optional[str] = Field(default=None, max_length=2000)


class ExamReviewPayload(BaseModel):
    reviews: list[ExamReviewItem]
    comment: Optional[str] = Field(default=None, max_length=4000)
