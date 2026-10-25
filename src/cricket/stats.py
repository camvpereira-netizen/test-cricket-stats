"""Stat queries over the DuckDB built by ingest.py.

All public functions take a DuckDB connection and return pandas DataFrames, so the
Streamlit UI, tests and a future MCP server share one implementation.

Players are identified by Cricsheet registry id (stable across name changes).

Career stats read the batting_scorecard and bowling_scorecard tables, which hold every
match whatever its source. Ball-by-ball matches fill every column; scorecard-only matches
(older Tests) may lack balls faced, fours, sixes or balls bowled, which are NULL there.
Strike rates and economy are worked out only over the innings where balls are known.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb
import pandas as pd

BOWLER_WICKET_KINDS = ("bowled", "caught", "caught and bowled", "lbw", "stumped", "hit wicket")
NOT_DISMISSED_KINDS = ("retired hurt", "retired not out")


@dataclass
class Filters:
    opponent: str | None = None          # e.g. "England"
    venue_contains: str | None = None    # substring of venue or city, e.g. "Lord's"
    date_from: date | None = None
    date_to: date | None = None

    def match_sql(self) -> tuple[str, list]:
        """SQL predicate on alias m (matches) plus params."""
        clauses, params = ["1=1"], []
        if self.opponent:
            clauses.append("(m.team1 = ? OR m.team2 = ?)")
            params += [self.opponent, self.opponent]
        if self.venue_contains:
            clauses.append("(m.venue ILIKE ? OR m.city ILIKE ?)")
            like = f"%{self.venue_contains}%"
            params += [like, like]
        if self.date_from:
            clauses.append("m.start_date >= ?")
            params.append(self.date_from)
        if self.date_to:
            clauses.append("m.start_date <= ?")
            params.append(self.date_to)
        return " AND ".join(clauses), params


def connect(db_path: str) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(db_path, read_only=True)


def find_players(con, query: str, limit: int = 20) -> pd.DataFrame:
    """Search players by name substring. Returns id, name, team, matches, span."""
    return con.execute(
        """
        SELECT a.player_id, a.player, string_agg(DISTINCT a.team, ' / ') AS team,
               count(DISTINCT a.match_id) AS matches,
               year(min(m.start_date)) AS first_year, year(max(m.start_date)) AS last_year
        FROM appearances a JOIN matches m USING (match_id)
        WHERE a.player ILIKE ? AND a.player_id IS NOT NULL
        GROUP BY a.player_id, a.player
        ORDER BY matches DESC
        LIMIT ?
        """,
        [f"%{query}%", limit],
    ).df()


def _batting_innings(con, player_id: str, f: Filters) -> pd.DataFrame:
    """One row per batting innings: runs, balls, fours, sixes (NULL if unknown), out flag."""
    pred, params = f.match_sql()
    opp = "AND s.opponent = ?" if f.opponent else ""
    sql = f"""
    SELECT s.match_id, m.start_date, s.innings_no, s.runs, s.balls, s.fours, s.sixes,
           s.out::INT AS out
    FROM batting_scorecard s JOIN matches m USING (match_id)
    WHERE {pred} AND s.player_id = ? {opp}
    ORDER BY m.start_date, s.innings_no
    """
    return con.execute(sql, params + [player_id] + ([f.opponent] if f.opponent else [])).df()


def _matches_played(con, player_id: str, f: Filters) -> int:
    pred, params = f.match_sql()
    opp = "AND a.team <> ?" if f.opponent else ""
    p = params + [player_id] + ([f.opponent] if f.opponent else [])
    return con.execute(
        f"""SELECT count(DISTINCT a.match_id) FROM appearances a JOIN matches m USING (match_id)
            WHERE {pred} AND a.player_id = ? {opp}""", p
    ).fetchone()[0]


def _known_sum(col: pd.Series) -> int | None:
    """Sum of a column that is NULL for scorecard-only innings; None if never known."""
    return int(col.sum()) if col.notna().any() else None


def batting_summary(con, player_id: str, f: Filters | None = None) -> dict:
    f = f or Filters()
    inns = _batting_innings(con, player_id, f)
    runs, outs = int(inns.runs.sum()), int(inns.out.sum())
    timed = inns[inns.balls.notna()]
    balls = int(timed.balls.sum())
    hs_row = inns.sort_values(["runs", "out"], ascending=[False, True]).head(1)
    hs = f"{int(hs_row.runs.iloc[0])}{'' if hs_row.out.iloc[0] else '*'}" if len(hs_row) else "-"
    return {
        "matches": _matches_played(con, player_id, f),
        "innings": len(inns),
        "not_outs": len(inns) - outs,
        "runs": runs,
        "highest": hs,
        "average": round(runs / outs, 2) if outs else None,
        "strike_rate": round(100 * int(timed.runs.sum()) / balls, 2) if balls else None,
        "hundreds": int((inns.runs >= 100).sum()),
        "fifties": int(((inns.runs >= 50) & (inns.runs < 100)).sum()),
        "ducks": int(((inns.runs == 0) & (inns.out == 1)).sum()),
        "fours": _known_sum(inns.fours),
        "sixes": _known_sum(inns.sixes),
        "innings_with_balls_known": len(timed),
    }


def _bowling_innings(con, player_id: str, f: Filters) -> pd.DataFrame:
    """One row per innings bowled in: legal balls (NULL if unknown), runs conceded, wickets."""
    pred, params = f.match_sql()
    opp = "AND s.opponent = ?" if f.opponent else ""
    sql = f"""
    SELECT s.match_id, m.start_date, s.innings_no, s.balls AS legal_balls, s.runs_conceded,
           s.wickets
    FROM bowling_scorecard s JOIN matches m USING (match_id)
    WHERE {pred} AND s.player_id = ? {opp}
    ORDER BY m.start_date, s.innings_no
    """
    return con.execute(sql, params + [player_id] + ([f.opponent] if f.opponent else [])).df()


def bowling_summary(con, player_id: str, f: Filters | None = None) -> dict:
    f = f or Filters()
    inns = _bowling_innings(con, player_id, f)
    runs, wkts = int(inns.runs_conceded.sum()), int(inns.wickets.sum())
    timed = inns[inns.legal_balls.notna()]
    balls, t_wkts = int(timed.legal_balls.sum()), int(timed.wickets.sum())
    best = inns.sort_values(["wickets", "runs_conceded"], ascending=[False, True]).head(1)
    # Match totals for 10-wicket matches
    per_match = inns.groupby("match_id").wickets.sum()
    return {
        "innings_bowled": len(inns),
        "overs": f"{balls // 6}.{balls % 6}" if len(timed) else None,
        "runs_conceded": runs,
        "wickets": wkts,
        "best_innings": f"{int(best.wickets.iloc[0])}/{int(best.runs_conceded.iloc[0])}" if len(best) else "-",
        "average": round(runs / wkts, 2) if wkts else None,
        "economy": round(6 * int(timed.runs_conceded.sum()) / balls, 2) if balls else None,
        "strike_rate": round(balls / t_wkts, 1) if t_wkts else None,
        "five_wicket_hauls": int((inns.wickets >= 5).sum()),
        "ten_wicket_matches": int((per_match >= 10).sum()),
        "innings_with_balls_known": len(timed),
    }


def compare(con, player_ids: list[str], f: Filters | None = None) -> pd.DataFrame:
    """Side-by-side batting + bowling summary for several players (rows = stats)."""
    names = dict(con.execute(
        "SELECT DISTINCT player_id, player FROM appearances WHERE player_id IN (SELECT unnest(?))",
        [player_ids],
    ).fetchall())
    cols = {}
    for pid in player_ids:
        bat = {f"bat_{k}": v for k, v in batting_summary(con, pid, f).items()}
        bowl = {f"bowl_{k}": v for k, v in bowling_summary(con, pid, f).items()}
        cols[names.get(pid, pid)] = {**bat, **bowl}
    return pd.DataFrame(cols)


def runs_by_year(con, player_id: str, f: Filters | None = None) -> pd.DataFrame:
    inns = _batting_innings(con, player_id, f or Filters())
    if inns.empty:
        return inns
    inns["year"] = pd.to_datetime(inns.start_date).dt.year
    g = inns.groupby("year").agg(runs=("runs", "sum"), outs=("out", "sum"), innings=("runs", "size"))
    g["average"] = (g.runs / g.outs.where(g.outs > 0)).round(2)
    return g.reset_index()


def innings_splits(con, player_id: str, f: Filters | None = None) -> pd.DataFrame:
    """Batting by innings of the match (1st to 4th)."""
    inns = _batting_innings(con, player_id, f or Filters())
    return _split(inns, "innings_no")


def home_away_split(con, player_id: str, f: Filters | None = None) -> pd.DataFrame:
    """Batting at home, away and at neutral venues (needs venue country from venues.py)."""
    inns = _batting_innings(con, player_id, f or Filters())
    if inns.empty:
        return _split(inns, "venue_type")
    where = con.execute(
        """SELECT m.match_id, CASE WHEN m.home_team IS NULL THEN
                    CASE WHEN m.country IS NULL THEN 'unknown' ELSE 'neutral' END
                  WHEN m.home_team = a.team THEN 'home' ELSE 'away' END AS venue_type
           FROM matches m JOIN appearances a USING (match_id) WHERE a.player_id = ?""",
        [player_id],
    ).df()
    return _split(inns.merge(where, on="match_id", how="left"), "venue_type")


def _split(inns: pd.DataFrame, by: str) -> pd.DataFrame:
    cols = ["innings", "runs", "outs", "average", "strike_rate", "hundreds", "fifties"]
    if inns.empty:
        return pd.DataFrame(columns=[by] + cols)
    inns = inns.assign(timed_runs=inns.runs.where(inns.balls.notna()))
    g = inns.groupby(by).agg(innings=("runs", "size"), runs=("runs", "sum"), outs=("out", "sum"),
                             balls=("balls", "sum"), timed_runs=("timed_runs", "sum"),
                             hundreds=("runs", lambda r: int((r >= 100).sum())),
                             fifties=("runs", lambda r: int(((r >= 50) & (r < 100)).sum())))
    g["average"] = (g.runs / g.outs.where(g.outs > 0)).round(2)
    g["strike_rate"] = (100 * g.timed_runs / g.balls.where(g.balls > 0)).round(2)
    return g.reset_index()[[by] + cols]


def teams(con) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT DISTINCT team FROM (SELECT team1 AS team FROM matches UNION SELECT team2 FROM matches) "
        "ORDER BY team").fetchall()]


def team_record(con, team: str, f: Filters | None = None) -> pd.DataFrame:
    """A team's results against each opponent: played, won, lost, drawn, tied."""
    f = f or Filters()
    pred, params = f.match_sql()
    return con.execute(
        f"""
        SELECT CASE WHEN m.team1 = ? THEN m.team2 ELSE m.team1 END AS opponent,
               count(*) AS played,
               count(*) FILTER (WHERE m.winner = ?) AS won,
               count(*) FILTER (WHERE m.winner IS NOT NULL AND m.winner <> ?) AS lost,
               count(*) FILTER (WHERE m.result = 'draw') AS drawn,
               count(*) FILTER (WHERE m.result = 'tie') AS tied,
               round(100.0 * count(*) FILTER (WHERE m.winner = ?) / count(*), 1) AS win_pct
        FROM matches m WHERE (m.team1 = ? OR m.team2 = ?) AND {pred}
        GROUP BY ALL ORDER BY played DESC
        """,
        [team, team, team, team, team, team] + params,
    ).df()


