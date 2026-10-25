# Test Cricket Stats

Search, compare and predict men's Test cricket, built on Cricsheet ball-by-ball data
plus Wikipedia results for every other Test since 1877, in DuckDB, with a Streamlit web app.
It doubles as a hands-on way to learn Claude Code.

Data from Cricsheet (cricsheet.org), ODC-BY 1.0. Results of Tests not in Cricsheet from
Wikipedia (en.wikipedia.org), CC BY-SA 4.0: if you publish that part of the data, share it
under the same licence.

## Setup (Mac)

```bash
git clone <your repo URL> && cd <repo>
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

PYTHONPATH=src python -m cricket.ingest --wikipedia   # Cricsheet (~17 MB) + ~310 Wikipedia pages, ~5 min first time
pytest -q                                   # 45 tests (hand-checked stats, parsing, models, every app page)
streamlit run app.py                        # opens the web app at http://localhost:8501
```

## The web app

Pages sit in a menu along the top. Each page is a file in `app_pages/` that calls one function in
`ui/views.py`; shared pieces (database, cached models, filters) are in `ui/common.py`.

| Menu | Page | What it shows |
|---|---|---|
| Home | Home | Number of Tests, latest Test, when the data was built, the latest results |
| Stats | Players, Compare, Teams, Leaderboards | Player summaries and splits, side-by-side comparison, team records and head-to-head with ratings, leaderboards. Filters (opponent, venue, dates) are in the sidebar on these pages |
| Predict | Predictions, Model accuracy | Match result, fourth-innings chase, a player's next innings, and how each model scores on held-out Tests |
| Coming soon | Ask, Games | Placeholders for the question-answering agent (phase 3) and games (phase 4) |
| About | About the data | Coverage by decade and level of detail |

## Nightly data refresh and hosting

- `.github/workflows/ci.yml` runs the tests on every push and pull request.
- `.github/workflows/refresh.yml` runs every night at 18:00 UTC (4am AEST) and can be started
  by hand from the Actions tab. It runs the tests, rebuilds the database (old Wikipedia pages
  are cached between runs; current season pages are fetched again), fails without publishing
  if fewer than `MIN_TESTS` Tests come out, and otherwise uploads `tests.duckdb` to the
  `data-latest` release. Raise `MIN_TESTS` when new Tests are added.
- The app fetches that file when its copy is missing or over 24 hours old, from this repo's
  release by default (`DEFAULT_DB_URL` in `ui/common.py`), or from `CRICKET_DB_URL` (environment
  variable or Streamlit secret) if set. Setting `CRICKET_DB` to a local file turns fetching off
  unless `CRICKET_DB_URL` is also set.
  This works for a public repo; a private repo's release files need a token, which the app
  does not handle yet.
- Deploy on Streamlit Community Cloud: New app, pick the repo, branch `main`, main file
  `app.py`. No secrets are needed.
- GitHub turns off scheduled workflows in a public repo after 60 days with no repository
  activity; re-enable it from the Actions tab if that happens.

## What's included

| Piece | File | What it does |
|---|---|---|
| Ingest | `src/cricket/ingest.py` | Downloads `tests_male_json.zip`, loads `matches` (with toss, margin, host country, home team), `appearances`, `deliveries`, `wickets`, `innings` (totals, declared) and `batting_order` tables, and a one-row `build_info` |
| Venues | `src/cricket/venues.py` | City → host nation lookup, so home/away/neutral works |
| Older Tests | `src/cricket/wikipedia.py` | Reads Wikipedia's "International cricket in <season>" pages (cached in `data/raw/wikipedia/`), fixes known page errors and cross-checks England's Tests against England's results lists |
| Mixed detail | `src/cricket/scorecards.py` | Adds result-only or scorecard-only matches beside Cricsheet's, skipping ones already present; `matches.detail` says which |
| Stats | `src/cricket/stats.py` | Batting and bowling summaries, compare, runs by year, innings and home/away splits, team records, head-to-head, leaderboards, latest results |
| Predictions | `src/cricket/predict.py` | Match result, fourth-innings chase (start or in play), and a player's next innings, each scored on held-out recent matches against a naive baseline |
| Web app | `app.py`, `app_pages/`, `ui/` | The Streamlit web app described above |
| Workflows | `.github/workflows/` | Tests on every push; nightly data refresh |
| Tests | `tests/` | Fake 2-match fixture for hand-checked stats; 160 simulated matches (`tests/synthetic.py`) for the models and an app test that opens every page |
| Claude context | `CLAUDE.md` | Data rules, stat definitions, roadmap |
| Subagent | `.claude/agents/stats-checker.md` | Reviews stat logic and adds tests |
| Skill | `.claude/skills/compare/SKILL.md` | `/compare Smith vs Root since 2015` inside Claude Code |

## Learning path with Claude Code

1. **Open it in Claude Code:** `cd test-cricket-stats && claude`. Ask "summarise this project";
   it reads `CLAUDE.md` automatically.
