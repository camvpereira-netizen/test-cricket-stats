"""Career totals for every men's Test cricketer, from Wikipedia's "List of <team> Test cricketers".

Each list has one row per capped player: cap number, name, first and last year, matches,
runs, highest score, batting average, balls, wickets, best bowling, bowling average,
catches and stumpings (some lists also innings, not outs, maidens, 100s/50s, 5w/10w).
Gaps are filled from each player's article infobox (infobox.py). That covers
players from 1877 on, where ball-by-ball and scorecard data only start in 2001.

Totals are for one team only (a player capped by two teams has a row on each list), and
each page states the date its figures are correct to. Licence: CC BY-SA 4.0, like the
season pages; keep the attribution.

Usage:
    python -m cricket.careers     # fetch (cached) and print a summary
"""
from __future__ import annotations

import re

import pandas as pd

from cricket.stats import _norm
from cricket.wikipedia import _rows, _strip, wikitext

LIST_PAGES = {
    "England": "List of England Test cricketers",
    "Australia": "List of Australia Test cricketers",
    "South Africa": "List of South Africa Test cricketers",
    "West Indies": "List of West Indies Test cricketers",
    "New Zealand": "List of New Zealand Test cricketers",
    "India": "List of India Test cricketers",
    "Pakistan": "List of Pakistan Test cricketers",
    "Sri Lanka": "List of Sri Lanka Test cricketers",
    "Zimbabwe": "List of Zimbabwe Test cricketers",
    "Bangladesh": "List of Bangladesh Test cricketers",
    "Ireland": "List of Ireland Test cricketers",
    "Afghanistan": "List of Afghanistan Test cricketers",
}
PEOPLE_URL = "https://cricsheet.org/register/people.csv"  # register ids with ESPNcricinfo ids (ODC-BY)
MAX_AGE_HOURS = 24 * 7  # the lists are updated after each series; a week old is fine

# Header label -> column; the second "Runs" and "Avg" in a table are the bowling ones
HEADERS = {
    "cap": "cap", "name": "name", "career": "career", "first": "first_year",
    "first test": "first_year", "last": "last_year", "last test": "last_year", "mat": "matches",
    "inn": "innings", "innings": "innings", "no": "not_outs", "runs": "runs", "hs": "high_score",
    "avg": "batting_average", "ave": "bowling_average", "100/50": "hundreds_fifties",
    "balls": "balls", "mdn": "maidens", "wkt": "wickets", "best": "best_bowling",
    "bbi": "best_bowling", "5/10 wicket": "five_ten", "ca": "catches", "ct": "catches", "st": "stumpings",
}
INT_COLUMNS = ["cap", "first_year", "last_year", "matches", "innings", "not_outs", "runs",
               "balls", "maidens", "runs_conceded", "wickets", "catches", "stumpings",
               "hundreds", "fifties", "five_wickets", "ten_wickets"]
FLOAT_COLUMNS = ["batting_average", "bowling_average"]
COLUMNS = ["team", "cap", "name", "wiki_title", "cricinfo_id", "first_year", "last_year",
           "matches", "innings", "not_outs", "runs", "high_score", "batting_average", "hundreds",
           "fifties", "balls", "maidens", "runs_conceded", "wickets", "best_bowling",
           "bowling_average", "five_wickets", "ten_wickets", "catches", "stumpings", "as_of"]


def _templates(cell: str) -> str:
    """Display text of the templates these lists use, before the generic strip."""
    cell = re.sub(r"\{\{(?:sort|nts|nowrap)\|(?:[^{}|]*\|)*([^{}|]*)\}\}", r"\1", cell)
    cell = re.sub(r"\{\{efn[^{}]*(?:\{\{[^{}]*\}\}[^{}]*)*\}\}", "", cell)
    return cell


