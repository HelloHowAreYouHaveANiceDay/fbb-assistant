"""fbb -- fantasy basketball co-manager CLI.

  python -m fbb.cli lineup [--period N] [--send] [--quiet]
      Pull roster + schedule, project today in league scoring, optimise, and (with --send) write the
      lineup to ESPN, then re-read to verify. --quiet prints nothing when no move was needed
      (cron mode: empty stdout = no Slack post).
  python -m fbb.cli backup
      Snapshot data/fbb.db (see fbb/backup.py).
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from . import db, espn
from .consts import BENCH, IR, SLOTS
from .lineup import Man, describe, moves_for, optimise, total
from .project import project

SEASON = 2027
LEAGUE = 211696
MODEL = "v0-blend"


def load_day(period: int | None):
    lg = espn.league(SEASON, LEAGUE, ["mSettings", "mTeam", "mRoster"], **({"scoringPeriodId": period} if period else {}))
    period = period or lg["scoringPeriodId"]
    swid = espn.my_swid()
    team = next(t for t in lg["teams"] if swid in (t.get("owners") or []))
    sched = espn.pro_schedule(SEASON)
    games = {}
    for pt in sched["settings"]["proTeams"]:
        for g in (pt.get("proGamesByScoringPeriod") or {}).get(str(period), []):
            games[pt["id"]] = g
    return lg, team, games, period


def build_men(team, games, now_ms: int) -> list[Man]:
    men = []
    for e in team["roster"]["entries"]:
        ppe = e["playerPoolEntry"]
        p = ppe["player"]
        g = games.get(p.get("proTeamId"))
        started = bool(g and g.get("date", 0) <= now_ms)
        inj = p.get("injuryStatus") or e.get("injuryStatus")
        val, note, inputs = project(p, SEASON, g is not None, inj)
        men.append(Man(pid=p["id"], name=p["fullName"], slot=e["lineupSlotId"], eligible=p.get("eligibleSlots") or [],
                       locked=bool(ppe.get("lineupLocked")) or started, value=round(val, 2), has_game=g is not None,
                       note=note, extra={"inj": inj, "inputs": inputs}))
    return men


def cmd_lineup(a) -> int:
    con = db.connect()
    lg, team, games, period = load_day(a.period)
    db.snapshot(con, "league_roster", {"settings": lg["settings"]["rosterSettings"], "team": team}, SEASON, LEAGUE, period)
    counts = lg["settings"]["rosterSettings"]["lineupSlotCounts"]
    men = build_men(team, games, int(time.time() * 1000))
    target = optimise(men, counts)
    moves = moves_for(men, target)
    before = {m.pid: m.slot for m in men}
    t0, t1 = total(men, before), total(men, target)
    status, resp, verified = None, None, None
    if moves and a.send:
        status, resp = espn.set_lineup(SEASON, LEAGUE, team["id"], period, moves)
        _, team2, _, _ = load_day(period)
        now_slots = {e["playerPoolEntry"]["player"]["id"]: e["lineupSlotId"] for e in team2["roster"]["entries"]}
        verified = all(now_slots.get(pid) == s for pid, s in target.items())
    cur = con.execute(
        "INSERT INTO lineup_decision(decided_at,season,league_id,team_id,scoring_period,model,projected_before,projected_after,moves,sent,status,response,detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (db.now(), SEASON, LEAGUE, team["id"], period, MODEL, t0, t1, json.dumps(moves), int(bool(moves and a.send)),
         status, (resp or "")[:4000], json.dumps({"verified": verified})))
    did = cur.lastrowid
    con.executemany("INSERT INTO player_day_projection VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [(did, m.pid, m.name, period, int(m.has_game), m.extra["inj"], m.value, json.dumps(m.extra["inputs"]),
                      m.slot, target.get(m.pid, m.slot), int(m.locked)) for m in men])
    con.commit()

    if not moves and a.quiet:
        return 0
    n_games = sum(1 for m in men if m.has_game and m.slot != IR)
    print(f"Barchskitball lineup, scoring period {period}: {n_games} of our men play today. "
          f"Projected {t0:.1f} -> {t1:.1f} pts ({MODEL}).")
    by = {m.pid: m for m in men}
    for mv in moves:
        m = by[mv["playerId"]]
        print(f"  {m.name}: {SLOTS.get(mv['fromLineupSlotId'])} -> {SLOTS.get(mv['toLineupSlotId'])} ({m.value:.1f}{', ' + m.note if m.note else ''})")
    if not moves:
        print("  ESPN already has the best lineup -- no moves.")
    if moves and not a.send:
        print("  DRY RUN -- nothing sent (pass --send).")
    if a.send and moves:
        ok = status == 200 and verified
        print(f"  ESPN: HTTP {status}; re-read {'matches' if verified else 'DOES NOT match'} the target." + ("" if ok else f" {str(resp)[:300]}"))
        if not ok:
            return 1
    if a.verbose:
        print("\n".join(describe(men, target)))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fbb")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("lineup")
    p.add_argument("--period", type=int)
    p.add_argument("--send", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    sub.add_parser("backup")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "lineup":
            return cmd_lineup(a)
        if a.cmd == "backup":
            from .backup import backup
            print(backup())
            return 0
    except espn.NotLoggedIn as e:
        print(f"fbb: {e}")
        return 3
    return 2


if __name__ == "__main__":
    sys.exit(main())
