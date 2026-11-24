"""Point-in-time features for projection v1 (stdlib only; the backtest and production share this code).

`Builder` consumes regular-season games IN DATE ORDER via `add_day(date, games)`. After it has
seen every day before D, `features(pid, team, opp, home, D)` uses only games strictly before D.
In the backtest, call `features` for all of D's rows first and `add_day(D, ...)` after.

A game is a dict {"home": team_id, "away": team_id, "rows": [player_game dicts]}. A row needs
player_id, team_id, min, starter, dnp and the box-score counts used by `fantasy_points`.
"""
from __future__ import annotations

import datetime as dt
import math

# league scoring (Barchskitball), per game
SCORING = {"pts": 1, "reb": 1, "ast": 1.5, "stl": 2, "blk": 2.5, "tov": -1, "fgm": 1, "fga": -1,
           "ftm": 1, "fta": -1, "tpm": 0.5}

HALFLIFE = 6.0          # games, EWMA of points / minutes
EWMA_WINDOW = 40        # played games looked back for the EWMA
PRIOR_SEASON_W = 0.5    # extra weight multiplier on last season's games inside the EWMA
MIN_GAMES = 3           # v0 rule: an average needs >= 3 games
OPP_SHRINK = 10.0       # team-games of shrinkage for opponent context
MISSING_MIN_FLOOR = 12.0


def fantasy_points(r) -> float:
    return float(sum((r[k] or 0) * w for k, w in SCORING.items()))


def season_of(date: str) -> int:
    """ESPN label: NBA 2025-26 = 2026."""
    y, m = int(date[:4]), int(date[5:7])
    return y + 1 if m >= 8 else y


def _d(s: str) -> dt.date:
    return dt.date(int(s[:4]), int(s[5:7]), int(s[8:10]))


def played(r) -> bool:
    return not r.get("dnp") and (r.get("min") or 0) > 0


def _poss(rows) -> float:
    return sum((r["fga"] or 0) + 0.44 * (r["fta"] or 0) - (r["oreb"] or 0) + (r["tov"] or 0) for r in rows)


class Builder:
    def __init__(self):
        self.pl: dict[int, list] = {}        # pid -> [(date, season, min, fp, starter, team)] played games
        self.team_games: dict[int, list] = {}  # tid -> [(date, season, frozenset(played pids))]
        self.team_ctx: dict[tuple, list] = {}  # (tid, season) -> [games, fp_allowed, pace]
        self.league: dict[int, list] = {}    # season -> [team_games, fp_allowed, pace]
        self._ewm_cache: dict = {}

    # ---- state ----
    def add_day(self, date: str, games: list[dict]) -> None:
        season = season_of(date)
        self._ewm_cache = {}
        for g in games:
            by = {g["home"]: [], g["away"]: []}
            for r in g["rows"]:
                if r["team_id"] in by:
                    by[r["team_id"]].append(r)
            pace = (_poss(by[g["home"]]) + _poss(by[g["away"]])) / 2.0
            for tid, opp in ((g["home"], g["away"]), (g["away"], g["home"])):
                rows = by[tid]
                pl = [r for r in rows if played(r)]
                for r in pl:
                    self.pl.setdefault(r["player_id"], []).append(
                        (date, season, float(r["min"]), fantasy_points(r), int(bool(r.get("starter"))), tid))
                self.team_games.setdefault(tid, []).append((date, season, frozenset(r["player_id"] for r in pl)))
                allowed = sum(fantasy_points(r) for r in pl)
                c = self.team_ctx.setdefault((opp, season), [0, 0.0, 0.0])   # what OPP allowed
                c[0] += 1; c[1] += allowed; c[2] += pace
                lg = self.league.setdefault(season, [0, 0.0, 0.0])
                lg[0] += 1; lg[1] += allowed; lg[2] += pace

    # ---- helpers ----
    def _ewm(self, pid: int, season: int):
        key = (pid, season)
        if key in self._ewm_cache:
            return self._ewm_cache[key]
        hist = [g for g in self.pl.get(pid, []) if g[1] in (season, season - 1)][-EWMA_WINDOW:]
        a = 0.5 ** (1.0 / HALFLIFE)
        sw = sfp = smin = 0.0
        n = len(hist)
        for i, g in enumerate(hist):
            w = a ** (n - 1 - i) * (1.0 if g[1] == season else PRIOR_SEASON_W)
            sw += w; sfp += w * g[3]; smin += w * g[2]
        out = None if sw == 0 else (sfp / sw, smin / sw, sfp / smin if smin > 0 else 0.0)
        self._ewm_cache[key] = out
        return out

    def _rel(self, tid: int, season: int, idx: int) -> float:
        c, lg = self.team_ctx.get((tid, season)), self.league.get(season)
        if not c or not lg or lg[0] == 0 or lg[idx] == 0:
            return 0.0
        rel = (c[idx] / c[0]) / (lg[idx] / lg[0]) - 1.0
        return rel * c[0] / (c[0] + OPP_SHRINK)

    # ---- features ----
    def features(self, pid: int, team: int, opp: int, home: int, date: str) -> dict | None:
        """None when the player has no regular-season game this season or last (no history)."""
        season = season_of(date)
        hist = self.pl.get(pid, [])
        cur = [g for g in hist if g[1] == season]
        prev = [g for g in hist if g[1] == season - 1]
        if not cur and not prev:
            return None
        D = _d(date)
        mean = lambda xs: sum(xs) / len(xs) if xs else None
        f: dict = {"n_cur": len(cur)}
        f["season_fp"] = mean([g[3] for g in cur])
        f["season_min"] = mean([g[2] for g in cur])
        l15 = [g for g in cur if (D - _d(g[0])).days <= 15]
        f["n_l15"] = len(l15)
        f["l15_fp"] = mean([g[3] for g in l15])
        f["prior_fp"] = mean([g[3] for g in prev]) if len(prev) >= MIN_GAMES else None
        f["prior_min"] = mean([g[2] for g in prev]) if len(prev) >= MIN_GAMES else None
        ew = self._ewm(pid, season)
        f["ewm_fp"], f["ewm_min"], f["ewm_rate"] = ew
        last = cur[-3:] or prev[-3:]
        f["last3_min"] = mean([g[2] for g in last])
        f["start_share"] = mean([g[4] for g in (cur[-5:] or prev[-5:])])
        # schedule: rest from the team's own previous game this season
        tg = [g for g in self.team_games.get(team, []) if g[1] == season]
        rest = (D - _d(tg[-1][0])).days if tg else 5
        f["rest"] = min(rest, 5)
        f["b2b"] = int(rest == 1)
        f["home"] = int(bool(home))
        # team games (this team) since his last played game: injury / DNP streak
        last_played = cur[-1][0] if cur else ""
        f["missed"] = min(sum(1 for g in tg if g[0] > last_played), 10) if cur else 0
        # teammates who were playing recently but sat the team's last game
        miss_min = 0.0
        if tg:
            recent = set().union(*[g[2] for g in tg[-11:-1]]) if len(tg) > 1 else set()
            for q in recent - tg[-1][2] - {pid}:
                if self.pl[q][-1][5] != team:      # traded away, not missing
                    continue
                e = self._ewm(q, season)
                if e and e[1] >= MISSING_MIN_FLOOR:
                    miss_min += e[1]
        f["team_missing_min"] = miss_min
        f["opp_fp_rel"] = self._rel(opp, season, 1)
        f["opp_pace_rel"] = self._rel(opp, season, 2)
        f["team_pace_rel"] = self._rel(team, season, 2)
        return f


