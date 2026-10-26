"""Match results for every men's Test from Wikipedia's "International cricket in <season>" pages.

Each season page lists every Test series with the Test number, dates, captains, venue and
result. That is result-level data only (no player figures), enough for team records,
head-to-head and the match-result model across all Tests since 1877.

Licence: Wikipedia text is CC BY-SA 4.0. Keep the attribution in the UI and README, and
share any republished copy of this layer under the same licence.

Usage:
    python -m cricket.wikipedia               # fetch (cached) and print a summary
    python -m cricket.ingest --wikipedia      # build the DB with these results added
"""
from __future__ import annotations

import re
import time
import urllib.parse
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from cricket.venues import home_team, host_country

ATTRIBUTION = "Match results for Tests outside Cricsheet from Wikipedia, CC BY-SA 4.0"
ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "raw" / "wikipedia"
USER_AGENT = ("TestCricketStats/0.1 (personal non-commercial research tool; "
              "https://github.com/camvpereira-netizen/test-cricket-stats)")
FIRST_SEASON, LAST_SEASON = 1876, date.today().year
RECENT_MAX_AGE_HOURS = 12  # pages for current seasons are fetched again after this, so new results arrive

# Codes used by Wikipedia's {{cr|XXX}} flag templates, mapped to Cricsheet team names
TEAM_CODES = {
    "AUS": "Australia", "ENG": "England", "RSA": "South Africa", "SAF": "South Africa",
    "SA": "South Africa", "ZAF": "South Africa", "WIN": "West Indies", "WI": "West Indies", "NZL": "New Zealand",
    "NZ": "New Zealand", "IND": "India", "PAK": "Pakistan", "SRI": "Sri Lanka", "SL": "Sri Lanka",
    "ZIM": "Zimbabwe", "BAN": "Bangladesh", "BD": "Bangladesh", "IRE": "Ireland",
    "AFG": "Afghanistan", "ICC": "ICC World XI", "WXI": "ICC World XI",
}
TEAM_NAMES = sorted(set(TEAM_CODES.values()) | {"World XI"}, key=len, reverse=True)
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


# ---------------------------------------------------------------- fetching

def wikitext(title: str, cache: Path = CACHE, max_age_hours: float | None = None) -> str | None:
    """Raw wikitext of a page (follows one redirect), cached on disk; None if missing.

    A cached copy is used unless it is older than max_age_hours (None: always use it).
    """
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / (re.sub(r"[^\w]+", "_", title) + ".wiki")
    fresh = path.exists() and (
        max_age_hours is None or time.time() - path.stat().st_mtime < max_age_hours * 3600)
    if fresh:
        text = path.read_text()
    else:
        url = "https://en.wikipedia.org/w/index.php?" + urllib.parse.urlencode(
            {"title": title, "action": "raw"})
        for attempt in range(6):
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            if r.status_code in (200, 404):
                break
            time.sleep(5 * 2 ** attempt)  # rate limited: back off
        else:
            r.raise_for_status()
        text = r.text if r.status_code == 200 else ""
        path.write_text(text)
        time.sleep(1)  # be polite
    if not text:
        return None
    m = re.match(r"#REDIRECT\s*\[\[([^\]#|]+)", text, re.I)
    return wikitext(m.group(1).strip(), cache, max_age_hours) if m else text


def season_titles(first: int = FIRST_SEASON, last: int = LAST_SEASON) -> list[str]:
    out = []
    for y in range(first, last + 1):
        nxt = str(y + 1) if (y + 1) % 100 == 0 else str(y + 1)[-2:]  # "1999–2000", else "1928–29"
        out += [f"International cricket in {y}", f"International cricket in {y}–{nxt}"]
    return out


# ---------------------------------------------------------------- parsing

