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

import re
import unicodedata
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
    host_country: str | list[str] | None = None  # where the Test was played, e.g. "India"
    team: str | None = None              # the player's own side
    innings_no: int | None = None        # 1 to 4: innings of the match
    venue_type: str | None = None        # "home", "away" or "neutral" for the player's side

    def match_sql(self) -> tuple[str, list]:
        """SQL predicate on alias m (matches) plus params."""
        clauses, params = ["1=1"], []
        if self.opponent:
            clauses.append("(m.team1 = ? OR m.team2 = ?)")
            params += [self.opponent, self.opponent]
        if self.venue_contains:
            # apostrophes ignored, so "Lords" finds "Lord's"
            clauses.append("(replace(m.venue, '''', '') ILIKE ? OR replace(m.city, '''', '') ILIKE ?)")
            plain = self.venue_contains.replace("'", "").replace("’", "")
            like = f"%{plain}%"
            params += [like, like]
        if self.date_from:
            clauses.append("m.start_date >= ?")
            params.append(self.date_from)
        if self.date_to:
            clauses.append("m.start_date <= ?")
            params.append(self.date_to)
        if self.host_country:
            hosts = [self.host_country] if isinstance(self.host_country, str) else list(self.host_country)
            clauses.append("list_contains(?, m.country)")
            params.append(hosts)
        return " AND ".join(clauses), params

    def row_sql(self, alias: str = "s", innings: bool = True) -> tuple[str, list]:
        """SQL predicate on a player's rows (alias has team, and opponent if innings) plus params.

        For appearances (innings=False) the opponent is "not the player's own team" and the
        innings filter does not apply.
        """
        if self.venue_type not in (None, "home", "away", "neutral"):
            raise ValueError("venue_type must be home, away or neutral")
        clauses, params = [], []
        if self.opponent:
            clauses.append(f"{alias}.opponent = ?" if innings else f"{alias}.team <> ?")
            params.append(self.opponent)
        if self.team:
            clauses.append(f"{alias}.team = ?")
            params.append(self.team)
        if self.innings_no and innings:
            clauses.append(f"{alias}.innings_no = ?")
            params.append(self.innings_no)
        if self.venue_type == "home":
            clauses.append(f"m.home_team = {alias}.team")
        elif self.venue_type == "away":
            clauses.append(f"m.home_team <> {alias}.team")
        elif self.venue_type == "neutral":
            clauses.append("m.home_team IS NULL AND m.country IS NOT NULL")
        return "".join(f" AND {c}" for c in clauses), params


def connect(db_path: str) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(db_path, read_only=True)


