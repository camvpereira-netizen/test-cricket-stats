"""Career totals from Wikipedia's lists of Test cricketers: parsing, linking, search and leaders.

The list snippets copy the three layouts the real pages use; expected values are read off them by hand.
"""
import duckdb
import pandas as pd
import pytest

from cricket import careers, stats

ENGLAND = """
Statistics are correct as of 12 September 2026 after the third Test.
{| class="wikitable sortable plainrowheaders"
|- align="center"
! scope="col" rowspan="2"| [[Cap (sport)|Cap]]
! scope="col" rowspan="2"| Name
! scope="col" rowspan="2"| First
! scope="col" rowspan="2"| Last
! scope="col" rowspan="2"| Mat
! scope="col" |[[Run (cricket)|Runs]]
! scope="col"  |HS
! scope="col"  |[[Batting average (cricket)|Avg]]
! scope="col"  |[[Delivery (cricket)|Balls]]
! scope="col"  |[[Wicket#Dismissing a batsman|Wkt]]
! scope="col"  |[[List of cricket terms#B|BBI]]
! scope="col"  |[[Bowling average|Ave]]
! scope="col"  |[[Caught (cricket)|Ca]]
! scope="col"  |[[Stumped|St]]
! scope="col" rowspan=2 class="unsortable" | Ref(s)
|- class="unsortable"
! scope="col" colspan=3 | [[Batting (cricket)|Batting]]
! scope="col" colspan=4 | [[Bowling (cricket)|Bowling]]
! scope="col" colspan=2 | [[Fielding (cricket)|Fielding]]
|- align="center"
| 24
! scope="row" | {{sortname|W. G.|Grace}}
| 1880 || 1899 || 22 || {{sort|01098|1,098}} || {{sort|1700|170}} || {{sort|3228|32.29}} || {{sort|00666|666}} || {{sort|009|9}} || {{sort|2148|2/12}} || {{nts|26.22}} || 39 || 0 || <ref>{{cite web |url=http://www.espncricinfo.com/england/content/player/13399.html |title=WG Grace}}</ref>
|- align="center"
| 111
! scope="row" | {{sortname|Jack|Board||Jack Board (cricketer)}}
| 1899 || 1906 || 6 || {{sort|00108|108}} || {{sort|0290|29}} || {{sort|1080|10.80}} || {{sort|00000|0}} || {{sort|-001|–}} || {{sort|-00001|–}} || – || 8 || 3 || <ref>{{cite web |url=http://www.espncricinfo.com/england/content/player/9999.html |title=Jack Board}}</ref>
|- align="center"
| 655
! scope="row" | '''{{sortname|Joe|Root}}'''
| 2012 || 2026 || 169 || {{sort|14278|14,278}} || {{sort|2620|262}} || {{sort|5045|50.45}} || {{sort|06332|6,332}} || {{sort|074|74}} || {{sort|5008|5/8}} || {{nts|48.09}} || 220 || 0 || <ref>{{cite web |url=https://www.espncricinfo.com/cricketers/joe-root-303669 |title=Joe Root}}</ref>
|}
"""

AUSTRALIA = """
Statistics are correct as of 22 October 2026.
{| class="wikitable sortable"
|-
! colspan=4 | Australian Test cricketers
! colspan=5 | [[Batting (cricket)|Batting]]
! colspan=6 | [[Bowling (cricket)|Bowling]]
|-
! [[Cap (sport)|Cap]]
! [[Name]]
! Career
! Mat
! [[Innings|Inn]]
! [[Not out|NO]]
! [[Run (cricket)|Runs]]
! HS
! [[Batting average (cricket)|Avg]]
! [[Cricket ball|Balls]]
! Mdn
! [[Run (cricket)|Runs]]
! [[Wicket|Wkt]]
! Best
! [[Bowling average|Avg]]
! [[Caught|Ca]]
! [[Stump (cricket)|St]]
|-
||124||{{sortname|Don|Bradman}}||1928–1948||52||80||10||6996||334||99.94||160||3||72||2||1/8||36.00||32||–
|-
||415||'''{{sortname|Steven|Smith|Steve Smith}}'''||2010–||127||227||28||11,003||239||55.29||1,425||12||1,008||19||3/18||53.05||224||–
|}
"""

INDIA = """
Statistics are correct as of 26 November 2025.
{| class="wikitable plainrowheaders sortable"
|-
! scope="col" | [[Cap (sport)|Cap]]
! scope="col" | Name
! scope="col" | First
! scope="col" | Last
! scope="col" | Mat
! scope="col" |[[Run (cricket)|Runs]]
! scope="col" |HS
! scope="col" |[[Batting average (cricket)|Avg]]
! scope="col" |[[Century (cricket)|100/50]]
! scope="col" |[[Wicket#Dismissing a batsman|Wkt]]
! scope="col" |[[List of cricket terms#B|BBI]]
! scope="col" |[[Bowling average|Ave]]
! scope="col" |5/10 wicket
! scope="col" |[[Caught (cricket)|Ca]]
! scope="col" |[[Stumped|St]]
|-
|187||[[Sachin Tendulkar]]||1989||2013||200||15921||248*||53.78||51/68||46||3/10||54.17||0/0||115||0
|}
"""


