"""The stats guru: plain-English questions answered by Claude calling the stats functions.

Claude never writes SQL. It can only call the tools below, each a thin wrapper over a
function in stats.py whose inputs are checked here first, so every number in an answer
comes from the same tested code as the rest of the app. Each tool call and the table it
returned are kept on the Answer so the UI can show how the answer was reached.

Needs the `anthropic` package and an API key; the UI passes in the client so tests can use
a fake one.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

import duckdb
import pandas as pd

from cricket import stats

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
MAX_ROUNDS = 8          # tool-calling rounds per question before giving up
MAX_ROWS = 50           # rows of a table sent back to Claude
MAX_QUESTION_CHARS = 500
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM = """You answer questions about men's Test cricket for a stats website, using only the \
tools provided.

Rules:
- Every number, name and date in your answer must come from a tool result in this \
conversation. Never use your own memory of cricket stats, even if you are sure. If the tools \
cannot answer the question, say so plainly and say what you can answer instead.
- Coverage: match results cover every men's Test since 1877. Innings-by-innings player stats \
(everything that takes filters) cover only Tests with scorecards, which is mostly December \
2001 onwards. Whole-career totals (player_career_totals, all_time_careers) cover every Test \
but cannot be filtered. Whenever you give a player stat, say which kind it is, for example \
"in Tests with scorecards (mostly since 2001)" or "whole career". If a question needs \
filtered stats from before 2001, say that data is not available yet.
- Find a player's id with find_player before any player tool. If several players match, pick \
the one the question most likely means and say who you picked.
- Team and host country names must match list_teams and list_host_countries exactly. \
"Asia" means Bangladesh, India, Pakistan, Sri Lanka and the United Arab Emirates as host \
countries; check list_host_countries for others.
- State any minimum you used (for example "minimum 15 innings").
- You cannot make predictions; point people to the Predictions page.
- Politely decline questions that are not about Test cricket.

