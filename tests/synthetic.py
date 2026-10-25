"""Simulated Cricsheet-format Tests for exercising the prediction models.

These matches are made up (test-only). Teams have hidden strengths and get a home boost,
so tests can check that the models learn the right direction without needing real data.
"""
import json
import random
import zipfile
from pathlib import Path

STRENGTH = {"Australia": 1.35, "India": 1.15, "England": 0.95, "Bangladesh": 0.7}
CITY = {"Australia": "Sydney", "India": "Mumbai", "England": "London", "Bangladesh": "Dhaka"}
HOME_BOOST = 1.15
BALL_CAP = 1500  # legal balls in the match before it is called a draw


def _innings(rng, bat, bowl, bat_s, bowl_s, chase_target, balls_left):
    players = [f"{bat} P{i}" for i in range(1, 12)]
    bowlers = [f"{bowl} P{i}" for i in (11, 10, 9, 8)]
    skill = [1.6, 1.5, 1.7, 1.6, 1.3, 1.1, 0.9, 0.6, 0.4, 0.3, 0.25]
    striker, non, nxt = 0, 1, 2
    runs = wkts = balls = 0
    overs = []
    over = 0
    while wkts < 10 and balls < balls_left:
        dels = []
        bowler = bowlers[over % 4]
        legal = 0
        while legal < 6 and wkts < 10 and balls < balls_left:
            d = {"batter": players[striker], "bowler": bowler, "non_striker": players[non]}
            if rng.random() < 0.01:
                d["runs"] = {"batter": 0, "extras": 1, "total": 1}
                d["extras"] = {"wides": 1}
                dels.append(d)
                runs += 1
                continue
            legal += 1
            balls += 1
            p_out = 0.034 * bowl_s / (bat_s * skill[striker])
            if rng.random() < p_out:
                kind = rng.choice(["bowled", "caught", "lbw", "caught", "run out"])
                d["runs"] = {"batter": 0, "extras": 0, "total": 0}
                d["wickets"] = [{"player_out": players[striker], "kind": kind}]
                dels.append(d)
                wkts += 1
                if nxt < 11:
                    striker, nxt = nxt, nxt + 1
                continue
            r = rng.choices([0, 1, 2, 3, 4, 6], [55, 25, 8, 1, 9, 2])[0]
            d["runs"] = {"batter": r, "extras": 0, "total": r}
            dels.append(d)
            runs += r
            if r % 2:
                striker, non = non, striker
            if chase_target and runs >= chase_target:
                break
        overs.append({"over": over, "deliveries": dels})
        striker, non = non, striker
        over += 1
        if chase_target and runs >= chase_target:
            break
    return {"team": bat, "overs": overs}, runs, wkts, balls


def simulate(n: int = 160, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    names = list(STRENGTH)
    out = []
    for i in range(n):
        t1, t2 = rng.sample(names, 2)
        host = rng.choice([t1, t2, t1, t2, None])
        city = CITY[host] if host else "Dubai"
        s = {t: STRENGTH[t] * (HOME_BOOST if t == host else 1.0) for t in (t1, t2)}
        toss = rng.choice([t1, t2])
        order = [toss, t2 if toss == t1 else t1]
        totals = {t1: 0, t2: 0}
        innings, balls_used, result = [], 0, None
        for k in range(4):
            bat = order[k % 2]
            bowl = order[(k + 1) % 2]
            target = totals[bowl] - totals[bat] + 1 if k == 3 else None
            if k == 3 and target <= 0:
                result = {"winner": bat, "by": {"innings": 1, "runs": 1 - target}}
                break
            inn, r, w, b = _innings(rng, bat, bowl, s[bat], s[bowl], target, BALL_CAP - balls_used)
            innings.append(inn)
            totals[bat] += r
            balls_used += b
            if k == 3:
                if r >= target:
                    result = {"winner": bat, "by": {"wickets": 10 - w}}
                elif w >= 10:
                    result = {"winner": bowl, "by": {"runs": target - 1 - r}}
            if balls_used >= BALL_CAP:
                break
        if result is None:
            result = {"result": "draw"}
        players = {t: [f"{t} P{j}" for j in range(1, 12)] for t in (t1, t2)}
        people = {p: p.replace(" ", "_") for ps in players.values() for p in ps}
        year = 2000 + i // 8
        out.append({
            "meta": {"data_version": "1.1.0"},
            "info": {"dates": [f"{year}-{1 + (i % 8):02d}-10"], "match_type": "Test",
                     "gender": "male", "teams": [t1, t2], "venue": f"{city} Ground", "city": city,
                     "players": players, "registry": {"people": people},
                     "toss": {"winner": toss, "decision": "bat"}, "outcome": result},
            "innings": innings,
        })
    return out


def write_zip(path: Path, n: int = 160, seed: int = 7) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for i, m in enumerate(simulate(n, seed)):
            zf.writestr(f"{5000 + i}.json", json.dumps(m))
    return path