def _strip(cell: str) -> str:
    """Wikitext cell to plain text: links to their label, templates and refs removed."""
    cell = re.sub(r"<ref[^>]*/>|<ref[^>]*>.*?</ref>", "", cell, flags=re.S)
    cell = cell.replace("&nbsp;", " ")
    cell = re.sub(r"\{\{([A-Z]{2,3})\}\}", lambda m: TEAM_CODES.get(m.group(1), m.group(0)), cell)
    cell = re.sub(r"\{\{cr[a-z0-9]*\|([^|}]+)[^}]*\}\}",
                  lambda m: TEAM_CODES.get(m.group(1).strip().upper(), m.group(1).strip()), cell, flags=re.I)
    cell = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", cell)
    cell = re.sub(r"\{\{(?:dts|nowrap|noflag)\|([^}]*)\}\}", lambda m: m.group(1).replace("|", " "), cell)
    cell = re.sub(r"\{\{[^}]*\}\}", "", cell)
    cell = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", cell)
    cell = re.sub(r"\[\S+\s+([^\]]*)\]", r"\1", cell)  # external link -> its label
    cell = re.sub(r"'''?|<[^>]+>", "", cell)
    if "|" in cell and not cell.startswith("|"):  # cell attributes: style="..." | value
        cell = cell.split("|")[-1]
    return re.sub(r"\s+", " ", cell).strip()


def _rows(text: str):
    """Yield (heading, cells) for every wikitable row; heading is the nearest section title."""
    heading = ""
    cells: list[str] = []
    in_table = False
    for line in text.splitlines():
        line = line.strip()
        h = re.match(r"^(={2,5})\s*(.*?)\s*\1\s*$", line)
        if h:
            heading = _strip(h.group(2))
            continue
        if line.startswith("{|"):
            in_table, cells = True, []
            continue
        if not in_table:
            continue
        if line.startswith("|}") or line.startswith("|-"):
            if cells:
                yield heading, cells
            cells = []
            if line.startswith("|}"):
                in_table = False
            continue
        if line.startswith("|") or line.startswith("!"):
            cells += [c for c in re.split(r"\|\||!!", line[1:])]
        elif cells:
            cells[-1] += " " + line


DEMONYMS = {"Australians": "Australia", "Englishmen": "England", "South Africans": "South Africa",
            "West Indians": "West Indies", "New Zealanders": "New Zealand", "Indians": "India",
            "Pakistanis": "Pakistan", "Sri Lankans": "Sri Lanka", "Zimbabweans": "Zimbabwe",
            "Bangladeshis": "Bangladesh", "Afghans": "Afghanistan"}


def _team(s: str) -> str | None:
    s = re.sub(r"^the\s+", "", s.strip(), flags=re.I)
    for k, v in DEMONYMS.items():
        if s.startswith(k):
            return v
    if s.startswith("MCC") or s.startswith("Marylebone"):
        return "England"  # England toured as MCC until the 1970s
    for name in TEAM_NAMES:
        if s == name or s.startswith(name + " ") or s.startswith(name + ","):
            return "ICC World XI" if name == "World XI" else name
    return None


def _teams_from_heading(heading: str) -> tuple[str | None, str | None]:
    """(home, away) from a section heading; home is None when the host has no Test side.

    'England in Australia' -> ('Australia', 'England')
    'Ireland vs Afghanistan in India' (neutral venue) -> ('Afghanistan', 'Ireland')
    'Australia in the United Arab Emirates' -> (None, 'Australia')
    """
    h = re.sub(r"\bin\b", "in", heading, flags=re.I)
    if "Super Series" in h:
        return "Australia", "ICC World XI"
    v = re.match(r"(.+?)\s+(?:vs?\.?|versus|against)\s+(.+?)(?:\s+in\s+.+)?$", h)
    if v and _team(v.group(1)) and _team(v.group(2)):
        return _team(v.group(2)), _team(v.group(1))
    m = re.match(r"(.+?)\s+in\s+(.+?)(?:\s*\(.*\))?$", h)
    if not m:
        return None, None
    return _team(m.group(2)), _team(m.group(1))


