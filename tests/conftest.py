"""Builds a tiny fake Cricsheet zip (2 Tests) in Cricsheet JSON format, then the DuckDB.

Expected values in test_stats.py are calculated by hand from these deliveries.
"""
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from cricket import ingest, stats  # noqa: E402


def dl(batter, bowler, non_striker, bat=0, extras=None, wicket=None):
    ex = extras or {}
    d = {"batter": batter, "bowler": bowler, "non_striker": non_striker,
         "runs": {"batter": bat, "extras": sum(ex.values()), "total": bat + sum(ex.values())}}
    if ex:
        d["extras"] = ex
    if wicket:
        d["wickets"] = [wicket]
    return d


def match(teams, players, date, venue, city, innings, outcome=None, toss=None):
    people = {p: f"id_{p}" for ps in players.values() for p in ps}
    info = {"dates": [date], "match_type": "Test", "gender": "male", "teams": teams,
            "venue": venue, "city": city, "players": players,
            "registry": {"people": people}, "outcome": outcome or {"result": "draw"}}
    if toss:
        info["toss"] = toss
    return {"meta": {"data_version": "1.1.0"}, "info": info, "innings": innings}


A = ["X", "Z", "W", "Y"]
B = [f"B{i}" for i in range(1, 9)]
C = ["C1", "C2"]

M1 = match(["A", "B"], {"A": A, "B": B}, "2020-01-01", "Lord's", "London", [
    {"team": "A", "overs": [
        {"over": 0, "deliveries": [
            dl("X", "B1", "Z", bat=4),
            dl("X", "B1", "Z", extras={"wides": 1}),
            dl("X", "B1", "Z", bat=2, extras={"noballs": 1}),
            dl("X", "B1", "Z", extras={"byes": 2}),
            dl("X", "B1", "Z", wicket={"player_out": "X", "kind": "caught", "fielders": [{"name": "B2"}]}),
            dl("Z", "B1", "W", bat=1),
            dl("Z", "B1", "W"),
        ]},
        {"over": 1, "deliveries": [
            dl("W", "B2", "Z", bat=6),
            dl("W", "B2", "Z", wicket={"player_out": "W", "kind": "retired hurt"}),
            dl("Z", "B2", "Y", wicket={"player_out": "Z", "kind": "run out", "fielders": [{"name": "B3"}]}),
        ]},
    ]},
    {"team": "B", "overs": [
        {"over": 0, "deliveries": [
            dl("B3", "X", "B8", wicket={"player_out": "B3", "kind": "lbw"}),
            dl("B4", "X", "B8", wicket={"player_out": "B4", "kind": "bowled"}),
            dl("B5", "X", "B8", bat=4),
            dl("B5", "X", "B8", wicket={"player_out": "B5", "kind": "bowled"}),
            dl("B6", "X", "B8", wicket={"player_out": "B6", "kind": "bowled"}),
            dl("B7", "X", "B8", wicket={"player_out": "B7", "kind": "bowled"}),
        ]},
    ]},
])

M2 = match(["A", "C"], {"A": A, "C": C}, "2021-06-01", "Melbourne Cricket Ground", "Melbourne", [
    {"team": "A", "overs": [
        {"over": 0, "deliveries": [dl("X", "C1", "Y", bat=6) for _ in range(6)]},
        {"over": 1, "deliveries": [dl("X", "C1", "Y", bat=6) for _ in range(3)] + [
            dl("X", "C1", "Y", wicket={"player_out": "X", "kind": "caught", "fielders": [{"name": "C2"}]})]},
    ]},
    {"team": "C", "overs": [
        {"over": 0, "deliveries": [dl("C2", "X", "C1", bat=1) for _ in range(6)]},
    ]},
], outcome={"winner": "A", "by": {"innings": 1, "runs": 48}}, toss={"winner": "C", "decision": "field"})


@pytest.fixture(scope="session")
def con(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cricsheet")
    zp = tmp / "tests_male_json.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("1001.json", json.dumps(M1))
        zf.writestr("1002.json", json.dumps(M2))
        zf.writestr("README.txt", "not a match")
    db = tmp / "tests.duckdb"
    ingest.build_db(zp, db)
    c = stats.connect(str(db))
    yield c
    c.close()


@pytest.fixture(scope="session")
def syn_con(tmp_path_factory):
    """DuckDB built from 160 simulated Tests (see tests/synthetic.py)."""
    import synthetic
    tmp = tmp_path_factory.mktemp("synthetic")
    db = tmp / "syn.duckdb"
    ingest.build_db(synthetic.write_zip(tmp / "syn.zip"), db)
    c = stats.connect(str(db))
    yield c
    c.close()
