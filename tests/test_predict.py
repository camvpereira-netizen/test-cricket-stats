"""Prediction models on simulated matches where the true team order and home boost are known.

Strengths in tests/synthetic.py: Australia > India > England > Bangladesh, home boost 15%.
"""
import math

import pytest

from cricket import predict


@pytest.fixture(scope="module")
def mm(syn_con):
    return predict.build_match_model(syn_con)


def test_match_probabilities_sum_to_one(mm):
    p = mm.predict("India", "England", "India")
    assert math.isclose(sum(p.values()), 1.0, abs_tol=1e-9)
    assert set(p) == {"India win", "draw", "England win"}


def test_ratings_learn_team_order(mm):
    order = list(mm.ratings_table().team)
    assert order == ["Australia", "India", "England", "Bangladesh"]


def test_home_advantage_and_rating_gap_positive(mm):
    c = mm.metrics["coefficients"]
    assert c["rating gap per 100 (win vs loss)"] > 0
    assert c["home (win vs loss)"] > 0
    at_home = mm.predict("England", "India", "England")["England win"]
    away = mm.predict("England", "India", "India")["England win"]
    assert at_home > away


def test_symmetry(mm):
    a = mm.predict("India", "England", None, toss_winner="India")
    b = mm.predict("England", "India", None, toss_winner="India")
    assert math.isclose(a["India win"], b["India win"], abs_tol=1e-6)  # solver tolerance


def test_match_model_beats_baseline(mm):
    assert mm.metrics["model"]["log_loss"] < mm.metrics["baseline"]["log_loss"]


def test_chase_model(syn_con, mm):
    cm = predict.build_chase_model(syn_con, mm)
    easy = cm.predict("India", "England", runs_needed=50, wickets_in_hand=10, match_overs=150)
    hard = cm.predict("India", "England", runs_needed=50, wickets_in_hand=1, match_overs=150)
    assert easy["India win"] > hard["India win"]
    assert math.isclose(sum(easy.values()), 1.0, abs_tol=1e-9)
    assert cm.metrics["model_at_start_of_chase"]["log_loss"] < cm.metrics["baseline"]["log_loss"]


def test_chase_target_hand_check(syn_con, mm):
    """Target = fielding side's runs minus chasing side's earlier runs, plus one."""
    s = predict.chase_states(syn_con, mm.history)
    mid = s.match_id.iloc[0]
    first = s[s.match_id == mid].iloc[0]
    runs = dict(syn_con.execute(
        "SELECT batting_team, sum(runs) FROM innings WHERE match_id = ? AND innings_no < 4 GROUP BY 1",
        [mid]).fetchall())
    assert first.target == runs[first.fielding] - runs[first.chasing] + 1
    assert first.runs_needed == first.target and first.wickets_in_hand == 10


def test_player_model(syn_con):
    pm = predict.build_player_model(syn_con)
    top = pm.batter("Australia_P3", "home")
    tail = pm.batter("Bangladesh_P11", "away")
    assert top["expected_runs"] > tail["expected_runs"]
    assert 0 <= top["p_100_plus"] <= top["p_50_plus"] <= top["p_30_plus"] <= 1
    assert pm.metrics["batting"]["runs_mae"]["model"] < pm.metrics["batting"]["runs_mae"]["position_average_baseline"]
    assert pm.batter("nobody") is None
    b = pm.bowler("India_P11")
    assert 0 <= b["p_5_plus"] <= b["p_3_plus"] <= b["p_1_plus"] <= 1


def test_prequential_uses_only_earlier_innings():
    import pandas as pd
    df = pd.DataFrame({"player_id": ["a", "a", "a", "b"], "runs": [10, 20, 30, 5]})
    v, n = predict._prequential(df, "runs", decay=1.0)
    assert list(v) == [0, 10, 30, 0] and list(n) == [0, 1, 2, 0]


def test_too_little_data_raises(con):
    with pytest.raises(ValueError):
        predict.build_match_model(con)


def test_match_model_beats_home_advantage_baseline(mm):
    m = mm.metrics
    assert m["model"]["log_loss"] < m["home_advantage_baseline"]["log_loss"]