def head_to_head(con, team_a: str, team_b: str, f: Filters | None = None) -> pd.DataFrame:
    """Every match between two teams, newest first."""
    f = f or Filters()
    pred, params = f.match_sql()
    return con.execute(
        f"""
        SELECT m.start_date, m.venue, m.country, m.toss_winner, m.toss_decision,
               coalesce(m.winner, m.result) AS result,
               CASE WHEN m.win_by_innings THEN 'innings and ' || m.win_by_runs || ' runs'
                    WHEN m.win_by_runs IS NOT NULL THEN m.win_by_runs || ' runs'
                    WHEN m.win_by_wickets IS NOT NULL THEN m.win_by_wickets || ' wickets' END AS margin
        FROM matches m
        WHERE ((m.team1 = ? AND m.team2 = ?) OR (m.team1 = ? AND m.team2 = ?)) AND {pred}
        ORDER BY m.start_date DESC
        """,
        [team_a, team_b, team_b, team_a] + params,
    ).df()


def batting_leaderboard(con, f: Filters | None = None, order_by: str = "runs",
                        min_innings: int = 10, limit: int = 25) -> pd.DataFrame:
    """Top batters in the filtered matches. order_by: runs, average, hundreds or strike_rate."""
    if order_by not in ("runs", "average", "hundreds", "strike_rate"):
        raise ValueError(order_by)
    f = f or Filters()
    pred, params = f.match_sql()
    opp = "AND s.opponent = ?" if f.opponent else ""
    sql = f"""
    SELECT s.player_id, any_value(s.player) AS player, any_value(s.team) AS team,
           count(*) AS innings, sum(s.runs) AS runs, sum(s.out::INT) AS outs,
           round(sum(s.runs) / nullif(sum(s.out::INT), 0), 2) AS average,
           round(100.0 * sum(s.runs) FILTER (WHERE s.balls IS NOT NULL)
                 / nullif(sum(s.balls), 0), 2) AS strike_rate,
           count(*) FILTER (WHERE s.runs >= 100) AS hundreds
    FROM batting_scorecard s JOIN matches m USING (match_id)
    WHERE {pred} AND s.player_id IS NOT NULL {opp}
    GROUP BY s.player_id HAVING count(*) >= ?
    ORDER BY {order_by} DESC NULLS LAST LIMIT ?
    """
    return con.execute(sql, params + ([f.opponent] if f.opponent else []) + [min_innings, limit]).df()


