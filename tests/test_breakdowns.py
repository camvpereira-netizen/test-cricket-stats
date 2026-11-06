"""Breakdowns, dismissals, best innings and the newer filters, hand-checked against the fixture.

M1: A v B at Lord's (England, 2020). X made 6 (caught, 4 balls) in the 1st innings and took 5/4
(lbw and four bowled) in the 2nd. W hit one six. B3, B4, B6 and B7 made ducks.
M2: A v C at the MCG (Australia, 2021). X made 54 (nine sixes, caught, 10 balls) in the 1st
innings and took 0/6 in the 2nd.
"""
from cricket import stats
from cricket.stats import Filters


def test_batting_breakdown_by_opponent_and_country(con):
    t = stats.batting_breakdown(con, "id_X", by="opponent")
    assert list(t.opponent) == ["C", "B"]          # most runs first
    assert list(t.runs) == [54, 6] and list(t.highest) == ["54", "6"]
    t = stats.batting_breakdown(con, "id_X", by="host_country").set_index("host_country")
    assert t.loc["England", "runs"] == 6 and t.loc["Australia", "average"] == 54
    assert t.loc["England", "strike_rate"] == 150.0   # 6 runs off 4 balls


def test_batting_breakdown_by_innings_year_and_ground(con):
    t = stats.batting_breakdown(con, "id_X", by="innings")
    assert list(t.match_innings) == [1] and t.runs.iloc[0] == 60 and t.average.iloc[0] == 30
    t = stats.batting_breakdown(con, "id_X", by="year")
    assert list(t.year) == [2020, 2021] and list(t.fifties) == [0, 1]
    t = stats.batting_breakdown(con, "id_X", by="ground")
    assert set(t.ground) == {"Lord's", "Melbourne Cricket Ground"}


def test_bowling_breakdown(con):
    t = stats.bowling_breakdown(con, "id_X", by="opponent").set_index("opponent")
    assert t.loc["B", "wickets"] == 5 and t.loc["B", "average"] == 0.8
    assert t.loc["B", "five_wicket_hauls"] == 1 and t.loc["B", "strike_rate"] == 1.2
    assert t.loc["C", "wickets"] == 0 and t.loc["C", "economy"] == 6.0


def test_dismissals(con):
    t = stats.dismissals(con, "id_X")
    assert list(t.how_out) == ["caught"] and t.times.iloc[0] == 2 and t.percent.iloc[0] == 100.0
    assert stats.dismissals(con, "id_Y").empty    # never out


def test_top_innings(con):
    bat = stats.top_innings(con, kind="batting", limit=3)
    assert bat.player.iloc[0] == "X" and bat.score.iloc[0] == "54" and bat.ground.iloc[0] == "Melbourne Cricket Ground"
    bowl = stats.top_innings(con, Filters(host_country="England"), kind="bowling", limit=1)
    assert bowl.figures.iloc[0] == "5/4" and bowl.overs.iloc[0] == "1.0"


def test_between_filter(con):
    assert stats.batting_summary(con, "id_X", Filters(between=["A", "B"]))["runs"] == 6
    assert stats.batting_summary(con, "id_X", Filters(between=["A", "C"]))["runs"] == 54
    assert stats.batting_summary(con, "id_X", Filters(between=["B", "C"]))["innings"] == 0


def test_venue_alternatives(con):
    assert stats.batting_summary(con, "id_X", Filters(venue_contains="gabba|lords"))["runs"] == 6
    assert stats.batting_summary(con, "id_X", Filters(venue_contains="lords|melbourne"))["runs"] == 60


def test_new_leaderboard_orders(con):
    lb = stats.batting_leaderboard(con, order_by="sixes", min_innings=1, limit=2)
    assert lb.player.iloc[0] == "X" and lb.sixes.iloc[0] == 9
    lb = stats.batting_leaderboard(con, order_by="ducks", min_innings=1, limit=1)
    assert lb.ducks.iloc[0] == 1
    lb = stats.bowling_leaderboard(con, order_by="five_wicket_hauls", min_wickets=1, limit=1)
    assert lb.player.iloc[0] == "X" and lb.five_wicket_hauls.iloc[0] == 1
