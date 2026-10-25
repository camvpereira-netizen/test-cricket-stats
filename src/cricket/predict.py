"""Baseline prediction models built from the DuckDB tables.

Three models, each fitted on older matches and scored on the most recent ones it never saw,
next to a naive baseline, so the UI can show whether the model beats guessing:

- MatchModel: win / draw / loss for any two teams. Elo-style team ratings with a home
  advantage, fed into a multinomial logistic regression with home, toss and the venue
  country's draw rate.
- ChaseModel: the fourth-innings chase, at the start or in play. Multinomial logistic
  regression on runs needed, wickets in hand, overs already bowled in the match (a proxy
  for time left), the rating gap and home.
- PlayerModel: a batter's next innings (expected runs, chance of 50+ and 100+) and a
  bowler's next innings (expected wickets, chance of 3+ and 5+). Recency-weighted form,
  shrunk towards the average for the batter's position, with a negative binomial
  (batting) or Poisson (bowling) spread.

All features use only what was known before the match (or ball) being predicted.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import optimize, stats as st
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss

from cricket.stats import NOT_DISMISSED_KINDS

OUTCOMES = ("loss", "draw", "win")  # class order for multinomial models, from team A's side
TEST_SHARE = 0.2  # most recent share of matches held out for evaluation


# ---------------------------------------------------------------- shared helpers

def _brier(probs: np.ndarray, y: np.ndarray) -> float:
    """Multi-class Brier score: mean squared error of the probability vector (lower is better)."""
    onehot = np.eye(probs.shape[1])[y]
    return float(np.mean(np.sum((probs - onehot) ** 2, axis=1)))


def _scores(probs: np.ndarray, y: np.ndarray) -> dict:
    return {
        "log_loss": round(float(log_loss(y, probs, labels=list(range(probs.shape[1])))), 4),
        "brier": round(_brier(probs, y), 4),
        "accuracy": round(float(np.mean(probs.argmax(1) == y)), 3),
    }


def _split_by_time(dates: pd.Series) -> pd.Series:
    """True for rows in the most recent TEST_SHARE of dates."""
    cutoff = dates.quantile(1 - TEST_SHARE)
    return dates > cutoff


# ---------------------------------------------------------------- team ratings

@dataclass
class EloConfig:
    k: float = 24.0          # rating change per result
    home: float = 60.0       # home advantage in rating points
    start: float = 1500.0
    warmup: int = 60         # first N matches only warm the ratings up; not used for training
    draw_decay: float = 0.9  # weight kept by each older match in a country's draw rate; 0.9 picked on
                             # 2016-22 validation Tests, as draw rates fell from the timeless-Test era
    train_from: int | None = None  # fit only on matches from this year (older ones still set ratings)


def match_history(con, cfg: EloConfig = EloConfig()) -> tuple[pd.DataFrame, dict[str, float]]:
    """Pre-match ratings and draw-rate context for every decided or drawn match, oldest first."""
    m = con.execute(
        """SELECT match_id, start_date, team1, team2, country, home_team, toss_winner,
                  winner, result FROM matches
           WHERE winner IS NOT NULL OR result IN ('draw', 'tie')
           ORDER BY start_date, match_id"""
    ).df()
    ratings: dict[str, float] = {}
    country_n: dict[str, int] = {}
    country_d: dict[str, int] = {}
    recent: list[int] = []  # 1 if draw, rolling window for the global draw rate
    rows = []
    for r in m.itertuples(index=False):
        r1, r2 = ratings.get(r.team1, cfg.start), ratings.get(r.team2, cfg.start)
        home = 1 if r.home_team == r.team1 else -1 if r.home_team == r.team2 else 0
        toss = 1 if r.toss_winner == r.team1 else -1 if r.toss_winner == r.team2 else 0
        g = np.mean(recent[-100:]) if recent else 0.3
        c = r.country or "?"
        n_c, d_c = country_n.get(c, 0.0), country_d.get(c, 0.0)
        draw_rate = (d_c + 10 * g) / (n_c + 10)  # country draw rate shrunk to recent global rate
        y = 2 if r.winner == r.team1 else 0 if r.winner == r.team2 else 1
        rows.append({"match_id": r.match_id, "start_date": r.start_date, "team1": r.team1,
                     "team2": r.team2, "elo1": r1, "elo2": r2, "home": home, "toss": toss,
                     "draw_rate": draw_rate, "y": y})
        # update after recording pre-match state
        e1 = 1 / (1 + 10 ** (-(r1 + home * cfg.home - r2) / 400))
        s1 = y / 2
        ratings[r.team1] = r1 + cfg.k * (s1 - e1)
        ratings[r.team2] = r2 - cfg.k * (s1 - e1)
        draw = int(y == 1)
        recent.append(draw)
        country_n[c] = cfg.draw_decay * n_c + 1
        country_d[c] = cfg.draw_decay * d_c + draw
    hist = pd.DataFrame(rows)
    hist.attrs["country_draw"] = {c: (country_d[c], country_n[c]) for c in country_n}
    hist.attrs["global_draw"] = float(np.mean(recent[-100:])) if recent else 0.3
    return hist, ratings


def _match_features(df: pd.DataFrame) -> np.ndarray:
    return np.column_stack([(df.elo1 - df.elo2) / 100, df.home, df.toss, df.draw_rate])


def _symmetrise(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Add every match from the other team's side so neither column order is favoured."""
    Xs = X.copy()
    Xs[:, :3] *= -1  # rating gap, home and toss flip; draw rate does not
    return np.vstack([X, Xs]), np.concatenate([y, 2 - y])