def bowling_leaderboard(con, f: Filters | None = None, order_by: str = "wickets",
                        min_wickets: int = 10, limit: int = 25) -> pd.DataFrame:
    """Top bowlers in the filtered matches. order_by: wickets, average, economy or strike_rate.

    For average, economy and strike rate, lower is better, so those sort ascending. Economy
    and strike rate use only innings where balls bowled are known.
    """
    if order_by not in ("wickets", "average", "economy", "strike_rate"):
        raise ValueError(order_by)
    direction = "DESC" if order_by == "wickets" else "ASC"
    f = f or Filters()
    pred, params = f.match_sql()
    opp = "AND s.opponent = ?" if f.opponent else ""
    sql = f"""
    WITH b AS (
        SELECT s.player_id, any_value(s.player) AS player, any_value(s.team) AS team,
               sum(s.balls) AS balls, sum(s.runs_conceded) AS runs, sum(s.wickets) AS wickets,
               sum(s.runs_conceded) FILTER (WHERE s.balls IS NOT NULL) AS timed_runs,
               sum(s.wickets) FILTER (WHERE s.balls IS NOT NULL) AS timed_wickets
        FROM bowling_scorecard s JOIN matches m USING (match_id)
        WHERE {pred} AND s.player_id IS NOT NULL {opp} GROUP BY s.player_id
    )
    SELECT player_id, player, team, balls // 6 || '.' || balls % 6 AS overs,
           runs AS runs_conceded, wickets,
           round(runs / nullif(wickets, 0), 2) AS average,
           round(6.0 * timed_runs / nullif(balls, 0), 2) AS economy,
           round(balls / nullif(timed_wickets, 0), 1) AS strike_rate
    FROM b WHERE wickets >= ?
    ORDER BY {order_by} {direction} NULLS LAST LIMIT ?
    """
    return con.execute(sql, params + ([f.opponent] if f.opponent else []) + [min_wickets, limit]).df()

