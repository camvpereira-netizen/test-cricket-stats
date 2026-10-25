"""Older Tests: parsing Wikipedia season pages, and mixing result-only and scorecard-only
matches with Cricsheet's ball-by-ball ones. Expected values are worked out by hand."""
import json
import zipfile
from datetime import date

import duckdb
import pandas as pd
import pytest

from conftest import M1, M2
from cricket import ingest, scorecards, stats, wikipedia as wp

SEASON = """
==December==
=== England in Australia ===
{| class="wikitable"
! No. !! Date !! Home captain !! Away captain !! Venue !! Result
|-
| [https://example.org/1 Test 176] || 30 Nov–5 December || [[Jack Ryder (cricketer)|Jack Ryder]] || [[Percy Chapman]] || [[Exhibition Ground]], [[Brisbane]] || {{cr|ENG}} by 675 runs
|-
| [https://example.org/2 Test No: 177] || 29 Dec–5 January || [[Jack Ryder]] || [[Percy Chapman]] || [[Melbourne Cricket Ground]], [[Melbourne]] || {{cr|AUS|1901}} by an innings and 12 runs
|-
| [https://example.org/3 Test 178a] || 8–10 January || [[Jack Ryder]] || [[Percy Chapman]] || [[Sydney Cricket Ground]], [[Sydney]] || Match abandoned
|}
=== MCC in New Zealand ===
{| class="wikitable"
|-
| [https://example.org/4 Test 179] || 10–13 January || [[Tom Lowry]] || [[Harold Gilligan]] || [[Basin Reserve]], [[Wellington]] || Match drawn
|}
===Triangular Tournament===
{| class="wikitable"
|-
| [https://example.org/5 Test 180] || 1–3 February || {{cr|ENG}} || [[C. B. Fry]] || {{cr|RSA}} || [[Louis Tancred]] || [[Lord's]], [[London]] || {{cr|RSA}} by 5 wkts
|}
"""


def test_parse_season_layouts():
    rows = {r["test_no"]: r for r in wp.parse_season(SEASON, "International cricket in 1928–29")}
    assert sorted(rows) == [176, 177, 179, 180]  # 178a was abandoned, not a Test
    r = rows[176]
    assert (r["team1"], r["team2"], r["venue"], r["city"]) == ("Australia", "England", "Exhibition Ground", "Brisbane")
    assert r["start_date"] == date(1928, 11, 30)
    assert rows[177]["start_date"] == date(1928, 12, 29)  # Dec stays in the season's first year
    assert rows[179]["team1"] == "New Zealand" and rows[179]["team2"] == "England"  # MCC = England
    assert rows[179]["start_date"] == date(1929, 1, 10)
    assert (rows[180]["team1"], rows[180]["team2"]) == ("England", "South Africa")


def test_results_and_repairs():
    raw = pd.DataFrame(wp.parse_season(SEASON, "International cricket in 1928–29"))
    df = wp.clean(raw).set_index("test_no")
    assert df.loc[176, "winner"] == "England" and df.loc[176, "win_by_runs"] == 675
    assert df.loc[177, "winner"] == "Australia" and df.loc[177, "win_by_innings"]
    assert df.loc[177, "win_by_runs"] == 12
    assert df.loc[180, "winner"] == "South Africa" and df.loc[180, "win_by_wickets"] == 5
    assert 179 not in df.index  # New Zealand had no Test status in January 1929


def test_clean_drops_pre_status_and_renumbers_duplicates():
    base = {"home_captain": "a", "away_captain": "b", "venue": "Lord's", "city": "London",
            "result_text": "Match drawn", "source": "wikipedia", "detail": "result", "season_page": "x"}
    raw = pd.DataFrame([
        {**base, "test_no": 10, "start_date": date(1914, 3, 6), "team1": "New Zealand", "team2": "Australia"},
        {**base, "test_no": 20, "start_date": date(1929, 6, 15), "team1": "England", "team2": "South Africa"},
        {**base, "test_no": 20, "start_date": date(1929, 6, 29), "team1": "England", "team2": "South Africa"},
        {**base, "test_no": 22, "start_date": date(1929, 7, 13), "team1": "England", "team2": "South Africa"},
    ])
    df = wp.clean(raw)
    assert list(df.test_no) == [20, 21, 22]  # NZ 1914 dropped; second "20" was really 21
    assert df.set_index("test_no").loc[21, "start_date"] == date(1929, 6, 29)


