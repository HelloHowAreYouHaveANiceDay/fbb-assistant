"""EXPLORATORY (not pre-registered, not gated): test-season MAE of a few v2 variants, to see where v2's
accuracy gain comes from. Run after evaluate.py.

    uv run --with numpy python research/v2/ablation.py
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("v2_evaluate", HERE / "evaluate.py")
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)


def main():
    meta, M, ok, names = ev.load_v2()
    season = np.array([m["season"] for m in meta])
    y = np.array([m["fp"] for m in meta])
    evalm = ok & np.array([bool(m["played"]) for m in meta])
    cols_A = [j for j, k in enumerate(names) if k not in ev.NEW_TERMS]
    cols_B = list(range(len(names)))
    res = json.loads((ev.OUT / "results.json").read_text())
    out = {}
    variants = {"A_last3 (v1 terms, long-history features, no redistribution)": (cols_A, 3, None),
                "B_last3 (= v2)": (cols_B, 3, None), "B_last10": (cols_B, 10, None), "B_all": (cols_B, None, None),
                "A_all": (cols_A, None, None)}
    for ts in ev.TESTS:
        te = np.where(evalm & (season == ts))[0]
        out[ts] = {"v1 (from results.json)": res["test"][str(ts)]["v1"]["all"]["mae"]}
        for nm, (cols, k, h) in variants.items():
            idx, w = ev.train_set(season, evalm, ts, k, h)
            beta = ev.fit(M, y, idx, cols, w)
            out[ts][nm] = ev.metrics(ev.predict(M, beta, cols, te), y[te])["mae"]
        print(ts, out[ts], flush=True)
    (ev.OUT / "ablation.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