def test_parse_three_layouts():
    eng = careers.parse_list(ENGLAND, "England").set_index("cap")
    assert list(eng.index) == [24, 111, 655]
    assert eng.loc[24, "name"] == "W. G. Grace" and eng.loc[24, "runs"] == 1098
    assert eng.loc[24, "batting_average"] == 32.29 and eng.loc[24, "best_bowling"] == "2/12"
    assert eng.loc[24, "cricinfo_id"] == "13399" and eng.loc[24, "catches"] == 39
    assert eng.loc[111, "wiki_title"] == "Jack Board (cricketer)"  # "||" inside the template
    assert pd.isna(eng.loc[111, "wickets"]) and eng.loc[111, "stumpings"] == 3
    assert eng.loc[655, "name"] == "Joe Root" and eng.loc[655, "cricinfo_id"] == "303669"
    assert eng.loc[655, "as_of"].startswith("12 September 2026")

    aus = careers.parse_list(AUSTRALIA, "Australia").set_index("cap")
    don = aus.loc[124]
    assert (don.first_year, don.last_year, don.innings, don.not_outs) == (1928, 1948, 80, 10)
    assert don.runs == 6996 and don.runs_conceded == 72 and don.bowling_average == 36.0  # 2nd Runs/Avg
    assert don.high_score == "334" and pd.isna(don.stumpings)
    smith = aus.loc[415]
    assert smith["name"] == "Steven Smith" and smith.wiki_title == "Steve Smith"
    assert smith.last_year == 2026 and smith.runs == 11003  # "2010–": still playing at the as-of date

    ind = careers.parse_list(INDIA, "India").iloc[0]
    assert (ind.hundreds, ind.fifties, ind.wickets) == (51, 68, 46)
    assert ind.high_score == "248*"


@pytest.fixture()
def ccon(tmp_path, con):
    """Copy of the fixture database with three list players: one in the matches, two not."""
    db = con.execute("SELECT path FROM duckdb_databases() WHERE NOT internal").fetchone()[0]
    copy = tmp_path / "c.duckdb"
    copy.write_bytes(open(db, "rb").read())
    c = duckdb.connect(str(copy))
    team, first = c.execute("""SELECT a.team, year(min(m.start_date)) FROM appearances a
                               JOIN matches m USING (match_id) WHERE a.player_id = 'id_B1'
                               GROUP BY ALL""").fetchone()
    lists = pd.concat([careers.parse_list(AUSTRALIA, "Australia"), careers.parse_list(ENGLAND, "England")])
    extra = lists.iloc[[0]].assign(team=team, cap=1, name="Bob B1", wiki_title="Bob B1",
                                   first_year=first, last_year=first, cricinfo_id="555",
                                   matches=1, runs=10, batting_average=5.0, wickets=0)
    people = pd.DataFrame({"identifier": ["id_B1"], "key_cricinfo": ["555"]})
    linked = careers.link_players(c, pd.concat([lists, extra], ignore_index=True), people)
    assert careers.load(c, linked) == 6
    yield c, linked
    c.close()


def test_link_players(ccon):
    c, linked = ccon
    ids = dict(zip(linked.name, linked.player_id))
    assert ids["Bob B1"] == "id_B1"  # by ESPNcricinfo id through the register
    assert ids["Don Bradman"] == "wp:australia:124" and ids["W. G. Grace"] == "wp:england:24"


def test_link_rejects_a_wrong_cited_profile(con):
    # Lists sometimes cite one profile for two players; that id is ignored, not shared
    two = careers.parse_list(ENGLAND, "England").iloc[:2].assign(cricinfo_id="555")
    people = pd.DataFrame({"identifier": ["id_B1"], "key_cricinfo": ["555"]})
    assert careers.link_players(con, two, people).player_id.str.startswith("wp:").all()


def test_search_and_career_totals_cover_list_only_players(ccon):
    c, _ = ccon
    found = stats.find_players(c, "bradman")
    assert found.player_id.iloc[0] == "wp:australia:124"
    assert (found.matches.iloc[0], found.first_year.iloc[0], found.last_year.iloc[0]) == (52, 1928, 1948)
    assert stats.find_players(c, "w g grace").player.iloc[0] == "W. G. Grace"
    b1 = stats.find_players(c, "bob").iloc[0]  # the list name finds the Cricsheet player
    assert b1.player_id == "id_B1" and b1.full_name == "Bob B1"
    assert stats.career_totals(c, "wp:australia:124").runs.iloc[0] == 6996


def test_career_leaderboard(ccon):
    c, _ = ccon
    lb = stats.career_leaderboard(c, "runs")
    assert list(lb.player.head(2)) == ["Joe Root", "Steven Smith"]
    avg = stats.career_leaderboard(c, "batting_average", min_matches=50)
    assert avg.player.iloc[0] == "Don Bradman" and avg.batting_average.iloc[0] == 99.94
    bowl = stats.career_leaderboard(c, "bowling_average", min_wickets=10)
    assert list(bowl.player) == ["Joe Root", "Steven Smith"]  # 48.09, 53.05; Grace has only 9
