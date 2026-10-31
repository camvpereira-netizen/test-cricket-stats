"""A free question parser: common plain-English stat questions turned into guru tool calls.

No AI is involved. The question is matched against a small cricket vocabulary (stats, team
and host country names, innings, dates, minimums, grounds); the words left over are looked up
as player names. When a question fits one of the shapes below it is answered by the same
checked tools the Claude guru uses (guru.run_tool), so the numbers are identical; when it
doesn't, parse() returns None and the caller can hand it to Claude or ask for a rephrase.

Shapes understood:
- leaderboards: "most wickets in India since 2015", "best batting average in the 4th innings"
- one player: "Root's average against India at home", "Anderson bowling at Lord's"
- several players: "compare Root, Smith and Kohli in Australia"
- teams: "India's record against Australia since 2000", "England v Australia head to head"
"""
from __future__ import annotations

import numbers
import re
from dataclasses import dataclass, field
from datetime import date

from cricket import guru, stats

ASIA = ["Bangladesh", "India", "Pakistan", "Sri Lanka", "United Arab Emirates"]
REGIONS = {"asia": ASIA, "the subcontinent": ASIA, "subcontinent": ASIA}
TEAM_ALIASES = {"aussies": "Australia", "proteas": "South Africa", "windies": "West Indies",
                "kiwis": "New Zealand", "uae": "United Arab Emirates", "nz": "New Zealand",
                "sa": "South Africa", "wi": "West Indies", "aus": "Australia", "eng": "England",
                "ind": "India", "pak": "Pakistan", "sl": "Sri Lanka"}
VENUE_ALIASES = {"mcg": "melbourne cricket ground", "scg": "sydney cricket ground"}
INNINGS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}

# Words that are never part of a player's name
VOCAB = set("""
a about after against all also among an and any are as at average averages avg away abroad
bat bats batted batter batters batting batsman batsmen be been before best between bowl bowled
bowler bowlers bowling by can career careers centuries century compare compared comparison
did do does each economy ever five fivefor fivefors for from get give got h2h had has have
head her his home how hundred hundreds i in innings inns is it its leading least list
lowest many match matches me min minimum most much my neutral number of on or overall
overseas per played player players please rate record records result results run runs score
scored scorer scorers scoring show since stat stats statistics strike take taken tell than
that the their them they this time to ton tons top total took until up v versus vs was
were what when where which who whole wicket wickets with won lost year years test tests
leaders leader highest high greatest good better been far taker takers getter getters
""".split())
BOWLING_WORDS = {"wicket", "wickets", "bowler", "bowlers", "bowling", "bowl", "bowled", "economy",
                 "fivefor", "fivefors"}
LEADER_WORDS = {"most", "best", "top", "highest", "lowest", "leading", "leaders", "leader",
                "who", "which", "greatest"}


@dataclass
class Plan:
    """What the parser understood: the tool calls to make and how to word the answer."""
    kind: str                       # leaderboard, career_leaderboard, player, compare, team, h2h
    calls: list[tuple[str, dict]] = field(default_factory=list)
    describe: str = ""              # the question as understood, e.g. "most wickets in India since 2015"
    players: list[str] = field(default_factory=list)


def _norm(text: str) -> str:
    text = text.lower().replace("’", "'")
    text = re.sub(r"'s\b", "", text)          # possessives: "root's" -> "root"
    text = text.replace("'", "")              # "lord's" (already "lord") and stray quotes
    text = re.sub(r"five[- ]for", "fivefor", text)
    text = re.sub(r"5[- ]?(?:wkt|wicket)s?[- ]?hauls?", "fivefors", text)
    text = re.sub(r"head[- ]to[- ]head", "h2h", text)
    text = re.sub(r"strike[- ]rate", "strikerate", text)
    text = text.replace(",", " and ")
    return re.sub(r"[^a-z0-9 ]+", " ", text)


def _take(text: str, pattern: str) -> tuple[re.Match | None, str]:
    """First match of pattern, and the text with it blanked out."""
    m = re.search(pattern, text)
    if not m:
        return None, text
    return m, text[:m.start()] + " " + text[m.end():]


