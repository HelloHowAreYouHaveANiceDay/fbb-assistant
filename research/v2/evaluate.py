"""Fit v2, pick the candidate on the dev season, and run the pre-registered gate vs v1
(docs/model-v2-prereg.md).

    python3 research/v1/build.py && python3 research/v2/build.py
    uv run --with numpy python research/v2/evaluate.py

v1 (the incumbent) is refit exactly as research/v1/evaluate.py does, from research/v1/out/dataset.jsonl.
Writes research/v2/out/results.json and research/v2/out/model_v2.json (the final fit on every complete
season of the chosen window; copied to data/ only when the gate passes).
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("v1_evaluate", ROOT / "research" / "v1" / "evaluate.py")
v1e = importlib.util.module_from_spec(_spec)   # research/v1/evaluate.py: v1 fit, injury join, lineup backtest, bootstrap
_spec.loader.exec_module(v1e)
from fbb.features_v1 import BASE_FEATURES, design  # noqa: E402
from fbb.features_v2 import NEW_TERMS, design_v2  # noqa: E402

OUT = ROOT / "research" / "v2" / "out"
RIDGE = 1.0
DEV = 2024
TESTS = [2025, 2026, 2027]
LINEUP_SEASONS = [2025, 2026]
FIRST_FIT = 2004            # 2002-03 has no prior-season history
TIERS = [(0, 15), (15, 25), (25, 32), (32, 99)]


def load_v2():
    keys, meta, X, names = None, [], [], None
    for line in open(OUT / "dataset.jsonl"):
        d = json.loads(line)
        if isinstance(d, dict):          # header, written before the first row with features
            keys = d["keys"]
            continue
        date, season, gid, pid, nm, tid, pl, inact, mins, fp, v0, fv, pm = d
        meta.append({"date": date, "season": season, "game_id": gid, "pid": pid, "name": nm, "team": tid,
                     "played": pl, "inactive": inact, "min": mins, "fp": fp, "v0pit": v0, "proxy_out_min": pm})
        if fv is None:
            X.append(None)
        else:
            x = design_v2(dict(zip(keys, fv)))
            if names is None:
                names = list(x)
            X.append([x[k] for k in names])
            meta[-1]["out_min"] = fv[keys.index("out_min")]
    n = len(meta)
    M = np.zeros((n, len(names)))
    ok = np.zeros(n, bool)
    for i, x in enumerate(X):
        if x is not None:
            M[i] = x
            ok[i] = True
    return meta, M, ok, names


def fit(M, y, idx, cols, w=None):
    X = M[np.ix_(idx, cols)]
    yy = y[idx]
    if w is None:
        w = np.ones(len(idx))
    Xw = X * w[:, None]
    pen = RIDGE * np.eye(len(cols)); pen[0, 0] = 0
    return np.linalg.solve(Xw.T @ X + pen, Xw.T @ yy)


def predict(M, beta, cols, idx):
    return np.maximum(0.0, M[np.ix_(idx, cols)] @ beta)


def metrics(p, y):
    e = np.asarray(p) - np.asarray(y)
    return {"n": int(len(e)), "mae": round(float(np.abs(e).mean()), 3), "rmse": round(float(np.sqrt((e ** 2).mean())), 3),
            "bias": round(float(e.mean()), 3)}


def candidates():
    out = {}
    for fs in ("A", "B"):
        for k in (3, 6, 10):
            out[f"{fs}_last{k}"] = (fs, k, None)
        out[f"{fs}_all"] = (fs, None, None)
        for h in (2, 4, 8):
            out[f"{fs}_all_hl{h}"] = (fs, None, h)
    return out


def train_set(season_arr, train_mask, target, k, h):
    lo = FIRST_FIT if k is None else max(FIRST_FIT, target - k)
    idx = np.where(train_mask & (season_arr >= lo) & (season_arr <= target - 1))[0]
    w = None if h is None else 0.5 ** (((target - 1) - season_arr[idx]) / h)
    return idx, w


def main():
    meta, M, ok, names = load_v2()
    cols_A = [j for j, k in enumerate(names) if k not in NEW_TERMS]      # v1's 17 terms
    cols_B = cols_A + [names.index(k) for k in NEW_TERMS]
    cols = {"A": cols_A, "B": cols_B}
    season = np.array([m["season"] for m in meta])
    y = np.array([m["fp"] for m in meta])
    mins = np.array([m["min"] for m in meta])
    playedm = np.array([bool(m["played"]) for m in meta])
    evalm = ok & playedm
    res = {"features": names, "n_rows": len(meta)}

    # ---- data check: report vs box-score-absence proxy (report-era rows) ----
    rep = [(m["out_min"], m["proxy_out_min"]) for m, o in zip(meta, ok) if o and m["season"] >= 2022 and m["played"]]
    a, b = np.array([r[0] for r in rep]), np.array([r[1] for r in rep])
    res["proxy_vs_report"] = {"n": len(rep), "mean_report": round(float(a.mean()), 3), "mean_proxy": round(float(b.mean()), 3),
                              "corr": round(float(np.corrcoef(a, b)[0, 1]), 3),
                              "share_nonzero_report": round(float((a > 0).mean()), 3),
                              "share_nonzero_proxy": round(float((b > 0).mean()), 3)}
    pre = np.array([m.get("out_min", 0.0) for m, o in zip(meta, ok) if o and m["season"] < 2022 and m["played"]])
    res["proxy_vs_report"]["pre2022_mean_proxy"] = round(float(pre.mean()), 3)
    res["proxy_vs_report"]["pre2022_share_nonzero"] = round(float((pre > 0).mean()), 3)
    print("proxy_vs_report", res["proxy_vs_report"], flush=True)

    # ---- dev: target 2023-24, fit on <= 2022-23 ----
    dev_idx = np.where(evalm & (season == DEV))[0]
    dev = {"v0pit": metrics([meta[i]["v0pit"] for i in dev_idx], y[dev_idx])}
    for c, (fs, k, h) in candidates().items():
        idx, w = train_set(season, evalm, DEV, k, h)
        beta = fit(M, y, idx, cols[fs], w)
        dev[c] = metrics(predict(M, beta, cols[fs], dev_idx), y[dev_idx])
        dev[c]["n_train"] = int(len(idx))
        print("dev", c, dev[c], flush=True)
    order = list(candidates())     # A before B; shorter / heavier windows first within ties is handled below
    best = min(dev[c]["mae"] for c in order)
    tied = [c for c in order if dev[c]["mae"] <= best + 0.001]
    pref = lambda c: (c[0] != "A", {"last3": 0, "last6": 1, "last10": 2, "all_hl2": 3, "all_hl4": 4, "all_hl8": 5,
                                    "all": 6}[c[2:]])
    chosen = sorted(tied, key=pref)[0]
    res["dev"] = {"target": DEV, "metrics": dev, "best_mae": best, "tied_within_0.001": tied, "chosen": chosen}
    print("chosen", chosen, flush=True)
    fs, k, h = candidates()[chosen]

    # ---- v1 incumbent, exactly as research/v1/evaluate.py ----
    rows1 = v1e.load()
    full1 = list(design(next(r["f"] for r in rows1 if r["f"])).keys())
    c1 = {"C1_history": BASE_FEATURES, "C2_history+minutes": BASE_FEATURES + ["last3_min_x_rate", "start_share"],
          "C3_full": full1}
    d_tr = v1e.evalset(rows1, {2024}, lambda r: r["date"] < "2024-02-01")
    d_va = v1e.evalset(rows1, {2024}, lambda r: r["date"] >= "2024-02-01")
    v1dev = {}
    for c, f in c1.items():
        co = v1e.fit(d_tr, f)
        v1dev[c] = v1e.metrics([v1e.pred(co, r) for r in d_va], [r["fp"] for r in d_va])
    v1chosen = min(v1dev, key=lambda c: v1dev[c]["mae"])
    res["v1_dev"] = {"metrics": v1dev, "chosen": v1chosen}
    print("v1 dev chosen", v1chosen, flush=True)
    p1 = {}
    for ts, trs in v1e.FOLDS.items():
        co = v1e.fit(v1e.evalset(rows1, set(trs)), c1[v1chosen])
        for r in rows1:
            if r["season"] == ts and r["f"] is not None:
                p1[(r["game_id"], r["pid"])] = v1e.pred(co, r)

    # ---- v2 rolling-origin test ----
    p2 = {}
    res["test"], res["coef"] = {}, {}
    for ts in TESTS:
        idx, w = train_set(season, evalm, ts, k, h)
        beta = fit(M, y, idx, cols[fs], w)
        res["coef"][ts] = dict(zip([names[j] for j in cols[fs]], beta.tolist()))
        sidx = np.where(ok & (season == ts))[0]
        pr = predict(M, beta, cols[fs], sidx)
        for i, v in zip(sidx, pr):
            p2[(meta[i]["game_id"], meta[i]["pid"])] = float(v)
        te = [i for i in sidx if playedm[i] and (meta[i]["game_id"], meta[i]["pid"]) in p1]
        n_v2_only = int(sum(1 for i in sidx if playedm[i]) - len(te))
        yy = y[te]
        pv2 = [p2[(meta[i]["game_id"], meta[i]["pid"])] for i in te]
        pv1 = [p1[(meta[i]["game_id"], meta[i]["pid"])] for i in te]
        pv0 = [meta[i]["v0pit"] for i in te]
        acc = {}
        for nm, p in (("v2", pv2), ("v1", pv1), ("v0pit", pv0)):
            acc[nm] = {"all": metrics(p, yy)}
            for lo, hi in TIERS:
                j = [t for t, i in enumerate(te) if lo <= mins[i] < hi]
                acc[nm][f"min_{lo}_{hi}"] = metrics([p[t] for t in j], yy[j])
        acc["n_train"] = int(len(idx))
        acc["played_rows_v2_only_excluded"] = n_v2_only
        res["test"][ts] = acc
        print("test", ts, {m: acc[m]["all"] for m in ("v2", "v1", "v0pit")}, "excluded", n_v2_only, flush=True)

    # ---- lineup points (research/v1 method) ----
    lrows = [m for m in meta if m["season"] >= 2024]
    pv = {"v2": {}, "v1": {}, "v0pit": {}}
    miss = {"v1": 0, "v2": 0}
    for i, r in enumerate(lrows):
        kk = (r["game_id"], r["pid"])
        if r["season"] not in LINEUP_SEASONS:
            continue
        for nm, src in (("v2", p2), ("v1", p1)):
            if kk in src:
                pv[nm][i] = src[kk]
            elif r["v0pit"] is not None:
                miss[nm] += 1
        if r["v0pit"] is not None:
            pv["v0pit"][i] = r["v0pit"]
    res["lineup_pred_missing"] = miss
    con = sqlite3.connect(f"file:{ROOT / 'data' / 'fbb.db'}?mode=ro", uri=True, timeout=120)
    outs, inj = v1e.injury_out(con, lrows)
    res["injury_join"] = inj
    print("injury join", {k: v for k, v in inj.items() if k != "top_unjoined"}, "missing preds", miss, flush=True)
    res["lineup"] = {}
    for label, oa in (("primary", False), ("oracle_availability", True)):
        wks, per = [], {}
        for s in LINEUP_SEASONS:
            tot, wk = v1e.lineup_backtest(con, lrows, pv, s, outs, oracle_avail=oa)
            wks.append(wk)
            per[s] = {"totals": {m: round(sum(d.values()), 1) for m, d in tot.items()},
                      "v2_minus_v1": v1e.bootstrap([wk], "v2", "v1"),
                      "v1_minus_v0pit": v1e.bootstrap([wk], "v1", "v0pit"),
                      "team_diffs_v2_v1": sorted(round(tot["v2"][t] - tot["v1"][t], 1) for t in tot["v2"]),
                      "teams_v2_better": int(sum(tot["v2"][t] > tot["v1"][t] for t in tot["v2"])),
                      "n_teams": len(tot["v2"])}
            print(label, s, per[s]["totals"], per[s]["v2_minus_v1"], per[s]["v1_minus_v0pit"], flush=True)
        per["pooled"] = {"v2_minus_v1": v1e.bootstrap(wks, "v2", "v1"), "v1_minus_v0pit": v1e.bootstrap(wks, "v1", "v0pit"),
                         "v2_minus_v0pit": v1e.bootstrap(wks, "v2", "v0pit")}
        print(label, "pooled", per["pooled"], flush=True)
        res["lineup"][label] = per

    # ---- gate ----
    mae_ok = {ts: res["test"][ts]["v2"]["all"]["mae"] < res["test"][ts]["v1"]["all"]["mae"] for ts in TESTS}
    lp = res["lineup"]["primary"]["pooled"]["v2_minus_v1"]
    lineup_ok = lp["diff"] > 0 and lp["lo95"] > 0
    res["gate"] = {"mae_each_season": mae_ok, "mae_ok": all(mae_ok.values()), "lineup_pooled_ci_excludes_zero": lineup_ok,
                   "pass": all(mae_ok.values()) and lineup_ok}
    print("gate", res["gate"], flush=True)

    # ---- production artifact: chosen window ending at the last complete season (2025-26) ----
    idx, w = train_set(season, evalm, 2027, k, h)
    beta = fit(M, y, idx, cols[fs], w)
    fit_seasons = sorted({int(s) for s in season[idx]})
    art = {"model": "v2-linear", "candidate": chosen, "fit_seasons": fit_seasons, "ridge": RIDGE,
           "recency_halflife": h, "coef": dict(zip([names[j] for j in cols[fs]], beta.tolist()))}
    (OUT / "model_v2.json").write_text(json.dumps(art, indent=1))
    if res["gate"]["pass"]:
        (ROOT / "data" / "model_v2.json").write_text(json.dumps(art, indent=1))
    (OUT / "results.json").write_text(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