@dataclass
class MatchModel:
    model: LogisticRegression
    ratings: dict[str, float]
    history: pd.DataFrame
    metrics: dict
    cfg: EloConfig = field(default_factory=EloConfig)

    def draw_rate(self, country: str | None) -> float:
        d, n = self.history.attrs["country_draw"].get(country or "?", (0, 0))
        g = self.history.attrs["global_draw"]
        return (d + 10 * g) / (n + 10)

    def predict(self, team_a: str, team_b: str, country: str | None = None,
                toss_winner: str | None = None) -> dict[str, float]:
        """P(team_a wins), P(draw), P(team_b wins). Unknown toss averages both outcomes."""
        ra = self.ratings.get(team_a, self.cfg.start)
        rb = self.ratings.get(team_b, self.cfg.start)
        home = 1 if country == team_a else -1 if country == team_b else 0
        tosses = [1 if toss_winner == team_a else -1] if toss_winner else [1, -1]
        X = np.array([[(ra - rb) / 100, home, t, self.draw_rate(country)] for t in tosses])
        p = self.model.predict_proba(X).mean(0)
        return {f"{team_a} win": float(p[2]), "draw": float(p[1]), f"{team_b} win": float(p[0])}

    def ratings_table(self) -> pd.DataFrame:
        return (pd.DataFrame({"team": list(self.ratings), "rating": list(self.ratings.values())})
                .sort_values("rating", ascending=False).round(0).reset_index(drop=True))

    def rating_history(self) -> pd.DataFrame:
        """Long table of each team's pre-match rating by date, for charts."""
        h = self.history
        a = h[["start_date", "team1", "elo1"]].set_axis(["date", "team", "rating"], axis=1)
        b = h[["start_date", "team2", "elo2"]].set_axis(["date", "team", "rating"], axis=1)
        return pd.concat([a, b]).sort_values("date")