def _start_date(text: str, season: str) -> date | None:
    """First day of a range like '29 Dec–5 January' or '15–19 March', using the season's years."""
    text = text.replace("&ndash;", "–").replace("-", "–")
    first = text.split("–")[0]
    day = re.search(r"\d{1,2}", first)
    month = re.search(r"[A-Za-z]{3,}", first) or re.search(r"[A-Za-z]{3,}", text)
    if not day or not month or month.group(0)[:3].lower() not in MONTHS:
        return None
    mon = MONTHS[month.group(0)[:3].lower()]
    year_in_cell = re.search(r"(18|19|20)\d{2}", text)
    y = re.match(r"(\d{4})(?:–(\d{2}))?", season)
    if year_in_cell:
        year = int(year_in_cell.group(0))
    elif y.group(2):  # southern season: Jul-Dec in the first year, Jan-Jun in the next
        year = int(y.group(1)) + (0 if mon >= 7 else 1)
    else:
        year = int(y.group(1))
    try:
        return date(year, mon, int(day.group(0)))
    except ValueError:
        return None


def _result(text: str, home: str, away: str) -> dict:
    t = text.lower()
    out = {"winner": None, "result": None, "win_by_runs": None, "win_by_wickets": None,
           "win_by_innings": False}
    if "tied" in t or t.strip() == "tie":
        out["result"] = "tie"
    elif "drawn" in t or t.strip() == "draw":
        out["result"] = "draw"
    elif "abandon" in t or "no result" in t or "cancel" in t:
        out["result"] = "no result"
    else:
        for team in (home, away):
            if t.startswith(team.lower()) or f"{team.lower()} won" in t or f"awarded to {team.lower()}" in t:
                out["winner"] = team
        if out["winner"] is None:
            return out
        runs = re.search(r"(\d+)\s*runs?", t)
        wkts = re.search(r"(\d+)\s*(?:wickets?|wkts?)\b", t)
        out["win_by_innings"] = bool(re.search(r"\binn", t))
        out["win_by_runs"] = int(runs.group(1)) if runs else None
        out["win_by_wickets"] = int(wkts.group(1)) if wkts else None
    return out


def _split_single_pipes(cells: list[str]) -> list[str]:
    """Rows written as '|a |b |c' on one line: split on pipes outside links and templates."""
    if len(cells) != 1:
        return cells
    out, depth, cur, text = [], 0, "", cells[0]
    for i, ch in enumerate(text):
        if text.startswith(("[[", "{{"), i):
            depth += 1
        elif text.startswith(("]]", "}}"), i):
            depth -= 1
        if ch == "|" and depth <= 0 and not text.startswith(("]]", "}}"), i - 1):
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return out + [cur]


# Rows labelled "1st Test", "Only Test" and so on carry no Test number; `clean` gives them one
ORDINAL_TEST = re.compile(r"^(?:\d+(?:st|nd|rd|th)|only|one-off|sole|first|second|third|fourth|fifth)\s+test\b", re.I)
NOT_MEN_TESTS = re.compile(r"women|youth|under-?\d+|\bU-?19\b|\bA team\b|\bXI\b(?<!World XI)", re.I)


