"""The free question reader, hand-checked against the fixture: M1 A v B at Lord's (England, 2020),
M2 A v C at the MCG (Australia, 2021). X made 6 and 54 in the first innings and took 5/4 against B."""
from cricket import questions


def test_player_with_filters(con):
    plan = questions.parse(con, "X batting in the 1st innings since 2021")
    assert plan.kind == "player" and plan.calls == [
        ("player_batting", {"player_id": "id_X", "filters": {"date_from": "2021-01-01", "innings": 1}})]
    a = questions.answer(con, "X batting in the 1st innings since 2021")
    assert "54 runs" in a.text and "scorecards" in a.text


def test_player_bowling_against_team(con):
    a = questions.answer(con, "X's wickets against B")
    assert a.steps[-1].tool == "player_bowling" and a.steps[-1].table.wickets.iloc[0] == 5


def test_leaderboard_in_host_country(con):
    plan = questions.parse(con, "Most wickets in Australia")
    assert plan.calls[0][0] == "bowling_leaderboard"
    assert plan.calls[0][1]["filters"] == {"host_countries": ["Australia"]}
    a = questions.answer(con, "Most wickets in Australia")
    assert "C1" in a.text and "leads with 1" in a.text


def test_venue_and_minimum(con):
    plan = questions.parse(con, "best batting average at lords minimum 1 innings")
    assert plan.calls[0][1]["filters"] == {"venue": "lords"} and "Lord's" in plan.describe
    assert plan.calls[0][1]["min_innings"] == 1 and plan.calls[0][1]["order_by"] == "average"


def test_unfiltered_leaderboard_uses_whole_careers(con):
    plan = questions.parse(con, "most test runs ever")
    assert plan.kind == "career_leaderboard" and plan.calls[0][1]["order_by"] == "runs"


def test_teams(con):
    a = questions.answer(con, "C record against A")
    assert a.steps[0].tool == "team_record" and "lost 1" in a.text
    h = questions.answer(con, "A v C head to head")
    assert h.steps[0].tool == "head_to_head" and "A won 1" in h.text


def test_compare_players(con):
    plan = questions.parse(con, "compare X, Z and W")
    assert plan.kind == "compare" and plan.calls[0][1]["player_ids"] == ["id_X", "id_Z", "id_W"]


def test_hands_over_what_it_cannot_read(con):
    assert questions.parse(con, "who was the best captain") is None
    assert questions.parse(con, "X in England v Z in Australia") is None  # different filters each


def test_date_phrases():
    f, _ = questions._year_range(" in the 2010s ")
    assert f == {"date_from": "2010-01-01", "date_to": "2019-12-31"}
    f, _ = questions._year_range(" after 2009 before 2016 ")
    assert f == {"date_from": "2010-01-01", "date_to": "2015-12-31"}
    f, _ = questions._year_range(" in 2023 ")
    assert f == {"date_from": "2023-01-01", "date_to": "2023-12-31"}


def test_short_words_are_not_partial_player_names(con):
    assert questions.parse(con, "B record at the zzz") is None


def test_venue_filter_ignores_apostrophes(con):
    from cricket.stats import Filters, batting_summary
    assert batting_summary(con, "id_X", Filters(venue_contains="Lords"))["runs"] == 6
