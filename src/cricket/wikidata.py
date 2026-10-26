"""Players' full names from Wikidata, for players the Cricsheet register gives only initials.

Cricsheet's People Register (people.csv) gives each player's ESPNcricinfo id; Wikidata
items carry the same id as property P2697, so one query per batch of ids returns the
item's English label ("Sam Konstas") and birth name, P1477 ("Samuel Konstas").
Only these ids are read; no ESPNcricinfo page is fetched.

Licence: Wikidata's data is CC0 (public domain dedication), so no attribution is required.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import requests

SPARQL_URL = "https://query.wikidata.org/sparql"
PEOPLE_URL = "https://cricsheet.org/register/people.csv"  # register ids with other sites' keys (ODC-BY)
USER_AGENT = ("TestCricketStats/0.1 (personal non-commercial research tool; "
              "https://github.com/camvpereira-netizen/test-cricket-stats)")
BATCH = 200

QUERY = """SELECT ?cid ?label ?birth WHERE {
  VALUES ?cid { %s }
  ?item wdt:P2697 ?cid .
  OPTIONAL { ?item rdfs:label ?label FILTER(lang(?label) = "en") }
  OPTIONAL { ?item wdt:P1477 ?birth FILTER(lang(?birth) = "en") }
}"""


def fetch_names(cricinfo_ids: list[str]) -> pd.DataFrame:
    """cricinfo_id, label, birth_name for every id Wikidata knows (several rows if it has several)."""
    rows = []
    ids = sorted({str(i) for i in cricinfo_ids if str(i).isdigit()})
    for start in range(0, len(ids), BATCH):
        values = " ".join(f'"{i}"' for i in ids[start:start + BATCH])  # digits only, checked above
        r = requests.post(SPARQL_URL, data={"query": QUERY % values},
                          headers={"User-Agent": USER_AGENT,
                                   "Accept": "application/sparql-results+json"}, timeout=60)
        r.raise_for_status()
        for b in r.json()["results"]["bindings"]:
            rows.append({"cricinfo_id": b["cid"]["value"], "label": b.get("label", {}).get("value"),
                         "birth_name": b.get("birth", {}).get("value")})
    return pd.DataFrame(rows, columns=["cricinfo_id", "label", "birth_name"])


def names_for_register(people: pd.DataFrame, wanted: set[str], found: pd.DataFrame) -> pd.DataFrame:
    """Rows (player_id, name) to add to player_names: label first, then birth name.

    people is Cricsheet's people.csv (identifier, key_cricinfo); wanted holds register ids.
    """
    keys = people[people.identifier.isin(wanted)][["identifier", "key_cricinfo"]].dropna()
    keys = keys.astype(str).assign(key_cricinfo=lambda d: d.key_cricinfo.str.replace(r"\.0$", "", regex=True))
    merged = keys.merge(found, left_on="key_cricinfo", right_on="cricinfo_id")
    out = pd.concat([merged[["identifier", "label"]].set_axis(["player_id", "name"], axis=1),
                     merged[["identifier", "birth_name"]].set_axis(["player_id", "name"], axis=1)])
    return out.dropna().drop_duplicates().reset_index(drop=True)


def add_full_names(con, people_csv: Path) -> int:
    """Add Wikidata names to player_names for every player in the matches. Returns rows added."""
    people = pd.read_csv(people_csv, dtype=str)
    wanted = {r[0] for r in con.execute(
        "SELECT DISTINCT player_id FROM appearances WHERE player_id IS NOT NULL").fetchall()}
    ids = people[people.identifier.isin(wanted)].key_cricinfo.dropna()
    names = names_for_register(people, wanted, fetch_names(ids.tolist()))
    return insert_names(con, names)


def insert_names(con, names: pd.DataFrame) -> int:
    """Append names after the register's own (so the register's full name still comes first)."""
    if names.empty:
        return 0
    start = con.execute("SELECT coalesce(max(position), -1) + 1 FROM player_names").fetchall()[0][0]
    names = names.assign(position=range(start, start + len(names)))
    before = con.execute("SELECT count(*) FROM player_names").fetchall()[0][0]
    con.register("tmp_df", names)
    con.execute("""INSERT INTO player_names BY NAME
                   SELECT t.* FROM tmp_df t ANTI JOIN player_names p USING (player_id, name)""")
    con.unregister("tmp_df")
    return con.execute("SELECT count(*) FROM player_names").fetchall()[0][0] - before