def parse_season(text: str, title: str) -> list[dict]:
    """Every Test listed on one season page.

    Teams come from the section heading ('England in Australia'). Where the heading names
    a host that has no Test side ('Australia in the United Arab Emirates'), team1 is left
    None and `resolve_teams` fills it in from the home captain.

    Rows without a Test number ('1st Test', or a Test listed as 'ODI 1207' under a Test
    sub-heading) come back with test_no None; `clean` numbers the ones that fill a gap.
    """
    season = title.rsplit(" ", 1)[-1]
    out = []
    sub, section = "", None  # sub: the table's sub-heading row, e.g. "Test series" or "ODI series"
    for heading, cells in _rows(text):
        if heading != section:
            sub, section = "", heading
        if len(cells) == 1 and cells[0].lstrip().startswith(("colspan", "style")):
            sub = _strip(cells[0])
            continue
        cells = _split_single_pipes(cells)
        if len(cells) < 4 or (len(cells) < 6 and "]], [[" not in cells[2]):
            continue
        if NOT_MEN_TESTS.search(heading) or NOT_MEN_TESTS.search(sub):
            continue
        label = _strip(cells[0])
        # "Test 2351a"-style numbers mark matches abandoned or cancelled without being Tests
        num = re.search(r"Test\s*(?:no\.?:?\s*)?(\d{1,4})(?![\dA-Za-z])", label, re.I)
        if num:
            test_no = int(num.group(1))
        elif ORDINAL_TEST.match(label) or (re.fullmatch(r"ODI\s*\d{1,4}", label) and "Test" in sub):
            test_no = None
        else:
            continue
        plain = [_strip(c) for c in cells]
        if len(plain) >= 8 and _team(plain[2]) and _team(plain[4]):
            # tournament layout: No, Date, Team 1, Captain 1, Team 2, Captain 2, Venue, Result
            home, away = _team(plain[2]), _team(plain[4])
            caps, venue, result = (plain[3], plain[5]), plain[6], plain[7]
        elif len(plain) >= 6 and _team(plain[2]) and _team(plain[3]):
            # one-off final: No, Date, Team 1, Team 2, Venue, Result
            home, away = _team(plain[2]), _team(plain[3])
            caps, venue, result = (None, None), plain[4], plain[5]
        elif len(plain) >= 6:
            home, away = _teams_from_heading(heading)
            caps, venue, result = (plain[2], plain[3]), plain[4], plain[5]
        else:  # recent pages: No, Date, Venue, Result
            home, away = _teams_from_heading(heading)
            caps, venue, result = (None, None), plain[2], plain[3]
        if not away:
            continue
        city = venue.split(",", 1)[1].strip() if "," in venue else None
        out.append({
            "match_id": f"wp{test_no}" if test_no else None, "test_no": test_no,
            "start_date": _start_date(plain[1], season), "team1": home, "team2": away,
            "venue": venue.split(",", 1)[0].strip(), "city": city,
            "home_captain": caps[0], "away_captain": caps[1], "result_text": result,
            "source": "wikipedia", "detail": "result", "season_page": title,
        })
    return out


def resolve_teams(raw: pd.DataFrame) -> pd.DataFrame:
    """Fill unknown home sides from captains seen elsewhere, then parse results and venues."""
    df = raw.copy()
    known = pd.concat([
        df[df.team1.notna()][["home_captain", "team1"]].set_axis(["captain", "team"], axis=1),
        df[["away_captain", "team2"]].set_axis(["captain", "team"], axis=1)])
    captain_team = known.groupby("captain").team.agg(lambda t: t.mode().iloc[0]).to_dict()
    unknown = df.team1.isna()
    df.loc[unknown, "team1"] = df.loc[unknown, "home_captain"].map(captain_team)
    df = df[df.team1.notna() & (df.team1 != df.team2)].copy()
    res = [_result(t, h, a) for t, h, a in zip(df.result_text, df.team1, df.team2)]
    df = pd.concat([df.reset_index(drop=True), pd.DataFrame(res)], axis=1)
    df = df.astype({"city": object, "venue": object}).where(df.notna(), None)
    df["country"] = [host_country(c, v) for c, v in zip(df.city, df.venue)]
    df["home_team"] = [home_team(a, b, c) if c else None
                       for a, b, c in zip(df.team1, df.team2, df.country)]
    df["toss_winner"] = df["toss_decision"] = None
    return df


# Date each side played its first Test; anything earlier on a season page is mislabelled
TEST_DEBUT = {
    "Australia": date(1877, 3, 15), "England": date(1877, 3, 15),
    "South Africa": date(1889, 3, 12), "West Indies": date(1928, 6, 23),
    "New Zealand": date(1930, 1, 10), "India": date(1932, 6, 25), "Pakistan": date(1952, 10, 16),
    "Sri Lanka": date(1982, 2, 17), "Zimbabwe": date(1992, 10, 18), "Bangladesh": date(2000, 11, 10),
    "ICC World XI": date(2005, 10, 14), "Ireland": date(2018, 5, 11), "Afghanistan": date(2018, 6, 14),
}


def _date_suspect(df: pd.DataFrame, slack_days: int = 30) -> pd.Series:
    """True where a date is well out of step with the Tests numbered just before or after it.

    Series in different countries overlap, so neighbouring numbers can be a few weeks out
    of order; only gaps beyond slack_days are flagged.
    """
    d = pd.to_datetime(df.sort_values("test_no").start_date).map(pd.Timestamp.toordinal).astype(float)
    slack = slack_days
    before = d.shift(1).rolling(3, min_periods=1).max().fillna(d)
    after = d[::-1].shift(1).rolling(3, min_periods=1).min()[::-1].fillna(d)
    return ((d < before - slack) | (d > after + slack)).reindex(df.index)