def build_match_model(con, cfg: EloConfig = EloConfig()) -> MatchModel:
    hist, ratings = match_history(con, cfg)
    usable = hist.iloc[cfg.warmup:]
    if cfg.train_from:
        usable = usable[pd.to_datetime(usable.start_date).dt.year >= cfg.train_from]
    if len(usable) < 30:
        raise ValueError(f"Need at least {cfg.warmup + 30} completed matches, found {len(hist)}")
    test = _split_by_time(pd.to_datetime(usable.start_date))
    tr, te = usable[~test], usable[test]

    def fit(df):
        X, y = _symmetrise(_match_features(df), df.y.to_numpy())
        return LogisticRegression(C=1.0, max_iter=1000).fit(X, y)

    m = fit(tr)
    yte = te.y.to_numpy()
    p_model = m.predict_proba(_match_features(te))
    # Baseline: symmetric training base rates (win and loss shares pooled, draw share kept)
    d = float(np.mean(tr.y == 1))
    p_base = np.tile([(1 - d) / 2, d, (1 - d) / 2], (len(te), 1))
    # Tougher baseline: training outcome shares for home, away and neutral sides
    Xs, ys = _symmetrise(_match_features(tr), tr.y.to_numpy())
    home_rates = {h: np.bincount(ys[Xs[:, 1] == h], minlength=3) / max((Xs[:, 1] == h).sum(), 1)
                  for h in (-1, 0, 1)}
    p_home = np.array([home_rates[h] for h in te.home])
    metrics = {
        "trained_on": len(tr), "tested_on": len(te),
        "test_from": str(pd.to_datetime(te.start_date).min().date()) if len(te) else None,
        "model": _scores(p_model, yte), "baseline": _scores(p_base, yte),
        "home_advantage_baseline": _scores(p_home, yte),
        # log-odds effects: win vs loss for the team-sided features, draw vs a result for draw rate
        "coefficients": {
            **dict(zip(["rating gap per 100 (win vs loss)", "home (win vs loss)", "toss (win vs loss)"],
                       np.round(m.coef_[2, :3] - m.coef_[0, :3], 3).tolist())),
            "venue draw rate (draw vs result)":
                round(float(m.coef_[1, 3] - (m.coef_[0, 3] + m.coef_[2, 3]) / 2), 3),
        },
    }
    return MatchModel(fit(usable), ratings, hist, metrics, cfg)  # refit on everything for use


# ---------------------------------------------------------------- fourth-innings chase

def chase_states(con, hist: pd.DataFrame) -> pd.DataFrame:
    """Over-by-over states of every fourth innings: runs needed, wickets in hand, match overs."""
    df = con.execute(
        f"""
        WITH fourth AS (
            SELECT i.match_id, i.batting_team, i.bowling_team FROM innings i
            WHERE i.innings_no = 4 AND NOT i.forfeited
        ),
        target AS (
            SELECT f.match_id,
                   sum(i.runs) FILTER (WHERE i.batting_team = f.bowling_team)
                 - coalesce(sum(i.runs) FILTER (WHERE i.batting_team = f.batting_team), 0) + 1 AS target,
                   sum(i.legal_balls) AS balls_before
            FROM fourth f JOIN innings i ON i.match_id = f.match_id AND i.innings_no < 4
            GROUP BY f.match_id
        ),
        per_over AS (
            SELECT d.match_id, d.over_no, sum(d.runs_total) AS runs,
                   count(*) FILTER (WHERE d.wides = 0 AND d.noballs = 0) AS balls
            FROM deliveries d JOIN fourth USING (match_id) WHERE d.innings_no = 4 GROUP BY ALL
        ),
        per_over_w AS (
            SELECT w.match_id, w.over_no, count(*) AS wkts FROM wickets w JOIN fourth USING (match_id)
            WHERE w.innings_no = 4 AND w.kind NOT IN {NOT_DISMISSED_KINDS} GROUP BY ALL
        ),
        cum AS (
            SELECT p.match_id, p.over_no,
                   sum(p.runs) OVER w - p.runs AS runs_before,
                   coalesce(sum(coalesce(pw.wkts, 0)) OVER w - coalesce(pw.wkts, 0), 0) AS wkts_before,
                   sum(p.balls) OVER w - p.balls AS balls_in_inns
            FROM per_over p LEFT JOIN per_over_w pw USING (match_id, over_no)
            WINDOW w AS (PARTITION BY p.match_id ORDER BY p.over_no)
        )
        SELECT f.match_id, f.batting_team AS chasing, f.bowling_team AS fielding, t.target,
               c.over_no, t.target - c.runs_before AS runs_needed, 10 - c.wkts_before AS wickets_in_hand,
               (t.balls_before + c.balls_in_inns) / 6.0 AS match_overs
        FROM fourth f JOIN target t USING (match_id) JOIN cum c USING (match_id)
        WHERE t.target > 0
        ORDER BY f.match_id, c.over_no
        """
    ).df()
    h = hist.set_index("match_id")
    df = df[df.match_id.isin(h.index)].copy()
    hm = h.loc[df.match_id]
    flip = (hm.team1.to_numpy() != df.chasing.to_numpy())
    sign = np.where(flip, -1, 1)
    df["rating_gap"] = (hm.elo1.to_numpy() - hm.elo2.to_numpy()) * sign / 100
    df["home"] = hm.home.to_numpy() * sign
    df["draw_rate"] = hm.draw_rate.to_numpy()
    y1 = hm.y.to_numpy()
    df["y"] = np.where(flip, 2 - y1, y1)  # from the chasing team's side
    df["start_date"] = hm.start_date.to_numpy()
    return df[(df.runs_needed > 0) & (df.wickets_in_hand > 0)]


