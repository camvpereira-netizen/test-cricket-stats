"""Full names from Wikidata, joined through the Cricsheet register's ESPNcricinfo ids.

No network: the Wikidata answer is a hand-written frame standing in for fetch_names.
"""
import duckdb
import pandas as pd
import pytest

from cricket import stats, wikidata
from test_search import NAMES, scon as _scon  # noqa: F401  (reuse the search fixture's match)

PEOPLE = """identifier,name,unique_name,key_cricinfo
id_M Labuschagne,M Labuschagne,M Labuschagne,787987
id_J Overton,J Overton,J Overton,
id_SPD Smith,SPD Smith,SPD Smith,267192
not_in_matches,X,X,1
"""
WIKIDATA = pd.DataFrame([
    {"cricinfo_id": "787987", "label": "Marnus Labuschagne", "birth_name": None},
    {"cricinfo_id": "267192", "label": "Steve Smith", "birth_name": "Steven Peter Devereux Smith"},
    {"cricinfo_id": "1", "label": "Not Playing", "birth_name": None},
])


def test_names_for_register_matches_ids():
    people = pd.read_csv(pd.io.common.StringIO(PEOPLE), dtype=str)
    out = wikidata.names_for_register(people, {"id_M Labuschagne", "id_J Overton", "id_SPD Smith"}, WIKIDATA)
    assert sorted(map(tuple, out.values)) == [
        ("id_M Labuschagne", "Marnus Labuschagne"), ("id_SPD Smith", "Steve Smith"),
        ("id_SPD Smith", "Steven Peter Devereux Smith")]


def test_add_full_names_fills_gaps_only(tmp_path, monkeypatch, _scon):
    db = _scon.execute("SELECT path FROM duckdb_databases() WHERE NOT internal").fetchone()[0]
    copy = tmp_path / "copy.duckdb"
    copy.write_bytes(open(db, "rb").read())
    (tmp_path / "people.csv").write_text(PEOPLE)
    monkeypatch.setattr(wikidata, "fetch_names", lambda ids: WIKIDATA[WIKIDATA.cricinfo_id.isin(ids)])
    c = duckdb.connect(str(copy))
    # "Steve Smith" is already in the register, so only two names are new
    assert wikidata.add_full_names(c, tmp_path / "people.csv") == 2
    assert stats.find_players(c, "marnus").full_name.iloc[0] == "Marnus Labuschagne"
    assert stats.find_players(c, "steve smith").full_name.iloc[0] == "Steve Smith"  # register first
    c.close()


def test_list_only_players_get_names_by_article(tmp_path, monkeypatch, _scon):
    db = _scon.execute("SELECT path FROM duckdb_databases() WHERE NOT internal").fetchone()[0]
    copy = tmp_path / "copy.duckdb"
    copy.write_bytes(open(db, "rb").read())
    c = duckdb.connect(str(copy))
    c.execute("""CREATE TABLE player_careers AS SELECT * FROM (VALUES
                 ('wp:australia:124', 'Don Bradman', 'Don Bradman', 'Australia', 52, 1928, 1948))
                 t(player_id, name, wiki_title, team, matches, first_year, last_year)""")
    monkeypatch.setattr(wikidata, "fetch_names_by_title", lambda titles: pd.DataFrame(
        [{"wiki_title": "Don Bradman", "label": "Don Bradman", "birth_name": "Donald George Bradman"}]))
    assert wikidata.add_list_names(c) == 2
    assert stats.find_players(c, "donald bradman").full_name.iloc[0] == "Donald George Bradman"
    c.close()


def test_article_iri_is_percent_encoded():
    assert wikidata._article_iri("Jack Board (cricketer)") == \
        "<https://en.wikipedia.org/wiki/Jack_Board_%28cricketer%29>"
    assert ">" not in wikidata._article_iri("a> } DROP")[1:-1]