def _year_range(text: str) -> tuple[dict, str]:
    out = {}
    this_year = date.today().year
    m, text = _take(text, r"\bbetween (\d{4}) and (\d{4})\b")
    if m:
        out = {"date_from": f"{m[1]}-01-01", "date_to": f"{m[2]}-12-31"}
    m, text = _take(text, r"\b(?:since|from|after) (\d{4})\b")
    if m:
        start = int(m[1]) + (1 if "after" in m[0] else 0)
        out["date_from"] = f"{start}-01-01"
    m, text = _take(text, r"\b(?:before|until|up to) (\d{4})\b")
    if m:
        end = int(m[1]) - (1 if "before" in m[0] else 0)
        out["date_to"] = f"{end}-12-31"
    m, text = _take(text, r"\b(?:in )?the (\d{3})0s\b|\b(\d{3})0s\b")
    if m:
        decade = m[1] or m[2]
        out = {"date_from": f"{decade}0-01-01", "date_to": f"{decade}9-12-31"}
    m, text = _take(text, r"\b(?:in the )?last (\d{1,2}) years\b")
    if m:
        out["date_from"] = f"{this_year - int(m[1])}-01-01"
    m, text = _take(text, r"\bthis year\b")
    if m:
        out["date_from"] = f"{this_year}-01-01"
    m, text = _take(text, r"\bin (\d{4})\b")
    if m:
        out = {"date_from": f"{m[1]}-01-01", "date_to": f"{m[1]}-12-31"}
    return out, text


def _venue(con, raw: str) -> tuple[str, str, str] | None:
    """For the ground or city named after "at": the text to filter on, a display name, the words used."""
    m = re.search(r"\bat ((?:the )?[A-Za-z'’.]+(?: [A-Za-z'’.]+){0,3})", raw)
    if not m:
        return None
    words = m[1].replace("’", "'").split()
    if words[0].lower() == "the":
        words = words[1:]
    for n in range(len(words), 0, -1):
        phrase = " ".join(words[:n])
        key = re.sub(r"[^a-z ]", "", phrase.lower()).strip()
        key = VENUE_ALIASES.get(key, key)
        if len(key) < 3 or key in VOCAB:
            continue
        row = con.execute(
            """SELECT CASE WHEN lower(replace(city, '''', '')) LIKE ? THEN city ELSE venue END
               FROM matches
               WHERE lower(replace(venue, '''', '')) LIKE ? OR lower(replace(city, '''', '')) LIKE ?
               ORDER BY length(venue) LIMIT 1""",
            [f"%{key}%"] * 3).fetchone()
        if row:
            # the shortest matching ground name, cut at its first comma: "Lord's, London" -> "Lord's"
            return key, row[0].split(",")[0], phrase
    return None


def _names(text: str, names: dict[str, str]) -> list[tuple[int, str]]:
    """(position, canonical name) for each team or country named in text, longest names first."""
    found = []
    for alias in sorted(names, key=len, reverse=True):
        for m in re.finditer(rf"\b{re.escape(alias)}\b", text):
            if not any(p <= m.start() < p + len(a) for p, _, a in found):
                found.append((m.start(), names[alias], alias))
    return sorted((p, n) for p, n, _ in found)


def _context(text: str, pos: int) -> str:
    """The word just before position pos."""
    before = text[:pos].split()
    return before[-1] if before else ""


