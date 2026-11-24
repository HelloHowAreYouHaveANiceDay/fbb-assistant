"""Projection v1: a linear model on point-in-time box-score features, in league points.

Method, backtest and caveats are in docs/model-v1.md. The coefficients live in data/model_v1.json;
rebuild them with research/v1/build.py + evaluate.py. Features come from `fbb.features_v1`, the
same code the backtest uses, run over the local box-score store (`game` / `player_game`).

Availability is applied the same way as v0: OUT/SUSPENSION/IR -> 0, DAY_TO_DAY -> x0.75, no game -> 0.
A man with no usable history, or a store more than STALE_DAYS behind, falls back to v0.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sqlite3

from .consts import OUT_STATUSES
from .features_v1 import Builder, predict, season_of
from .project import DTD_FACTOR

MODEL_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "model_v1.json"
STALE_DAYS = 3


def load_model(path: pathlib.Path | str = MODEL_PATH) -> dict | None:
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return None


def build_state(con: sqlite3.Connection, date: str) -> tuple[Builder, str | None]:
    """Feed every regular-season NBA game of this season and last, strictly before `date`."""
    season = season_of(date)
    start = f"{season - 2}-08-01"
    cols = ["game_id", "player_id", "name", "team_id", "opp_team_id", "home", "starter", "dnp", "min", "pts", "fgm",
            "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "reb", "ast", "stl", "blk", "tov"]
    games = con.execute(
        "SELECT game_id, game_date, home_team_id, away_team_id FROM game WHERE season_type=2 AND game_date>=? "
        "AND game_date<? AND home_team_id<=30 AND away_team_id<=30 ORDER BY game_date, game_id", (start, date)).fetchall()
    rows: dict[int, list] = {}
    for r in con.execute(
            f"SELECT {','.join('pg.' + c for c in cols)} FROM player_game pg JOIN game g USING(game_id) WHERE "
            "g.season_type=2 AND g.game_date>=? AND g.game_date<? AND g.home_team_id<=30 AND g.away_team_id<=30",
            (start, date)):
        rows.setdefault(r[0], []).append(dict(zip(cols, r)))
    b = Builder()
    day, cur = [], None
    for gid, gdate, h, a in games:
        if gid not in rows:
            continue
        if gdate != cur and day:
            b.add_day(cur, day)
            day = []
        cur = gdate
        day.append({"home": h, "away": a, "rows": rows[gid]})
    if day:
        b.add_day(cur, day)
    return b, cur


def is_stale(last_date: str | None, date: str) -> bool:
    if last_date is None:
        return True
    return (dt.date.fromisoformat(date) - dt.date.fromisoformat(last_date)).days > STALE_DAYS


def project_v1(builder: Builder | None, model: dict | None, pid: int, team: int | None, opp: int | None,
               home: bool, date: str, has_game: bool, injury: str | None) -> tuple[float, str, dict] | None:
    """(value, note, inputs) or None when v1 can't project him (caller falls back to v0)."""
    if builder is None or model is None or team is None or opp is None:
        return None
    f = builder.features(pid, team, opp, int(bool(home)), date)
    base = predict(model["coef"], f)
    if base is None:
        return None
    inputs = {"v1": round(base, 2), "ewm_fp": round(f["ewm_fp"], 2), "n_cur": f["n_cur"], "missed": f["missed"]}
    if not has_game:
        return 0.0, "no game", inputs
    if injury in OUT_STATUSES:
        return 0.0, injury, inputs
    if injury == "DAY_TO_DAY":
        return base * DTD_FACTOR, "DTD", inputs
    return base, "", inputs