def _chase_features(df: pd.DataFrame) -> np.ndarray:
    need = df.runs_needed.to_numpy(float)
    return np.column_stack([np.log1p(need), need / 100, df.wickets_in_hand, df.match_overs / 100,
                            df.rating_gap, df.home, df.draw_rate])


@dataclass
class ChaseModel:
    model: LogisticRegression
    metrics: dict
    match_model: MatchModel

    def predict(self, chasing: str, fielding: str, runs_needed: int, wickets_in_hand: int = 10,
                match_overs: float = 280.0, country: str | None = None) -> dict[str, float]:
        mm = self.match_model
        gap = (mm.ratings.get(chasing, mm.cfg.start) - mm.ratings.get(fielding, mm.cfg.start)) / 100
        home = 1 if country == chasing else -1 if country == fielding else 0
        row = pd.DataFrame([{"runs_needed": runs_needed, "wickets_in_hand": wickets_in_hand,
                             "match_overs": match_overs, "rating_gap": gap, "home": home,
                             "draw_rate": mm.draw_rate(country)}])
        p = self.model.predict_proba(_chase_features(row))[0]
        return {f"{chasing} win": float(p[2]), "draw": float(p[1]), f"{fielding} win": float(p[0])}


def build_chase_model(con, match_model: MatchModel) -> ChaseModel:
    s = chase_states(con, match_model.history)
    if s.match_id.nunique() < 20:
        raise ValueError(f"Need at least 20 fourth-innings chases, found {s.match_id.nunique()}")
    first = s.groupby("match_id").start_date.first()
    test_ids = set(first[_split_by_time(pd.to_datetime(first))].index)
    te_mask = s.match_id.isin(test_ids)
    tr, te = s[~te_mask], s[te_mask]
    fit = lambda df: LogisticRegression(C=1.0, max_iter=2000).fit(_chase_features(df), df.y)
    m = fit(tr)
    classes = list(m.classes_)
    def proba(model, df):  # pad if a class (e.g. tie-as-draw) never occurred in training
        p = model.predict_proba(_chase_features(df))
        out = np.zeros((len(df), 3))
        out[:, classes] = p
        return out
    # evaluate on the state at the start of each held-out chase (one row per match)
    te0 = te.groupby("match_id").head(1)
    base = np.tile(np.bincount(tr.groupby("match_id").y.first(), minlength=3) /
                   tr.match_id.nunique(), (len(te0), 1))
    metrics = {"chases_trained_on": int(tr.match_id.nunique()),
               "chases_tested_on": int(te0.match_id.nunique()),
               "model_at_start_of_chase": _scores(proba(m, te0), te0.y.to_numpy()),
               "baseline": _scores(base, te0.y.to_numpy()),
               "model_all_overs": _scores(proba(m, te), te.y.to_numpy())}
    return ChaseModel(fit(s), metrics, match_model)


# ---------------------------------------------------------------- players

def all_batting_innings(con) -> pd.DataFrame:
    """One row per batter per innings, with date, batting position and venue type.

    Innings with no known batting position (some scorecard-only sources) are left out,
    because the model's prior depends on position.
    """
    return con.execute(
        """
        SELECT s.player_id, s.match_id, s.innings_no, m.start_date, s.position, s.runs,
               s.out::INT AS out,
               CASE WHEN m.home_team IS NULL THEN 'neutral'
                    WHEN m.home_team = s.team THEN 'home' ELSE 'away' END AS venue_type
        FROM batting_scorecard s JOIN matches m USING (match_id)
        WHERE s.player_id IS NOT NULL AND s.position IS NOT NULL
        ORDER BY s.player_id, m.start_date, s.innings_no
        """
    ).df()


