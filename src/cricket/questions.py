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
# Grounds go by several names in the sources; "|" separates the names searched for
VENUE_ALIASES = {"mcg": "melbourne cricket ground", "scg": "sydney cricket ground",
                 "gabba": "gabba|brisbane cricket ground",
                 "oval": "kennington oval|the oval", "kennington oval": "kennington oval|the oval",
                 "kotla": "kotla|arun jaitley", "feroz shah kotla": "kotla|arun jaitley",
                 "arun jaitley": "kotla|arun jaitley", "chepauk": "chidambaram",
                 "motera": "sardar patel|narendra modi|gujarat stadium",
                 "narendra modi": "narendra modi|sardar patel|gujarat stadium",
                 "eden gardens": "eden garden", "ssc": "sinhalese|singhalese",
                 "sinhalese sports club": "sinhalese|singhalese", "p sara": "sara oval|saravanamuttu",
                 "supersport park": "supersport park|centurion park"}
# "most runs by an Indian batter": nationality words for the player's side
NATIONALITIES = {"australian": "Australia", "english": "England", "indian": "India",
                 "pakistani": "Pakistan", "south african": "South Africa", "sri lankan": "Sri Lanka",
                 "new zealander": "New Zealand", "west indian": "West Indies",
                 "bangladeshi": "Bangladesh", "zimbabwean": "Zimbabwe", "afghan": "Afghanistan",
                 "irish": "Ireland"}
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
cricket cricketer cricketers batter batters international internationals
create make made hit hits hitting taken scores give gave so what whats which there
indepth detailed detail details full breakdown break down analysis analyse analyze analytical
profile deep dive thorough comprehensive complete summary overview look into
ducks duck sixes six fours four fifties fifty strikerate conversion dismissed dismissals dismissal
out got how often way ways usually mostly
each every opponent opponents opposition team teams country countries nation nations host
ground grounds venue venues stadium stadiums season seasons calendar
""".split())
BATTING_WORDS = {"runs", "batting", "bat", "bats", "batted", "batter", "batsman", "hundreds", "hundred",
                 "centuries", "century", "fifties", "fifty", "ducks", "sixes", "fours", "highest",
                 "scored", "score", "scores", "dismissals", "dismissed"}
REPORT_WORDS = {"indepth", "detailed", "detail", "details", "breakdown", "analysis", "analyse",
                "analyze", "analytical", "profile", "deep", "thorough", "comprehensive", "complete",
                "overview", "full"}
# Tests between two sides, by series name
SERIES = {"ashes": ("England", "Australia"), "border gavaskar": ("India", "Australia"),
          "frank worrell": ("Australia", "West Indies"), "trans tasman": ("Australia", "New Zealand"),
          "anderson tendulkar": ("England", "India"), "pataudi": ("England", "India"),
          "wisden trophy": ("England", "West Indies"), "richards botham": ("England", "West Indies"),
          "basil doliveira": ("England", "South Africa"), "d oliveira": ("England", "South Africa")}
SPLITS = [  # (pattern on the question, breakdown)
    (r"\b(?:by|each|every|per|all) (?:calendar )?(?:year|season)s?\b|\byear by year\b", "year"),
    (r"\b(?:by|each|every|per) innings\b|\binnings by innings\b", "innings"),
    (r"\b(?:by|each|every|per|all|different|v|vs|against) (?:opponents?|opposition|teams?|sides?)\b", "opponent"),
    (r"\b(?:by|each|every|per|all|different) (?:host )?(?:country|countries|nations?)\b", "host_country"),
    (r"\b(?:by|each|every|per|all|different) (?:grounds?|venues?|stadiums?)\b", "ground"),
    (r"\bhome (?:and|or|v|vs) away\b", "home_away"),
]
FOCUS = [  # (words, stat key, leaderboard order for batting or bowling)
    ({"ducks", "duck"}, "ducks", "ducks"),
    ({"sixes", "six"}, "sixes", "sixes"),
    ({"fours", "four"}, "fours", None),
    ({"fifties", "fifty"}, "fifties", "fifties"),
    ({"hundreds", "hundred", "centuries", "century", "tons", "ton"}, "hundreds", "hundreds"),
    ({"highest"}, "highest", None),
    ({"strikerate"}, "strike_rate", "strike_rate"),
    ({"fivefors", "fivefor"}, "five_wicket_hauls", "five_wicket_hauls"),
    ({"economy"}, "economy", "economy"),
    ({"dismissed", "dismissals", "dismissal", "out"}, "dismissals", None),
    ({"average", "averages", "avg"}, "average", "average"),
]
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
    focus: str | None = None        # the stat asked about, e.g. "ducks", "hundreds"


def _norm(text: str) -> str:
    text = text.lower().replace("’", "'")
    text = re.sub(r"'s\b", "", text)          # possessives: "root's" -> "root"
    text = text.replace("'", "")              # "lord's" (already "lord") and stray quotes
    text = re.sub(r"five[- ]for", "fivefor", text)
    text = re.sub(r"\b(?:5|five)[- ]?(?:wkt|wicket)s?[- ]?hauls?|\bfifers?\b|\b5wi\b|\b5 ?fors?\b", "fivefors", text)
    text = re.sub(r"\bin[- ]depth\b", "indepth", text)
    text = re.sub(r"\bdeep[- ]dive\b", "indepth", text)
    text = re.sub(r"\bd[' ]?oliveira\b", "doliveira", text)
    text = re.sub(r"\bborder[- ]gavaskar\b", "border gavaskar", text)
    text = re.sub(r"\btrans[- ]tasman\b", "trans tasman", text)
    text = re.sub(r"\byear[- ]by[- ]year\b", "year by year", text)
    text = re.sub(r"\bhow (?:does|did|do) .{1,40}? (?:get|got|gets) out\b",
                  lambda m: m[0] + " dismissals", text)
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
        for alt in key.split("|"):
            row = con.execute(
                """SELECT CASE WHEN lower(replace(city, '''', '')) LIKE ? THEN city ELSE venue END
                   FROM matches
                   WHERE lower(replace(venue, '''', '')) LIKE ? OR lower(replace(city, '''', '')) LIKE ?
                   ORDER BY length(venue) LIMIT 1""",
                [f"%{alt}%"] * 3).fetchone()
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


def _player_label(found) -> str:
    """Full name where the register has one ("Steve Smith"), else the scorecard name ("SPD Smith")."""
    full = found.full_name.iloc[0] if "full_name" in found else None
    return full if isinstance(full, str) and full else found.player.iloc[0]


def _focus(words: set[str]) -> str | None:
    for vocab, key, _ in FOCUS:
        if vocab & words:
            return key
    return None


def _is_bowler(con, player_id: str) -> bool:
    """A specialist bowler: at least 50 Test wickets and a batting average under 25."""
    c = stats.career_totals(con, player_id)
    if c.empty:
        return False
    wkts = c.wickets.sum() if c.wickets.notna().any() else 0
    avg = c.batting_average.iloc[0] if len(c) == 1 else None
    return bool(wkts >= 50 and (avg is None or avg != avg or avg < 25))


def parse(con, question: str) -> Plan | None:
    raw = question.strip()
    text = " " + _norm(raw) + " "
    filters: dict = {}

    for key, pair in SERIES.items():
        m, text = _take(text, rf"\b(?:in |during )?(?:the )?{key}(?: series| trophy| tests?)?\b")
        if m:
            filters["between"] = list(pair)
            break

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

    split = None
    for pattern, by in SPLITS:
        m, text = _take(text, pattern)
        if m:
            split = by
            break

    m, text = _take(text, r"\b(at home|home|away from home|away|overseas|abroad|neutral venues?|neutral)\b")
    if m:
        filters["venue_type"] = ("home" if m[1] in ("at home", "home") else
                                 "neutral" if m[1].startswith("neutral") else "away")

    for adj, side in NATIONALITIES.items():
        m, text = _take(text, rf"\b(?:an? )?{adj}s?\b")
        if m:
            filters.setdefault("team", side)
            break
    m, text = _take(text, r"\b(?:best|top) (?:bowling )?figures\b|\b(?:highest|biggest|best) (?:individual )?(?:scores|score|innings)\b")
    top_kind = None if not m else ("bowling" if "figures" in m[0] else "batting")
    if top_kind:
        _, text = _take(text, r"\bin (?:a|an|one|single) (?:innings|test|match)\b")
    if re.search(r"\bin (?:a|an|one|single|the same) (?:calendar )?(?:year|season|series|match|test|innings)\b", text):
        return None   # records within one year, series or match: no tool for those yet

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
    wordset = set(words)
    bowling = bool(BOWLING_WORDS & wordset) or "fivefors" in text
    is_leader = bool(LEADER_WORDS & wordset) or limit_given
    whole_career = bool({"career", "ever", "overall", "whole"} & wordset) or "all time" in text
    report = bool(REPORT_WORDS & wordset)
    focus = _focus(wordset)
    if focus in ("five_wicket_hauls", "economy"):
        bowling = True
    batting_words = bool(BATTING_WORDS & wordset)

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
        if found.empty and " " in chunk:
            # "jimmy anderson": a nickname for the first name is fine if the surname is exact
            found = stats.find_players(con, chunk, limit=1, min_score=0.85)
            names = set(stats._norm(found.player.iloc[0]).split()) | \
                set(stats._norm(str(found.full_name.iloc[0])).split()) if not found.empty else set()
            if chunk.split()[-1] not in names:
                found = found.iloc[0:0]
        if found.empty:
            return None  # a word we don't understand: let the AI model or the person rephrase
        name_words = stats._norm(found.player.iloc[0]).split() + \
            stats._norm(str(found.full_name.iloc[0] or "")).split()
        if len(chunk) < 4 and chunk not in name_words:
            return None  # "mcg" is a prefix of "mcgrath": too short to trust a partial match
        if " " not in chunk and chunk not in name_words and any(
                w.startswith(chunk) and len(chunk) < 0.75 * len(w) for w in name_words):
            return None  # "weather" is the start of "Weatherald": an ordinary word, not a name
        players.append((found.player_id.iloc[0], _player_label(found)))
    players = list(dict.fromkeys(players))

    if players and named_teams:
        return None  # "England's record at the MCG" read as a player: too unsure
    if top_kind and players:
        if top_kind == "batting":
            focus = "highest"
        else:
            bowling, focus = True, None
        top_kind = None
    if players and not bowling and not batting_words and focus in (None, "average", "strike_rate") \
            and all(_is_bowler(con, p) for p, _ in players):
        bowling = True   # "Lyon's record at the SCG", "Rabada strike rate"
    where = _describe_filters(filters, venue_label)
    # Players
    if len(players) == 1:
        pid, name = players[0]
        if split:
            tool = "player_bowling_split" if bowling else "player_batting_split"
            return Plan("split", [(tool, {"player_id": pid, "by": split, "filters": filters})],
                        f"{name}'s {'bowling' if bowling else 'batting'} by {SPLIT_LABELS[split]}{where}", [name])
        if report:
            return Plan("report", _report_calls(pid, filters), f"an in-depth look at {name}{where}", [name])
        if focus == "dismissals" and not bowling:
            return Plan("dismissals", [("player_dismissals", {"player_id": pid, "filters": filters})],
                        f"how {name} got out{where}", [name], focus)
        tool = "player_bowling" if bowling else "player_batting"
        calls = [(tool, {"player_id": pid, "filters": filters})]
        if not filters:
            calls.insert(0, ("player_career_totals", {"player_id": pid}))
        return Plan("player", calls, f"{name}'s {'bowling' if bowling else 'batting'}{where}", [name], focus)
    if 2 <= len(players) <= 4:
        if len(filters.get("host_countries", [])) > 1 and "asia" not in text and not dates:
            return None  # "Root in Australia v Smith in England": different filters per player
        ids = [p for p, _ in players]
        calls = [("compare_players", {"player_ids": ids, "filters": filters})]
        if report or split:
            by = split or ("host_country" if filters.get("venue_type") == "away" or not filters
                           else "opponent" if not filters.get("opponent") else "year")
            tool = "player_bowling_split" if bowling else "player_batting_split"
            for pid in ids:
                calls.append((tool, {"player_id": pid, "by": by, "filters": filters}))
                if report and not bowling:
                    calls.append(("player_dismissals", {"player_id": pid, "filters": filters}))
        label = ", ".join(n for _, n in players[:-1]) + f" and {players[-1][1]}"
        return Plan("compare", calls,
                    f"{'an in-depth comparison of ' if report else ''}{label}"
                    f"{'' if report else ' compared'}{' (bowling)' if bowling else ''}{where}",
                    [n for _, n in players], focus)
    if len(players) > 4:
        return None

    if top_kind:
        return Plan("top_innings", [("best_innings", {"kind": top_kind, "filters": filters, "limit": limit})],
                    f"{'best bowling figures' if top_kind == 'bowling' else 'highest scores'} in an innings{where}")
    if {"team", "teams", "side", "sides", "nation", "nations", "country", "countries"} & wordset and is_leader:
        return None   # "which team has won the most Tests in India": no team leaderboards yet

    # Teams
    if named_teams or ("opponent" in filters and {"record", "results", "won", "lost", "h2h"} & wordset) \
            or ("between" in filters and not is_leader and not focus):
        if "between" in filters and not named_teams:
            named_teams = list(filters.pop("between"))
            vs_pair = True
        wants_h2h = "h2h" in words or (vs_pair and not {"record", "records"} & wordset) or \
            (len(named_teams) == 2 and not is_leader)
        if wants_h2h:
            pair = named_teams[:2] if len(named_teams) >= 2 else named_teams[:1] + [filters.get("opponent")]
            if len(pair) == 2 and all(pair):
                rest = {k: v for k, v in filters.items() if k != "opponent"}
                return Plan("h2h", [("head_to_head", {"team_a": pair[0], "team_b": pair[1], "filters": rest,
                                                      "limit": limit if limit_given else guru.MAX_ROWS})],
                            f"{pair[0]} v {pair[1]} head to head{_describe_filters(rest, venue_label)}")
        if named_teams and not (is_leader and (bowling or focus or {"runs", "average"} & wordset)):
            team = named_teams[0]
            return Plan("team", [("team_record", {"team": team, "filters": filters})], f"{team}'s record{where}")
        if named_teams:
            filters["team"] = named_teams[0]   # "most wickets for India" without "for"
            where = _describe_filters(filters, venue_label)

    # Leaderboards
    if not (is_leader or focus or {"runs", "wickets", "wicket"} & wordset):
        return None
    if bowling:
        order = next((o for v, k, o in FOCUS if o in stats.BOWLING_ORDER and v & wordset), None) or "wickets"
        if focus == "strike_rate" or "strikerate" in wordset:
            order = "strike_rate"
    else:
        order = next((o for v, k, o in FOCUS if o in stats.BATTING_ORDER and v & wordset), None) or "runs"
    if order not in (stats.BOWLING_ORDER if bowling else stats.BATTING_ORDER):
        return None
    stat = {"strike_rate": "strike rate", "five_wicket_hauls": "five-wicket hauls"}.get(order, order)
    if bowling and order in ("strike_rate", "economy"):
        stat = f"bowling {stat}"
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
    lowest_best = rate and bowling
    if bowling:
        min_w = minimum[0] if minimum else (20 if rate else 1)
        args = {"filters": filters, "order_by": order, "min_wickets": min_w, "limit": limit}
        return Plan("leaderboard", [("bowling_leaderboard", args)],
                    f"{'best' if lowest_best or order == 'average' else 'most'} {stat}{where}"
                    + (f" (minimum {min_w} wickets)" if rate or minimum else ""))
    min_i = minimum[0] if minimum else (20 if rate else 1)
    args = {"filters": filters, "order_by": order, "min_innings": min_i, "limit": limit}
    return Plan("leaderboard", [("batting_leaderboard", args)],
                f"{'best' if rate else 'most'} {stat}{where}" + (f" (minimum {min_i} innings)" if rate or minimum else ""))


SPLIT_LABELS = {"year": "year", "innings": "innings of the match", "home_away": "home and away",
                "opponent": "opponent", "host_country": "host country", "ground": "ground"}


def _report_calls(pid: str, filters: dict) -> list[tuple[str, dict]]:
    """Lookups for an in-depth look at one player."""
    calls = [] if filters else [("player_career_totals", {"player_id": pid})]
    calls += [("player_batting", {"player_id": pid, "filters": filters}),
              ("player_bowling", {"player_id": pid, "filters": filters})]
    for by in ("year", "home_away", "host_country", "opponent", "innings"):
        if by == "home_away" and filters.get("venue_type"):
            continue
        calls.append(("player_batting_split", {"player_id": pid, "by": by, "filters": filters}))
    calls.append(("player_dismissals", {"player_id": pid, "filters": filters}))
    return calls


def _describe_filters(f: dict, venue_label: str | None = None) -> str:
    parts = []
    if f.get("between"):
        parts.append(f"in {' v '.join(f['between'])} Tests")
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


def _num(v):
    """A plain float or None from a table cell (NaN, NA and None become None)."""
    try:
        return None if v is None or v != v else float(v)
    except (TypeError, ValueError):
        return None


def _bat_line(r) -> str:
    """'1,146 runs at 81.86 in 17 innings (5 hundreds, 4 fifties, highest 239, strike rate 63.4)'."""
    extra = [f"{_fmt(r.get('hundreds'))} hundreds", f"{_fmt(r.get('fifties'))} fifties"]
    if r.get("highest") not in (None, "-"):
        extra.append(f"highest {r.get('highest')}")
    if _num(r.get("strike_rate")) is not None:
        extra.append(f"strike rate {_fmt(r.get('strike_rate'))}")
    return (f"{_fmt(r.get('runs'))} runs at {_fmt(r.get('average'))} in {_fmt(r.get('innings'))} innings"
            f" ({', '.join(extra)})")


def _bowl_line(r) -> str:
    fives = _num(r.get("five_wicket_hauls")) or 0
    extra = [f"best {r.get('best_innings')}", f"{_fmt(fives)} five-wicket haul{'' if fives == 1 else 's'}"]
    if _num(r.get("economy")) is not None:
        extra.append(f"economy {_fmt(r.get('economy'))}")
    if _num(r.get("strike_rate")) is not None:
        extra.append(f"strike rate {_fmt(r.get('strike_rate'))}")
    return (f"{_fmt(r.get('wickets'))} wickets at {_fmt(r.get('average'))} in "
            f"{_fmt(r.get('innings_bowled'))} innings ({', '.join(extra)})")


FOCUS_TEXT = {  # focus -> (summary key, words)
    "ducks": ("ducks", "ducks"), "sixes": ("sixes", "sixes"), "fours": ("fours", "fours"),
    "fifties": ("fifties", "fifties"), "hundreds": ("hundreds", "hundreds"),
    "highest": ("highest", "highest score"), "strike_rate": ("strike_rate", "strike rate"),
    "average": ("average", "average"), "five_wicket_hauls": ("five_wicket_hauls", "five-wicket hauls"),
    "economy": ("economy", "economy rate"),
}
CAREER_FOCUS = {"hundreds": "hundreds", "fifties": "fifties", "highest": "high_score",
                "average": "batting_average", "five_wicket_hauls": "five_wickets"}
CAREER_FOCUS_BOWL = {"average": "bowling_average", "five_wicket_hauls": "five_wickets"}


def _focus_sentence(focus: str | None, r, is_bowling: bool) -> str:
    if focus not in FOCUS_TEXT:
        return ""
    key, label = FOCUS_TEXT[focus]
    if key not in r:
        return ""
    v = r.get(key)
    if v is None or (isinstance(v, float) and v != v):
        return f"The scorecards don't record {label} for these innings. "
    return f"**{label.capitalize()}: {_fmt(v)}**. "


def _label(v) -> str:
    """A table key for text: 2017.0 -> "2017"."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _best_worst(t, key: str, label: str, min_inns: int = 5) -> str:
    """'best in England (avg 62.1, 20 inns), weakest in India (avg 25.3, 9 inns)'."""
    if t is None or t.empty or "average" not in t:
        return ""
    t = t[(t.innings >= min_inns) & t.average.notna()]
    if len(t) < 2:
        return ""
    hi, lo = t.loc[t.average.idxmax()], t.loc[t.average.idxmin()]
    return (f"best {label} {_label(hi[key])} (average {_fmt(hi.average)}, {_fmt(hi.innings)} innings), "
            f"weakest {label} {_label(lo[key])} (average {_fmt(lo.average)}, {_fmt(lo.innings)} innings)")


def _player_text(plan: Plan, steps: list) -> str:
    head = f"Understood as **{plan.describe}**."
    t = steps[-1].table
    r = t.iloc[0]
    is_bowling = plan.calls[-1][0] == "player_bowling"
    played = r.innings_bowled if is_bowling else r.innings
    career = steps[0].table if len(steps) == 2 else None
    if career is not None and not career.empty:
        key = "wickets" if is_bowling else "runs"
        avg = "bowling_average" if is_bowling else "batting_average"
        total = int(career[key].sum()) if career[key].notna().any() else None
        whole = f"Whole career (every Test): {_fmt(total)} {key}" + \
            (f" at {_fmt(career[avg].iloc[0])}" if len(career) == 1 else "")
        col = (CAREER_FOCUS_BOWL if is_bowling else CAREER_FOCUS).get(plan.focus or "")
        if col and len(career) == 1 and col in career and _num(career[col].iloc[0]) is not None or \
                (col == "high_score" and len(career) == 1 and career[col].iloc[0] is not None):
            whole += f", {FOCUS_TEXT[plan.focus][1]} **{_fmt(career[col].iloc[0])}**"
        whole += "."
        if not played:
            return f"{head} {whole}"
        body = _bowl_line(r) if is_bowling else _bat_line(r)
        return (f"{head} {whole} In Tests with scorecards (mostly since 2001): "
                f"{_focus_sentence(plan.focus, r, is_bowling)}{body}.")
    if not played:
        return (f"{head} No matching innings in Tests with scorecards (mostly since 2001); filtered "
                "player stats from before 2001 aren't available yet.")
    body = _bowl_line(r) if is_bowling else _bat_line(r)
    return (f"{head} In Tests with scorecards (mostly since 2001): "
            f"{_focus_sentence(plan.focus, r, is_bowling)}{body}.")


def _compare_text(plan: Plan, steps: list) -> str:
    head = f"Understood as **{plan.describe}**."
    t = steps[0].table.set_index("stat")
    if len(plan.players) == len(t.columns):
        t.columns = plan.players   # full names, in the order the ids were given
    names = list(t.columns)
    bowling = "(bowling)" in plan.describe or plan.focus in ("five_wicket_hauls", "economy")
    lines = []
    for n in names:
        c = t[n]
        bat = {k[4:]: c[k] for k in c.index if k.startswith("bat_")}
        bowl = {k[5:]: c[k] for k in c.index if k.startswith("bowl_")}
        parts = []
        if _num(bat.get("innings")) and not bowling:
            parts.append(_bat_line(bat))
        if _num(bowl.get("wickets")) and (bowling or (_num(bowl.get("wickets")) or 0) >= 20):
            parts.append(_bowl_line(bowl))
        career = ""
        if _num(c.get("career_matches")):
            career = (f" Whole career: {_fmt(c.get('career_matches'))} Tests, {_fmt(c.get('career_runs'))} runs at "
                      f"{_fmt(c.get('career_batting_average'))}"
                      + (f", {_fmt(c.get('career_wickets'))} wickets at {_fmt(c.get('career_bowling_average'))}"
                         if (_num(c.get('career_wickets')) or 0) >= 20 else "") + ".")
        lines.append(f"- **{n}**: " + ("; ".join(parts) if parts else "no matching innings") + "." + career)
    # who leads
    notes = []
    key, label, low = (("bowl_average", "bowling average", True) if bowling else ("bat_average", "average", False))
    vals = {n: _num(t.loc[key, n]) for n in names if key in t.index and _num(t.loc[key, n]) is not None}
    if len(vals) >= 2:
        best = (min if low else max)(vals, key=vals.get)
        notes.append(f"{best} has the {'lowest' if low else 'highest'} {label} ({_fmt(vals[best])})")
    if not bowling and "bat_hundreds" in t.index:
        rates = {}
        for n in names:
            h, f, i = (_num(t.loc[k, n]) for k in ("bat_hundreds", "bat_fifties", "bat_innings"))
            if i:
                rates[n] = (h or 0, f or 0, i)
        if len(rates) >= 2:
            conv = {n: h / (h + f) for n, (h, f, i) in rates.items() if h + f}
            if conv:
                best = max(conv, key=conv.get)
                notes.append(f"{best} turns the most fifties into hundreds "
                             f"({100 * conv[best]:.0f}% of 50+ scores are hundreds)")
            per = {n: i / h for n, (h, f, i) in rates.items() if h}
            if per:
                best = min(per, key=per.get)
                notes.append(f"{best} makes a hundred most often (one every {per[best]:.1f} innings)")
    extra = []
    # per-player split highlights and dismissals
    by_player: dict[str, list[str]] = {}
    ids = plan.calls[0][1]["player_ids"]
    id_to_name = dict(zip(ids, names))
    for (tool, args), s in zip(plan.calls[1:], steps[1:]):
        n = id_to_name.get(args["player_id"], args["player_id"])
        if tool == "player_batting_split":
            label = {"host_country": "in", "opponent": "against", "ground": "at", "year": "in"}.get(args["by"])
            if label:
                bw = _best_worst(s.table, stats.BREAKDOWNS[args["by"]], label)
                if bw:
                    by_player.setdefault(n, []).append(bw)
        elif tool == "player_dismissals" and s.table is not None and len(s.table):
            top = s.table.iloc[0]
            by_player.setdefault(n, []).append(f"most often out {top.how_out} ({_fmt(top.percent)}%)")
    for n, items in by_player.items():
        extra.append(f"- **{n}**: " + "; ".join(items) + ".")
    text = f"{head} In Tests with scorecards (mostly since 2001):\n\n" + "\n".join(lines)
    if notes:
        text += "\n\n" + ". ".join(x[0].upper() + x[1:] for x in notes) + "."
    if extra:
        text += "\n\nBreakdowns (minimum 5 innings):\n\n" + "\n".join(extra)
    if len(steps) > 1:
        text += "\n\nThe full tables are under the lookups below."
    return text


def _report_text(plan: Plan, steps: list) -> str:
    head = f"Understood as **{plan.describe}**."
    tables = {(tool, args.get("by")): s.table for (tool, args), s in zip(plan.calls, steps)}
    out = [head]
    career = tables.get(("player_career_totals", None))
    if career is not None and not career.empty and len(career) == 1:
        c = career.iloc[0]
        out.append(f"Whole career (every Test): {_fmt(c.matches)} Tests, {_fmt(c.runs)} runs at "
                   f"{_fmt(c.batting_average)}, {_fmt(c.hundreds)} hundreds, highest {_fmt(c.high_score)}"
                   + (f"; {_fmt(c.wickets)} wickets at {_fmt(c.bowling_average)}" if (_num(c.wickets) or 0) else "")
                   + ".")
    bat = tables[("player_batting", None)].iloc[0]
    bowl = tables[("player_bowling", None)].iloc[0]
    if not bat.innings and not bowl.innings_bowled:
        out.append("No matching innings in Tests with scorecards (mostly since 2001).")
        return " ".join(out)
    lines = []
    if bat.innings:
        lines.append(f"- **Batting**: {_bat_line(bat)}.")
    if bowl.innings_bowled and bowl.wickets:
        lines.append(f"- **Bowling**: {_bowl_line(bowl)}.")
    ha = tables.get(("player_batting_split", "home_away"))
    if ha is not None and len(ha):
        lines.append("- **Home and away**: " + ", ".join(
            f"{r.venue_type} {_fmt(r.runs)} runs at {_fmt(r.average)}" for r in ha.itertuples()) + ".")
    for by, label in (("host_country", "in"), ("opponent", "against"), ("year", "in")):
        bw = _best_worst(tables.get(("player_batting_split", by)), stats.BREAKDOWNS[by], label)
        if bw:
            lines.append(f"- **By {SPLIT_LABELS[by]}** (minimum 5 innings): {bw}.")
    inn = tables.get(("player_batting_split", "innings"))
    if inn is not None and len(inn):
        lines.append("- **By innings of the match**: " + ", ".join(
            f"{['', '1st', '2nd', '3rd', '4th'][int(r.match_innings)]} {_fmt(r.average)}" for r in inn.itertuples()) + ".")
    dis = tables.get(("player_dismissals", None))
    if dis is not None and len(dis):
        lines.append("- **Dismissals**: " + ", ".join(
            f"{r.how_out} {_fmt(r.percent)}%" for r in dis.head(4).itertuples()) + ".")
    return (" ".join(out) + " In Tests with scorecards (mostly since 2001):\n\n" + "\n".join(lines)
            + "\n\nThe full tables are under the lookups below.")


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
    t = steps[0].table if plan.kind == "compare" else steps[-1].table
    if plan.kind != "report" and (t is None or t.empty):
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
        text = _player_text(plan, steps)
    elif plan.kind == "compare":
        text = _compare_text(plan, steps)
    elif plan.kind == "report":
        text = _report_text(plan, steps)
    elif plan.kind == "top_innings":
        r = t.iloc[0]
        val = r.figures if "figures" in t else r.score
        text = (f"{head} {r.player} ({r.team}) {val} against {r.opponent} at {r.ground} on "
                f"{str(r.start_date)[:10]}, {SCORECARD_NOTE}.")
    elif plan.kind == "dismissals":
        top = t.iloc[0]
        text = (f"{head} Most often {top.how_out}: {_fmt(top.times)} of {_fmt(t.times.sum())} dismissals "
                f"({_fmt(top.percent)}%), {SCORECARD_NOTE}.")
    elif plan.kind == "split":
        by = plan.calls[0][1]["by"]
        bowl = plan.calls[0][0] == "player_bowling_split"
        if bowl:
            best = t.loc[t.wickets.idxmax()]
            text = (f"{head} Most wickets {'in' if by in ('year', 'host_country') else 'against' if by == 'opponent' else 'for'} "
                    f"{_label(best[stats.BREAKDOWNS[by]])}: {_fmt(best.wickets)} at {_fmt(best.average)}, {SCORECARD_NOTE}.")
        else:
            best = t.loc[t.runs.idxmax()]
            label = {"opponent": "against", "ground": "at", "innings": "in innings", "home_away": ""}.get(by, "in")
            text = (f"{head} Most runs {label} {_label(best[stats.BREAKDOWNS[by]])}: {_fmt(best.runs)} at {_fmt(best.average)}, "
                    f"{SCORECARD_NOTE}.").replace("  ", " ")
    elif plan.kind == "team":
        text = f"{head} Every Test since 1877 that matches is counted."
        opp = plan.calls[0][1]["filters"].get("opponent")
        if opp and len(t) == 1:
            r = t.iloc[0]
            text = (f"{head} Played {_fmt(r.played)}, won {_fmt(r.won)}, lost {_fmt(r.lost)}, drawn "
                    f"{_fmt(r.drawn)} (win rate {_fmt(r.win_pct)}%), over every Test since 1877 that matches.")
        elif len(t) > 1:
            text = (f"{head} Played {_fmt(t.played.sum())}, won {_fmt(t.won.sum())}, lost {_fmt(t.lost.sum())}, "
                    f"drawn {_fmt(t.drawn.sum())}, over every Test since 1877 that matches. By opponent below.")
    elif plan.kind == "h2h":
        res = t.result.value_counts()
        a, b = plan.calls[0][1]["team_a"], plan.calls[0][1]["team_b"]
        shown = "" if len(t) < plan.calls[0][1]["limit"] else f" (the {len(t)} most recent matching Tests)"
        text = (f"{head} {a} won {int(res.get(a, 0))}, {b} won {int(res.get(b, 0))}, drawn "
                f"{int(res.get('draw', 0))}{shown}.")
    else:
        text = f"{head} Results are {SCORECARD_NOTE}."
    return guru.Answer(text, steps)