def _fits(row, others: pd.DataFrame, slack_days: int = 30) -> bool:
    """Whether a row's date sits between the Tests numbered just below and above it."""
    d, slack = pd.Timestamp(row.start_date), pd.Timedelta(days=slack_days)
    below = others[others.test_no < row.test_no]
    above = others[others.test_no > row.test_no]
    lo = pd.Timestamp(below.loc[below.test_no.idxmax(), "start_date"]) if len(below) else None
    hi = pd.Timestamp(above.loc[above.test_no.idxmin(), "start_date"]) if len(above) else None
    return (lo is None or d >= lo - slack) and (hi is None or d <= hi + slack)


def _listed(pool: pd.DataFrame, numbered: pd.DataFrame, days: int = 3) -> pd.Series:
    """True for pool rows already among the numbered ones (same two teams, start within days).

    A row that lost a shared number must also differ in venue: two Tests of one series can
    carry the same mistyped number and date (Nagpur and Delhi, both "Test 1589").
    """
    by_pair: dict[frozenset, list] = {}
    for r in numbered.itertuples():
        by_pair.setdefault(frozenset((r.team1, r.team2)), []).append((pd.Timestamp(r.start_date), r.venue))
    return pd.Series([any(abs((pd.Timestamp(p.start_date) - d).days) <= days
                          and not (p.spare and p.venue != venue)
                          for d, venue in by_pair.get(frozenset((p.team1, p.team2)), []))
                      for p in pool.itertuples()], index=pool.index, dtype=bool)


def fill_gaps(df: pd.DataFrame, slack_days: int = 3) -> tuple[pd.DataFrame, list[int]]:
    """Number unnumbered rows (test_no None) that fill a gap in the numbering.

    Each run of missing numbers lies between two numbered Tests; the unnumbered rows dated
    between those two (give or take slack_days) and not already listed are its candidates.
    When there are exactly as many candidates as missing numbers, they take the numbers in
    date order (page order on the same date). Returns the numbered rows and the numbers filled.
    """
    df = df if "spare" in df else df.assign(spare=False)
    numbered = df[df.test_no.notna()].copy()
    pool = df[df.test_no.isna()].copy()
    pool = pool[~_listed(pool, numbered)]
    pool = pool.drop_duplicates(["start_date", "team1", "team2", "venue"])
    nums = sorted(int(n) for n in numbered.test_no)
    dates = numbered.set_index(numbered.test_no.astype(int)).start_date
    filled = []
    slack = pd.Timedelta(days=slack_days)
    for lo, hi in zip(nums, nums[1:]):
        run = list(range(lo + 1, hi))
        if not run:
            continue
        start, end = pd.Timestamp(dates[lo]), pd.Timestamp(dates[hi])
        when = pd.to_datetime(pool.start_date)
        cand = pool[(when >= start - slack) & (when <= end + slack)].sort_values(["start_date", "row_order"])
        if len(cand) != len(run):
            continue
        cand = cand.assign(test_no=run)
        numbered = pd.concat([numbered, cand])
        pool = pool.drop(cand.index)
        filled += run
    return numbered, filled


