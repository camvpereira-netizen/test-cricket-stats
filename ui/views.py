"""One function per page of the web app. Each takes the shared Ctx; stat SQL stays in cricket.stats."""
from __future__ import annotations

import streamlit as st

from cricket import scorecards, stats
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
    a.page_link(PAGES["compare"], label="Compare players", icon=":material/compare_arrows:")
    b.page_link(PAGES["teams"], label="Team records and head-to-head", icon=":material/groups:")
    b.page_link(PAGES["leaderboards"], label="Leaderboards", icon=":material/leaderboard:")
    c.page_link(PAGES["predict"], label="Predict a Test", icon=":material/insights:")
    c.page_link(PAGES["about"], label="Where the data comes from", icon=":material/info:")


def player(ctx: Ctx):
    st.title("Player")
    pid = player_picker(ctx.con, "Search player", "one")
    if not pid:
        return
    f = ctx.filters
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Batting")
        st.json(stats.batting_summary(ctx.con, pid, f))
    with c2:
        st.subheader("Bowling")
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


def compare(ctx: Ctx):
    st.title("Compare players")
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
    c1, c2, c3 = st.columns(3)
    kind = c1.radio("Leaderboard", ["Batting", "Bowling"], horizontal=True)
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


def ask(ctx: Ctx):
    st.title("Ask a question")
    st.info("Coming in phase 3 of the plan: ask in plain English and an agent answers from this "
            "database, showing the query it ran. Every number is re-checked before it is shown.",
            icon=":material/construction:")
    st.write("Questions it is being built to answer:")
    st.markdown("- Who has the best batting average in fourth innings in Asia since 2010, minimum 15 innings?\n"
                "- How does Root's average in Australia compare with Smith's in England?\n"
                "- Which bowlers have taken five-fors in both innings of a Test at Lord's?\n"
                "- What are India's chances in the next Test against England in Chennai?")


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
