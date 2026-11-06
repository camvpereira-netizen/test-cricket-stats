"""Golden questions the free reader should understand, checked against the real database.

Skipped unless data/tests.duckdb exists (build it with `python -m cricket.ingest --wikipedia`
or download the data-latest release). Each line of golden_questions.txt must be read and
answered without an AI model.
"""
from pathlib import Path

import pytest

from cricket import questions

DB = Path(__file__).resolve().parents[1] / "data" / "tests.duckdb"
QUESTIONS = [q.strip() for q in (Path(__file__).parent / "golden_questions.txt").read_text().splitlines()
             if q.strip() and not q.startswith("#")]


@pytest.fixture(scope="module")
def real_con():
    if not DB.exists():
        pytest.skip("no real database at data/tests.duckdb")
    import duckdb
    c = duckdb.connect(str(DB), read_only=True)
    yield c
    c.close()


@pytest.mark.parametrize("question", QUESTIONS)
def test_reader_answers(real_con, question):
    a = questions.answer(real_con, question)
    assert a is not None, f"not understood: {question}"
    assert a.text and not any(s.error for s in a.steps)


def test_known_answers(real_con):
    """A few figures checked by hand against the published records."""
    a = questions.answer(real_con, "how many runs did steve smith score in 2014")
    assert "1,146 runs" in a.text                       # Smith's 2014 calendar year
    a = questions.answer(real_con, "how many centuries did tendulkar score")
    assert "hundreds **51**" in a.text                  # whole career
    a = questions.answer(real_con, "bradman's highest score")
    assert "**334**" in a.text