def _norm(name: str) -> str:
    """Lower case, accents and punctuation removed: "Ross Taylor-Smith" -> "ross taylor smith"."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]+", " ", plain.lower()).strip()


def _sql_norm(col: str) -> str:
    return f"trim(regexp_replace(lower(strip_accents({col})), '[^a-z]+', ' ', 'g'))"


def find_players(con, query: str, limit: int = 20, min_score: float = 0.85) -> pd.DataFrame:
    """Fuzzy player search over Cricsheet names ("SPD Smith") and register names ("Steve Smith").

    A name scores 1 if a word starts with the query, 0.9 if it contains it elsewhere. Otherwise
    every query word must match a word of the name: a prefix ("ste" for "steve") scores 1, else
    Jaro-Winkler similarity (typos such as "stve"); words of one or two letters only match as
    prefixes, so "s smith" finds initials and "root" doesn't find "RO Hinds". The name's score is its worst query word, or the
    whole-string similarity less 0.05 if higher ("devilliers").
    Returns id, name, full_name, team, matches, span, score; best match first.
    """
    q = _norm(query)
    if not q:
        return pd.DataFrame(columns=["player_id", "player", "full_name", "team", "matches",
                                     "first_year", "last_year", "score"])
    has_names = _has_table(con, "player_names")
    has_careers = _has_table(con, "player_careers")
    register = "UNION ALL SELECT player_id, name FROM player_names" if has_names else ""
    if has_careers:
        register += " UNION ALL SELECT player_id, name FROM player_careers"
    # first register name with a full first name matching one of the initials and the same
    # surname; the register also keeps other sources' mistakes (J Overton as "Craig Overton")
    full_name = """(SELECT name FROM player_names n WHERE n.player_id = p.player_id
                    AND regexp_matches(name, '^[^ .]{2,} ') AND name <> p.player
                    AND contains(split_part(p.player, ' ', 1), name[1])
                    AND split_part(name, ' ', -1) = split_part(p.player, ' ', -1)
                    ORDER BY position LIMIT 1)""" if has_names else "NULL"
    if has_careers:  # else the name on Wikipedia's list of the player's team, if it differs
        full_name = f"""coalesce({full_name}, (SELECT any_value(c.name) FROM player_careers c
                        WHERE c.player_id = p.player_id AND c.name <> p.player))"""
    careers = """
        UNION ALL
        SELECT player_id, any_value(name), string_agg(DISTINCT team, ' / '), sum(matches)::BIGINT,
               min(first_year), max(last_year)
        FROM player_careers
        WHERE player_id IN (SELECT player_id FROM best WHERE score >= $min_score)
          AND player_id NOT IN (SELECT player_id FROM appearances WHERE player_id IS NOT NULL)
        GROUP BY player_id""" if has_careers else ""
    # whole-career matches and span from the lists where known, else from the matches here
    career_totals = """LEFT JOIN (SELECT player_id, sum(matches) AS matches, min(first_year) AS first_year,
                                  max(last_year) AS last_year FROM player_careers GROUP BY player_id) c
                       USING (player_id)""" if has_careers else ""
    pick = (lambda col, fn: f"{fn}(p.{col}, c.{col})") if has_careers else (lambda col, fn: f"p.{col}")
    return con.execute(
        f"""
        WITH names AS (
            SELECT DISTINCT player_id, player AS name FROM appearances WHERE player_id IS NOT NULL
            {register}
        ),
        scored AS (
            SELECT player_id, {_sql_norm("name")} AS n FROM names
        ),
        best AS (
            SELECT player_id, max(greatest(
                CASE WHEN starts_with(n, $q) OR contains(n, ' ' || $q) THEN 1.0
                     WHEN contains(n, $q) THEN 0.9 ELSE 0.0 END,
                jaro_winkler_similarity(replace(n, ' ', ''), replace($q, ' ', '')) - 0.05,
                list_min(list_transform($words, lambda w: list_max(list_transform(
                    string_split(n, ' '), lambda a: CASE
                        WHEN starts_with(a, w) THEN 1.0
                        WHEN length(w) <= 2 OR length(a) <= 2 THEN 0.0
                        ELSE jaro_winkler_similarity(w, a) END))))
            )) AS score,
            -- breaks ties between several-word queries: "s smith" is closer to "S Smith" (SPD)
            -- than to "GC Smith", though every word matches both
            max(jaro_winkler_similarity(n, $q)) FILTER (WHERE len($words) > 1) AS closeness
            FROM scored GROUP BY player_id
        ),
        players AS (
            SELECT a.player_id, any_value(a.player) AS player,
                   string_agg(DISTINCT a.team, ' / ') AS team,
                   count(DISTINCT a.match_id) AS matches,
                   year(min(m.start_date)) AS first_year, year(max(m.start_date)) AS last_year
            FROM appearances a JOIN matches m USING (match_id)
            WHERE a.player_id IN (SELECT player_id FROM best WHERE score >= $min_score)
            GROUP BY a.player_id
            {careers}
        )
        SELECT p.player_id, p.player, {full_name} AS full_name, p.team,
               {pick("matches", "greatest")}::BIGINT AS matches, {pick("first_year", "least")} AS first_year,
               {pick("last_year", "greatest")} AS last_year, round(b.score, 3) AS score
        FROM players p JOIN best b USING (player_id) {career_totals}
        ORDER BY score DESC, b.closeness DESC NULLS LAST, matches DESC, player
        LIMIT $limit
        """,
        {"q": q, "words": q.split(), "min_score": min_score, "limit": limit},
    ).df()


def _has_table(con, name: str) -> bool:
    return con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
                       [name]).fetchall()[0][0] > 0


CAREER_COLUMNS = ["team", "cap", "name", "first_year", "last_year", "matches", "innings", "not_outs",
                  "runs", "high_score", "batting_average", "hundreds", "fifties", "balls", "maidens",
                  "runs_conceded", "wickets", "best_bowling", "bowling_average", "five_wickets",
                  "ten_wickets", "catches", "stumpings", "as_of"]


def career_totals(con, player_id: str) -> pd.DataFrame:
    """Whole-career figures for each team the player was capped by, from Wikipedia's lists.

    These cover every Test, unlike the scorecard stats; columns a list lacks are NULL.
    """
    if not _has_table(con, "player_careers"):
        return pd.DataFrame()
    df = con.execute("SELECT * FROM player_careers WHERE player_id = ? ORDER BY first_year",
                     [player_id]).df()
    return df.reindex(columns=CAREER_COLUMNS)  # older builds lack five_wickets and ten_wickets


FROM_BALLS = "Ball-by-ball (every Test)"
FROM_LISTS = "Wikipedia"


def _scorecard_careers(con, player_id: str) -> pd.DataFrame:
    """Per team, the player's Test figures from ball-by-ball matches only (all columns known)."""
    return con.execute(
        """
        WITH bb AS (SELECT match_id, start_date FROM matches WHERE detail = 'ball_by_ball'),
        apps AS (SELECT a.team, count(*) AS matches, year(min(start_date)) AS first_year,
                        year(max(start_date)) AS last_year
                 FROM appearances a JOIN bb USING (match_id) WHERE a.player_id = $p GROUP BY a.team),
        bat AS (SELECT team, count(*) AS innings, count(*) - sum(out::INT) AS not_outs,
                       sum(runs)::BIGINT AS runs, sum(balls)::BIGINT AS balls_faced,
                       sum(fours)::BIGINT AS fours, sum(sixes)::BIGINT AS sixes,
                       count(*) FILTER (WHERE runs >= 100) AS hundreds,
                       count(*) FILTER (WHERE runs >= 50 AND runs < 100) AS fifties,
                       arg_max(runs::VARCHAR || CASE WHEN out THEN '' ELSE '*' END,
                               runs * 2 + CASE WHEN out THEN 0 ELSE 1 END) AS high_score
                FROM batting_scorecard JOIN bb USING (match_id) WHERE player_id = $p GROUP BY team),
        bowl_inns AS (SELECT s.* FROM bowling_scorecard s JOIN bb USING (match_id) WHERE player_id = $p),
        bowl_match AS (SELECT team, match_id, sum(wickets) AS w, sum(runs_conceded) AS r
                       FROM bowl_inns GROUP BY ALL),
        bowl AS (SELECT team, count(*) AS bowl_innings, sum(balls)::BIGINT AS balls,
                        sum(maidens)::BIGINT AS maidens, sum(runs_conceded)::BIGINT AS runs_conceded,
                        sum(wickets)::BIGINT AS wickets,
                        arg_min(wickets || '/' || runs_conceded, runs_conceded - wickets * 10000) AS best_bowling,
                        count(*) FILTER (WHERE wickets = 4) AS four_wickets,
                        count(*) FILTER (WHERE wickets >= 5) AS five_wickets
                 FROM bowl_inns GROUP BY team),
        bbm AS (SELECT team, arg_min(w || '/' || r, r - w * 10000) AS best_match,
                       count(*) FILTER (WHERE w >= 10) AS ten_wickets
                FROM bowl_match GROUP BY team),
        -- catches and stumpings by the fielding side's XI (a substitute isn't in the XI, so
        -- isn't credited); caught and bowled is a catch for the bowler
        dismissals AS (
            SELECT w.match_id, i.bowling_team AS team, trim(f.name) AS fielder, w.kind
            FROM wickets w JOIN innings i USING (match_id, innings_no)
                 JOIN bb USING (match_id), unnest(string_split(w.fielders, ', ')) AS f(name)
            WHERE w.kind IN ('caught', 'stumped') AND w.fielders <> ''
            UNION ALL
            SELECT w.match_id, i.bowling_team, w.bowler, 'caught'
            FROM wickets w JOIN innings i USING (match_id, innings_no) JOIN bb USING (match_id)
            WHERE w.kind = 'caught and bowled'),
        field AS (SELECT a.team, count(*) FILTER (WHERE d.kind = 'caught') AS catches,
                         count(*) FILTER (WHERE d.kind = 'stumped') AS stumpings
                  FROM dismissals d JOIN appearances a
                    ON a.match_id = d.match_id AND a.team = d.team AND a.player = d.fielder
                  WHERE a.player_id = $p GROUP BY a.team)
        SELECT * FROM apps LEFT JOIN bat USING (team) LEFT JOIN bowl USING (team)
                 LEFT JOIN bbm USING (team) LEFT JOIN field USING (team)
        """, {"p": player_id}).df()


