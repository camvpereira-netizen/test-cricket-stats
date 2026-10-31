"""Streamlit web app: search, compare and predict Test cricket from Cricsheet and Wikipedia data.

Run:  streamlit run app.py
The database is data/tests.duckdb (build it with `python -m cricket.ingest --wikipedia`), or set
CRICKET_DB_URL to the nightly build's release file and the app fetches it.
Each page is a file in app_pages/ that calls its function in ui/views.py.
"""
import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))
from cricket import ATTRIBUTION, stats  # noqa: E402
from ui import common, views  # noqa: E402

st.set_page_config(page_title="Test Cricket Stats", page_icon=":material/sports_cricket:", layout="wide")

if not common.ensure_db():
    st.error("No database yet. Run `PYTHONPATH=src python -m cricket.ingest --wikipedia` first "
             "(see README), or set CRICKET_DB_URL.")
    st.stop()


def page(name: str, title: str, icon: str, default: bool = False) -> st.Page:
    p = st.Page(f"app_pages/{name}.py", title=title, icon=f":material/{icon}:", default=default)
    views.PAGES[name] = p
    return p


STATS_PAGES = ["player", "compare", "teams", "leaderboards"]
nav = st.navigation({
    "": [page("home", "Home", "home", default=True)],
    "Stats": [page("player", "Players", "person_search"),
              page("compare", "Compare", "compare_arrows"),
              page("teams", "Teams", "groups"),
              page("leaderboards", "Leaderboards", "leaderboard"),
              page("ask", "Ask the guru", "chat")],
    "Predict": [page("predict", "Predictions", "insights"),
                page("accuracy", "Model accuracy", "fact_check")],
    "Coming soon": [page("games", "Games", "sports_esports")],
    "About": [page("about", "About the data", "info")],
}, position="top")

if nav.url_path in {views.PAGES[n].url_path for n in STATS_PAGES}:
    st.session_state["_filters"] = common.sidebar_filters(common.con())

nav.run()

st.divider()
st.caption(ATTRIBUTION)