def clean(raw: pd.DataFrame) -> pd.DataFrame:
    """Repair the mistakes season pages are known to contain, and flag dates that look wrong.

    - drop "Tests" before a side had Test status (e.g. 1914 New Zealand v Australia)
    - the same match listed on two pages: keep one
    - two matches given one number next to an unused one: they take both, in date order
    - otherwise, of matches sharing a number keep the one whose date fits the numbers around
      it (the first listed if both fit); the others join the unnumbered rows
    - unnumbered rows ("1st Test") take the missing numbers they fit, see `fill_gaps`
    - a date out of step with the Test numbers around it is kept but marked date_suspect
    """
    raw = raw.copy()
    if "row_order" not in raw:
        raw["row_order"] = range(len(raw))
    raw = resolve_teams(raw)
    # fixtures listed before they are played have no result yet
    raw = raw[~((pd.to_datetime(raw.start_date) > pd.Timestamp.today()) & raw.winner.isna() & raw.result.isna())]
    df = raw[raw.apply(lambda r: r.start_date is not None and all(
        r.start_date >= TEST_DEBUT.get(t, date.max) for t in (r.team1, r.team2)), axis=1)]
    df = df.drop_duplicates(["test_no", "start_date", "team1", "team2", "venue"])
    df = df.sort_values(["test_no", "row_order"]).reset_index(drop=True)
    numbered = df[df.test_no.notna()]
    used = set(numbered.test_no)
    keep, spare, moved = [], [], []
    for n, grp in numbered.groupby("test_no", sort=False):
        if len(grp) == 1:
            keep.append(grp.index[0])
            continue
        others = numbered.drop(grp.index)
        fit = [i for i in grp.index if _fits(grp.loc[i], others)]
        if len(grp) == 2 and len(fit) == 2 and (n - 1 not in used or n + 1 not in used):
            # a neighbouring number is free: the pair takes both, in date order
            early, late = grp.sort_values(["start_date", "row_order"]).index
            below_is_gap = n - 1 not in used and n - 2 in used
            free = n - 1 if below_is_gap or n + 1 in used else n + 1
            df.at[early if free < n else late, "test_no"] = free
            used.add(free)
            moved.append(free)
            keep += [early, late]
            continue
        first = (fit or list(grp.index))[0]
        keep.append(first)
        spare += [i for i in grp.index if i != first]
    df["spare"] = df.index.isin(spare)
    df.loc[spare, "test_no"] = None
    df = df.loc[keep + spare + list(df.index[df.test_no.isna() & ~df.index.isin(spare)])]
    df, filled = fill_gaps(df)
    filled += moved
    df = df.astype({"test_no": int}).sort_values("test_no").reset_index(drop=True)
    df["test_no_inferred"] = df.test_no.isin(filled)
    df["date_suspect"] = _date_suspect(df)
    df["match_id"] = "wp" + df.test_no.astype(str)
    return df.drop(columns=["row_order", "spare"])


def all_tests(first: int = FIRST_SEASON, last: int = LAST_SEASON) -> pd.DataFrame:
    rows = []
    recent = set(season_titles(date.today().year - 1, last))
    for title in season_titles(first, last):
        text = wikitext(title, max_age_hours=RECENT_MAX_AGE_HOURS if title in recent else None)
        if text:
            rows += parse_season(text, title)
    if not rows:
        return pd.DataFrame()
    raw = pd.DataFrame(rows)
    raw["row_order"] = range(len(raw))  # breaks ties between same-day unnumbered Tests
    return clean(raw)