def test_captain_fills_home_side_at_neutral_host():
    base = {"venue": "Dubai International Cricket Stadium", "city": "Dubai", "result_text": "Match drawn",
            "source": "wikipedia", "detail": "result", "season_page": "x", "start_date": date(2018, 10, 7)}
    raw = pd.DataFrame([
        {**base, "test_no": 1, "team1": "Pakistan", "team2": "Sri Lanka", "home_captain": "Sarfaraz Ahmed",
         "away_captain": "X"},
        {**base, "test_no": 2, "team1": None, "team2": "Australia", "home_captain": "Sarfaraz Ahmed",
         "away_captain": "Tim Paine"},
    ])
    df = wp.resolve_teams(raw).set_index("test_no")
    assert df.loc[2, "team1"] == "Pakistan"
    assert df.loc[2, "country"] == "United Arab Emirates" and df.loc[2, "home_team"] is None


@pytest.fixture()
def mixed(tmp_path):
    """The two fixture Tests plus one result-only and one scorecard-only older match."""
    zp = tmp_path / "t.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("1001.json", json.dumps(M1))
        zf.writestr("1002.json", json.dumps(M2))
    db = tmp_path / "t.duckdb"
    ingest.build_db(zp, db)
    con = duckdb.connect(str(db))
    matches = pd.DataFrame([
        # same teams as fixture match 1001, a day out: already there, only gets its number
        {"match_id": "wp900", "test_no": 900, "start_date": date(2020, 1, 2), "team1": "B", "team2": "A",
         "winner": "B", "source": "wikipedia", "detail": "result"},
        {"match_id": "wp5", "test_no": 5, "start_date": date(1950, 1, 1), "team1": "A", "team2": "B",
         "venue": "Lord's", "city": "London", "winner": "A", "win_by_runs": 10,
         "source": "wikipedia", "detail": "result"},
        {"match_id": "sc6", "test_no": 6, "start_date": date(1951, 1, 1), "team1": "A", "team2": "C",
         "winner": None, "result": "draw", "source": "wisden", "detail": "scorecard"},
    ])
    batting = pd.DataFrame([
        # X makes 100 with balls faced unknown, then 20 not out off 40 balls
        {"match_id": "sc6", "innings_no": 1, "team": "A", "opponent": "C", "player": "X", "player_id": "id_X",
         "position": 1, "runs": 100, "balls": None, "out": True, "how_out": "bowled"},
        {"match_id": "sc6", "innings_no": 3, "team": "A", "opponent": "C", "player": "X", "player_id": "id_X",
         "position": 1, "runs": 20, "balls": 40, "out": False},
    ])
    bowling = pd.DataFrame([
        {"match_id": "sc6", "innings_no": 2, "team": "A", "opponent": "C", "player": "X", "player_id": "id_X",
         "balls": None, "runs_conceded": 30, "wickets": 3},
    ])
    counts = scorecards.add_matches(con, matches, batting, bowling)
    yield con, counts
    con.close()


def test_add_matches_skips_existing(mixed):
    con, counts = mixed
    assert counts["matches_added"] == 2 and counts["already_present"] == 1
    assert counts["existing_matches_numbered"] == 1
    assert con.execute("SELECT test_no FROM matches WHERE match_id = '1001'").fetchone()[0] == 900
    assert con.execute("SELECT count(*) FROM matches").fetchone()[0] == 4


def test_result_only_match_counts_in_team_record(mixed):
    con, _ = mixed
    r = stats.team_record(con, "A").set_index("opponent")
    assert r.loc["B", "played"] == 2 and r.loc["B", "won"] == 1 and r.loc["B", "drawn"] == 1
    assert r.loc["C", "played"] == 2 and r.loc["C", "drawn"] == 1


def test_scorecard_only_innings_in_player_stats(mixed):
    con, _ = mixed
    s = stats.batting_summary(con, "id_X")
    # Ball-by-ball: 6 off 4 and 54 off 10 (both out). Scorecard: 100 (out, balls unknown), 20* off 40
    assert s["matches"] == 3 and s["innings"] == 4 and s["runs"] == 180
    assert s["average"] == 60.0 and s["hundreds"] == 1
    assert s["strike_rate"] == round(100 * 80 / 54, 2)  # only innings with balls known
    assert s["innings_with_balls_known"] == 3
    b = stats.bowling_summary(con, "id_X")
    assert b["wickets"] == 8 and b["runs_conceded"] == 40 and b["average"] == 5.0
    assert b["economy"] == 5.0  # the 1951 innings has no balls, so economy is from 2020-21 only
    assert b["innings_with_balls_known"] == 2
    lb = stats.batting_leaderboard(con, min_innings=1).set_index("player")
    assert lb.loc["X", "runs"] == 180 and lb.loc["X", "strike_rate"] == round(100 * 80 / 54, 2)


def test_coverage(mixed):
    con, _ = mixed
    cov = scorecards.coverage(con).set_index(["decade", "detail"]).matches
    assert cov[("1950s", "result")] == 1 and cov[("1950s", "scorecard")] == 1
    assert cov[("2020s", "ball_by_ball")] == 2