2. **Try the skill:** `/compare SPD Smith vs JE Root against India`.
3. **Try the subagent:** "Add a batting split by innings number (1st–4th), then use the
   stats-checker agent to verify it."
4. **Add a hook:** ask Claude to add a PostToolUse hook in `.claude/settings.json` that runs
   `pytest -q` after edits to `src/`.
5. **Build an MCP server:** ask Claude to wrap `find_players`, `batting_summary`,
   `bowling_summary` and `compare` as an MCP server, then connect it to Claude Desktop so you can
   ask questions in plain English from the chat app.

## How the predictions work

| Model | Method | Inputs |
|---|---|---|
| Match result | Elo-style team ratings (with home advantage) fed into a multinomial logistic regression for win / draw / loss | Rating gap, home/away/neutral, toss (averaged if unknown), venue country's past draw rate |
| Fourth-innings chase | Multinomial logistic regression fitted on every over of past fourth innings | Runs needed, wickets in hand, overs already bowled in the match (proxy for time left), rating gap, home |
| Player's next innings | Recency-weighted form shrunk towards the average for the batter's position; negative binomial spread for runs, Poisson for wickets; home/away factor | Player's past innings in Cricsheet |

Every model is fitted on older matches and scored on the most recent 20% (log loss, Brier
score, MAE) against a naive baseline. The scores are on the app's Model accuracy page.
Background: Allsopp & Clarke (2004, JRSS A) on team ratings and home advantage in Tests;
Akhtar & Scarf (2012, Int. J. Forecasting) on multinomial win/draw/loss forecasts in play;
Scarf, Shi & Akhtar (2011, JRSS A) on negative binomial distributions of Test innings scores.

## What the database holds (built 25 Oct 2026)

| Detail | Tests | Span | Source | What works |
|---|---|---|---|---|
| Ball-by-ball | 896 | Dec 2001 to Oct 2026 | Cricsheet | Everything |
| Result only | 1,731 | Mar 1877 to Oct 2026 | Wikipedia season pages | Team records, head-to-head, match-result model |

That is 2,627 of the 2,638 Tests numbered up to 18 Oct 2026. The 11 missing (Tests 860, 1207,
1591, 1624–1627, 1630, 1632, 1792, 2585) are not listed on any season page. The Wikipedia layer was checked
two ways: on the 895 Tests it shares with Cricsheet the result agrees in 894 (the other is the
2009 Lahore Test abandoned after the attack on Sri Lanka's bus: Cricsheet says draw, Wikipedia
"no result"), and on England's 1,019 Tests it agrees with England's separately edited results
lists in all but 1, after 22 dates were corrected from those lists. One result (Test 414,
Pakistan v New Zealand 1955) is wrong on its page and left blank.

Cricsheet itself has gaps between 2001 and 2008 (about 145 Tests) and leaves out Afghanistan;
those now come from Wikipedia as results, so team records are complete but player figures for
those matches are not.

## Accuracy on real data (match-result model on the full history; others on Cricsheet)

Match result, tested on 175 Tests from 2 Jun 2022 and fitted on 2,391 earlier ones from 1900:
log loss 0.884, Brier 0.516, 58.9% right. Before the older Tests were added (167 Tests from 23 May
2022), it scored log loss 0.89 and 61.7%, so the long history makes it about as good, not better.
A country's draw rate now fades older matches (factor 0.9 per match, picked on 2016–22 validation
Tests), because draws were far more common before the 1990s.

| Model | Model | Naive baseline |
|---|---|---|
| Match result (175 Tests) | 58.9% right, log loss 0.88 | 45.7% (always "a win"), 53.7% with home advantage; log loss 1.08–1.09 |
| Fourth-innings chase, at the start (132 chases) | 83.3% right, log loss 0.49 | 49.2%, log loss 0.95 |
| Batter's next innings (6,562 innings) | 21.8 runs average error | 22.5 (position average) |
| Bowler's next innings (3,516 innings) | 1.21 wickets average error | 1.32 (everyone's average) |

## Known limits

- Player stats cover only ball-by-ball (and any future scorecard) Tests, so careers before 2002
  are missing and some 2001–2008 Tests are too. Wikipedia gives results, not player figures.
- Cricsheet withholds Afghanistan's matches, so Afghanistan has team results (from Wikipedia)
  but no player stats.
- Wikipedia result rows have no toss, so the match model treats toss as unknown for them.
- Fours and sixes are counted from batter runs of 4 or 6, so the rare all-run four is counted as a boundary.
- Cricsheet has no venue country, so `venues.py` maps cities by hand. Unknown cities count as
  "unknown" in splits and neutral in models; add them to the lookup if you spot one.
- Ratings start every team at 1500 at its first Test, so the first 60 Tests (1877–1900) only
  warm the ratings up and are not used to fit the match model.
- The chase model can't see rain or bad light, which use time without overs being bowled.
- Predictions are probabilities, not certainties. Check the Model accuracy page before trusting one.
- Players are named as Cricsheet names them (e.g. "SPD Smith"); search by surname.
