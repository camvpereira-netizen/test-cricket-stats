---
name: compare
description: Compare two or more Test cricketers from the local database. Use when asked to compare players, e.g. "/compare Smith vs Root in England since 2015".
---

# Compare Test players

1. Parse the request into player names and optional filters
   (opponent, venue/city, date range).
2. For each name, run a short Python snippet from the project root:
   ```python
   import sys; sys.path.insert(0, "src")
   from cricket import stats
   con = stats.connect("data/tests.duckdb")
   print(stats.find_players(con, "<name>").to_string())
   ```
   If more than one plausible match comes back, ask which player is meant before going on.
3. Call `stats.compare(con, [ids...], stats.Filters(...))` and print the result.
4. Reply with:
   - a compact markdown table of the key stats (matches, runs, average, 100s,
     wickets, bowling average) for each player
   - two or three sentences on what stands out, noting sample size
   - the line "Matches in Cricsheet only; not full careers. Data from Cricsheet (cricsheet.org), ODC-BY 1.0"
5. Never fill gaps from memory or other websites. If the database lacks the data, say so.
