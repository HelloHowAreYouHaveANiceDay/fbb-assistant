"""Build the point-in-time dataset for the v2 backtest (stdlib).

Walks every regular-season day from 2002-10 in order. Features for all of a day's player_game rows come
from games strictly before the day (`fbb.features_v2.BuilderV2`); then the day is added to the state.

Teammates Out (the minutes-redistribution input):
  - seasons 2021-22 on: the official 5PM-ET report for that game (`report_out_rows`, the production path);
  - seasons before 2021-22 (no reports): TRAINING-TIME PROXY = recent rotation teammates with no box-score
    row in that game (inactive). Production never uses the proxy.
For report-era rows the proxy value of out_min is also stored (`out_min_proxy`) to check agreement.

Synthesised 'inactive' rows (as research/v1/build.py) are added for seasons >= 2023-24 only; they matter only
for the lineup backtest.

    python3 research/v2/build.py
Writes research/v2/out/dataset.jsonl (header line = feature keys, then one list per player-game) and
coverage.json.
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
from fbb.features_v1 import fantasy_points, played, season_of, v0_pit  # noqa: E402
from fbb.features_v2 import TEAM_IDS, BuilderV2, report_out_rows  # noqa: E402
from fbb.names import key  # noqa: E402

START = "2002-08-01"
REPORT_FROM = 2022          # ESPN season label of 2021-22, the first season with official reports
INACTIVE_FROM = 2024
OUT = ROOT / "research" / "v2" / "out"
COLS = ["game_id", "player_id", "name", "team_id", "opp_team_id", "home", "starter", "dnp", "min", "pts", "fgm",
        "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "reb", "ast", "stl", "blk", "tov"]
FKEYS = None


def connect():
    return sqlite3.connect(f"file:{ROOT / 'data' / 'fbb.db'}?mode=ro", uri=True, timeout=120)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    con = connect()
    games = con.execute("SELECT game_id, game_date, home_team_id, away_team_id, final FROM game WHERE season_type=2 "
                        "AND game_date>=? AND home_team_id<=30 AND away_team_id<=30 ORDER BY game_date, game_id",
                        (START,)).fetchall()
    rows = defaultdict(list)
    q = f"SELECT {','.join('pg.' + c for c in COLS)} FROM player_game pg JOIN game g USING(game_id) " \
        "WHERE g.season_type=2 AND g.game_date>=? AND g.home_team_id<=30 AND g.away_team_id<=30"
    for r in con.execute(q, (START,)):
        rows[r[0]].append(dict(zip(COLS, r)))
    by_day = defaultdict(list)
    cov = {"games_by_season": defaultdict(int), "games_without_rows": [], "not_final": 0,
           "report_days": defaultdict(lambda: [0, 0]), "out_resolved": defaultdict(lambda: [0, 0, 0]),
           "team_check": defaultdict(lambda: defaultdict(int))}
    for gid, date, h, a, final in games:
        cov["games_by_season"][season_of(date)] += 1
        if not rows.get(gid):
            cov["games_without_rows"].append([gid, date])
            continue
        if not final:
            cov["not_final"] += 1
        by_day[date].append({"game_id": gid, "home": h, "away": a, "rows": rows[gid]})
    b = BuilderV2()
    t0 = time.time()
    n = n_inactive = 0
    last_team: dict = {}
    cur_season = None
    keys = None
    with open(OUT / "dataset.jsonl", "w") as fh:
        for date in sorted(by_day):
            day = by_day[date]
            season = season_of(date)
            if season != cur_season:
                b.prune(season)
                cur_season = season
                print(season, round(time.time() - t0, 1), n, flush=True)
            teams_today = {g["home"] for g in day} | {g["away"] for g in day}
            present = {r["player_id"] for g in day for r in g["rows"]}
            proxy = {t: b.recent_pool(t, season) - present for t in teams_today}
            if season >= REPORT_FROM:
                out_rows, found = report_out_rows(con, date)
                cov["report_days"][season][0] += 1
                cov["report_days"][season][1] += int(found)
                outs = b.resolve_out(out_rows)
                for tn, pl in out_rows:     # Out rows: total / name known / in a playing team's recent rotation
                    pids = b.by_name.get(key(pl, from_report=True), set())
                    c = cov["out_resolved"][season]
                    c[0] += 1
                    c[1] += int(bool(pids))
                    c[2] += int(any(p in b.recent_pool(t, season) for t in teams_today for p in pids))
            else:
                outs = proxy
            by_team = defaultdict(list)
            if season >= INACTIVE_FROM:
                for pid, (s, t, nm) in last_team.items():
                    if s == season or s == season - 1:
                        by_team[t].append((pid, s, nm))

            def emit(g, pid, nm, tid, opp, home, pl, inact, mins, fp):
                nonlocal keys, n
                f = b.features(pid, tid, opp, home, date, out=outs.get(tid, ()))
                if f is not None and keys is None:
                    keys = list(f)
                    fh.write(json.dumps({"keys": keys}) + "\n")
                pm = None
                if season >= REPORT_FROM and f is not None:
                    pm = round(b.out_features(pid, tid, date, proxy.get(tid, ()))["out_min"], 3)
                fh.write(json.dumps([date, season, g["game_id"], pid, nm if season >= INACTIVE_FROM else None, tid, pl,
                                     inact, mins, fp, round(v0_pit(f), 4) if f else None,
                                     [round(f[k], 5) if isinstance(f[k], float) else f[k] for k in keys] if f else None,
                                     pm]) + "\n")
                n += 1

            for g in day:
                for r in g["rows"]:
                    pl = played(r)
                    emit(g, r["player_id"], r["name"], r["team_id"], r["opp_team_id"], r["home"], int(pl), 0,
                         r["min"] or 0.0, fantasy_points(r) if pl else 0.0)
                if season >= INACTIVE_FROM:
                    for tid, opp, home in ((g["home"], g["away"], 1), (g["away"], g["home"], 0)):
                        for pid, s, nm in by_team.get(tid, []):
                            if pid in present or (s == season - 1 and b.pl.get(pid) and b.pl[pid][-1][1] == season):
                                continue
                            emit(g, pid, nm, tid, opp, home, 0, 1, 0.0, 0.0)
                            n_inactive += 1
            # report team-name check: real team of resolved Out men who have a row today
            if season >= REPORT_FROM:
                real = {r["player_id"]: r["team_id"] for g in day for r in g["rows"]}
                for tid, ps in outs.items():
                    for p in ps:
                        if p in real:
                            cov["team_check"][season]["same" if real[p] == tid else "diff"] += 1
            for g in day:
                for r in g["rows"]:
                    last_team[r["player_id"]] = (season, r["team_id"], r["name"])
            b.add_day(date, day)
    cov["inactive_rows_added"] = n_inactive
    cov["rows"] = n
    cov["secs"] = round(time.time() - t0, 1)
    for k in ("games_by_season", "report_days", "out_resolved"):
        cov[k] = dict(cov[k])
    cov["team_check"] = {s: dict(v) for s, v in cov["team_check"].items()}
    cov["n_games_without_rows"] = len(cov["games_without_rows"])
    cov["team_ids_checked"] = len(TEAM_IDS)
    (OUT / "coverage.json").write_text(json.dumps(cov, indent=1))
    print(json.dumps({k: v for k, v in cov.items() if k != "games_without_rows"}))


if __name__ == "__main__":
    main()
