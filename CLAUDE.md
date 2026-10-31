# Test Cricket Stats — project guide for Claude

## What this is
A personal tool to search and compare **men's Test cricket** player stats, built on
Cricsheet ball-by-ball data. It is also a learning project for Claude Code features.

## Data rules (do not break)
- **Data sources: Cricsheet** (https://cricsheet.org, ODC-BY 1.0) for ball-by-ball, and
  **Wikipedia** season pages (CC BY-SA 4.0) for results of Tests Cricsheet lacks.
  Keep `cricket.ATTRIBUTION` (both credits) in the UI. Fetch Wikipedia with `action=raw`, a
  User-Agent and a pause between requests; the API endpoint is often rate limited.
- **Do not scrape ESPNcricinfo, Cricbuzz or any site whose terms prohibit automated access.**
  If a feature seems to need them, stop and ask instead.
- Raw downloads go in `data/raw/`; the DuckDB database is `data/tests.duckdb`. Both are gitignored.
- `matches.detail` is `ball_by_ball`, `scorecard` or `result`. Player stats read
  `batting_scorecard` / `bowling_scorecard` (NULL balls, fours, sixes where a source lacks
  them; rates use only innings where balls are known). Any player stat shown must say it
  covers matches with scorecards, not a full career. Whole-career totals come only from
  `player_careers` and must be labelled as such (filters can't apply to them).
- New older-match sources go through `scorecards.add_matches`, which skips matches already present.

## Stat definitions (keep consistent with official scoring)
- Balls faced: deliveries to the batter excluding wides (no-balls count as faced).
- Batting dismissals: exclude `retired hurt` and `retired not out`.
- Runs conceded by bowler: total runs minus byes, leg byes and penalty runs.
- Legal balls bowled: exclude wides and no-balls.
- Bowler wickets: bowled, caught, caught and bowled, lbw, stumped, hit wicket only.

## Layout
- `src/cricket/ingest.py` — download Cricsheet zip and People Register `names.csv`, load into DuckDB tables.
- `src/cricket/venues.py` — city → host nation lookup used by ingest for home/away.
- `src/cricket/wikipedia.py` — season-page parser, repairs and England cross-check.
- `src/cricket/careers.py` — every Test player's career totals from Wikipedia's per-team lists
  (`player_careers`; ids are Cricsheet's where linked, else `wp:<team>:<cap>`).
- `src/cricket/wikidata.py` — full player names from Wikidata (CC0), matched by register ids.
- `src/cricket/scorecards.py` — adds result-only / scorecard-only matches; coverage table.
- `src/cricket/stats.py` — all stat SQL lives here; UI and future MCP server only call these functions.
- `src/cricket/guru.py` — the Ask page's stats guru: Claude answers questions by calling checked
  wrappers of `stats.py` functions (tools). Never give it a raw SQL tool; new questions it can't
  answer need a new tested stats function and tool. Tests use a fake client (`tests/test_guru.py`).
- `src/cricket/predict.py` — prediction models. Features must use only information from before
  the match/ball being predicted; every model reports held-out metrics against a baseline.
- `app.py` — Streamlit web app entry and top menu; pages are `app_pages/<name>.py`, each calling
  `ui/views.<name>()`; `ui/common.py` holds the database fetch, cached models and filters.
  A new page needs a file in `app_pages/`, a view function, an entry in `app.py` and the
  `PAGES` list in `tests/test_app.py`.
- `.github/workflows/refresh.yml` — nightly rebuild, published as `tests.duckdb` on the
  `data-latest` release; the app downloads it when `CRICKET_DB_URL` is set.
- `tests/` — pytest with a hand-built fixture match. Every new stat needs a hand-checked test.
  `tests/synthetic.py` simulates matches (test-only) for model tests; never show its output as real.

## Conventions
- Python 3.11+, type hints, no ORM — plain DuckDB SQL with parameters (never f-string user input into SQL).
- Run `pytest -q` after any change to `stats.py` or `ingest.py`.
- Keep functions returning pandas DataFrames so the UI and MCP layer can share them.

## Roadmap (from the plan doc "Test cricket app: data, agents and games plan")
0. [x] Ingest, stats, predictions, every Test since 1877 (results), web app with top menu
1. [ ] Nightly refresh on GitHub Actions (workflow written; needs the GitHub repo)
2. [ ] MCP server exposing the stats, query and prediction functions as tools
3. [ ] Agents (router, stats, SQL, prediction) with a checker and a golden question set; the Ask page
   (Ask page done as the stats guru: one Claude tool loop over the stats functions, no SQL)
4. [ ] Games: higher or lower, guess the cricketer, guess the Test, predict the Test
5. [ ] Player data before 2001 from licensed sources; Wikidata player details
