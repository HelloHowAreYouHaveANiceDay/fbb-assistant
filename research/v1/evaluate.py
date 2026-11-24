"""Fit v1 and run the pre-registered gate (docs/model-v1-prereg.md).

    uv run --with numpy python research/v1/evaluate.py

Reads research/v1/out/dataset.jsonl (from build.py) and the read-only store for rosters and
injury reports. Writes research/v1/out/results.json and data/model_v1.json (the coefficients
of the final fit on every complete season, used by fbb/project_v1.py).
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import random
import sqlite3
import sys
from collections import defaultdict

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fbb.features_v1 import BASE_FEATURES, design  # noqa: E402
from fbb.lineup import Man, optimise  # noqa: E402
from fbb.names import key  # noqa: E402

OUT = ROOT / "research" / "v1" / "out"
SLOT_COUNTS = {'0': 1, '1': 1, '2': 1, '3': 1, '4': 1, '5': 1, '7': 1, '9': 1, '12': 4, '13': 2}
TIERS = [(0, 15), (15, 25), (25, 32), (32, 99)]
RIDGE = 1.0
FOLDS = {2025: [2024], 2026: [2024, 2025], 2027: [2024, 2025, 2026]}
LINEUP_SEASONS = [2025, 2026]


def load():
    rows = [json.loads(l) for l in open(OUT / "dataset.jsonl")]
    return rows


def fit(rows, feats):
    X = np.array([[design(r["f"])[k] for k in feats] for r in rows])
    y = np.array([r["fp"] for r in rows])
    pen = RIDGE * np.eye(len(feats)); pen[0, 0] = 0
    beta = np.linalg.solve(X.T @ X + pen, X.T @ y)
    return dict(zip(feats, beta.tolist()))


def pred(coef, r):
    x = design(r["f"])
    return max(0.0, sum(c * x[k] for k, c in coef.items()))


def metrics(p, y):
    p, y = np.asarray(p), np.asarray(y)
    e = p - y
    return {"n": int(len(y)), "mae": round(float(np.abs(e).mean()), 3), "rmse": round(float(np.sqrt((e ** 2).mean())), 3),
            "bias": round(float(e.mean()), 3)}


def accuracy(rows, preds):
    out = {}
    for name, p in preds.items():
        out[name] = {"all": metrics(p, [r["fp"] for r in rows])}
        for lo, hi in TIERS:
            idx = [i for i, r in enumerate(rows) if lo <= r["min"] < hi]
            out[name][f"min_{lo}_{hi}"] = metrics([p[i] for i in idx], [rows[i]["fp"] for i in idx])
    return out


def evalset(rows, seasons, extra=lambda r: True):
    return [r for r in rows if r["season"] in seasons and r["played"] and r["f"] is not None and extra(r)]


# ---------------- lineup backtest ----------------
def rosters(con, season):
    p = json.loads(con.execute("SELECT payload FROM raw_snapshot WHERE kind='hist_rosters' AND season=? ORDER BY id DESC",
                               (season,)).fetchone()[0])
    out = {}
    for t in p["teams"]:
        out[t["id"]] = [(e["playerPoolEntry"]["player"]["id"], e["playerPoolEntry"]["player"]["fullName"],
                         e["playerPoolEntry"]["player"].get("eligibleSlots") or []) for e in t["roster"]["entries"]]
    return out


def injury_out(con, rows):
    """{date: set(pid)} listed Out on that date's 5PM report, plus join stats."""
    name_pid = defaultdict(set)
    for r in rows:
        name_pid[key(r["name"])].add(r["pid"])
    day_pids = defaultdict(set)
    for r in rows:
        day_pids[r["date"]].add(r["pid"])
    out, stats, misses = defaultdict(set), defaultdict(int), defaultdict(int)
    for rdate, gdate, player, status in con.execute(
            "SELECT report_date, game_date, player, status FROM injury_report WHERE report_hour='05PM'"):
        m, d, y = gdate.split("/")
        date = f"{y}-{m}-{d}"
        if date < "2023-10-24":
            continue
        stats["rows"] += 1
        cands = name_pid.get(key(player, from_report=True), set())
        hit = cands & day_pids.get(date, set())
        if cands:
            stats["name_known"] += 1
        if hit:
            stats["joined"] += 1
            if status == "Out":
                out[date] |= hit
        elif date in day_pids:
            stats["unjoined"] += 1
            misses[player] += 1
    stats["dates"] = len({d for d in out})
    stats["top_unjoined"] = sorted(misses.items(), key=lambda kv: -kv[1])[:15]
    return out, dict(stats)


