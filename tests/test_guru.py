"""The stats guru: tool checking against the fixture match, and the question loop with a fake Claude."""
from datetime import date
from types import SimpleNamespace as NS

import pytest

from cricket import guru
from cricket.stats import Filters, batting_summary, batting_leaderboard, bowling_summary


# New filters, hand-checked against the fixture: M1 at Lord's (England), M2 at the MCG (Australia).
# X batted in the first innings of both (6 and 54) and bowled in the second (5/4 and 0/6).
def test_host_country_filter(con):
    assert batting_summary(con, "id_X", Filters(host_country="England"))["runs"] == 6
    assert batting_summary(con, "id_X", Filters(host_country=["Australia"]))["runs"] == 54
    s = batting_summary(con, "id_X", Filters(host_country=["England", "Australia"]))
    assert s["runs"] == 60 and s["matches"] == 2


def test_innings_filter(con):
    assert batting_summary(con, "id_X", Filters(innings_no=1))["innings"] == 2
    assert batting_summary(con, "id_X", Filters(innings_no=2))["innings"] == 0
    assert bowling_summary(con, "id_X", Filters(innings_no=2))["wickets"] == 5
    assert bowling_summary(con, "id_X", Filters(innings_no=1))["wickets"] == 0


def test_team_and_venue_type_filters(con):
    assert batting_summary(con, "id_X", Filters(team="A"))["runs"] == 60
    s = batting_summary(con, "id_X", Filters(team="B"))
    assert s["runs"] == 0 and s["matches"] == 0
    # Teams A, B and C have no home country, so both Tests count as neutral
    assert batting_summary(con, "id_X", Filters(venue_type="neutral"))["matches"] == 2
    assert batting_summary(con, "id_X", Filters(venue_type="home"))["innings"] == 0
    lb = batting_leaderboard(con, Filters(team="B"), min_innings=1)
    assert set(lb.team) == {"B"}
    with pytest.raises(ValueError):
        Filters(venue_type="abroad").row_sql()


def test_tools_check_inputs(con):
    with pytest.raises(guru.ToolInputError, match="Unknown team"):
        guru.run_tool(con, "team_record", {"team": "Narnia"})
    with pytest.raises(guru.ToolInputError, match="date"):
        guru.run_tool(con, "player_batting", {"player_id": "id_X", "filters": {"date_from": "last year"}})
    with pytest.raises(guru.ToolInputError, match="No player"):
        guru.run_tool(con, "player_batting", {"player_id": "x' OR 1=1 --"})
    with pytest.raises(guru.ToolInputError, match="Unknown filters"):
        guru.run_tool(con, "batting_leaderboard", {"filters": {"sql": "DROP TABLE matches"}})
    with pytest.raises(guru.ToolInputError, match="no tool"):
        guru.run_tool(con, "run_sql", {"query": "SELECT 1"})


def test_tools_return_stats(con):
    found = guru.run_tool(con, "find_player", {"name": "X"})
    assert found.player_id.iloc[0] == "id_X"
    bat = guru.run_tool(con, "player_batting", {
        "player_id": "id_X", "filters": {"host_countries": ["England"], "date_to": "2020-12-31"}})
    assert bat.runs.iloc[0] == 6
    lb = guru.run_tool(con, "bowling_leaderboard", {"min_wickets": 1, "limit": 1})
    assert lb.player.iloc[0] == "X" and len(lb) == 1
    cmp = guru.run_tool(con, "compare_players", {"player_ids": ["id_X", "id_Z"]}).set_index("stat")
    assert cmp.loc["bat_runs", "X"] == 60
    assert guru.run_tool(con, "head_to_head", {"team_a": "A", "team_b": "C"}).result.iloc[0] == "A"
    assert set(guru.run_tool(con, "list_host_countries", {}).host_country) == {"England", "Australia"}


def test_every_tool_is_handled(con):
    """Each declared tool runs (or rejects its input) rather than falling through to 'no tool'."""
    for tool in guru.TOOLS:
        try:
            guru.run_tool(con, tool["name"], {})
        except guru.ToolInputError as e:
            assert "no tool" not in str(e), tool["name"]


class FakeClient:
    """Plays back scripted responses and records each request."""

    def __init__(self, responses):
        self.responses, self.requests = list(responses), []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})  # the list grows later
        return self.responses.pop(0)


def reply(*blocks, stop="end_turn"):
    return NS(content=list(blocks), stop_reason=stop, usage=NS(input_tokens=100, output_tokens=20))


def text(t):
    return NS(type="text", text=t)


def call(name, args, id_="t1"):
    return NS(type="tool_use", id=id_, name=name, input=args)


def test_ask_runs_tools_and_answers(con):
    client = FakeClient([
        reply(call("find_player", {"name": "X"}), stop="tool_use"),
        reply(call("player_batting", {"player_id": "id_X"}, "t2"),
              call("player_bowling", {"player_id": "nobody"}, "t3"), stop="tool_use"),
        reply(text("X has 60 runs at 30.00 in Tests with scorecards.")),
    ])
    a = guru.ask(con, "How many runs has X scored?", client)
    assert a.ok and a.text.startswith("X has 60 runs")
    assert [s.tool for s in a.steps] == ["find_player", "player_batting", "player_bowling"]
    assert a.steps[1].table.runs.iloc[0] == 60
    assert a.steps[2].error and "No player" in a.steps[2].error
    assert a.input_tokens == 300 and a.output_tokens == 60
    # Both results of the second round go back in one message, the bad one marked as an error
    results = client.requests[2]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t2", "t3"]
    assert results[0]["is_error"] is False and results[1]["is_error"] is True
    assert '"runs": 60' in results[0]["content"]
    req = client.requests[0]
    assert req["model"] == guru.DEFAULT_MODEL and req["fallbacks"] == "default"
    assert {t["name"] for t in req["tools"]} == {t["name"] for t in guru.TOOLS}


def test_ask_passes_history_and_handles_refusal(con):
    client = FakeClient([reply(stop="refusal")])
    a = guru.ask(con, "And in England?", client, history=[("Root's average?", "50.1 in Tests with scorecards.")])
    assert not a.ok
    assert [m["role"] for m in client.requests[0]["messages"]] == ["user", "assistant", "user"]


def test_ask_stops_after_max_rounds(con):
    client = FakeClient([reply(call("list_teams", {}), stop="tool_use") for _ in range(guru.MAX_ROUNDS)])
    a = guru.ask(con, "Loop forever", client)
    assert not a.ok and len(a.steps) == guru.MAX_ROUNDS


def test_ask_rejects_empty_and_long_questions(con):
    client = FakeClient([])
    assert not guru.ask(con, "   ", client).ok
    assert not guru.ask(con, "x" * (guru.MAX_QUESTION_CHARS + 1), client).ok
    assert client.requests == []


def test_dates_parse():
    assert guru._date("2010-01-31", "d") == date(2010, 1, 31)
    assert guru._date(None, "d") is None
