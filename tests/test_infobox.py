"""Filling list gaps from article infoboxes, and the Cricinfo-style whole-career tables.

Expected values are read off the snippets and the fixture matches by hand.
"""
import pandas as pd

from cricket import careers, infobox, stats
from test_careers import ENGLAND, INDIA, ccon  # noqa: F401  (fixture)

BRADMAN = """{{Short description|Australian cricketer}}
{{Infobox cricketer
| name = Don Bradman
| image = Don Bradman.jpg
| country = Australia
| testcap = 124
| columns = 2
| column1 = [[Test cricket|Test]]
| column2 = [[First-class cricket|First-class]]
| matches1 = 52
| matches2 = 234
| runs1 = 6,996
| runs2 = 28,067
| bat avg1 = 99.94
| 100s/50s1 = 29/13
| top score1 = 334
| deliveries1 = 160
| wickets1 = 2
| bowl avg1 = 36.00
| fivefor1 = 0
| tenfor1 = 0
| best bowling1 = 1/8
| catches/stumpings1 = 32/–
| family={{cslist|3 children|semi=true}}
}}
'''Sir Donald George Bradman''' was an Australian cricketer.
"""

GRACE = """{{Infobox cricketer
| name = W. G. Grace
| columns = 2
| column1 = [[First-class cricket|First-class]]
| column2 = [[Test cricket|Test]]
| matches2 = 22
| runs2 = 1,098
| 100s/50s2 = 2/5
| deliveries2 = 666
| wickets2 = 9
| fivefor2 = 0
| tenfor2 = 0
| catches/stumpings2 = 39/–
}}"""


def test_parse_infobox():
    don = infobox.parse(BRADMAN)
    assert (don["matches"], don["runs"], don["hundreds"], don["fifties"]) == (52, 6996, 29, 13)
    assert (don["balls"], don["wickets"], don["five_wickets"], don["ten_wickets"]) == (160, 2, 0, 0)
    assert don["best_bowling"] == "1/8" and (don["catches"], don["stumpings"]) == (32, 0)
    grace = infobox.parse(GRACE)  # the Test column is the second here
    assert (grace["matches"], grace["hundreds"], grace["fifties"]) == (22, 2, 5)
    assert infobox.parse("no infobox here") is None


def test_runs_from_average():
    assert infobox.runs_from_average(26.22, 9) == 236  # 236 / 9 = 26.222
    assert infobox.runs_from_average(48.09, 74) == 3559  # Root
    assert infobox.runs_from_average(30.0, 400) is None  # 11,998 to 12,002 all round to 30.00
    assert infobox.runs_from_average(None, 3) is None and infobox.runs_from_average(20.0, 0) is None


def test_fill_only_from_a_matching_infobox():
    eng = careers.parse_list(ENGLAND, "England")
    stale = dict(infobox.parse(GRACE), matches=21)  # e.g. not updated after the last Test
    filled = infobox.fill(eng, {"W. G. Grace": infobox.parse(GRACE)}).set_index("cap")
    grace = filled.loc[24]
    assert grace.infobox_used and (grace.hundreds, grace.fifties) == (2, 5)
    assert (grace.five_wickets, grace.ten_wickets) == (0, 0)
    assert grace.runs_conceded == 236  # from the average, as England's list has no runs conceded
    assert grace.balls == 666  # the list's own value is kept
    assert not filled.loc[655].infobox_used and pd.isna(filled.loc[655].hundreds)  # no article given
    skipped = infobox.fill(eng, {"W. G. Grace": stale}).set_index("cap").loc[24]
    assert not skipped.infobox_used and pd.isna(skipped.hundreds)


def test_india_five_ten_column():
    ind = careers.parse_list(INDIA, "India").iloc[0]
    assert (ind.five_wickets, ind.ten_wickets) == (0, 0)


def test_test_career_from_ball_by_ball(con):
    """Player X: 6 (4 balls, one four) and 54 (10 balls, nine sixes); 5/4 and 0/6 with the ball."""
    c = stats.test_career(con, "id_X")
    bat, bowl, other = (c[k].iloc[0] for k in ("batting", "bowling", "other"))
    assert (bat.matches, bat.innings, bat.not_outs, bat.runs, bat.high_score) == (2, 2, 0, 60, "54")
    assert (bat.average, bat.balls_faced, bat.strike_rate) == (30.0, 14, 428.57)
    assert (bat.hundreds, bat.fifties, bat.fours, bat.sixes) == (0, 1, 1, 9)
    assert bat.source == stats.FROM_BALLS
    assert (bowl.innings, bowl.balls, bowl.runs, bowl.wickets) == (2, 12, 10, 5)
    assert (bowl.best_innings, bowl.best_match) == ("5/4", "5/4")
    assert (bowl.average, bowl.economy, bowl.strike_rate) == (2.0, 5.0, 2.4)
    assert (bowl.four_wickets, bowl.five_wickets, bowl.ten_wickets) == (0, 1, 0)
    assert (other.first_year, other.last_year, other.catches, other.stumpings) == (2020, 2021, 0, 0)
    assert pd.isna(other.cap)
    assert stats.test_career(con, "id_B2")["other"].catches.iloc[0] == 1  # caught X in the first Test


def test_test_career_from_lists(ccon):
    c, _ = ccon
    don = stats.test_career(c, "wp:australia:124")
    bat, bowl, other = (don[k].iloc[0] for k in ("batting", "bowling", "other"))
    assert (bat.innings, bat.not_outs, bat.runs, bat.average) == (80, 10, 6996, 99.94)
    assert bat.balls_faced is None and bat.fours is None and bat.source == stats.FROM_LISTS
    assert (bowl.balls, bowl.runs, bowl.wickets, bowl.best_innings) == (160, 72, 2, "1/8")
    assert (bowl.economy, bowl.strike_rate) == (2.7, 80.0) and bowl.best_match is None
    assert (other.cap, other.first_year, other.last_year, other.catches) == (124, 1928, 1948, 32)
    # Bob B1's one Test is in the ball-by-ball data (no runs, one wicket); the list row says
    # 10 runs and no wickets, so a Test must be missing or misattributed: the list is trusted
    assert stats.test_career(c, "id_B1")["bowling"].source.iloc[0] == stats.FROM_LISTS
    c.execute("UPDATE player_careers SET runs = 0, wickets = 1 WHERE player_id = 'id_B1'")
    b1 = stats.test_career(c, "id_B1")  # now they agree, so it is counted from the deliveries
    assert b1["bowling"].source.iloc[0] == stats.FROM_BALLS and b1["other"].cap.iloc[0] == 1


def test_certain_zeros_from_lists(ccon):
    c, _ = ccon
    board = stats.test_career(c, "wp:england:111")  # Jack Board: top score 29, never took a wicket
    assert (board["batting"].hundreds.iloc[0], board["batting"].fifties.iloc[0]) == (0, 0)
    assert board["bowling"].five_wickets.iloc[0] == 0 and board["other"].stumpings.iloc[0] == 3
    don = stats.test_career(c, "wp:australia:124")
    assert pd.isna(don["batting"].hundreds.iloc[0])  # 334 top score: needs the article
    assert (don["bowling"].four_wickets.iloc[0], don["bowling"].ten_wickets.iloc[0]) == (0, 0)