def parse(con, question: str) -> Plan | None:
    raw = question.strip()
    text = " " + _norm(raw) + " "
    filters: dict = {}

    venue_label = None
    if found := _venue(con, raw):
        filters["venue"], venue_label, phrase = found
        text = re.sub(r"\bat (?:the )?" + r"\s+".join(map(re.escape, _norm(phrase).split())) + r"\b", " ", text)

    dates, text = _year_range(text)
    filters.update(dates)

    m, text = _take(text, r"\b(?:minimum|min|at least) (?:of )?(\d+)(?: (innings|inns|wickets|matches|tests))?\b")
    minimum = (int(m[1]), m[2] or "") if m else None
    m, text = _take(text, r"\btop (\d+)\b")
    limit = min(int(m[1]), guru.MAX_ROWS) if m else 10
    limit_given = m is not None

    m, text = _take(text, r"\b(first|1st|second|2nd|third|3rd|fourth|4th) innings\b")
    if m:
        filters["innings"] = INNINGS[m[1]]

    m, text = _take(text, r"\b(at home|home|away from home|away|overseas|abroad|neutral venues?|neutral)\b")
    if m:
        filters["venue_type"] = ("home" if m[1] in ("at home", "home") else
                                 "neutral" if m[1].startswith("neutral") else "away")

    # Teams, host countries and regions, read with the word before them
    teams = {t.lower(): t for t in stats.teams(con)}
    teams.update({k: v for k, v in TEAM_ALIASES.items() if v in teams.values()})
    hosts = {h.lower(): h for h in stats.host_countries(con)}
    hosts.update({k: v for k, v in TEAM_ALIASES.items() if v in hosts.values()})
    named_teams: list[str] = []
    vs_pair = False
    for region, countries in REGIONS.items():
        m, text = _take(text, rf"\bin {region}\b")
        if m:
            filters["host_countries"] = [c for c in countries if c in hosts.values()]
            break
    for pos, name in _names(text, {**teams, **{k: v for k, v in hosts.items() if k not in teams}}):
        before = _context(text, pos)
        if before == "in" and name in hosts.values():
            filters.setdefault("host_countries", []).append(name)
        elif before in ("against", "vs", "v", "versus"):
            filters["opponent"] = name   # "India's record against Australia", "England v Australia"
            vs_pair = vs_pair or (before != "against" and bool(named_teams))
        elif before == "for":
            filters["team"] = name
        else:
            named_teams.append(name)
    for alias in sorted({**teams, **hosts}, key=len, reverse=True):
        text = re.sub(rf"\b{re.escape(alias)}\b", " ", text)

    words = text.split()
    bowling = bool(BOWLING_WORDS & set(words)) or "fivefors" in text
    is_leader = bool(LEADER_WORDS & set(words)) or limit_given
    whole_career = bool({"career", "ever", "overall", "whole"} & set(words)) or "all time" in text
    split = ("year" if re.search(r"\b(?:by|each|per|every) year\b", text) else
             "innings" if re.search(r"\b(?:by|each|per) innings\b", text) else
             "home_away" if not filters.get("venue_type") and re.search(r"\bhome (?:and|or|v|vs) away\b", " ".join(words)) else None)

    # Leftover words that aren't vocabulary are player names; connectors split them
    chunks, current = [], []
    for w in words:
        if w in ("and", "or", "vs", "v", "versus", "with", "compared", "compare", "than") or w in VOCAB or w.isdigit():
            if current:
                chunks.append(" ".join(current))
                current = []
        else:
            current.append(w)
    if current:
        chunks.append(" ".join(current))
    players = []
    for chunk in chunks:
        found = stats.find_players(con, chunk, limit=1, min_score=0.9)
        if found.empty:
            return None  # a word we don't understand: let Claude or the person rephrase
        if len(chunk) < 4 and chunk not in stats._norm(found.player.iloc[0]).split():
            return None  # "mcg" is a prefix of "mcgrath": too short to trust a partial match
        players.append((found.player_id.iloc[0], found.player.iloc[0]))
    players = list(dict.fromkeys(players))

    if players and named_teams:
        return None  # "England's record at the MCG" read as a player: too unsure
    where = _describe_filters(filters, venue_label)
    # Players
    if len(players) == 1:
        pid, name = players[0]
        if split:
            return Plan("split", [("player_batting_split", {"player_id": pid, "by": split, "filters": filters})],
                        f"{name}'s batting by {split.replace('_', '/')}{where}", [name])
        tool = "player_bowling" if bowling else "player_batting"
        calls = [(tool, {"player_id": pid, "filters": filters})]
        if not filters:
            calls.insert(0, ("player_career_totals", {"player_id": pid}))
        return Plan("player", calls, f"{name}'s {'bowling' if bowling else 'batting'}{where}", [name])
    if 2 <= len(players) <= 4:
        if len(filters.get("host_countries", [])) > 1 and "asia" not in text and not dates:
            return None  # "Root in Australia v Smith in England": different filters per player
        ids = [p for p, _ in players]
        return Plan("compare", [("compare_players", {"player_ids": ids, "filters": filters})],
                    f"{', '.join(n for _, n in players)} compared{where}", [n for _, n in players])
    if len(players) > 4:
        return None

    # Teams
    if named_teams or ("opponent" in filters and {"record", "results", "won", "lost", "h2h"} & set(words)):
        wants_h2h = "h2h" in words or (vs_pair and not {"record", "records"} & set(words)) or \
            (len(named_teams) == 2 and not is_leader)
        if wants_h2h:
            pair = named_teams[:2] if len(named_teams) >= 2 else named_teams[:1] + [filters.get("opponent")]
            if len(pair) == 2 and all(pair):
                rest = {k: v for k, v in filters.items() if k != "opponent"}
                return Plan("h2h", [("head_to_head", {"team_a": pair[0], "team_b": pair[1], "filters": rest,
                                                      "limit": limit if limit_given else 20})],
                            f"{pair[0]} v {pair[1]} head to head{_describe_filters(rest, venue_label)}")
        if named_teams and not (is_leader and (bowling or {"runs", "average", "hundreds", "centuries"} & set(words))):
            team = named_teams[0]
            return Plan("team", [("team_record", {"team": team, "filters": filters})], f"{team}'s record{where}")
        if named_teams:
            filters["team"] = named_teams[0]   # "most wickets for India" without "for"
            where = _describe_filters(filters, venue_label)

    # Leaderboards
    if not (is_leader or {"runs", "wickets", "wicket", "average", "hundreds", "centuries", "economy"} & set(words)):
        return None
    if bowling:
        order = ("average" if "average" in words else "economy" if "economy" in words
                 else "strike_rate" if "strikerate" in words else "wickets")
    else:
        order = ("average" if "average" in words else
                 "hundreds" if {"hundreds", "hundred", "centuries", "century", "tons", "ton"} & set(words)
                 else "strike_rate" if "strikerate" in words else "runs")
    stat = f"{'bowling ' if bowling and order not in ('wickets', 'average') else ''}{order.replace('_', ' ')}"
    if order == "average":
        stat = "bowling average" if bowling else "batting average"
    rate = order in ("average", "economy", "strike_rate")
    career_order = {"runs": "runs", "wickets": "wickets"}.get(order) or \
        ("bowling_average" if bowling and order == "average" else "batting_average" if order == "average" else None)
    if not filters and career_order and (whole_career or not rate or minimum is None):
        min_matches = minimum[0] if minimum and minimum[1] in ("matches", "tests") else (20 if rate else 1)
        min_wkts = minimum[0] if minimum and minimum[1] == "wickets" else (50 if career_order == "bowling_average" else 0)
        args = {"order_by": career_order, "min_matches": min_matches, "min_wickets": min_wkts, "limit": limit}
        qual = (f" (minimum {min_matches} Tests)" if rate and career_order == "batting_average" else
                f" (minimum {min_wkts} wickets)" if career_order == "bowling_average" else "")
        return Plan("career_leaderboard", [("all_time_careers", args)], f"{stat} in Test history{qual}")
    if bowling:
        min_w = minimum[0] if minimum else (20 if rate else 1)
        args = {"filters": filters, "order_by": order, "min_wickets": min_w, "limit": limit}
        return Plan("leaderboard", [("bowling_leaderboard", args)],
                    f"{'best' if rate else 'most'} {stat}{where}" + (f" (minimum {min_w} wickets)" if rate or minimum else ""))
    min_i = minimum[0] if minimum else (20 if rate else 1)
    args = {"filters": filters, "order_by": order, "min_innings": min_i, "limit": limit}
    return Plan("leaderboard", [("batting_leaderboard", args)],
                f"{'best' if rate else 'most'} {stat}{where}" + (f" (minimum {min_i} innings)" if rate or minimum else ""))


