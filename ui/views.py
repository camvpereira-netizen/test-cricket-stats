"""One function per page of the web app. Each takes the shared Ctx; stat SQL stays in cricket.stats."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from cricket import guru, questions, scorecards, stats
from ui import common
from ui.common import Ctx, models, player_picker, show_probs, tidy


def home(ctx: Ctx):
    st.title("Test Cricket Stats")
    st.write("Search, compare and predict men's Test cricket, from the first Test in 1877 to the latest.")
    s = stats.data_summary(ctx.con)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tests", f"{s['tests']:,}")
    c2.metric("With scorecards", f"{s['with_scorecards']:,}")
    c3.metric("Latest Test", f"{s['latest_test']:%d %b %Y}")
    c4.metric("Data built", f"{s['built_at']:%d %b %Y}" if s["built_at"] else "Not recorded")
    st.subheader("Latest results")
    st.dataframe(stats.recent_results(ctx.con), hide_index=True, width="stretch",
                 column_config={"date": st.column_config.DateColumn(format="D MMM YYYY")})
    st.subheader("What you can do")
    a, b, c = st.columns(3)
    a.page_link(PAGES["player"], label="Look up a player", icon=":material/person_search:")
    b.page_link(PAGES["ask"], label=GURU_TITLE, icon=":material/chat:")
    c.page_link(PAGES["about"], label="Where the data comes from", icon=":material/info:")
    st.caption("Coming soon: player comparisons, team records, leaderboards, predictions and games.")


BAT_LABELS = {"team": "Team", "matches": "Mat", "innings": "Inns", "not_outs": "NO", "runs": "Runs",
              "high_score": "HS", "average": "Ave", "balls_faced": "BF", "strike_rate": "SR",
              "hundreds": "100s", "fifties": "50s", "fours": "4s", "sixes": "6s", "source": "Source"}
BOWL_LABELS = {"team": "Team", "matches": "Mat", "innings": "Inns", "balls": "Balls", "runs": "Runs",
               "wickets": "Wkts", "best_innings": "BBI", "best_match": "BBM", "average": "Ave",
               "economy": "Econ", "strike_rate": "SR", "four_wickets": "4w", "five_wickets": "5w",
               "ten_wickets": "10w", "source": "Source"}
OTHER_LABELS = {"team": "Team", "cap": "Cap", "first_year": "First Test", "last_year": "Last Test",
                "catches": "Ct", "stumpings": "St", "figures_as_of": "Figures as of"}


def _career_table(df, labels: dict, places: dict | None = None):
    """A career table as text, Cricinfo style: fixed decimals, and "-" where a figure is unknown."""
    out = df.astype(object).map(lambda v: int(v) if isinstance(v, float) and v.is_integer() else v)
    for col, n in (places or {}).items():
        out[col] = [f"{v:.{n}f}" if v is not None and not pd.isna(v) else None for v in df[col]]
    out = out.where(out.notna(), "-").astype(str).replace({"<NA>": "-", "None": "-", "nan": "-"})
    st.dataframe(out.rename(columns=labels), hide_index=True, width="stretch")


def player(ctx: Ctx):
    st.title("Player")
    pid = player_picker(ctx.con, "Search player", "one")
    if not pid:
        return
    career = stats.test_career(ctx.con, pid)
    if not career["batting"].empty:
        st.subheader("Batting")
        _career_table(career["batting"], BAT_LABELS, {"average": 2, "strike_rate": 2})
        st.subheader("Bowling")
        _career_table(career["bowling"], BOWL_LABELS, {"average": 2, "economy": 2, "strike_rate": 1})
        st.subheader("Other")
        _career_table(career["other"], OTHER_LABELS)
        st.caption(
            "Whole Test career, one row per team. Where every Test is in Cricsheet's ball-by-ball "
            "data (most players since 2002) every figure is counted from the deliveries. Otherwise "
            "figures come from Wikipedia's list of the team's Test cricketers, with gaps filled from "
            "the player's article. A dash means no open source has it: balls faced, fours and sixes "
            "weren't recorded for most early Tests, and best-in-a-match, 4-wicket innings and "
            "bowling innings need full scorecards. Filters don't apply to these tables.")
    f = ctx.filters
    if not stats.batting_summary(ctx.con, pid)["innings"] and not stats.bowling_summary(ctx.con, pid)["innings_bowled"]:
        st.info("No ball-by-ball scorecards for this player's Tests (they start in 2001), so the "
                "filtered stats and splits aren't available.")
        return
    st.divider()
    st.subheader("Filtered stats and splits (ball-by-ball Tests, 2001 on)")
    st.caption("These cover only Tests with ball-by-ball scorecards, not the whole career. "
               "The sidebar filters apply.")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Batting**")
        st.json(stats.batting_summary(ctx.con, pid, f))
    with c2:
        st.markdown("**Bowling**")
        st.json(stats.bowling_summary(ctx.con, pid, f))
    yearly = stats.runs_by_year(ctx.con, pid, f)
    if not yearly.empty:
        st.subheader("Runs by year")
        st.bar_chart(yearly.set_index("year")["runs"])
    s1, s2 = st.columns(2)
    with s1:
        st.subheader("By innings of the match")
        st.dataframe(stats.innings_splits(ctx.con, pid, f), hide_index=True)
    with s2:
        st.subheader("Home, away and neutral")
        st.dataframe(stats.home_away_split(ctx.con, pid, f), hide_index=True)


def coming_soon():
    st.info("Coming soon. This page is an early preview while we focus on player search and the "
            "stats guru, so it may change or show gaps.", icon=":material/construction:")


def compare(ctx: Ctx):
    st.title("Compare players")
    coming_soon()
    cols = st.columns(2)
    with cols[0]:
        p1 = player_picker(ctx.con, "Player 1", "p1")
    with cols[1]:
        p2 = player_picker(ctx.con, "Player 2", "p2")
    if p1 and p2:
        cmp = stats.compare(ctx.con, [p1, p2], ctx.filters)
        st.dataframe(cmp.astype(str).replace("None", "-"), width="stretch")


def teams(ctx: Ctx):
    st.title("Teams")
    coming_soon()
    c1, c2 = st.columns(2)
    team = c1.selectbox("Team", ctx.teams, key="team")
    rival = c2.selectbox("Head-to-head against", ["(none)"] + [t for t in ctx.teams if t != team])
    st.subheader(f"{team} record by opponent")
    st.dataframe(stats.team_record(ctx.con, team, ctx.filters), hide_index=True, width="stretch")
    if rival != "(none)":
        h2h = stats.head_to_head(ctx.con, team, rival, ctx.filters)
        won = int((h2h.result == team).sum())
        lost = int((h2h.result == rival).sum())
        st.subheader(f"{team} v {rival}: {len(h2h)} matches, {won} won, {lost} lost, "
                     f"{len(h2h) - won - lost} drawn or tied")
        st.dataframe(h2h, hide_index=True, width="stretch")
    m = models()
    if "match" in m:
        st.subheader("Team ratings over time")
        hist = m["match"].rating_history()
        chart = hist.pivot_table(index="date", columns="team", values="rating").ffill()
        show = [team] + ([rival] if rival != "(none)" else [])
        st.line_chart(chart[[t for t in show if t in chart]])
        st.caption("Elo-style rating before each match; 1500 is the starting value.")


def leaderboards(ctx: Ctx):
    st.title("Leaderboards")
    coming_soon()
    c1, c2, c3 = st.columns(3)
    kind = c1.radio("Leaderboard", ["All-time careers", "Batting", "Bowling"], horizontal=True)
    if kind == "All-time careers":
        order = c2.selectbox("Sort by", list(stats.CAREER_ORDER))
        qual = c3.number_input("Minimum Tests", 1, 200, 20 if order.endswith("average") else 1)
        lb = stats.career_leaderboard(ctx.con, order, int(qual),
                                      min_wickets=50 if order == "bowling_average" else 0)
        st.dataframe(tidy(lb), hide_index=True, width="stretch")
        st.caption("Every Test since 1877, from Wikipedia's lists of each team's Test cricketers; "
                   "the sidebar filters don't apply. Bowling average needs 50 wickets.")
        return
    st.caption("From scorecards, which start in 2001; the filters apply.")
    if kind == "Batting":
        order = c2.selectbox("Sort by", ["runs", "average", "hundreds", "strike_rate"])
        qual = c3.number_input("Minimum innings", 1, 200, 20)
        lb = stats.batting_leaderboard(ctx.con, ctx.filters, order, int(qual))
    else:
        order = c2.selectbox("Sort by", ["wickets", "average", "economy", "strike_rate"])
        qual = c3.number_input("Minimum wickets", 1, 500, 30)
        lb = stats.bowling_leaderboard(ctx.con, ctx.filters, order, int(qual))
    st.dataframe(tidy(lb), hide_index=True, width="stretch")


def predict(ctx: Ctx):
    st.title("Predict")
    coming_soon()
    m = models()
    st.caption("Probabilities from simple models fitted to past Tests (match results since 1877; "
               "chases and players from ball-by-ball Tests since 2001). "
               "See Model accuracy for how often they beat a naive guess.")
    kind = st.radio("Predict", ["Match result", "Fourth-innings chase", "Player's next innings"],
                    horizontal=True, key="pred_kind")
    team_list = ctx.teams
    if kind == "Match result":
        if "match" not in m:
            st.warning(m.get("error"))
            return
        c1, c2, c3, c4 = st.columns(4)
        a = c1.selectbox("Team A", team_list, key="pa")
        b = c2.selectbox("Team B", [t for t in team_list if t != a], key="pb")
        where = c3.selectbox("Played in", [a, b, "Neutral venue"], key="pw")
        toss = c4.selectbox("Toss won by", ["Not known yet", a, b], key="pt")
        show_probs(m["match"].predict(a, b, None if where == "Neutral venue" else where,
                                      None if toss == "Not known yet" else toss))
        st.caption("Ratings: " + ", ".join(f"{t} {m['match'].ratings.get(t, 1500):.0f}" for t in (a, b)))
    elif kind == "Fourth-innings chase":
        if "chase" not in m:
            st.warning(m.get("error"))
            return
        c1, c2, c3 = st.columns(3)
        a = c1.selectbox("Chasing team", team_list, key="ca")
        b = c2.selectbox("Fielding team", [t for t in team_list if t != a], key="cb")
        where = c3.selectbox("Played in", [a, b, "Neutral venue"], key="cw")
        c4, c5, c6 = st.columns(3)
        need = c4.number_input("Runs still needed", 1, 800, 250)
        wk = c5.slider("Wickets in hand", 1, 10, 10)
        overs = c6.number_input("Overs bowled in the match so far", 0, 900, 280,
                                help="Proxy for time left: a five-day Test allows about 450 overs.")
        show_probs(m["chase"].predict(a, b, int(need), int(wk), float(overs),
                                      None if where == "Neutral venue" else where))
        st.caption("Rain and bad light use up time without overs being bowled, "
                   "so draws can be more likely than shown.")
    else:
        pid = player_picker(ctx.con, "Player", "pred")
        where = st.radio("Venue", ["home", "away", "neutral"], horizontal=True)
        if pid:
            c1, c2 = st.columns(2)
            with c1:
                st.subheader("Next innings batting")
                f = m["player"].batter(pid, where)
                if f:
                    st.json(f)
                else:
                    st.info("No batting innings found.")
            with c2:
                st.subheader("Next innings bowling")
                f = m["player"].bowler(pid, where)
                if f:
                    st.json(f)
                else:
                    st.info("Has not bowled in these matches.")


def accuracy(ctx: Ctx):
    st.title("Model accuracy")
    coming_soon()
    m = models()
    st.write("Each model is fitted on older matches and scored on the most recent 20% it never saw. "
             "Lower log loss, Brier score and MAE are better; the baseline is a naive guess.")
    if "match" in m:
        st.subheader("Match result")
        st.json(m["match"].metrics)
        st.subheader("Fourth-innings chase")
        st.json(m["chase"].metrics)
    else:
        st.warning(m.get("error"))
    st.subheader("Players' next innings")
    st.json(m["player"].metrics)


GURU_TITLE = "Ask the guru"  # working name until Cam picks one
GURU_EXAMPLES = [
    "Who has the best batting average in the fourth innings in Asia since 2010, minimum 15 innings?",
    "How does Joe Root's average in Australia compare with Steve Smith's in England?",
    "Which bowlers have the most wickets at Lord's?",
    "What is India's record against Australia since 2000?",
]
TOOL_LABELS = {t["name"]: t["name"].replace("_", " ").capitalize() for t in guru.TOOLS}


def _show_guru_answer(a: guru.Answer, by_claude: bool):
    st.markdown(a.text)
    if a.steps:
        with st.expander(f"How the guru worked this out ({len(a.steps)} lookups)"):
            st.caption("Each lookup runs one of the site's tested stat functions. The guru cannot "
                       "write its own database queries.")
            for step in a.steps:
                args = ", ".join(f"{k}={v}" for k, v in step.args.items()) or "no inputs"
                st.markdown(f"**{TOOL_LABELS.get(step.tool, step.tool)}** ({args})")
                if step.error:
                    st.warning(step.error)
                elif step.table is not None:
                    st.dataframe(tidy(step.table), hide_index=True, width="stretch")
    st.caption(f"Answered by Claude: {a.input_tokens:,} input and {a.output_tokens:,} output tokens"
               if by_claude else "Answered by the built-in question reader (free, no AI)")


def ask(ctx: Ctx):
    st.title(GURU_TITLE)
    st.write("Ask about men's Test cricket in plain English. Common questions are read by the site "
             "itself for free; others go to Claude if it is set up. Either way the numbers come from "
             "the same tested stat functions as the rest of the site, and every lookup is shown.")
    st.caption("Player stats with filters cover Tests with scorecards (mostly 2001 on); whole-career "
               "totals cover every Test. Check the lookups if an answer looks wrong.")
    history = st.session_state.setdefault("guru_history", [])
    for q, a, by_claude in history:
        with st.chat_message("user"):
            st.write(q)
        with st.chat_message("assistant"):
            _show_guru_answer(a, by_claude)
    if not history:
        st.write("Try one of these:")
        for i, q in enumerate(GURU_EXAMPLES):
            if st.button(q, key=f"guru_ex_{i}"):
                st.session_state["guru_pending"] = q
    question = st.chat_input("Ask a question", max_chars=guru.MAX_QUESTION_CHARS) \
        or st.session_state.pop("guru_pending", None)
    if not question:
        return
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        answer, by_claude = questions.answer(ctx.con, question), False
        if answer is None:
            answer, by_claude = _ask_claude(ctx, question, history)
            if answer is None:
                return
        _show_guru_answer(answer, by_claude)
    history.append((question, answer, by_claude))


def _ask_claude(ctx: Ctx, question: str, history: list):
    """The Claude guru, for questions the built-in reader doesn't understand. (answer, True) or (None, False)."""
    client, why = common.guru_client()
    password = common.secret("GURU_PASSWORD")
    if client is None or (password and not st.session_state.get("guru_unlocked")):
        st.write("I couldn't read that question. Try naming a player, team or stat, for example: "
                 "\"most wickets in India since 2015\", \"Root's average against Australia\", or "
                 "\"England's record at Lord's\".")
        if password and client is not None:
            if st.text_input("Password for harder questions (uses Claude)", type="password",
                             key="guru_pw") == password:
                st.session_state["guru_unlocked"] = True
                st.rerun()
        return None, False
    if blocked := common.guru_take_question():
        st.warning(blocked)
        return None, False
    with st.spinner("Asking Claude..."):
        try:
            return guru.ask(ctx.con, question, client,
                            model=common.secret("GURU_MODEL", guru.DEFAULT_MODEL),
                            effort=common.secret("GURU_EFFORT", guru.DEFAULT_EFFORT),
                            history=[(q, a.text) for q, a, _ in history[-3:] if a.ok]), True
        except Exception as e:  # API errors: bad key, spend limit reached, outage
            st.error(f"Claude couldn't answer just now ({type(e).__name__}). Please try again later.")
            return None, False


