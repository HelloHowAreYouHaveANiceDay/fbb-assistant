"""Barchskitball league history (2018 on) as raw snapshots: settings, teams, final rosters,
matchups/schedule, draft, and ESPN's season projection + actual splits for the top players.

  python3 -m fbb.history --seasons 2018-2026
Each season x kind is stored once (re-run with --force to refetch).
"""
from __future__ import annotations

import argparse
import json

from . import db, espn
from .cli import LEAGUE


def players_top(season: int, limit: int = 600) -> list:
    flt = {"players": {"limit": limit, "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
                       "filterStatsForTopScoringPeriodIds": {"value": 2, "additionalValue": [
                           f"00{season}", f"10{season}", f"01{season}", f"02{season}", f"03{season}"]}}}
    d = espn.get(f"{espn.READS}/seasons/{season}/segments/0/leagues/{LEAGUE}?view=kona_player_info",
                 headers={"x-fantasy-filter": json.dumps(flt)})
    return [{"id": p["player"]["id"], "name": p["player"]["fullName"], "proTeamId": p["player"].get("proTeamId"),
             "onTeamId": p.get("onTeamId"), "eligible": p["player"].get("eligibleSlots"),
             "stats": [{k: s.get(k) for k in ("id", "statSourceId", "statSplitTypeId", "scoringPeriodId",
                                                "appliedTotal", "appliedAverage", "stats")}
                       for s in p["player"].get("stats") or []]} for p in d.get("players", [])]


KINDS = {
    "hist_league": lambda y: espn.league(y, LEAGUE, ["mSettings", "mTeam", "mStandings"]),
    "hist_rosters": lambda y: espn.league(y, LEAGUE, ["mRoster"]),
    "hist_matchups": lambda y: espn.league(y, LEAGUE, ["mMatchup", "mMatchupScore"]),
    "hist_draft": lambda y: espn.league(y, LEAGUE, ["mDraftDetail"]),
    "hist_players": players_top,
}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", default="2018-2026")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    lo, hi = (int(x) for x in a.seasons.split("-"))
    con = db.connect()
    for y in range(lo, hi + 1):
        for kind, fn in KINDS.items():
            if not a.force and con.execute("SELECT 1 FROM raw_snapshot WHERE kind=? AND season=?", (kind, y)).fetchone():
                continue
            try:
                payload = fn(y)
                db.snapshot(con, kind, payload, y, LEAGUE)
                n = len(payload) if isinstance(payload, list) else len(payload.get("teams") or payload.get("schedule") or [])
                print(f"{y} {kind}: ok ({n})", flush=True)
            except Exception as e:
                print(f"{y} {kind}: FAILED {str(e)[:120]}", flush=True)


if __name__ == "__main__":
    main()
