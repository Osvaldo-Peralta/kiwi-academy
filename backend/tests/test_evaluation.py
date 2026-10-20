"""Tests del motor de evaluación (todos los tipos de ejercicio)."""
import pytest

from backend.app.core.evaluation import grade_exercise
from backend.app.core.models import Exercise


def _ex(**kw):
    base = {"id": "t1", "prompt": "p", "explanation": ""}
    base.update(kw)
    return Exercise(**base)


# ------------------------------------------------------------- multiple_choice
def test_multiple_choice_correct():
    ex = _ex(type="multiple_choice", options=[{"id": "a", "text": "A"}, {"id": "b", "text": "B"}], answer="b")
    out = grade_exercise(ex, "b")
    assert out.correct is True and out.ratio == 1.0


def test_multiple_choice_incorrect_mentions_choice():
    ex = _ex(type="multiple_choice", options=[{"id": "a", "text": "Capa 3"}, {"id": "b", "text": "Capa 4"}], answer="b")
    out = grade_exercise(ex, "a")
    assert out.correct is False and "Capa 3" in out.feedback


def test_multiple_choice_case_insensitive():
    ex = _ex(type="multiple_choice", options=[{"id": "a", "text": "A"}], answer="A")
    assert grade_exercise(ex, "a").correct is True


# ------------------------------------------------------------- multiple_select
def test_multiple_select_exact():
    ex = _ex(type="multiple_select", options=[{"id": x, "text": x.upper()} for x in "abc"], answer=["a", "c"])
    out = grade_exercise(ex, ["c", "a"])  # orden irrelevante
    assert out.correct is True and out.ratio == 1.0


def test_multiple_select_partial_credit():
    ex = _ex(type="multiple_select", options=[{"id": x, "text": x} for x in "abcd"], answer=["a", "b", "c"])
    out = grade_exercise(ex, ["a", "b"])  # falta c
    assert out.correct is False
    assert out.ratio == pytest.approx(2 / 3)


def test_multiple_select_wrong_picks_reduce():
    ex = _ex(type="multiple_select", options=[{"id": x, "text": x} for x in "abcd"], answer=["a", "b"])
    out = grade_exercise(ex, ["a", "c", "d"])  # 1 acierto, 2 fallos -> (1-2)/2 -> suelo 0
    assert out.ratio == 0.0


# ------------------------------------------------------------- fill_blank
def test_fill_blank_single_accepted_variants():
    ex = _ex(type="fill_blank", answer={"accepted": ["TXT", "txt"]})
    assert grade_exercise(ex, "  tXt ").correct is True


def test_fill_blank_case_sensitive():
    ex = _ex(type="fill_blank", answer={"accepted": ["S."], "case_sensitive": True})
    assert grade_exercise(ex, "s.").correct is False
    assert grade_exercise(ex, "S.").correct is True


def test_fill_blank_regex():
    ex = _ex(type="fill_blank", answer={"regex": r"10\.\d+\.\d+\.\d+"})
    assert grade_exercise(ex, "10.20.30.40").correct is True
    assert grade_exercise(ex, "192.168.1.1").correct is False


def test_fill_blank_multi_partial():
    ex = _ex(type="fill_blank", answer={"blanks": [{"accepted": ["443"]}, {"accepted": ["22"]}]})
    out = grade_exercise(ex, ["443", "23"])
    assert out.correct is False and out.ratio == 0.5
    assert out.detail["wrong_blanks"] == [2]


# ------------------------------------------------------------- ordering
def test_ordering_correct():
    ex = _ex(type="ordering", items=[{"id": "a", "text": "A"}, {"id": "b", "text": "B"}], answer=["a", "b"])
    assert grade_exercise(ex, ["a", "b"]).correct is True


def test_ordering_wrong_gives_partial_and_names_misplaced():
    ex = _ex(type="ordering",
             items=[{"id": "s1", "text": "SYN"}, {"id": "s2", "text": "SYN-ACK"}, {"id": "s3", "text": "ACK"}],
             answer=["s1", "s2", "s3"])
    out = grade_exercise(ex, ["s1", "s3", "s2"])
    assert out.correct is False
    assert out.ratio == pytest.approx(1 / 3)
    assert "SYN-ACK" in out.feedback


# ------------------------------------------------------------- matching
def test_matching_correct_and_partial():
    ex = _ex(type="matching",
             pairs=[{"left_id": "l1", "left": "SSH", "right_id": "r1", "right": "22"},
                    {"left_id": "l2", "left": "DNS", "right_id": "r2", "right": "53"}],
             answer={"l1": "r1", "l2": "r2"})
    assert grade_exercise(ex, {"l1": "r1", "l2": "r2"}).correct is True
    out = grade_exercise(ex, {"l1": "r1", "l2": "r1"})
    assert out.correct is False and out.ratio == 0.5


# ------------------------------------------------------------- free_text
def test_free_text_needs_review_and_counts_keywords():
    ex = _ex(type="free_text",
             answer={"model_answer": "x", "keywords": ["tunneling", "aislar", "bloquear"], "min_keywords": 2})
    out = grade_exercise(ex, "hay dns tunneling, conviene aislar el host")
    assert out.correct is None and out.needs_review is True
    assert set(out.detail["keywords_covered"]) == {"tunneling", "aislar"}


def test_free_text_empty():
    ex = _ex(type="free_text", answer={"keywords": ["a"], "min_keywords": 1})
    out = grade_exercise(ex, "")
    assert out.correct is None and out.ratio == 0.0