def _num(v):
    return None if v is None or pd.isna(v) else v


def _div(a, b, scale: float = 1, places: int = 2):
    a, b = _num(a), _num(b)
    return round(scale * a / b, places) if a is not None and b else None


def test_career(con, player_id: str) -> dict[str, pd.DataFrame]:
    """Whole-career Test figures in three tables, "batting", "bowling" and "other", one row per team.

    Laid out like a Cricinfo profile's Tests row. Where every Test the player played for a team
    is in the ball-by-ball data (and agrees with the list on runs and wickets), all of it is
    worked out here from the deliveries. Otherwise
    the row comes from Wikipedia (the team's list, gaps filled from the player's article), and
    columns no source has for those years are None: balls faced, strike rate, fours, sixes,
    bowling innings, best in a match and 4-wicket innings (and innings and not outs for teams
    whose list lacks them). The "source" column says which.
    """
    lists = career_totals(con, player_id)
    lists = lists.set_index("team") if not lists.empty else pd.DataFrame()
    cards = _scorecard_careers(con, player_id).set_index("team")
    bat, bowl, other = [], [], []
    for team in list(dict.fromkeys([*lists.index, *cards.index])):
        lr = lists.loc[team] if team in lists.index else None
        cr = cards.loc[team] if team in cards.index else None
        # every Test is ball-by-ball if there are as many as the list has (more if the list is
        # behind); with the same count the runs and wickets must agree too, else a Test is
        # missing or misattributed and the list is trusted
        full = cr is not None and (lr is None or cr.matches > (_num(lr.matches) or 0) or (
            cr.matches == lr.matches
            and (_num(cr.runs) or 0) == (_num(lr.runs) or 0)
            and (_num(cr.wickets) or 0) == (_num(lr.wickets) or 0)))
        if full:
            outs = _num(cr.innings) and cr.innings - cr.not_outs
            bat.append({"team": team, "matches": cr.matches, "innings": _num(cr.innings) or 0,
                        "not_outs": _num(cr.not_outs), "runs": _num(cr.runs), "high_score": _num(cr.high_score),
                        "average": _div(cr.runs, outs), "balls_faced": _num(cr.balls_faced),
                        "strike_rate": _div(cr.runs, cr.balls_faced, 100), "hundreds": _num(cr.hundreds),
                        "fifties": _num(cr.fifties), "fours": _num(cr.fours), "sixes": _num(cr.sixes),
                        "source": FROM_BALLS})
            bowl.append({"team": team, "matches": cr.matches, "innings": _num(cr.bowl_innings) or 0,
                         "balls": _num(cr.balls) or 0, "runs": _num(cr.runs_conceded) or 0,
                         "wickets": _num(cr.wickets) or 0, "best_innings": _num(cr.best_bowling),
                         "best_match": _num(cr.best_match), "average": _div(cr.runs_conceded, cr.wickets),
                         "economy": _div(cr.runs_conceded, cr.balls, 6),
                         "strike_rate": _div(cr.balls, cr.wickets, 1, 1),
                         "four_wickets": _num(cr.four_wickets) or 0, "five_wickets": _num(cr.five_wickets) or 0,
                         "ten_wickets": _num(cr.ten_wickets) or 0, "source": FROM_BALLS})
            fielding = {"catches": _num(cr.catches) or 0, "stumpings": _num(cr.stumpings) or 0}
        else:
            bowled = bool(_num(lr.balls)) or bool(_num(lr.wickets))
            # counts the sources lack can still be certain: no hundreds with a top score of 99
            hs = int(re.sub(r"\D", "", str(lr.high_score)) or 0) if _num(lr.high_score) else None
            best = int(str(lr.best_bowling).split("/")[0]) if re.match(r"\d+/", str(lr.best_bowling)) else 0
            wkts = _num(lr.wickets) or 0
            bat.append({"team": team, "matches": _num(lr.matches), "innings": _num(lr.innings),
                        "not_outs": _num(lr.not_outs), "runs": _num(lr.runs), "high_score": _num(lr.high_score),
                        "average": _num(lr.batting_average), "balls_faced": None, "strike_rate": None,
                        "hundreds": _num(lr.hundreds) if hs is None or hs >= 100 or _num(lr.hundreds) else 0,
                        "fifties": _num(lr.fifties) if hs is None or hs >= 50 or _num(lr.fifties) else 0,
                        "fours": None, "sixes": None, "source": FROM_LISTS})
            bowl.append({"team": team, "matches": _num(lr.matches), "innings": None,
                         "balls": _num(lr.balls), "runs": _num(lr.runs_conceded),
                         "wickets": wkts, "best_innings": _num(lr.best_bowling) if bowled else None,
                         "best_match": None, "average": _num(lr.bowling_average),
                         "economy": _div(lr.runs_conceded, lr.balls, 6),
                         "strike_rate": _div(lr.balls, lr.wickets, 1, 1),
                         "four_wickets": 0 if best < 4 else None,
                         "five_wickets": 0 if best < 5 else _num(lr.five_wickets),
                         "ten_wickets": 0 if wkts < 10 else _num(lr.ten_wickets), "source": FROM_LISTS})
            fielding = {"catches": _num(lr.catches) or 0, "stumpings": _num(lr.stumpings) or 0}
        other.append({"team": team, "cap": _num(lr.cap) if lr is not None else None,
                      "first_year": _num(lr.first_year) if lr is not None else cr.first_year,
                      "last_year": max((x for x in [_num(lr.last_year) if lr is not None else None,
                                                    cr.last_year if cr is not None else None] if x),
                                       default=None),
                      **fielding,
                      "figures_as_of": (_num(lr.as_of) if lr is not None and not full else "latest Test")})
    return {"batting": pd.DataFrame(bat), "bowling": pd.DataFrame(bowl), "other": pd.DataFrame(other)}


