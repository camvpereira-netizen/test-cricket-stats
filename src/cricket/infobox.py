"""Test figures from each player's Wikipedia article ({{Infobox cricketer}}), to fill list gaps.

The per-team lists in careers.py lack some columns: most have no hundreds/fifties or
five-wicket hauls, England's has no innings or runs conceded, India's no balls. A player's
article infobox has a "Test" column with matches, runs, average, 100s/50s, top score,
deliveries, wickets, bowling average, 5 wickets in an innings, 10 in a match, best bowling and
catches/stumpings. Not innings, not outs, balls faced, fours, sixes or best in a match.

An infobox is only used where its matches and runs equal the list row's, so a stale infobox
or one adding up two teams (a player capped by both) is left out rather than mixed in.
Licence: CC BY-SA 4.0, like the lists.
"""
from __future__ import annotations

import re

import pandas as pd

from cricket.wikipedia import _strip, prefetch, wikitext

MAX_AGE_HOURS = 24 * 7

# infobox field (without its column number) -> our column
FIELDS = {
    "matches": "matches", "runs": "runs", "bat avg": "batting_average", "100s/50s": "hundreds_fifties",
    "top score": "high_score", "deliveries": "balls", "wickets": "wickets",
    "bowl avg": "bowling_average", "fivefor": "five_wickets", "tenfor": "ten_wickets",
    "best bowling": "best_bowling", "catches/stumpings": "catches_stumpings",
}


def _infobox(text: str) -> str | None:
    """The body of the {{Infobox cricketer ...}} template, nested templates included."""
    start = re.search(r"\{\{\s*Infobox cricketer", text, re.I)
    if not start:
        return None
    depth, i = 0, start.start()
    while i < len(text) - 1:
        pair = text[i:i + 2]
        if pair == "{{":
            depth, i = depth + 1, i + 2
        elif pair == "}}":
            depth, i = depth - 1, i + 2
            if depth == 0:
                return text[start.end():i - 2]
        else:
            i += 1
    return None


def _params(body: str) -> dict[str, str]:
    """Top-level |name = value pairs of a template body."""
    out, depth, cur = {}, 0, ""
    parts = []
    i = 0
    while i < len(body):
        two = body[i:i + 2]
        if two in ("{{", "[["):
            depth += 1
            cur += two
            i += 2
            continue
        if two in ("}}", "]]"):
            depth -= 1
            cur += two
            i += 2
            continue
        if body[i] == "|" and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += body[i]
        i += 1
    parts.append(cur)
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


def _value(raw: str) -> str:
    v = re.sub(r"<!--.*?-->", "", raw, flags=re.S)
    v = _strip(re.sub(r"\{\{(?:nowrap|nobr|sort)\|(?:[^{}|]*\|)*([^{}|]*)\}\}", r"\1", v))
    return v.replace(",", "").replace("–", "-").replace("—", "-").replace("−", "-").strip()


def _int(v: str | None):
    m = re.fullmatch(r"\d+", (v or "").strip())
    return int(m.group(0)) if m else None


def parse(text: str) -> dict | None:
    """Test figures from an article's infobox, or None if it has no Test column."""
    body = _infobox(text or "")
    if body is None:
        return None
    params = _params(body)
    col = next((k[len("column"):] for k, v in params.items()
                if re.fullmatch(r"column\d+", k) and _value(v).lower().startswith("test")), None)
    if col is None:
        return None
    raw = {ours: _value(params.get(f"{field}{col}", "")) for field, ours in FIELDS.items()}
    out = {k: _int(raw[k]) for k in ("matches", "runs", "balls", "wickets", "five_wickets", "ten_wickets")}
    out["high_score"] = raw["high_score"].replace(" ", "") or None
    out["best_bowling"] = raw["best_bowling"].replace(" ", "") or None
    hf = re.fullmatch(r"(\d+)\s*/\s*(\d+)", raw["hundreds_fifties"])
    out["hundreds"], out["fifties"] = (int(hf.group(1)), int(hf.group(2))) if hf else (None, None)
    cs = re.fullmatch(r"(\d+|-)\s*/\s*(\d+|-)", raw["catches_stumpings"])
    out["catches"], out["stumpings"] = ((_int(cs.group(1)) or 0, _int(cs.group(2)) or 0)
                                        if cs else (None, None))
    return out


def fetch(titles: list[str]) -> dict[str, dict | None]:
    """Parsed infobox for each article title (cached; 50 articles per request)."""
    prefetch(titles, max_age_hours=MAX_AGE_HOURS)
    return {t: parse(wikitext(t, max_age_hours=MAX_AGE_HOURS) or "") for t in titles}


def runs_from_average(average, wickets):
    """Runs conceded when a 2-decimal average and the wickets pin it to one whole number."""
    if pd.isna(average) or pd.isna(wickets) or not wickets:
        return None
    lo, hi = (average - 0.005) * wickets, (average + 0.005) * wickets
    fits = [r for r in range(int(lo), int(hi) + 2) if round(r / wickets, 2) == round(average, 2)]
    return fits[0] if len(fits) == 1 else None


def fill(careers: pd.DataFrame, boxes: dict[str, dict | None]) -> pd.DataFrame:
    """Fill gaps in the list rows from matching infoboxes; never overwrite a list value.

    Adds infobox_used (the infobox agreed with the row on matches and runs). Runs conceded
    missing from a list are worked out from the bowling average where only one total fits.
    """
    df = careers.copy()
    for c in ("five_wickets", "ten_wickets"):
        if c not in df:
            df[c] = pd.array([None] * len(df), dtype="Int64")
    used = []
    for i, r in df.iterrows():
        box = boxes.get(r.wiki_title)
        ok = bool(box) and box["matches"] == r.matches and (
            pd.isna(r.runs) or box["runs"] in (None, r.runs))
        used.append(ok)
        if not ok:
            continue
        for c in ("hundreds", "fifties", "balls", "five_wickets", "ten_wickets", "catches", "stumpings"):
            if pd.isna(df.at[i, c]) and box[c] is not None:
                df.at[i, c] = box[c]
        if pd.isna(df.at[i, "wickets"]) and box["wickets"] is not None:
            df.at[i, "wickets"] = box["wickets"]
    df["infobox_used"] = used
    missing = df.runs_conceded.isna()
    df.loc[missing, "runs_conceded"] = [runs_from_average(a, w) for a, w in
                                        zip(df.bowling_average[missing], df.wickets[missing])]
    df["runs_conceded"] = df.runs_conceded.astype("Int64")
    return df
