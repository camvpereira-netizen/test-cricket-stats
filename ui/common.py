"""Shared pieces of the web app: getting the database, cached models, filters and pickers."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

from cricket import predict, stats

ROOT = Path(__file__).resolve().parents[1]
DB = Path(os.environ.get("CRICKET_DB", ROOT / "data" / "tests.duckdb"))
DB_MAX_AGE_HOURS = 24  # fetch the nightly build once the local copy is older than this
# The nightly workflow publishes here; override with CRICKET_DB_URL (env or Streamlit secret)
DEFAULT_DB_URL = ("https://github.com/camvpereira-netizen/test-cricket-stats/releases/download/"
                  "data-latest/tests.duckdb")


def _db_url() -> str | None:
    """Where to fetch the database from. None when CRICKET_DB points at a local file to use as is."""
    if url := os.environ.get("CRICKET_DB_URL"):
        return url
    try:
        if url := st.secrets.get("CRICKET_DB_URL"):
            return url
    except FileNotFoundError:  # no secrets file
        pass
    except Exception as e:  # a secrets file that does not parse
        st.warning(f"Could not read the app's secrets ({e}); using the default data link.")
    return None if "CRICKET_DB" in os.environ else DEFAULT_DB_URL


def download_db(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".download")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    os.replace(tmp, dest)  # swap in whole, so a reader never sees half a file


def ensure_db() -> bool:
    """True once a database is available, fetching the latest build first when one is configured."""
    url = _db_url()
    stale = not DB.exists() or time.time() - DB.stat().st_mtime > DB_MAX_AGE_HOURS * 3600
    if url and stale:
        try:
            with st.spinner("Fetching the latest data..."):
                download_db(url, DB)
        except requests.RequestException as e:
            if DB.exists():
                st.warning(f"Could not fetch the latest data ({e}); showing the last copy.")
            else:
                st.error(f"Could not fetch the data from {url}: {e}")
    return DB.exists()


@st.cache_resource
def get_con(db_mtime: float):
    return stats.connect(str(DB))


def con():
    return get_con(DB.stat().st_mtime)


@st.cache_resource(show_spinner="Fitting prediction models...")
def get_models(db_mtime: float) -> dict:
    c = get_con(db_mtime)
    out = {}
    try:
        out["match"] = predict.build_match_model(c)
        out["chase"] = predict.build_chase_model(c, out["match"])
    except ValueError as e:
        out["error"] = str(e)
    out["player"] = predict.build_player_model(c)
    return out


def models() -> dict:
    return get_models(DB.stat().st_mtime)


@dataclass
class Ctx:
    """What every page needs; filters apply to the stats pages only."""
    con: object
    teams: list[str]
    filters: stats.Filters


def ctx() -> Ctx:
    c = con()
    return Ctx(con=c, teams=stats.teams(c), filters=st.session_state.get("_filters", stats.Filters()))


def sidebar_filters(c) -> stats.Filters:
    with st.sidebar:
        st.header("Filters")
        st.caption("Apply to the stats pages.")
        team_list = stats.teams(c)
        opponent = st.selectbox("Against", ["Any"] + team_list, key="f_opponent")
        venue = st.text_input("Venue or city contains", "", key="f_venue")
        span = c.execute("SELECT min(start_date), max(start_date) FROM matches").fetchone()
        picked = st.date_input("Date range", value=(span[0], span[1]),
                               min_value=span[0], max_value=span[1], key="f_dates")
        d_from, d_to = picked if len(picked) == 2 else (span[0], span[1])
        s = stats.data_summary(c)
        st.caption(f"Team records cover all {s['tests']:,} Tests from {span[0]:%Y}. Player stats cover "
                   f"only the {s['with_scorecards']:,} Tests with scorecards (mostly 2001 on), "
                   "not full careers.")
    return stats.Filters(opponent=None if opponent == "Any" else opponent,
                         venue_contains=venue or None, date_from=d_from, date_to=d_to)


def player_picker(c, label: str, key: str):
    q = st.text_input(label, key=f"q_{key}", placeholder="e.g. Smith, Root, Cummins")
    if not q:
        return None
    found = stats.find_players(c, q)
    if found.empty:
        st.warning("No players found.")
        return None
    opts = {f"{r.player} ({r.team}, {r.first_year}–{r.last_year}, {r.matches} Tests)": r.player_id
            for r in found.itertuples()}
    return opts[st.selectbox("Select", list(opts), key=f"s_{key}")]


def show_probs(p: dict[str, float]):
    cols = st.columns(len(p))
    for col, (k, v) in zip(cols, p.items()):
        col.metric(k, f"{100 * v:.0f}%")


def tidy(df: pd.DataFrame) -> pd.DataFrame:
    return df.drop(columns=[c for c in df.columns if c.endswith("player_id")], errors="ignore")