def all_bowling_innings(con) -> pd.DataFrame:
    """One row per bowler per innings bowled in: legal balls (NULL if unknown) and wickets."""
    return con.execute(
        """
        SELECT s.player_id, s.match_id, s.innings_no, m.start_date, s.balls, s.wickets,
               CASE WHEN m.home_team IS NULL THEN 'neutral'
                    WHEN m.home_team = s.team THEN 'home' ELSE 'away' END AS venue_type
        FROM bowling_scorecard s JOIN matches m USING (match_id)
        WHERE s.player_id IS NOT NULL
        ORDER BY s.player_id, m.start_date, s.innings_no
        """
    ).df()


def _position_band(pos: float) -> str:
    return "openers (1-2)" if pos <= 2 else "top order (3-5)" if pos <= 5 else \
        "lower middle (6-8)" if pos <= 8 else "tail (9-11)"


def _prequential(df: pd.DataFrame, value: str, decay: float) -> tuple[np.ndarray, np.ndarray]:
    """For each row, decayed sum of `value` and decayed count over that player's EARLIER rows."""
    s_val = np.zeros(len(df))
    s_n = np.zeros(len(df))
    v = df[value].to_numpy(float)
    pid = df.player_id.to_numpy()
    acc_v = acc_n = 0.0
    for i in range(len(df)):
        if i == 0 or pid[i] != pid[i - 1]:
            acc_v = acc_n = 0.0
        s_val[i], s_n[i] = acc_v, acc_n
        acc_v = decay * acc_v + v[i]
        acc_n = decay * acc_n + 1
    return s_val, s_n


def _nb_tail(mu: np.ndarray | float, r: float, k: int) -> np.ndarray:
    """P(X >= k) for a negative binomial with mean mu and size r."""
    mu = np.asarray(mu, float)
    return st.nbinom.sf(k - 1, r, r / (r + mu))


@dataclass
class PlayerModel:
    bat: pd.DataFrame
    bowl: pd.DataFrame
    band_mean: dict[str, float]
    bowl_mean: float
    venue_factor: dict[str, float]
    bowl_venue_factor: dict[str, float]
    nb_size: float
    metrics: dict
    decay: float = 0.97
    k_bat: float = 8.0
    k_bowl: float = 8.0

    def batter(self, player_id: str, venue_type: str = "neutral") -> dict | None:
        """Forecast for this batter's next innings."""
        h = self.bat[self.bat.player_id == player_id]
        if h.empty:
            return None
        w = self.decay ** np.arange(len(h))[::-1]
        recent_pos = h.position.tail(10).median()
        band = _position_band(recent_pos)
        prior = self.band_mean[band]
        mu = (np.sum(w * h.runs) + self.k_bat * prior) / (w.sum() + self.k_bat)
        mu *= self.venue_factor.get(venue_type, 1.0)
        r = self.nb_size
        dist = st.nbinom(r, r / (r + mu))
        return {
            "expected_runs": round(float(mu), 1),
            "median_runs": int(dist.median()),
            "range_80pct": f"{int(dist.ppf(0.1))}-{int(dist.ppf(0.9))}",
            "p_30_plus": round(float(_nb_tail(mu, r, 30)), 3),
            "p_50_plus": round(float(_nb_tail(mu, r, 50)), 3),
            "p_100_plus": round(float(_nb_tail(mu, r, 100)), 3),
            "batting_position_band": band,
            "position_average_runs_per_innings": round(prior, 1),
            "innings_in_history": len(h),
        }

    def bowler(self, player_id: str, venue_type: str = "neutral") -> dict | None:
        """Forecast for this bowler's next innings with the ball (Poisson wickets)."""
        h = self.bowl[self.bowl.player_id == player_id]
        if h.empty:
            return None
        w = self.decay ** np.arange(len(h))[::-1]
        lam = (np.sum(w * h.wickets) + self.k_bowl * self.bowl_mean) / (w.sum() + self.k_bowl)
        lam *= self.bowl_venue_factor.get(venue_type, 1.0)
        return {
            "expected_wickets": round(float(lam), 2),
            "p_1_plus": round(float(st.poisson.sf(0, lam)), 3),
            "p_3_plus": round(float(st.poisson.sf(2, lam)), 3),
            "p_5_plus": round(float(st.poisson.sf(4, lam)), 3),
            "innings_in_history": len(h),
        }


