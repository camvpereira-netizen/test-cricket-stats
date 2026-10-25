"""Hand-checked tests for innings totals, splits, team records and leaderboards.

All expected values come from the two fixture matches in conftest.py.
"""
from cricket.stats import (batting_leaderboard, bowling_leaderboard, head_to_head,
                           home_away_split, innings_splits, team_record)
from cricket.venues import home_team, host_country


def test_innings_totals(con):
    rows = con.execute("SELECT runs, wickets, legal_balls FROM innings "
                       "WHERE match_id = '1001' ORDER BY innings_no").fetchall()
    # Inns 1: 4 + 1 (wide) + 3 (2 off no-ball + 1) + 2 (byes) + 1 + 6 = 17.
    # Wickets: X caught, Z run out; W retired hurt does not count. Legal balls: 5 + 3 = 8.
    assert rows[0] == (17, 2, 8)
    assert rows[1] == (4, 5, 6)


def test_batting_order(con):
    order = con.execute("SELECT player FROM batting_order WHERE match_id = '1001' AND innings_no = 1 "
                        "ORDER BY position").fetchall()
    assert [r[0] for r in order] == ["X", "Z", "W", "Y"]


def test_match_info(con):
    row = con.execute("SELECT country, home_team, toss_winner, toss_decision, winner, win_by_innings "
                      "FROM matches WHERE match_id = '1002'").fetchone()
    assert row == ("Australia", None, "C", "field", "A", True)


def test_venue_lookup():
    assert host_country("Cardiff", None) == "England"
    assert host_country(None, "Kensington Oval, Bridgetown, Barbados") == "West Indies"
    assert host_country("Dubai", None) == "United Arab Emirates"
    assert host_country("Nowhere", "Unknown Ground") is None
    assert home_team("India", "England", "India") == "India"
    assert home_team("Pakistan", "England", "United Arab Emirates") is None


def test_innings_splits(con):
    s = innings_splits(con, "id_X")
    assert list(s.innings_no) == [1]
    assert s.runs.iloc[0] == 60 and s.innings.iloc[0] == 2


def test_home_away_split_neutral(con):
    s = home_away_split(con, "id_X")
    assert list(s.venue_type) == ["neutral"] and s.runs.iloc[0] == 60


def test_team_record(con):
    r = team_record(con, "A").set_index("opponent")
    assert r.loc["B", "drawn"] == 1 and r.loc["B", "won"] == 0
    assert r.loc["C", "won"] == 1 and r.loc["C", "win_pct"] == 100.0
    assert team_record(con, "C").loc[0, "lost"] == 1


def test_head_to_head(con):
    h = head_to_head(con, "C", "A")
    assert len(h) == 1 and h.result.iloc[0] == "A" and h.margin.iloc[0] == "innings and 48 runs"


def test_batting_leaderboard(con):
    lb = batting_leaderboard(con, min_innings=1)
    top = lb.iloc[0]
    assert top.player == "X" and top.runs == 60 and top.innings == 2 and top.average == 30.0
    y = lb.set_index("player").loc["Y"]  # non-striker only in both matches
    assert y.innings == 2 and y.runs == 0 and y.outs == 0
    by_avg = batting_leaderboard(con, min_innings=1, order_by="average")
    assert by_avg.iloc[0].player == "X"  # W (6*) and Y have no average


def test_bowling_leaderboard(con):
    lb = bowling_leaderboard(con, min_wickets=1)
    assert lb.iloc[0].player == "X" and lb.iloc[0].wickets == 5 and lb.iloc[0].average == 2.0
    assert set(lb.player) == {"X", "B1", "C1"}  # B2 only had a run out: not a bowler wicket
    opp = bowling_leaderboard(con, min_wickets=0, order_by="economy")
    assert opp.set_index("player").loc["C1", "runs_conceded"] == 54


def test_mackay_is_australia():
    from cricket.venues import host_country
    assert host_country("Mackay", "Great Barrier Reef Arena, Mackay") == "Australia"


def test_recent_results(con):
    from cricket import stats
    # M2 (2021, A won by an innings and 48) is newer than M1 (2020, drawn)
    r = stats.recent_results(con)
    assert list(r.result) == ["A won by an innings and 48 runs", "Drawn"]
    assert r.teams.iloc[0] == "A v C" and r.venue.iloc[1] == "Lord's"


def test_data_summary_and_build_info(con, tmp_path):
    import json
    import zipfile

    from conftest import M1, M2
    from cricket import ingest, stats
    s = stats.data_summary(con)
    assert s["tests"] == 2 and s["with_scorecards"] == 2 and s["built_at"] is None
    assert str(s["latest_test"]) == "2021-06-01"
    zp, db = tmp_path / "t.zip", tmp_path / "t.duckdb"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("1001.json", json.dumps(M1))
        zf.writestr("1002.json", json.dumps(M2))
    ingest.build_db(zp, db)
    info = ingest.write_build_info(db)
    assert info["tests"] == 2 and info["ball_by_ball"] == 2
    c = stats.connect(str(db))
    assert stats.data_summary(c)["built_at"] is not None
    c.close()
