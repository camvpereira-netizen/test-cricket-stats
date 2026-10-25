"""Add matches from sources other than Cricsheet (scorecard-only or result-only).

Cricsheet's ball-by-ball matches stay the reference: an incoming match that is already in
the database (same two teams, start dates within MATCH_WINDOW_DAYS) is not added again;
it only lends its Test number to the existing row. Everything else is appended to
`matches`, and to `batting_scorecard`, `bowling_scorecard`, `innings` and `appearances`
when the source has player-level rows.

`matches.detail` says what each match holds:
- ball_by_ball: Cricsheet deliveries, so every stat works
- scorecard: batting and bowling figures per innings, no deliveries
- result: teams, venue, date and result only (team records and the match model use these)
"""
from __future__ import annotations

import pandas as pd

MATCH_WINDOW_DAYS = 3

MATCH_COLUMNS = [
    "match_id", "test_no", "start_date", "team1", "team2", "venue", "city", "country",
    "home_team", "toss_winner", "toss_decision", "winner", "result", "win_by_runs",
    "win_by_wickets", "win_by_innings", "source", "detail",
]
BATTING_COLUMNS = ["match_id", "innings_no", "team", "opponent", "player", "player_id",
                   "position", "runs", "balls", "fours", "sixes", "out", "how_out"]
BOWLING_COLUMNS = ["match_id", "innings_no", "team", "opponent", "player", "player_id",
                   "balls", "maidens", "runs_conceded", "wickets"]


def _existing_match(con, team1: str, team2: str, start) -> tuple[str, int | None] | None:
    row = con.execute(
        """SELECT match_id, test_no FROM matches
           WHERE ((team1 = ? AND team2 = ?) OR (team1 = ? AND team2 = ?))
             AND abs(date_diff('day', start_date, ?::DATE)) <= ?
           ORDER BY abs(date_diff('day', start_date, ?::DATE)) LIMIT 1""",
        [team1, team2, team2, team1, start, MATCH_WINDOW_DAYS, start],
    ).fetchone()
    return row


def add_matches(con, matches: pd.DataFrame, batting: pd.DataFrame | None = None,
                bowling: pd.DataFrame | None = None) -> dict[str, int]:
    """Append matches not already present. Returns counts of what happened."""
    matches = matches.reindex(columns=MATCH_COLUMNS)
    new_ids, numbered = [], 0
    for r in matches.itertuples(index=False):
        hit = _existing_match(con, r.team1, r.team2, r.start_date)
        if hit:
            if pd.notna(r.test_no) and hit[1] is None:
                con.execute("UPDATE matches SET test_no = ? WHERE match_id = ?", [int(r.test_no), hit[0]])
                numbered += 1
            continue
        if con.execute("SELECT 1 FROM matches WHERE match_id = ?", [r.match_id]).fetchone():
            continue
        new_ids.append(r.match_id)
    new = matches[matches.match_id.isin(new_ids)]
    if len(new):
        con.register("tmp_m", new)
        con.execute(f"INSERT INTO matches BY NAME SELECT * FROM tmp_m")
        con.unregister("tmp_m")

    counts = {"matches_added": len(new), "existing_matches_numbered": numbered,
              "already_present": len(matches) - len(new)}
    for table, df, cols in (("batting_scorecard", batting, BATTING_COLUMNS),
                            ("bowling_scorecard", bowling, BOWLING_COLUMNS)):
        if df is None or df.empty:
            counts[table] = 0
            continue
        df = df.reindex(columns=cols)
        df = df[df.match_id.isin(new_ids)]
        con.register("tmp_s", df)
        con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM tmp_s")
        con.unregister("tmp_s")
        counts[table] = len(df)
    if batting is not None or bowling is not None:
        _add_appearances(con, new_ids)
    return counts


def _add_appearances(con, match_ids: list[str]) -> None:
    """Players who batted or bowled in the new matches count as having played in them."""
    con.execute(
        """INSERT INTO appearances BY NAME
           SELECT DISTINCT match_id, team, player, player_id FROM (
               SELECT match_id, team, player, player_id FROM batting_scorecard
               UNION SELECT match_id, team, player, player_id FROM bowling_scorecard)
           WHERE match_id IN (SELECT unnest(?))""",
        [match_ids],
    )


def coverage(con) -> pd.DataFrame:
    """Matches by decade and level of detail, so the UI can say what each stat covers."""
    return con.execute(
        """SELECT (year(start_date) // 10 * 10)::VARCHAR || 's' AS decade, detail, count(*) AS matches
           FROM matches GROUP BY ALL ORDER BY decade, detail"""
    ).df()