def build_player_model(con, decay: float = 0.97, k_bat: float = 8.0,
                       k_bowl: float = 8.0) -> PlayerModel:
    bat = all_batting_innings(con)
    bowl = all_bowling_innings(con)
    # Band from the median of the batter's previous 10 positions (as `batter()` does at
    # prediction time), not this innings' own position, which isn't known in advance.
    prev_pos = (bat.groupby("player_id").position
                .transform(lambda s: s.shift().rolling(10, min_periods=1).median()))
    bat["band"] = prev_pos.fillna(bat.position).map(_position_band)
    test_mask = _split_by_time(pd.to_datetime(bat.start_date))
    band_mean = bat[~test_mask].groupby("band").runs.mean().to_dict()
    for b in ("openers (1-2)", "top order (3-5)", "lower middle (6-8)", "tail (9-11)"):
        band_mean.setdefault(b, float(bat.runs.mean()) if len(bat) else 20.0)

    # Batting: prequential (prior-only) forecasts for every innings
    s_v, s_n = _prequential(bat, "runs", decay)
    prior = bat.band.map(band_mean).to_numpy()
    mu0 = (s_v + k_bat * prior) / (s_n + k_bat)
    tr = ~test_mask.to_numpy()
    venue_factor = {v: float(bat.runs[tr & (bat.venue_type == v)].sum() /
                             max(mu0[tr & (bat.venue_type == v)].sum(), 1e-9))
                    for v in ("home", "away", "neutral") if (tr & (bat.venue_type == v)).any()}
    mu = mu0 * bat.venue_type.map(venue_factor).fillna(1.0).to_numpy()
    y = bat.runs.to_numpy()

    def nll(log_r):
        r = math.exp(log_r)
        return -np.sum(st.nbinom.logpmf(y[tr], r, r / (r + mu[tr])))
    nb_size = math.exp(optimize.minimize_scalar(nll, bounds=(-4, 4), method="bounded").x)

    te = ~tr
    p50 = _nb_tail(mu[te], nb_size, 50)
    p50_base = _nb_tail(prior[te], nb_size, 50)
    hit50 = (y[te] >= 50).astype(float)

    # Bowling
    btest = _split_by_time(pd.to_datetime(bowl.start_date)).to_numpy()
    bowl_mean = float(bowl.wickets[~btest].mean()) if len(bowl) else 1.0
    b_v, b_n = _prequential(bowl, "wickets", decay)
    lam0 = (b_v + k_bowl * bowl_mean) / (b_n + k_bowl)
    btr = ~btest
    bowl_venue_factor = {v: float(bowl.wickets[btr & (bowl.venue_type == v)].sum() /
                                  max(lam0[btr & (bowl.venue_type == v)].sum(), 1e-9))
                         for v in ("home", "away", "neutral") if (btr & (bowl.venue_type == v)).any()}
    lam = lam0 * bowl.venue_type.map(bowl_venue_factor).fillna(1.0).to_numpy()
    wk = bowl.wickets.to_numpy()
    p3 = st.poisson.sf(2, lam[btest])
    p3_base = st.poisson.sf(2, np.full(btest.sum(), bowl_mean))
    hit3 = (wk[btest] >= 3).astype(float)

    def mae(a, b):
        return round(float(np.mean(np.abs(a - b))), 2) if len(a) else None

    def brier(p, o):
        return round(float(np.mean((p - o) ** 2)), 4) if len(p) else None

    metrics = {
        "batting": {
            "innings_tested_on": int(te.sum()),
            "runs_mae": {"model": mae(mu[te], y[te]), "position_average_baseline": mae(prior[te], y[te])},
            "p50_brier": {"model": brier(p50, hit50), "position_average_baseline": brier(p50_base, hit50)},
            "venue_factors": {k: round(v, 3) for k, v in venue_factor.items()},
            "negative_binomial_size": round(nb_size, 3),
        },
        "bowling": {
            "innings_tested_on": int(btest.sum()),
            "wickets_mae": {"model": mae(lam[btest], wk[btest]),
                            "everyone_average_baseline": mae(np.full(btest.sum(), bowl_mean), wk[btest])},
            "p3_brier": {"model": brier(p3, hit3), "everyone_average_baseline": brier(p3_base, hit3)},
            "venue_factors": {k: round(v, 3) for k, v in bowl_venue_factor.items()},
        },
    }
    return PlayerModel(bat, bowl, band_mean, bowl_mean, venue_factor, bowl_venue_factor,
                       nb_size, metrics, decay, k_bat, k_bowl)
