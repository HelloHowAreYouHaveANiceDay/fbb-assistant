"""Build the point-in-time dataset for the v1 backtest (stdlib).

Walks every regular-season day from 2023-10-24 in order: features for all of that day's
player_game rows (played or DNP) come from games strictly before the day, then the day is added
to the state. Writes research/v1/out/dataset.jsonl (one row per player-game) and
coverage.json (data checks).

    python3 research/v1/build.py
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import time
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fbb.features_v1 import Builder, fantasy_points, played, season_of, v0_pit  # noqa: E402

START = "2023-10-24"
# season_type 2 also carries the All-Star weekend games (team ids > 30); they are not NBA games for us.
OUT = ROOT / "research" / "v1" / "out"
COLS = ["game_id", "player_id", "name", "team_id", "opp_team_id", "home", "starter", "dnp", "min", "pts", "fgm",
        "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "reb", "ast", "stl", "blk", "tov"]


def connect():
    return sqlite3.connect(f"file:{ROOT / 'data' / 'fbb.db'}?mode=ro", uri=True, timeout=120)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    con = connect()
    games = con.execute("SELECT game_id, game_date, home_team_id, away_team_id, final FROM game "
                        "WHERE season_type=2 AND game_date>=? AND home_team_id<=30 AND away_team_id<=30 ORDER BY game_date, game_id", (START,)).fetchall()
    rows = defaultdict(list)
    q = f"SELECT {','.join('pg.' + c for c in COLS)} FROM player_game pg JOIN game g USING(game_id) " \
        "WHERE g.season_type=2 AND g.game_date>=? AND g.home_team_id<=30 AND g.away_team_id<=30"
    for r in con.execute(q, (START,)):
        rows[r[0]].append(dict(zip(COLS, r)))
    by_day = defaultdict(list)
    cov = {"games_by_season": defaultdict(int), "games_without_rows": [], "not_final": 0}
    for gid, date, h, a, final in games:
        cov["games_by_season"][season_of(date)] += 1
        if not rows.get(gid):
            cov["games_without_rows"].append([gid, date])
            continue
        if not final:
            cov["not_final"] += 1
        by_day[date].append({"game_id": gid, "home": h, "away": a, "rows": rows[gid]})
    b = Builder()
    t0 = time.time()
    n = n_inactive = 0
    last_team: dict[int, tuple] = {}     # pid -> (season, team, name) of his latest box-score row
    with open(OUT / "dataset.jsonl", "w") as fh:
        for date in sorted(by_day):
            day = by_day[date]
            season = season_of(date)
            # ESPN box scores list only the ACTIVE list (incl. coach's-decision DNPs); inactive / injured men
            # have no row. Add an 'inactive' row for every man whose latest row (this season, or last season
            # if he has none yet) was with a team playing today, so the lineup backtest sees he had a game.
            by_team = defaultdict(list)
            for pid, (s, t, nm) in last_team.items():
                if s == season or s == season - 1:
                    by_team[t].append((pid, s, nm))
            seen = {r["player_id"] for g in day for r in g["rows"]}   # real rows anywhere today win
            for g in day:
                for r in g["rows"]:
                    f = b.features(r["player_id"], r["team_id"], r["opp_team_id"], r["home"], date)
                    fh.write(json.dumps({
                        "date": date, "season": season, "game_id": g["game_id"], "pid": r["player_id"],
                        "name": r["name"], "team": r["team_id"], "played": int(played(r)), "inactive": 0,
                        "min": r["min"] or 0.0, "fp": fantasy_points(r) if played(r) else 0.0, "f": f,
                        "v0pit": v0_pit(f) if f else None,
                    }) + "\n")
                    n += 1
                for tid, opp, home in ((g["home"], g["away"], 1), (g["away"], g["home"], 0)):
                    for pid, s, nm in by_team.get(tid, []):
                        if pid in seen or (s == season - 1 and b.pl.get(pid) and b.pl[pid][-1][1] == season):
                            continue
                        f = b.features(pid, tid, opp, home, date)
                        fh.write(json.dumps({
                            "date": date, "season": season, "game_id": g["game_id"], "pid": pid, "name": nm,
                            "team": tid, "played": 0, "inactive": 1, "min": 0.0, "fp": 0.0, "f": f,
                            "v0pit": v0_pit(f) if f else None}) + "\n")
                        n_inactive += 1
            for g in day:
                for r in g["rows"]:
                    last_team[r["player_id"]] = (season, r["team_id"], r["name"])
            b.add_day(date, day)
    cov["inactive_rows_added"] = n_inactive
    cov["rows"] = n
    cov["secs"] = round(time.time() - t0, 1)
    cov["games_by_season"] = dict(cov["games_by_season"])
    cov["n_games_without_rows"] = len(cov["games_without_rows"])
    (OUT / "coverage.json").write_text(json.dumps(cov, indent=1))
    print(json.dumps({k: v for k, v in cov.items() if k != "games_without_rows"}))


if __name__ == "__main__":
    main()