def v0_pit(f: dict | None) -> float:
    """v0 minus its ESPN component: 0.6 x last-15-days + 0.4 x season, each with >= 3 games."""
    if f is None:
        return 0.0
    parts = []
    if f["n_l15"] >= MIN_GAMES:
        parts.append((0.6, f["l15_fp"]))
    if f["n_cur"] >= MIN_GAMES:
        parts.append((0.4, f["season_fp"]))
    if parts:
        return sum(w * v for w, v in parts) / sum(w for w, _ in parts)
    if f["prior_fp"] is not None:
        return f["prior_fp"]
    return f["season_fp"] if f["season_fp"] is not None else f["ewm_fp"]


# ---- v1 design matrix (linear model; coefficients are fitted in research/v1 and shipped as JSON) ----
def design(f: dict) -> dict:
    ew = f["ewm_fp"]
    sfp = f["season_fp"] if f["season_fp"] is not None else ew
    l15 = f["l15_fp"] if f["l15_fp"] is not None else sfp
    has_prior = f["prior_fp"] is not None
    pri = f["prior_fp"] if has_prior else sfp
    small = 1.0 / (1.0 + f["n_cur"])
    rate = f["ewm_rate"]
    l3m = f["last3_min"] if f["last3_min"] is not None else f["ewm_min"]
    return {
        "const": 1.0,
        "ewm_fp": ew,
        "season_fp": sfp,
        "l15_fp": l15,
        "prior_fp": pri,
        "prior_gap_small": small * (pri - ew),
        "last3_min_x_rate": l3m * rate,
        "start_share": f["start_share"] or 0.0,
        "b2b_x_fp": f["b2b"] * ew,
        "rest3_x_fp": int(f["rest"] >= 3) * ew,
        "home_x_fp": f["home"] * ew,
        "back_from_miss_x_fp": int(f["missed"] > 0) * ew,
        "log_missed_x_fp": math.log1p(f["missed"]) * ew,
        "team_missing_min_x_rate": f["team_missing_min"] * rate,
        "opp_fp_rel_x_fp": f["opp_fp_rel"] * ew,
        "opp_pace_rel_x_fp": f["opp_pace_rel"] * ew,
        "team_pace_rel_x_fp": f["team_pace_rel"] * ew,
    }


BASE_FEATURES = ["const", "ewm_fp", "season_fp", "l15_fp", "prior_fp", "prior_gap_small"]


def predict(coef: dict, f: dict | None) -> float | None:
    if f is None:
        return None
    x = design(f)
    return max(0.0, sum(c * x[k] for k, c in coef.items()))