CAREER_ORDER = {"runs": "runs DESC", "batting_average": "batting_average DESC",
                "wickets": "wickets DESC", "bowling_average": "bowling_average ASC",
                "matches": "matches DESC", "catches": "catches DESC"}


def career_leaderboard(con, order_by: str = "runs", min_matches: int = 1, min_wickets: int = 0,
                       limit: int = 50, player_ids: list[str] | None = None) -> pd.DataFrame:
    """All-time leaders over every Test, from the per-team career totals.

    A player capped by two teams counts once, with runs and wickets added up and averages
    worked out again from the totals where the lists give the parts.
    """
    if order_by not in CAREER_ORDER:
        raise ValueError(f"order_by must be one of {sorted(CAREER_ORDER)}")
    if not _has_table(con, "player_careers"):
        return pd.DataFrame()
    return con.execute(
        f"""SELECT player_id, any_value(name) AS player, string_agg(team, ' / ') AS team,
                   min(first_year) AS first_year, max(last_year) AS last_year,
                   sum(matches)::INT AS matches, sum(runs)::INT AS runs,
                   CASE WHEN count(*) = 1 THEN any_value(batting_average)
                        WHEN bool_and(innings IS NOT NULL AND not_outs IS NOT NULL)
                        THEN round(sum(runs) / nullif(sum(innings) - sum(not_outs), 0), 2) END
                       AS batting_average,
                   sum(wickets)::INT AS wickets,
                   CASE WHEN count(*) = 1 THEN any_value(bowling_average)
                        WHEN bool_and(runs_conceded IS NOT NULL OR coalesce(wickets, 0) = 0)
                        THEN round(sum(runs_conceded) / nullif(sum(wickets), 0), 2) END
                       AS bowling_average,
                   sum(catches)::INT AS catches
            FROM player_careers WHERE ? IS NULL OR list_contains(?, player_id) GROUP BY player_id
            HAVING sum(matches) >= ? AND coalesce(sum(wickets), 0) >= ? AND {order_by} IS NOT NULL
            ORDER BY {CAREER_ORDER[order_by]}, matches DESC LIMIT ?""",
        [player_ids, player_ids, min_matches, min_wickets, limit]).df()