def _describe_filters(f: dict, venue_label: str | None = None) -> str:
    parts = []
    if f.get("team"):
        parts.append(f"for {f['team']}")
    if f.get("opponent"):
        parts.append(f"against {f['opponent']}")
    hosts = f.get("host_countries")
    if hosts:
        parts.append("in Asia" if sorted(hosts) == sorted(ASIA) else "in " + " or ".join(hosts))
    if f.get("venue"):
        parts.append(f"at {venue_label or f['venue']}")
    if f.get("venue_type"):
        parts.append({"home": "at home", "away": "away from home", "neutral": "at neutral venues"}[f["venue_type"]])
    if f.get("innings"):
        parts.append(f"in the {['', '1st', '2nd', '3rd', '4th'][f['innings']]} innings")
    if f.get("date_from") and f.get("date_to") and f["date_from"][:4] == f["date_to"][:4]:
        parts.append(f"in {f['date_from'][:4]}")
    elif f.get("date_from") and f.get("date_to"):
        parts.append(f"from {f['date_from'][:4]} to {f['date_to'][:4]}")
    elif f.get("date_from"):
        parts.append(f"since {f['date_from'][:4]}")
    elif f.get("date_to"):
        parts.append(f"up to {f['date_to'][:4]}")
    return (" " + " ".join(parts)) if parts else ""