def _name(cell: str) -> tuple[str, str]:
    """(display name, article title) from a name cell."""
    m = re.search(r"\{\{sortname\|([^|}]*)\|([^|}]*)((?:\|[^}]*)?)\}\}", cell)
    if m:
        name = f"{m.group(1).strip()} {m.group(2).strip()}"
        extra = [a.strip() for a in m.group(3).split("|") if a.strip()]
        dab = next((a.split("=", 1)[1] for a in extra if a.startswith("dab=")), None)
        target = next((a for a in extra if "=" not in a), None)
        title = target or (f"{name} ({dab})" if dab else name)
        return name, title
    link = re.search(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", cell)
    if link:
        return (link.group(2) or link.group(1)).strip(), link.group(1).strip()
    plain = _strip(cell)
    return plain, plain


def _number(text: str):
    t = text.replace(",", "").replace("*", "").strip()
    return pd.to_numeric(t, errors="coerce") if re.fullmatch(r"-?\d+(\.\d+)?", t) else None


def _rejoin(cells: list[str]) -> list[str]:
    """Undo splits on a "||" inside a template, e.g. {{sortname|Jack|Board||Jack Board (cricketer)}}."""
    out: list[str] = []
    for c in cells:
        if out and out[-1].count("{{") > out[-1].count("}}"):
            out[-1] += "||" + c
        else:
            out.append(c)
    return out


def parse_list(text: str, team: str) -> pd.DataFrame:
    """One row per player on a team's list page."""
    as_of = re.search(r"correct as of ([^.<]*?\d{4})", text)
    header: list[str] | None = None
    rows = []
    for _, cells in _rows(text):
        cells = _rejoin(cells)
        labels = [_strip(_templates(c)).lower().rstrip(".") for c in cells]
        if "name" in labels and "mat" in labels:
            header, seen = [], set()
            for lab in labels:
                key = HEADERS.get(lab, lab)
                if key in seen and key in ("runs", "batting_average"):
                    key = {"runs": "runs_conceded", "batting_average": "bowling_average"}[key]
                seen.add(key)
                header.append(key)
            continue
        if header is None or len(cells) < len(header) - 1:
            continue
        cells = [c[1:] if c.startswith("|") else c for c in cells]
        row = {"team": team}
        for key, cell in zip(header, cells):
            if key == "name":
                row["name"], row["wiki_title"] = _name(re.sub(r"^.*?scope=\"row\"\s*\|", "", cell))
                row["name"] = re.sub(r"[‡†*]|'''", "", row["name"]).strip()
            else:
                row[key] = _strip(_templates(cell)).replace("–", "-").replace("—", "-").strip()
        ids = re.findall(r"cricinfo\.com/\S*?(?:player/|cricketers/[a-z0-9-]*?-)(\d+)", " ".join(cells))
        row["cricinfo_id"] = ids[0] if ids else None
        if _number(row.get("cap", "")) is None or not row.get("name"):
            continue
        rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    if "career" in df:  # "1877–1894", "1877", or "2010–" for a current player
        years = df.career.str.extractall(r"(\d{4})")[0].unstack()
        df["first_year"] = years[0]
        df["last_year"] = years.ffill(axis=1).iloc[:, -1]
        ongoing = df.career.str.strip().str.endswith(("-", "–"))
        if as_of and ongoing.any():
            df.loc[ongoing, "last_year"] = as_of.group(1)[-4:]
    if "hundreds_fifties" in df:
        hf = df.hundreds_fifties.str.extract(r"(\d+)\s*/\s*(\d+)")
        df["hundreds"], df["fifties"] = hf[0], hf[1]
    if "five_ten" in df:  # India's "5/10 wicket": five-wicket innings / ten-wicket matches
        ft = df.five_ten.str.extract(r"(\d+)\s*/\s*(\d+)")
        df["five_wickets"], df["ten_wickets"] = ft[0], ft[1]
    for c in INT_COLUMNS + FLOAT_COLUMNS:
        if c in df:
            df[c] = df[c].map(lambda v: _number(str(v)) if pd.notna(v) else None)
    df["as_of"] = as_of.group(1).strip() if as_of else None
    df = df.reindex(columns=COLUMNS)
    for c in INT_COLUMNS:
        df[c] = df[c].astype("Int64")
    for c in FLOAT_COLUMNS:
        df[c] = df[c].astype("Float64")
    return df


def all_careers(infoboxes: bool = True) -> pd.DataFrame:
    """Every list row; with infoboxes, gaps filled from the players' articles (infobox.py)."""
    frames = []
    for team, title in LIST_PAGES.items():
        text = wikitext(title, max_age_hours=MAX_AGE_HOURS)
        if text:
            frames.append(parse_list(text, team))
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    if infoboxes and not df.empty:
        from cricket import infobox
        df = infobox.fill(df, infobox.fetch(sorted(set(df.wiki_title.dropna()))))
    return df


def _surname(name: str) -> str:
    return _norm(name).split(" ")[-1]


def link_players(con, careers: pd.DataFrame, people: pd.DataFrame | None = None) -> pd.DataFrame:
    """Add player_id: the Cricsheet id where the player is in the matches, else a 'wp:' id.

    Matched first by ESPNcricinfo id through the register (people.csv), then by team, surname
    and an overlapping career span, taking the closest first-name match.
    """
    df = careers.copy()
    df["player_id"] = None
    has_names = con.execute("SELECT count(*) FROM information_schema.tables "
                            "WHERE table_name = 'player_names'").fetchall()[0][0]
    names = con.execute(f"""SELECT DISTINCT player_id, player AS name FROM appearances
                            {'UNION SELECT player_id, name FROM player_names' if has_names else ''}""").df()
    if people is not None and "key_cricinfo" in people:
        keys = people.dropna(subset=["key_cricinfo"])
        by_cricinfo = dict(zip(keys.key_cricinfo.str.replace(r"\.0$", "", regex=True), keys.identifier))
        # list pages occasionally cite the wrong profile (two players, one id): only trust an
        # id cited once on its page whose player shares the surname
        once = ~df.duplicated(["team", "cricinfo_id"], keep=False)
        df["player_id"] = df.cricinfo_id.where(once).map(by_cricinfo)
        surnames = names.assign(s=names.name.map(_surname)).groupby("player_id").s.agg(set).to_dict()
        ok = [not isinstance(pid, str) or _surname(n) in surnames.get(pid, set())
              for pid, n in zip(df.player_id, df.name)]
        df.loc[~pd.Series(ok, index=df.index), "player_id"] = None
    known = con.execute(
        """SELECT a.player_id, a.team, any_value(a.player) AS player,
                  year(min(m.start_date)) AS first_year, year(max(m.start_date)) AS last_year
           FROM appearances a JOIN matches m USING (match_id)
           WHERE a.player_id IS NOT NULL GROUP BY ALL""").df()
    ids_in_matches = set(known.player_id)
    df.loc[~df.player_id.isin(ids_in_matches), "player_id"] = None
    taken = set(df.player_id.dropna())
    for i, r in df[df.player_id.isna() & (df.last_year >= 2001)].iterrows():
        surname = _surname(r["name"])
        cand = known[(known.team == r.team) & (known.player.map(_surname) == surname)
                     & (known.last_year >= r.first_year) & (known.first_year <= r.last_year)
                     & ~known.player_id.isin(taken)]
        if len(cand) > 1:  # several with this surname: the one whose register names include the name
            full = names[names.player_id.isin(cand.player_id) & (names.name.map(_norm) == _norm(r["name"]))]
            if full.empty:  # else by initials: "Michael Bracewell" is MG, not DAJ, Bracewell
                first = _norm(r["name"])[:1]
                cand = cand[cand.player.map(lambda p: p.split()[0].isupper() and first.upper() in p.split()[0])]
            else:
                cand = cand[cand.player_id.isin(full.player_id)]
        if len(cand) == 1:
            df.at[i, "player_id"] = cand.player_id.iloc[0]
            taken.add(cand.player_id.iloc[0])
    team_code = df.team.str.lower().str.replace(" ", "-")
    df["player_id"] = df.player_id.fillna("wp:" + team_code + ":" + df.cap.astype(str))
    return df


def load(con, careers: pd.DataFrame) -> int:
    """Store the linked careers as the player_careers table. Returns rows stored."""
    con.register("tmp_df", careers)
    con.execute("CREATE OR REPLACE TABLE player_careers AS SELECT * FROM tmp_df")
    con.unregister("tmp_df")
    return con.execute("SELECT count(*) FROM player_careers").fetchall()[0][0]


if __name__ == "__main__":
    df = all_careers()
    print(df.groupby("team").agg(players=("cap", "size"), top_cap=("cap", "max"), as_of=("as_of", "first")))
