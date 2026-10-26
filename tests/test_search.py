"""Fuzzy player search over Cricsheet names and register name variants (names.csv)."""
import json
import zipfile

import duckdb
import pytest
from conftest import dl, match

from cricket import ingest, stats

AUS = ["SPD Smith", "PJ Cummins", "M Labuschagne"]
ENG = ["JE Root", "J Overton", "GC Smith"]
NAMES = """identifier,name
id_SPD Smith,Steve Smith
id_SPD Smith,Steven Smith
id_SPD Smith,S Smith
id_PJ Cummins,Pat Cummins
id_PJ Cummins,Anderson Cummins
id_J Overton,Craig Overton
id_JE Root,Joe Root
id_GC Smith,Graeme Smith
not_in_matches,Someone Else
"""


@pytest.fixture(scope="module")
def scon(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("search")
    m = match(["Australia", "England"], {"Australia": AUS, "England": ENG}, "2023-06-16",
              "Edgbaston", "Birmingham", [
                  {"team": "Australia", "overs": [{"over": 0, "deliveries": [
                      dl("SPD Smith", "JE Root", "PJ Cummins", bat=1),
                      dl("PJ Cummins", "JE Root", "SPD Smith",
                         wicket={"player_out": "PJ Cummins", "kind": "bowled"})]}]}])
    zp = tmp / "t.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("1.json", json.dumps(m))
    db = tmp / "t.duckdb"
    ingest.build_db(zp, db)
    (tmp / "names.csv").write_text(NAMES)
    c = duckdb.connect(str(db))
    assert ingest.load_player_names(c, tmp / "names.csv") == 8  # "Someone Else" left out
    c.close()
    c = stats.connect(str(db))
    yield c
    c.close()


def top(con, q):
    df = stats.find_players(con, q)
    return df.player.iloc[0] if len(df) else None


@pytest.mark.parametrize("q", ["Steve Smith", "steve smith", "STEVEN SMITH", "stve smith",
                               "steve smiht", "SPD Smith", "s smith", "ste smi"])
def test_full_names_initials_and_typos(scon, q):
    assert top(scon, q) == "SPD Smith"


def test_surname_lists_everyone_with_it(scon):
    assert set(stats.find_players(scon, "smith").player) == {"SPD Smith", "GC Smith"}
    assert top(scon, "graeme smith") == "GC Smith"


def test_typos_and_no_space(scon):
    assert top(scon, "labuschange") == "M Labuschagne"
    assert top(scon, "pat cumins") == "PJ Cummins"
    assert top(scon, "joe") == "JE Root"


def test_no_loose_matches(scon):
    assert stats.find_players(scon, "virat kohli").empty
    assert stats.find_players(scon, "  ").empty
    assert list(stats.find_players(scon, "steve smith").player) == ["SPD Smith"]


def test_full_name_label_skips_register_mistakes(scon):
    full = stats.find_players(scon, "smith").set_index("player").full_name
    assert full.to_dict() == {"SPD Smith": "Steve Smith", "GC Smith": "Graeme Smith"}
    # The register also lists "Anderson Cummins" and "Craig Overton" for these two
    assert stats.find_players(scon, "pat cummins").full_name.iloc[0] == "Pat Cummins"
    assert stats.find_players(scon, "j overton").full_name.isna().all()


def test_works_without_names_table(con):
    # Databases built before names.csv was loaded still search Cricsheet names
    assert stats.find_players(con, "B1").player_id.iloc[0] == "id_B1"
    assert stats.find_players(con, "B1").full_name.isna().all()
