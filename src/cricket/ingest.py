"""Download Cricsheet Test match JSON and load it into DuckDB.

Usage:
    python -m cricket.ingest                 # download men's Tests and build data/tests.duckdb
    python -m cricket.ingest --zip path.zip  # build from a zip you already have
    python -m cricket.ingest --wikipedia --min-tests 2627   # nightly build: fail if Tests went missing
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import duckdb
import pandas as pd
import requests

from cricket.stats import BOWLER_WICKET_KINDS, NOT_DISMISSED_KINDS
from cricket.venues import home_team, host_country

DEFAULT_URL = "https://cricsheet.org/downloads/tests_male_json.zip"
NAMES_URL = "https://cricsheet.org/register/names.csv"  # People Register name variants (ODC-BY)
ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "tests.duckdb"


def download(url: str = DEFAULT_URL, dest_dir: Path = RAW_DIR) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / url.rsplit("/", 1)[-1]
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    return dest


def iter_matches(zip_path: Path) -> Iterable[tuple[str, dict]]:
    """Yield (match_id, parsed_json) for every match file, without extracting to disk."""
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.endswith(".json"):
                yield Path(name).stem, json.loads(zf.read(name))


def parse_match(match_id: str, m: dict) -> tuple[dict, list[dict], list[dict], list[dict], list[dict], list[dict]]:
    info = m["info"]
    registry = info.get("registry", {}).get("people", {})
    teams = info["teams"]
    outcome = info.get("outcome", {})
    by = outcome.get("by", {})
    toss = info.get("toss", {})
    country = host_country(info.get("city"), info.get("venue"))

    match_row = {
        "match_id": match_id,
        "start_date": info["dates"][0],
        "match_type": info.get("match_type"),
        "gender": info.get("gender"),
        "team1": teams[0],
        "team2": teams[1],
        "venue": info.get("venue"),
        "city": info.get("city"),
        "country": country,
        "home_team": home_team(teams[0], teams[1], country),
        "toss_winner": toss.get("winner"),
        "toss_decision": toss.get("decision"),  # bat / field
        "winner": outcome.get("winner"),
        "result": outcome.get("result"),  # draw / tie / no result
        "win_by_runs": by.get("runs"),
        "win_by_wickets": by.get("wickets"),
        "win_by_innings": bool(by.get("innings")),
        "source": "cricsheet",
        "detail": "ball_by_ball",
    }

    appearances = [
        {"match_id": match_id, "team": team, "player": p, "player_id": registry.get(p)}
        for team, players in info.get("players", {}).items()
        for p in players
    ]

    deliveries, wickets, innings, order = [], [], [], []
    for inn_no, inn in enumerate(m.get("innings", []), start=1):
        bat = inn["team"]
        bowl = teams[1] if bat == teams[0] else teams[0]
        n_before = len(deliveries)
        n_wkts_before = len(wickets)
        for over in inn.get("overs", []):
            for ball_no, d in enumerate(over["deliveries"], start=1):
                ex = d.get("extras", {})
                runs = d["runs"]
                deliveries.append({
                    "match_id": match_id, "innings_no": inn_no,
                    "batting_team": bat, "bowling_team": bowl,
                    "over_no": over["over"], "ball_no": ball_no,
                    "batter": d["batter"], "batter_id": registry.get(d["batter"]),
                    "non_striker": d["non_striker"],
                    "non_striker_id": registry.get(d["non_striker"]),
                    "bowler": d["bowler"], "bowler_id": registry.get(d["bowler"]),
                    "runs_batter": runs["batter"], "runs_extras": runs["extras"],
                    "runs_total": runs["total"],
                    "wides": ex.get("wides", 0), "noballs": ex.get("noballs", 0),
                    "byes": ex.get("byes", 0), "legbyes": ex.get("legbyes", 0),
                    "penalty": ex.get("penalty", 0),
                })
                for w in d.get("wickets", []):
                    wickets.append({
                        "match_id": match_id, "innings_no": inn_no,
                        "over_no": over["over"], "ball_no": ball_no,
                        "player_out": w["player_out"],
                        "player_out_id": registry.get(w["player_out"]),
                        "kind": w["kind"], "bowler": d["bowler"],
                        "fielders": ", ".join(f.get("name", "") for f in w.get("fielders", [])),
                    })
        inn_dels = deliveries[n_before:]
        seen: list[str] = []
        for d in inn_dels:
            for who in (d["batter"], d["non_striker"]):
                if who not in seen:
                    seen.append(who)
        order += [{"match_id": match_id, "innings_no": inn_no, "player": p,
                   "player_id": registry.get(p), "position": i}
                  for i, p in enumerate(seen, start=1)]
        pen = inn.get("penalty_runs", {})
        innings.append({
            "match_id": match_id, "innings_no": inn_no,
            "batting_team": bat, "bowling_team": bowl,
            "runs": sum(d["runs_total"] for d in inn_dels) + pen.get("pre", 0) + pen.get("post", 0),
            "wickets": sum(1 for w in wickets[n_wkts_before:]
                           if w["kind"] not in ("retired hurt", "retired not out")),
            "legal_balls": sum(1 for d in inn_dels if d["wides"] == 0 and d["noballs"] == 0),
            "declared": bool(inn.get("declared")),
            "forfeited": bool(inn.get("forfeited")),
        })
    return match_row, appearances, deliveries, wickets, innings, order


def build_db(zip_path: Path, db_path: Path = DB_PATH) -> dict[str, int]:
    matches, apps, dels, wkts, inns, order = [], [], [], [], [], []
    for mid, m in iter_matches(zip_path):
        if m["info"].get("match_type") != "Test":
            continue
        a, b, c, d, e, o = parse_match(mid, m)
        matches.append(a); apps.extend(b); dels.extend(c); wkts.extend(d); inns.extend(e)
        order.extend(o)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    frames = {
        "matches": pd.DataFrame(matches),
        "appearances": pd.DataFrame(apps),
        "deliveries": pd.DataFrame(dels),
        "wickets": pd.DataFrame(wkts),
        "innings": pd.DataFrame(inns),
        "batting_order": pd.DataFrame(order),
    }
    for name, df in frames.items():
        con.register("tmp_df", df)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM tmp_df")
        con.unregister("tmp_df")
    con.execute("ALTER TABLE matches ALTER start_date TYPE DATE")
    # pandas stores integer columns holding NULLs as floats; restore integers
    con.execute("ALTER TABLE matches ALTER win_by_runs TYPE INTEGER")
    con.execute("ALTER TABLE matches ALTER win_by_wickets TYPE INTEGER")
    con.execute("ALTER TABLE matches ADD COLUMN test_no INTEGER")
    derive_scorecards(con)
    con.close()
    return {k: len(v) for k, v in frames.items()}


def load_player_names(con, csv_path: Path) -> int:
    """Store the Cricsheet register's other names ("Steve Smith" for "SPD Smith") for search.

    Keeps only people who appear in the matches; `position` keeps the register's order, so the
    first full name listed is the one shown beside the Cricsheet name.
    """
    names = pd.read_csv(csv_path, dtype=str).rename(columns={"identifier": "player_id"})
    names["position"] = range(len(names))
    con.register("tmp_df", names[["player_id", "name", "position"]])
    con.execute("""CREATE OR REPLACE TABLE player_names AS SELECT * FROM tmp_df
                   WHERE player_id IN (SELECT player_id FROM appearances)""")
    con.unregister("tmp_df")
    return con.execute("SELECT count(*) FROM player_names").fetchone()[0]


def derive_scorecards(con) -> None:
    """Build per-innings batting and bowling scorecards from the ball-by-ball tables.

    Scorecard-only matches (from other sources) are appended to the same two tables, so
    career stats read these instead of deliveries. Columns a source can't supply are NULL.
    """
    con.execute(f"""
    CREATE OR REPLACE TABLE batting_scorecard AS
    WITH who AS (
        SELECT match_id, innings_no, player, player_id, min(position) AS position
        FROM batting_order GROUP BY ALL
        UNION ALL
        SELECT DISTINCT w.match_id, w.innings_no, w.player_out, w.player_out_id, NULL
        FROM wickets w ANTI JOIN batting_order o
          ON o.match_id = w.match_id AND o.innings_no = w.innings_no AND o.player = w.player_out
    ),
    bat AS (
        SELECT match_id, innings_no, batter AS player, sum(runs_batter) AS runs,
               count(*) FILTER (WHERE wides = 0) AS balls,
               count(*) FILTER (WHERE runs_batter = 4) AS fours,
               count(*) FILTER (WHERE runs_batter = 6) AS sixes
        FROM deliveries GROUP BY ALL
    ),
    outs AS (
        SELECT match_id, innings_no, player_out AS player, any_value(kind) AS how_out,
               bool_or(kind NOT IN {NOT_DISMISSED_KINDS}) AS out
        FROM wickets GROUP BY ALL
    )
    SELECT w.match_id, w.innings_no, i.batting_team AS team, i.bowling_team AS opponent,
           w.player, w.player_id, w.position,
           coalesce(b.runs, 0) AS runs, coalesce(b.balls, 0) AS balls,
           coalesce(b.fours, 0) AS fours, coalesce(b.sixes, 0) AS sixes,
           coalesce(o.out, false) AS out, o.how_out
    FROM who w JOIN innings i USING (match_id, innings_no)
    LEFT JOIN bat b USING (match_id, innings_no, player)
    LEFT JOIN outs o USING (match_id, innings_no, player)
    """)
    con.execute(f"""
    CREATE OR REPLACE TABLE bowling_scorecard AS
    WITH per_over AS (
        SELECT match_id, innings_no, over_no, bowler, any_value(bowler_id) AS bowler_id,
               count(*) FILTER (WHERE wides = 0 AND noballs = 0) AS balls,
               sum(runs_total - byes - legbyes - penalty) AS runs
        FROM deliveries GROUP BY ALL
    ),
    wk AS (
        SELECT d.match_id, d.innings_no, d.bowler, count(*) AS wickets
        FROM wickets w JOIN deliveries d
          ON d.match_id = w.match_id AND d.innings_no = w.innings_no
         AND d.over_no = w.over_no AND d.ball_no = w.ball_no
        WHERE w.kind IN {BOWLER_WICKET_KINDS} GROUP BY ALL
    )
    SELECT p.match_id, p.innings_no, i.bowling_team AS team, i.batting_team AS opponent,
           p.bowler AS player, any_value(p.bowler_id) AS player_id,
           sum(p.balls) AS balls,
           count(*) FILTER (WHERE p.balls >= 6 AND p.runs = 0) AS maidens,
           sum(p.runs) AS runs_conceded, coalesce(any_value(wk.wickets), 0) AS wickets
    FROM per_over p JOIN innings i USING (match_id, innings_no)
    LEFT JOIN wk USING (match_id, innings_no, bowler)
    GROUP BY p.match_id, p.innings_no, i.bowling_team, i.batting_team, p.bowler
    """)
    # Scorecard-only rows leave these NULL, so give the columns nullable integer types
    for t, cols in {"batting_scorecard": ("position", "balls", "fours", "sixes"),
                    "bowling_scorecard": ("balls", "maidens")}.items():
        for c in cols:
            con.execute(f"ALTER TABLE {t} ALTER {c} TYPE INTEGER")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", type=Path, help="Use an existing Cricsheet zip instead of downloading")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--wikipedia", action="store_true",
                    help="Also add result-level data for Tests Cricsheet lacks, from Wikipedia")
    ap.add_argument("--min-tests", type=int, default=0,
                    help="Exit with an error if the database ends up with fewer Tests than this")
    args = ap.parse_args()
    zip_path = args.zip or download(args.url)
    counts = build_db(zip_path, args.db)
    print(f"Built {args.db}: " + ", ".join(f"{k}={v:,}" for k, v in counts.items()))
    try:
        names_csv = download(NAMES_URL)
    except requests.RequestException as e:  # search still works on Cricsheet names alone
        print(f"Player names not loaded: {e}")
    else:
        con = duckdb.connect(str(args.db))
        print(f"Player names: {load_player_names(con, names_csv):,}")
        try:  # full names for players the register knows only by initials
            from cricket import wikidata
            print(f"Wikidata names: {wikidata.add_full_names(con, download(wikidata.PEOPLE_URL)):,}")
        except requests.RequestException as e:
            print(f"Wikidata names not loaded: {e}")
        con.close()
    if args.wikipedia:
        from cricket import scorecards, wikipedia
        tests, report = wikipedia.england_checked(wikipedia.all_tests())
        con = duckdb.connect(str(args.db))
        added = scorecards.add_matches(con, tests)
        con.close()
        print("Wikipedia: " + ", ".join(f"{k}={v:,}" for k, v in added.items()))
        print("Checks: " + ", ".join(f"{k}={len(v) if isinstance(v, list) else v}"
                                     for k, v in {**report, **wikipedia.check(tests)}.items()))
    info = write_build_info(args.db)
    print("Build: " + ", ".join(f"{k}={v}" for k, v in info.items()))
    if info["tests"] < args.min_tests:
        sys.exit(f"Only {info['tests']:,} Tests, expected at least {args.min_tests:,}; not publishing.")


def write_build_info(db_path: Path) -> dict:
    """One-row build_info table: when the data was built and what it holds, shown in the app."""
    con = duckdb.connect(str(db_path))
    con.execute("""CREATE OR REPLACE TABLE build_info AS
        SELECT ?::TIMESTAMP AS built_at, count(*) AS tests,
               count(*) FILTER (WHERE detail = 'ball_by_ball') AS ball_by_ball,
               max(start_date) AS latest_test
        FROM matches""", [datetime.now(timezone.utc).replace(tzinfo=None)])  # UTC
    row = con.execute("SELECT * FROM build_info").df().iloc[0].to_dict()
    con.close()
    return row


if __name__ == "__main__":
    main()