Answer style: lead with the answer in one or two plain sentences, then a short markdown table \
if it helps. No preamble."""

FILTERS_SCHEMA = {
    "type": "object",
    "description": "Optional filters on which matches and innings count. Leave out any that are not needed.",
    "properties": {
        "opponent": {"type": "string", "description": "Team played against, e.g. 'England'."},
        "team": {"type": "string", "description": "The player's own side, e.g. 'Australia'."},
        "host_countries": {"type": "array", "items": {"type": "string"},
                           "description": "Countries the Test was played in, e.g. ['India', 'Sri Lanka']."},
        "venue": {"type": "string", "description": "Text in the ground or city name, e.g. \"Lord's\"."},
        "innings": {"type": "integer", "enum": [1, 2, 3, 4], "description": "Innings of the match."},
        "venue_type": {"type": "string", "enum": ["home", "away", "neutral"],
                       "description": "Whether the player's side was at home, away or at a neutral venue."},
        "date_from": {"type": "string", "description": "First match start date, YYYY-MM-DD."},
        "date_to": {"type": "string", "description": "Last match start date, YYYY-MM-DD."},
    },
    "additionalProperties": False,
}


def _tool(name: str, description: str, properties: dict | None = None,
          required: list[str] | None = None) -> dict:
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": properties or {},
                             "required": required or [], "additionalProperties": False}}


PLAYER_ID = {"type": "string", "description": "Player id from find_player."}
LIMIT = {"type": "integer", "minimum": 1, "maximum": MAX_ROWS, "description": "Rows to return (default 10)."}

TOOLS = [
    _tool("find_player", "Search players by name (typos and partial names are fine). Returns ids, "
          "full names, teams and first and last years, best match first.",
          {"name": {"type": "string"}}, ["name"]),
    _tool("list_teams", "Every Test team name, exactly as the other tools expect it."),
    _tool("list_host_countries", "Every country Tests have been played in, as the host_countries "
          "filter expects it."),
    _tool("data_coverage", "How many Tests the database has, how many have scorecards, and the "
          "latest Test date."),
    _tool("player_career_totals", "A player's whole-career totals for each team they played for, "
          "covering every Test since 1877. Cannot be filtered.", {"player_id": PLAYER_ID}, ["player_id"]),
    _tool("player_batting", "A player's batting in Tests with scorecards (mostly 2001 on): matches, "
          "innings, runs, average, strike rate, hundreds, fifties and more.",
          {"player_id": PLAYER_ID, "filters": FILTERS_SCHEMA}, ["player_id"]),
    _tool("player_bowling", "A player's bowling in Tests with scorecards (mostly 2001 on): wickets, "
          "average, economy, strike rate, best figures, five-fors and ten-wicket matches.",
          {"player_id": PLAYER_ID, "filters": FILTERS_SCHEMA}, ["player_id"]),
    _tool("player_batting_split", "A player's batting broken down by innings of the match, by home/"
          "away/neutral, or by calendar year, in Tests with scorecards.",
          {"player_id": PLAYER_ID, "by": {"type": "string", "enum": ["innings", "home_away", "year"]},
           "filters": FILTERS_SCHEMA}, ["player_id", "by"]),
    _tool("compare_players", "Side-by-side whole-career totals plus filtered batting and bowling for "
          "two to four players.",
          {"player_ids": {"type": "array", "items": PLAYER_ID, "minItems": 2, "maxItems": 4},
           "filters": FILTERS_SCHEMA}, ["player_ids"]),
    _tool("batting_leaderboard", "Top batters in Tests with scorecards that match the filters.",
          {"filters": FILTERS_SCHEMA,
           "order_by": {"type": "string", "enum": ["runs", "average", "hundreds", "strike_rate"]},
           "min_innings": {"type": "integer", "minimum": 1}, "limit": LIMIT}),
    _tool("bowling_leaderboard", "Top bowlers in Tests with scorecards that match the filters. "
          "Average, economy and strike rate sort lowest first.",
          {"filters": FILTERS_SCHEMA,
           "order_by": {"type": "string", "enum": ["wickets", "average", "economy", "strike_rate"]},
           "min_wickets": {"type": "integer", "minimum": 1}, "limit": LIMIT}),
    _tool("all_time_careers", "All-time leaders by whole-career totals over every Test since 1877. "
          "Cannot be filtered.",
          {"order_by": {"type": "string", "enum": sorted(stats.CAREER_ORDER)},
           "min_matches": {"type": "integer", "minimum": 1}, "min_wickets": {"type": "integer", "minimum": 0},
           "limit": LIMIT}),
    _tool("team_record", "A team's played, won, lost, drawn and win percentage against each "
          "opponent, over every Test since 1877 that matches the filters (opponent, host_countries, "
          "venue and dates apply).", {"team": {"type": "string"}, "filters": FILTERS_SCHEMA}, ["team"]),
    _tool("head_to_head", "Every Test between two teams, newest first, with venue, toss and result.",
          {"team_a": {"type": "string"}, "team_b": {"type": "string"}, "filters": FILTERS_SCHEMA,
           "limit": LIMIT}, ["team_a", "team_b"]),
    _tool("recent_results", "The latest Tests in the database, newest first.", {"limit": LIMIT}),
]


class ToolInputError(ValueError):
    """A tool input that failed checking; the message goes back to Claude to correct."""


@dataclass
class Step:
    tool: str
    args: dict
    table: pd.DataFrame | None = None
    error: str | None = None


@dataclass
class Answer:
    text: str
    steps: list[Step] = field(default_factory=list)
    ok: bool = True
    input_tokens: int = 0
    output_tokens: int = 0


def _date(value, name: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ToolInputError(f"{name} must be a date like 2010-01-31, not {value!r}") from None


def _one_of(value: str, allowed: list[str], what: str) -> str:
    if value not in allowed:
        raise ToolInputError(f"Unknown {what} {value!r}. Use one of: {', '.join(allowed)}")
    return value


def _filters(con, raw: dict | None) -> stats.Filters:
    raw = raw or {}
    unknown = set(raw) - set(FILTERS_SCHEMA["properties"])
    if unknown:
        raise ToolInputError(f"Unknown filters: {', '.join(sorted(unknown))}")
    teams, hosts = stats.teams(con), stats.host_countries(con)
    innings = raw.get("innings")
    if innings is not None and innings not in (1, 2, 3, 4):
        raise ToolInputError("innings must be 1, 2, 3 or 4")
    venue_type = raw.get("venue_type")
    if venue_type is not None:
        _one_of(venue_type, ["home", "away", "neutral"], "venue_type")
    host_list = raw.get("host_countries") or None
    venue = (raw.get("venue") or "").strip()
    if len(venue) > 60:
        raise ToolInputError("venue text is too long")
    return stats.Filters(
        opponent=_one_of(raw["opponent"], teams, "team") if raw.get("opponent") else None,
        team=_one_of(raw["team"], teams, "team") if raw.get("team") else None,
        host_country=[_one_of(h, hosts, "host country") for h in host_list] if host_list else None,
        venue_contains=venue or None,
        innings_no=innings,
        venue_type=venue_type,
        date_from=_date(raw.get("date_from"), "date_from"),
        date_to=_date(raw.get("date_to"), "date_to"),
    )


def _player(con, player_id) -> str:
    known = con.execute(
        "SELECT count(*) FROM appearances WHERE player_id = ?", [player_id]).fetchone()[0]
    if not known and stats._has_table(con, "player_careers"):
        known = con.execute(
            "SELECT count(*) FROM player_careers WHERE player_id = ?", [player_id]).fetchone()[0]
    if not known:
        raise ToolInputError(f"No player with id {player_id!r}. Use find_player to get an id.")
    return player_id


def _limit(args: dict, default: int = 10) -> int:
    n = args.get("limit", default)
    if not isinstance(n, int) or not 1 <= n <= MAX_ROWS:
        raise ToolInputError(f"limit must be a whole number from 1 to {MAX_ROWS}")
    return n


def _row(d: dict) -> pd.DataFrame:
    return pd.DataFrame([d])


def run_tool(con, name: str, args: dict) -> pd.DataFrame:
    """Run one tool against the database. Raises ToolInputError for bad input."""
    if not isinstance(args, dict):
        raise ToolInputError("tool input must be an object")
    f = lambda: _filters(con, args.get("filters"))  # noqa: E731
    if name == "find_player":
        q = str(args.get("name", "")).strip()
        if not q or len(q) > 60:
            raise ToolInputError("name must be 1 to 60 characters")
        return stats.find_players(con, q, limit=8)
    if name == "list_teams":
        return pd.DataFrame({"team": stats.teams(con)})
    if name == "list_host_countries":
        return pd.DataFrame({"host_country": stats.host_countries(con)})
    if name == "data_coverage":
        return _row(stats.data_summary(con))
    if name == "player_career_totals":
        return stats.career_totals(con, _player(con, args.get("player_id")))
    if name == "player_batting":
        pid = _player(con, args.get("player_id"))
        return _row(stats.batting_summary(con, pid, f()))
    if name == "player_bowling":
        pid = _player(con, args.get("player_id"))
        return _row(stats.bowling_summary(con, pid, f()))
    if name == "player_batting_split":
        pid = _player(con, args.get("player_id"))
        split = {"innings": stats.innings_splits, "home_away": stats.home_away_split,
                 "year": stats.runs_by_year}.get(args.get("by"))
        if split is None:
            raise ToolInputError("by must be innings, home_away or year")
        return split(con, pid, f())
    if name == "compare_players":
        ids = args.get("player_ids")
        if not isinstance(ids, list) or not 2 <= len(ids) <= 4:
            raise ToolInputError("player_ids must list two to four ids")
        return stats.compare(con, [_player(con, p) for p in ids], f()).reset_index(names="stat")
    if name == "batting_leaderboard":
        order = _one_of(args.get("order_by", "runs"), ["runs", "average", "hundreds", "strike_rate"], "order_by")
        return stats.batting_leaderboard(con, f(), order, int(args.get("min_innings", 10)), _limit(args))
    if name == "bowling_leaderboard":
        order = _one_of(args.get("order_by", "wickets"), ["wickets", "average", "economy", "strike_rate"],
                        "order_by")
        return stats.bowling_leaderboard(con, f(), order, int(args.get("min_wickets", 10)), _limit(args))
    if name == "all_time_careers":
        order = _one_of(args.get("order_by", "runs"), sorted(stats.CAREER_ORDER), "order_by")
        return stats.career_leaderboard(con, order, int(args.get("min_matches", 1)),
                                        int(args.get("min_wickets", 0)), _limit(args))
    if name == "team_record":
        team = _one_of(args.get("team"), stats.teams(con), "team")
        return stats.team_record(con, team, f())
    if name == "head_to_head":
        teams = stats.teams(con)
        a, b = _one_of(args.get("team_a"), teams, "team"), _one_of(args.get("team_b"), teams, "team")
        return stats.head_to_head(con, a, b, f()).head(_limit(args, 20))
    if name == "recent_results":
        return stats.recent_results(con, _limit(args))
    raise ToolInputError(f"There is no tool called {name!r}")


def _result_json(df: pd.DataFrame) -> str:
    out = {"rows": json.loads(df.head(MAX_ROWS).to_json(orient="records", date_format="iso"))}
    if len(df) > MAX_ROWS:
        out["note"] = f"Showing the first {MAX_ROWS} of {len(df)} rows."
    return json.dumps(out)


def ask(con, question: str, client, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT,
        history: list[tuple[str, str]] | None = None) -> Answer:
    """Answer one question. history holds earlier (question, answer text) pairs for follow-ups."""
    question = question.strip()
    if not question:
        return Answer("Ask a question about men's Test cricket.", ok=False)
    if len(question) > MAX_QUESTION_CHARS:
        return Answer(f"Please keep questions under {MAX_QUESTION_CHARS} characters.", ok=False)
    messages: list[dict] = []
    for q, a in history or []:
        messages += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    messages.append({"role": "user", "content": question})
    answer = Answer("")
    for _ in range(MAX_ROUNDS):
        response = client.beta.messages.create(
            model=model, max_tokens=16000, system=SYSTEM, tools=TOOLS, messages=messages,
            output_config={"effort": effort}, cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA], fallbacks="default",
        )
        answer.input_tokens += getattr(response.usage, "input_tokens", 0) or 0
        answer.output_tokens += getattr(response.usage, "output_tokens", 0) or 0
        if response.stop_reason == "refusal":
            answer.text, answer.ok = "Sorry, I can't answer that one.", False
            return answer
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "pause_turn":
            continue
        calls = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not calls:
            answer.text = "\n\n".join(b.text for b in response.content if b.type == "text").strip()
            if response.stop_reason == "max_tokens":
                answer.text += "\n\n(The answer was cut off.)"
                answer.ok = False
            return answer
        results = []
        for call in calls:
            step = Step(call.name, dict(call.input) if isinstance(call.input, dict) else {})
            try:
                step.table = run_tool(con, call.name, call.input)
                content, is_error = _result_json(step.table), False
            except (ValueError, TypeError, duckdb.Error) as e:
                step.error = str(e)
                content, is_error = f"Error: {e}", True
            answer.steps.append(step)
            results.append({"type": "tool_result", "tool_use_id": call.id, "content": content,
                            "is_error": is_error})
        messages.append({"role": "user", "content": results})
    answer.text = "Sorry, that question needed more lookups than I'm allowed. Try asking it more simply."
    answer.ok = False
    return answer