SCORECARD_NOTE = "in Tests with scorecards (mostly since 2001)"


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "-"
    if isinstance(v, numbers.Integral):
        return f"{int(v):,}"
    if isinstance(v, numbers.Real):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return str(v)


def answer(con, question: str) -> guru.Answer | None:
    """Answer a question without AI, or None if the parser doesn't understand it."""
    plan = parse(con, question)
    if plan is None:
        return None
    steps = []
    for tool, args in plan.calls:
        step = guru.Step(tool, args)
        try:
            step.table = guru.run_tool(con, tool, args)
        except guru.ToolInputError as e:
            step.error = str(e)
            return None
        steps.append(step)
    t = steps[-1].table
    if t is None or t.empty:
        return guru.Answer(f"I understood this as **{plan.describe}**, but found no matching innings or "
                           f"matches{'' if plan.kind in ('team', 'h2h') else ' ' + SCORECARD_NOTE}.",
                           steps)
    head = f"Understood as **{plan.describe}**."
    if plan.kind == "career_leaderboard":
        r = t.iloc[0]
        col = plan.calls[0][1]["order_by"]
        text = f"{head} {r.player} leads with {_fmt(r[col])} (whole careers, every Test since 1877)."
    elif plan.kind == "leaderboard":
        r, col = t.iloc[0], plan.calls[0][1]["order_by"]
        text = f"{head} {r.player} ({r.team}) leads with {_fmt(r[col])}, {SCORECARD_NOTE}."
    elif plan.kind == "player":
        r = t.iloc[0]
        is_bowling = plan.calls[-1][0] == "player_bowling"
        played = r.innings_bowled if is_bowling else r.innings
        if is_bowling:
            body = f"{_fmt(r.wickets)} wickets at {_fmt(r.average)} in {_fmt(r.innings_bowled)} innings"
        else:
            body = (f"{_fmt(r.runs)} runs at {_fmt(r.average)} in {_fmt(r.innings)} innings, "
                    f"{_fmt(r.hundreds)} hundreds")
        scorecard = (f"In Tests with scorecards (mostly since 2001): {body}." if played else
                     "No matching innings in Tests with scorecards (mostly since 2001); filtered player "
                     "stats from before 2001 aren't available yet.")
        career = steps[0].table if len(steps) == 2 else None
        if career is not None and not career.empty:
            key = "wickets" if is_bowling else "runs"
            avg = "bowling_average" if is_bowling else "batting_average"
            total = int(career[key].sum()) if career[key].notna().any() else None
            whole = f"Whole career (every Test): {_fmt(total)} {key}" + \
                (f" at {_fmt(career[avg].iloc[0])}" if len(career) == 1 else "") + "."
            text = f"{head} {whole}" + (f" {scorecard}" if played else "")
        else:
            text = f"{head} {scorecard}"
    elif plan.kind == "team":
        text = f"{head} Every Test since 1877 that matches is counted."
        opp = plan.calls[0][1]["filters"].get("opponent")
        if opp and len(t) == 1:
            r = t.iloc[0]
            text = (f"{head} Played {_fmt(r.played)}, won {_fmt(r.won)}, lost {_fmt(r.lost)}, drawn "
                    f"{_fmt(r.drawn)} (win rate {_fmt(r.win_pct)}%), over every Test since 1877 that matches.")
    elif plan.kind == "h2h":
        res = t.result.value_counts()
        a, b = plan.calls[0][1]["team_a"], plan.calls[0][1]["team_b"]
        text = (f"{head} {a} won {int(res.get(a, 0))}, {b} won {int(res.get(b, 0))}, drawn {int(res.get('draw', 0))} "
                f"in the {len(t)} most recent matching Tests shown.")
    else:
        text = f"{head} Results are {SCORECARD_NOTE}."
    return guru.Answer(text, steps)