def lineup_backtest(con, rows, preds_by_key, season, outs, oracle_avail=False, seeds=5):
    """Season totals of actual points started, per fantasy team and per model."""
    ros = rosters(con, season)
    day = defaultdict(dict)          # date -> pid -> row index
    for i, r in enumerate(rows):
        if r["season"] == season:
            day[r["date"]][r["pid"]] = i
    models = list(preds_by_key) + ["oracle"] + [f"random{s}" for s in range(seeds)]
    tot = {m: defaultdict(float) for m in models}           # model -> team -> pts
    weekly = {m: defaultdict(float) for m in models}         # model -> iso week -> pts (all teams)
    for date in sorted(day):
        wk = dt.date.fromisoformat(date).isocalendar()[:2]
        for tid, men_src in ros.items():
            for m in models:
                rng = random.Random(f"{date}|{tid}|{m}")
                men = []
                for pid, name, elig in men_src:
                    i = day[date].get(pid)
                    if i is None:
                        v = 0.0
                    else:
                        r = rows[i]
                        if m == "oracle":
                            v = r["fp"] if r["played"] else 0.0
                        elif m.startswith("random"):
                            v = 1.0 + rng.random()
                        elif pid in outs.get(date, ()) or (oracle_avail and not r["played"]):
                            v = 0.0
                        else:
                            v = preds_by_key[m].get(i, 0.0)
                    men.append(Man(pid=pid, name=name, slot=12, eligible=elig, locked=False, value=max(v, 0.0)))
                tgt = optimise(men, SLOT_COUNTS)
                pts = 0.0
                for pid, s in tgt.items():
                    if s in (12, 13):
                        continue
                    i = day[date].get(pid)
                    if i is not None and rows[i]["played"]:
                        pts += rows[i]["fp"]
                tot[m][tid] += pts
                weekly[m][wk] += pts
    rnd_t = {tid: np.mean([tot[f"random{s}"][tid] for s in range(seeds)]) for tid in ros}
    rnd_w = {wk: np.mean([weekly[f"random{s}"][wk] for s in range(seeds)]) for wk in weekly["oracle"]}
    for s in range(seeds):
        del tot[f"random{s}"], weekly[f"random{s}"]
    tot["random"], weekly["random"] = rnd_t, rnd_w
    return {m: dict(v) for m, v in tot.items()}, {m: dict(v) for m, v in weekly.items()}


def bootstrap(weekly_list, a="v1", b="v0pit", reps=2000, seed=7):
    """95% interval of the summed paired diff, resampling ISO weeks within each season."""
    rng = np.random.default_rng(seed)
    diffs = [np.array([w[a][k] - w[b][k] for k in w[a]]) for w in weekly_list]
    point = float(sum(d.sum() for d in diffs))
    bs = [sum(rng.choice(d, len(d), replace=True).sum() for d in diffs) for _ in range(reps)]
    return {"diff": round(point, 1), "lo95": round(float(np.percentile(bs, 2.5)), 1),
            "hi95": round(float(np.percentile(bs, 97.5)), 1)}