def _batting_innings(con, player_id: str, f: Filters) -> pd.DataFrame:
    """One row per batting innings: runs, balls, fours, sixes (NULL if unknown), out flag."""
    pred, params = f.match_sql()
    rows, row_params = f.row_sql()
    sql = f"""
    SELECT s.match_id, m.start_date, s.innings_no, s.runs, s.balls, s.fours, s.sixes,
           s.out::INT AS out
    FROM batting_scorecard s JOIN matches m USING (match_id)
    WHERE {pred} AND s.player_id = ? {rows}
    ORDER BY m.start_date, s.innings_no
    """
    return con.execute(sql, params + [player_id] + row_params).df()


def _matches_played(con, player_id: str, f: Filters) -> int:
    pred, params = f.match_sql()
    rows, row_params = f.row_sql("a", innings=False)
    return con.execute(
        f"""SELECT count(DISTINCT a.match_id) FROM appearances a JOIN matches m USING (match_id)
            WHERE {pred} AND a.player_id = ? {rows}""", params + [player_id] + row_params
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
    rows, row_params = f.row_sql()
    sql = f"""
    SELECT s.match_id, m.start_date, s.innings_no, s.balls AS legal_balls, s.runs_conceded,
           s.wickets
    FROM bowling_scorecard s JOIN matches m USING (match_id)
    WHERE {pred} AND s.player_id = ? {rows}
    ORDER BY m.start_date, s.innings_no
    """
    return con.execute(sql, params + [player_id] + row_params).df()


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
    """Side-by-side summary for several players (rows = stats).

    career_ rows are whole careers from Wikipedia's lists (filters don't apply); bat_ and
    bowl_ rows come from the scorecards, which start in 2001.
    """
    names = dict(con.execute(
        "SELECT DISTINCT player_id, player FROM appearances WHERE player_id IN (SELECT unnest(?))",
        [player_ids],
    ).fetchall())
    careers = career_leaderboard(con, "matches", limit=len(player_ids), player_ids=player_ids) \
        if _has_table(con, "player_careers") else pd.DataFrame(columns=["player_id"])
    careers = careers.set_index("player_id")
    cols = {}
    for pid in player_ids:
        career = {}
        if pid in careers.index:
            r = careers.loc[pid]
            names.setdefault(pid, r.player)
            career = {f"career_{k}": (None if pd.isna(r[k]) else r[k]) for k in
                      ("team", "first_year", "last_year", "matches", "runs", "batting_average",
                       "wickets", "bowling_average", "catches")}
        bat = {f"bat_{k}": v for k, v in batting_summary(con, pid, f).items()}
        bowl = {f"bowl_{k}": v for k, v in bowling_summary(con, pid, f).items()}
        cols[names.get(pid, pid)] = {**career, **bat, **bowl}
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


def host_countries(con) -> list[str]:
    """Countries Tests have been played in (from venues.py), for the host country filter."""
    return [r[0] for r in con.execute(
        "SELECT DISTINCT country FROM matches WHERE country IS NOT NULL ORDER BY country").fetchall()]


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
    rows, row_params = f.row_sql()
    sql = f"""
    SELECT s.player_id, any_value(s.player) AS player, any_value(s.team) AS team,
           count(*) AS innings, sum(s.runs) AS runs, sum(s.out::INT) AS outs,
           round(sum(s.runs) / nullif(sum(s.out::INT), 0), 2) AS average,
           round(100.0 * sum(s.runs) FILTER (WHERE s.balls IS NOT NULL)
                 / nullif(sum(s.balls), 0), 2) AS strike_rate,
           count(*) FILTER (WHERE s.runs >= 100) AS hundreds
    FROM batting_scorecard s JOIN matches m USING (match_id)
    WHERE {pred} AND s.player_id IS NOT NULL {rows}
    GROUP BY s.player_id HAVING count(*) >= ?
    ORDER BY {order_by} DESC NULLS LAST LIMIT ?
    """
    return con.execute(sql, params + row_params + [min_innings, limit]).df()


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
    rows, row_params = f.row_sql()
    sql = f"""
    WITH b AS (
        SELECT s.player_id, any_value(s.player) AS player, any_value(s.team) AS team,
               sum(s.balls) AS balls, sum(s.runs_conceded) AS runs, sum(s.wickets) AS wickets,
               sum(s.runs_conceded) FILTER (WHERE s.balls IS NOT NULL) AS timed_runs,
               sum(s.wickets) FILTER (WHERE s.balls IS NOT NULL) AS timed_wickets
        FROM bowling_scorecard s JOIN matches m USING (match_id)
        WHERE {pred} AND s.player_id IS NOT NULL {rows} GROUP BY s.player_id
    )
    SELECT player_id, player, team, balls // 6 || '.' || balls % 6 AS overs,
           runs AS runs_conceded, wickets,
           round(runs / nullif(wickets, 0), 2) AS average,
           round(6.0 * timed_runs / nullif(balls, 0), 2) AS economy,
           round(balls / nullif(timed_wickets, 0), 1) AS strike_rate
    FROM b WHERE wickets >= ?
    ORDER BY {order_by} {direction} NULLS LAST LIMIT ?
    """
    return con.execute(sql, params + row_params + [min_wickets, limit]).df()

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
