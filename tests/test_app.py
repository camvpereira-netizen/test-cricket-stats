"""Smoke test: every page of the Streamlit app renders against the simulated database."""
from pathlib import Path

import pytest

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
APP = str(Path(__file__).resolve().parents[1] / "app.py")
PAGES = ["home", "player", "compare", "teams", "leaderboards", "predict", "accuracy", "ask", "games",
         "about"]


@pytest.fixture()
def app(syn_con, monkeypatch):
    db = syn_con.execute("SELECT path FROM duckdb_databases() WHERE NOT internal").fetchone()[0]
    monkeypatch.setenv("CRICKET_DB", db)
    monkeypatch.delenv("CRICKET_DB_URL", raising=False)
    # ui.common reads CRICKET_DB at import, so make each test import it afresh
    import sys
    for mod in [m for m in sys.modules if m == "ui" or m.startswith("ui.")]:
        del sys.modules[mod]
    return AppTest.from_file(APP, default_timeout=60).run()


def test_every_page_renders(app):
    assert not app.exception, app.exception
    assert len(app.metric) == 4  # home page headline numbers
    for name in PAGES:
        app.switch_page(f"app_pages/{name}.py").run()
        assert not app.exception, (name, app.exception)


def test_player_and_predict_pages(app):
    app.switch_page("app_pages/player.py").run()
    assert app.selectbox(key="f_opponent")  # stats pages get the sidebar filters
    app.text_input(key="q_one").set_value("P3").run()
    assert not app.exception, app.exception
    app.switch_page("app_pages/predict.py").run()
    assert len(app.metric) >= 3  # match-result probabilities
    app.radio(key="pred_kind").set_value("Fourth-innings chase").run()
    assert not app.exception, app.exception
    app.radio(key="pred_kind").set_value("Player's next innings").run()
    app.text_input(key="q_pred").set_value("India").run()
    assert not app.exception, app.exception