def main():
    rows = load()
    con = sqlite3.connect(f"file:{ROOT / 'data' / 'fbb.db'}?mode=ro", uri=True, timeout=120)
    res = {}
    full = list(design(next(r["f"] for r in rows if r["f"])).keys())
    cands = {"C1_history": BASE_FEATURES, "C2_history+minutes": BASE_FEATURES + ["last3_min_x_rate", "start_share"],
             "C3_full": full}
    # ---- dev: 2023-24, fit through Jan, validate Feb on ----
    dev_tr = evalset(rows, {2024}, lambda r: r["date"] < "2024-02-01")
    dev_va = evalset(rows, {2024}, lambda r: r["date"] >= "2024-02-01")
    dev = {"v0pit": metrics([r["v0pit"] for r in dev_va], [r["fp"] for r in dev_va])}
    for c, feats in cands.items():
        co = fit(dev_tr, feats)
        dev[c] = metrics([pred(co, r) for r in dev_va], [r["fp"] for r in dev_va])
    chosen = min(cands, key=lambda c: dev[c]["mae"])
    res["dev"] = {"n_train": len(dev_tr), "metrics": dev, "chosen": chosen}
    print("dev", json.dumps(res["dev"]))
    feats = cands[chosen]
    # ---- rolling-origin test ----
    res["test"], res["coef"] = {}, {}
    preds_v1 = {}
    for ts, trs in FOLDS.items():
        co = fit(evalset(rows, set(trs)), feats)
        res["coef"][ts] = co
        te = evalset(rows, {ts})
        p1 = [pred(co, r) for r in te]
        res["test"][ts] = accuracy(te, {"v1": p1, "v0pit": [r["v0pit"] for r in te]})
        print(ts, "v1", res["test"][ts]["v1"]["all"], "v0pit", res["test"][ts]["v0pit"]["all"])
        # predictions for EVERY row of the season (DNPs too) for the lineup backtest
        for i, r in enumerate(rows):
            if r["season"] == ts and r["f"] is not None:
                preds_v1[i] = pred(co, r)
    preds_v0 = {i: r["v0pit"] for i, r in enumerate(rows) if r["f"] is not None}
    # ---- lineup points ----
    outs, inj_stats = injury_out(con, rows)
    res["injury_join"] = inj_stats
    print("injury join", inj_stats)
    res["lineup"] = {}
    for label, oa in (("primary", False), ("oracle_availability", True)):
        weeklies, per = [], {}
        for s in LINEUP_SEASONS:
            tot, wk = lineup_backtest(con, rows, {"v1": preds_v1, "v0pit": preds_v0}, s, outs, oracle_avail=oa)
            weeklies.append(wk)
            per[s] = {"team_totals": {m: {str(t): round(v, 1) for t, v in d.items()} for m, d in tot.items()},
                      "totals": {m: round(sum(d.values()), 1) for m, d in tot.items()},
                      "paired": bootstrap([wk]),
                      "teams_v1_better": sum(tot["v1"][t] > tot["v0pit"][t] for t in tot["v1"]),
                      "n_teams": len(tot["v1"])}
            print(label, s, per[s]["totals"], per[s]["paired"], per[s]["teams_v1_better"])
        per["pooled"] = bootstrap(weeklies)
        print(label, "pooled", per["pooled"])
        res["lineup"][label] = per
    # ---- gate ----
    mae_ok = all(res["test"][s]["v1"]["all"]["mae"] < res["test"][s]["v0pit"]["all"]["mae"] for s in FOLDS)
    lp = res["lineup"]["primary"]["pooled"]
    lineup_ok = lp["diff"] > 0 and lp["lo95"] > 0
    res["gate"] = {"mae_each_season": mae_ok, "lineup_pooled_ci_excludes_zero": lineup_ok, "pass": mae_ok and lineup_ok}
    print("gate", res["gate"])
    # ---- production artifact: fit on every complete season ----
    final = fit(evalset(rows, {2024, 2025, 2026}), feats)
    (ROOT / "data" / "model_v1.json").write_text(json.dumps(
        {"model": "v1-linear", "candidate": chosen, "fit_seasons": [2024, 2025, 2026], "ridge": RIDGE,
         "coef": final}, indent=1))
    (OUT / "results.json").write_text(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
