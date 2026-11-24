"""Per-game projection for one day, in THIS league's scoring.

v0 is a transparent blend, not a fitted model -- the fitted model is the research track
(docs/research-plan.md). Every input is an ESPN `appliedAverage`, i.e. already in league points:

    proj = w_espn * ESPN season projection  +  w_l15 * last-15 actual  +  w_season * season actual

missing inputs drop out and the weights renormalise. Then availability:
    OUT / SUSPENSION / IR -> 0;  DAY_TO_DAY -> x DTD_FACTOR;  no NBA game that day -> 0.
"""
from __future__ import annotations

from .consts import OUT_STATUSES

WEIGHTS = {"espn": 0.5, "l15": 0.3, "season": 0.2}
DTD_FACTOR = 0.75
MIN_GAMES_FOR_ACTUALS = 3


def split_avgs(player: dict, season: int) -> dict:
    out = {}
    for s in player.get("stats") or []:
        if s.get("seasonId") != season or s.get("scoringPeriodId", 0) != 0:
            continue
        src, split = s.get("statSourceId"), s.get("statSplitTypeId")
        avg = s.get("appliedAverage")
        gp = (s.get("stats") or {}).get("42")
        if avg is None:
            continue
        if src == 1 and split == 0:
            out["espn"] = avg
        elif src == 0 and split == 2 and (gp or 0) >= MIN_GAMES_FOR_ACTUALS:
            out["l15"] = avg
        elif src == 0 and split == 0 and (gp or 0) >= MIN_GAMES_FOR_ACTUALS:
            out["season"] = avg
    return out


def project(player: dict, season: int, has_game: bool, injury: str | None) -> tuple[float, str, dict]:
    avgs = split_avgs(player, season)
    w = {k: v for k, v in WEIGHTS.items() if k in avgs}
    base = sum(avgs[k] * v for k, v in w.items()) / sum(w.values()) if w else 0.0
    if not has_game:
        return 0.0, "no game", avgs
    if injury in OUT_STATUSES:
        return 0.0, injury, avgs
    if injury == "DAY_TO_DAY":
        return base * DTD_FACTOR, "DTD", avgs
    return base, "", avgs
