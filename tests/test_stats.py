from datetime import date

from cricket.stats import Filters, batting_summary, bowling_summary, compare, find_players


def test_find_players(con):
    df = find_players(con, "X")
    assert df.player_id.iloc[0] == "id_X"
    assert df.matches.iloc[0] == 2


def test_batting_career(con):
    s = batting_summary(con, "id_X")
    # Inns 1: 4 + 2 (off a no-ball) = 6 runs; balls faced 4 (wide excluded, no-ball and bye count)
    # Inns 2: 9 sixes = 54 runs off 10 balls
    assert s["matches"] == 2
    assert s["innings"] == 2
    assert s["runs"] == 60
    assert s["average"] == 30.0
    assert s["strike_rate"] == round(100 * 60 / 14, 2)
    assert s["highest"] == "54"
    assert s["fifties"] == 1 and s["hundreds"] == 0
    assert s["fours"] == 1 and s["sixes"] == 9


def test_retired_hurt_is_not_out(con):
    s = batting_summary(con, "id_W")
    assert s["runs"] == 6 and s["innings"] == 1 and s["not_outs"] == 1
    assert s["average"] is None
    assert s["highest"] == "6*"


def test_non_striker_only_counts_as_innings(con):
    s = batting_summary(con, "id_B8")
    assert s["innings"] == 1 and s["not_outs"] == 1 and s["runs"] == 0 and s["ducks"] == 0


def test_duck(con):
    assert batting_summary(con, "id_B4")["ducks"] == 1


def test_run_out_counts_as_dismissal_not_bowler_wicket(con):
    assert batting_summary(con, "id_Z")["not_outs"] == 0
    assert bowling_summary(con, "id_B2")["wickets"] == 0


def test_bowling_extras_rules(con):
    s = bowling_summary(con, "id_B1")
    # legal balls 5 (wide + no-ball excluded); conceded 4+1+3+0(bye)+0+1+0 = 9
    assert s["overs"] == "0.5"
    assert s["runs_conceded"] == 9
    assert s["wickets"] == 1


def test_bowling_career(con):
    s = bowling_summary(con, "id_X")
    assert s["overs"] == "2.0"
    assert s["runs_conceded"] == 10
    assert s["wickets"] == 5
    assert s["best_innings"] == "5/4"
    assert s["average"] == 2.0 and s["economy"] == 5.0 and s["strike_rate"] == 2.4
    assert s["five_wicket_hauls"] == 1 and s["ten_wicket_matches"] == 0


def test_opponent_filter(con):
    s = batting_summary(con, "id_X", Filters(opponent="B"))
    assert s["matches"] == 1 and s["runs"] == 6
    b = bowling_summary(con, "id_X", Filters(opponent="C"))
    assert b["wickets"] == 0 and b["runs_conceded"] == 6


def test_venue_and_date_filters(con):
    assert batting_summary(con, "id_X", Filters(venue_contains="melbourne"))["runs"] == 54
    assert batting_summary(con, "id_X", Filters(date_from=date(2021, 1, 1)))["runs"] == 54
    assert batting_summary(con, "id_X", Filters(date_to=date(2020, 12, 31)))["runs"] == 6


def test_compare(con):
    df = compare(con, ["id_X", "id_W"])
    assert list(df.columns) == ["X", "W"]
    assert df.loc["bat_runs", "X"] == 60