def recent_results(con, limit: int = 10) -> pd.DataFrame:
    """The latest Tests in the database, newest first, with the result in words."""
    return con.execute(
        """
        SELECT m.start_date AS date, m.team1 || ' v ' || m.team2 AS teams,
               coalesce(m.venue, m.city) AS venue,
               CASE WHEN m.winner IS NOT NULL THEN m.winner || ' won' ||
                        CASE WHEN m.win_by_innings THEN ' by an innings and ' || m.win_by_runs || ' runs'
                             WHEN m.win_by_runs IS NOT NULL THEN ' by ' || m.win_by_runs || ' runs'
                             WHEN m.win_by_wickets IS NOT NULL THEN ' by ' || m.win_by_wickets || ' wickets'
                             ELSE '' END
                    WHEN m.result = 'draw' THEN 'Drawn'
                    WHEN m.result = 'tie' THEN 'Tied'
                    ELSE 'Result not recorded' END AS result
        FROM matches m ORDER BY m.start_date DESC LIMIT ?
        """,
        [limit],
    ).df()


def data_summary(con) -> dict:
    """Headline counts for the home page; built_at is None if the database predates build_info."""
    tests, bbb, latest = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE detail <> 'result'), max(start_date) FROM matches"
    ).fetchone()
    has_info = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'build_info'").fetchone()[0]
    built = con.execute("SELECT built_at FROM build_info").fetchone()[0] if has_info else None
    return {"tests": tests, "with_scorecards": bbb, "latest_test": latest, "built_at": built}