def games(ctx: Ctx):
    st.title("Games")
    st.info("Coming in phase 4 of the plan. Every clue and answer comes from a database query, "
            "so the games never state a wrong fact.", icon=":material/construction:")
    for name, how in [
        ("Guess the cricketer", "A daily mystery player. Each guess shows whether country, debut decade, "
                                "role and career numbers are a match, higher or lower."),
        ("Higher or lower", "Two players or Tests side by side: which has the higher stat?"),
        ("Guess the Test", "A few clues from one Test. Name the series or the ground."),
        ("Predict the Test", "Call the result before each Test and see how you do against the model."),
    ]:
        st.subheader(name)
        st.write(how)


def about(ctx: Ctx):
    st.title("About the data")
    st.write("**Ball-by-ball** matches come from Cricsheet and support every stat. **Result** matches "
             "come from Wikipedia's season pages and only have teams, venue, date and result, so they "
             "count in team records, head-to-head and the match-result model but not in player stats.")
    cov = scorecards.coverage(ctx.con)
    st.bar_chart(cov.pivot_table(index="decade", columns="detail", values="matches", fill_value=0))
    st.dataframe(cov, hide_index=True)
    s = stats.data_summary(ctx.con)
    if s["built_at"]:
        st.caption(f"Data built {s['built_at']:%d %b %Y %H:%M} UTC. It is rebuilt nightly once the "
                   "scheduled refresh is running.")


# Filled in by app.py so pages can link to each other
PAGES: dict = {}