def _full_date(text: str) -> date | None:
    m = re.fullmatch(r"(\d{1,2}) ([A-Za-z]+) ((?:18|19|20)\d{2})", text)
    if not m or m.group(2)[:3].lower() not in MONTHS:
        return None
    try:
        return date(int(m.group(3)), MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
    except ValueError:
        return None


ENGLAND_PAGES = [f"England cricket team Test results ({span})" for span in (
    "1877–1914", "1920–1939", "1946–1959", "1960–1974", "1975–1989", "1990–2004", "2005–2019",
    "2020–present")]


def england_results() -> pd.DataFrame:
    """England's Tests from the per-era results lists: overall Test number, date, opponent, venue."""
    rows = []
    for title in ENGLAND_PAGES:
        text = wikitext(title, max_age_hours=RECENT_MAX_AGE_HOURS if "present" in title else None)
        if not text:
            continue
        for _, cells in _rows(text):
            # Columns vary a little between eras, so find each field by its content
            nums = [int(c) for c in map(_strip, cells) if re.fullmatch(r"\d{1,4}", c)]
            plain = [_strip(c) for c in cells]
            at = next((i for i in (2, 3) if i < len(plain) and _team(plain[i])), None)
            opp = _team(plain[at]) if at else None
            venue = plain[at + 1] if at and at + 1 < len(plain) else None  # venue follows opponent
            home = any(c in ("Home", "(H)") for c in plain) or bool(venue and "(H)" in venue)
            res = next((m.group(1) for c in cells if (m := re.search(r"\{\{crsh\|([WLDT])", c))), None)
            when = next((d for c in cells if (d := _full_date(_strip(c)))), None)
            if len(nums) < 2 or not (opp and venue and when):
                continue
            rows.append({"test_no": nums[1], "start_date": when, "opponent": opp,
                         "venue": re.sub(r"\s*\((H|A|N)\)", "", venue),
                         "england_home": home, "outcome": res})
    return pd.DataFrame(rows)


def cross_check_england(df: pd.DataFrame, eng: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Use England's results lists to correct dates and fill gaps in England's Tests.

    The two lists are edited separately on Wikipedia, so where they agree the row is more
    likely right. Where only the date differs the England list wins (it gives the year in
    full); other disagreements are counted and left as they are.
    """
    if eng.empty:
        return df, {}
    df = df.copy()
    by_no = eng.set_index("test_no")
    fixed_dates, disagree, result_disagree, filled = [], [], [], []
    for i, r in df.iterrows():
        if "England" not in (r.team1, r.team2) or r.test_no not in by_no.index:
            continue
        e = by_no.loc[r.test_no]
        other = r.team2 if r.team1 == "England" else r.team1
        if e.opponent != other:
            disagree.append(int(r.test_no))
            continue
        eng_outcome = {"W": "England", "L": other, "D": "draw", "T": "tie"}.get(e.outcome)
        if eng_outcome and eng_outcome != (r.winner if pd.notna(r.winner) else r.result):
            result_disagree.append(int(r.test_no))
        if e.start_date != r.start_date:
            df.at[i, "start_date"] = e.start_date
            fixed_dates.append(int(r.test_no))
    missing = eng[~eng.test_no.isin(df.test_no) & eng.opponent.notna()]
    for e in missing.itertuples():
        home, away = ("England", e.opponent) if e.england_home else (e.opponent, "England")
        winner = {"W": "England", "L": e.opponent}.get(e.outcome)
        df.loc[len(df)] = {**{c: None for c in df.columns}, "match_id": f"wp{e.test_no}",
                           "test_no": e.test_no, "start_date": e.start_date, "team1": home,
                           "team2": away, "venue": e.venue.split(",", 1)[0].strip(),
                           "city": e.venue.split(",", 1)[1].strip() if "," in e.venue else None,
                           "winner": winner, "win_by_innings": False,
                           "result": {"D": "draw", "T": "tie"}.get(e.outcome),
                           "source": "wikipedia", "detail": "result",
                           "season_page": "England Test results", "date_suspect": False}
        filled.append(int(e.test_no))
    df = df.sort_values("test_no").reset_index(drop=True)
    df["date_suspect"] = _date_suspect(df)
    df = df.astype({"city": object, "venue": object}).where(df.notna(), None)
    df["country"] = [host_country(c, v) for c, v in zip(df.city, df.venue)]
    df["home_team"] = [home_team(a, b, c) if c else None for a, b, c in zip(df.team1, df.team2, df.country)]
    return df, {"england_dates_fixed": fixed_dates, "england_opponent_disagrees": disagree,
                "england_result_disagrees": result_disagree,
                "england_tests_added": filled}


def england_checked(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """all_tests() output corrected against England's results lists."""
    return cross_check_england(df, england_results())


def check(df: pd.DataFrame) -> dict:
    """Gaps and inconsistencies worth looking at before trusting the data."""
    nums = set(df.test_no)
    top = max(nums) if nums else 0
    return {
        "tests": len(df),
        "highest_test_no": top,
        "missing_numbers": sorted(set(range(1, top + 1)) - nums),
        "no_result": df[df.winner.isna() & df.result.isna()].test_no.tolist(),
        "date_suspect": df[df.date_suspect].test_no.tolist(),
        "no_country": df[df.country.isna()].test_no.tolist(),
    }


if __name__ == "__main__":
    tests, report = england_checked(all_tests())
    print({**report, **check(tests)})
