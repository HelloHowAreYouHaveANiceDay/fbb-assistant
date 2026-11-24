"""NBA box-score ingest from ESPN's public site API (no auth, no bro session).

ESPN athlete ids == fantasy player ids, so these rows join to league rosters directly.

  python3 -m fbb.ingest boxscores --start 2024-10-22 --end 2026-11-23
Idempotent: a game already in `game` with status final is skipped.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
import urllib.request

from . import db

SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"

SCHEMA = """
CREATE TABLE IF NOT EXISTS game (
  game_id INTEGER PRIMARY KEY, game_date TEXT NOT NULL, start_utc TEXT, season_type INTEGER,
  home_team_id INTEGER, away_team_id INTEGER, home_score INTEGER, away_score INTEGER, final INTEGER,
  fetched_at TEXT);
CREATE INDEX IF NOT EXISTS game_date ON game(game_date);
CREATE TABLE IF NOT EXISTS player_game (
  game_id INTEGER, player_id INTEGER, name TEXT, team_id INTEGER, opp_team_id INTEGER, home INTEGER,
  starter INTEGER, dnp INTEGER, dnp_reason TEXT, min REAL, pts INTEGER, fgm INTEGER, fga INTEGER,
  tpm INTEGER, tpa INTEGER, ftm INTEGER, fta INTEGER, oreb INTEGER, dreb INTEGER, reb INTEGER,
  ast INTEGER, stl INTEGER, blk INTEGER, tov INTEGER, pf INTEGER, pm INTEGER,
  PRIMARY KEY (game_id, player_id));
CREATE INDEX IF NOT EXISTS player_game_player ON player_game(player_id);
"""


def _get(url: str, tries: int = 4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "fbb-assistant"}), timeout=30) as r:
                return json.loads(r.read())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def _split(v: str):
    try:
        a, b = v.split("-")
        return int(a), int(b)
    except Exception:
        return None, None


def _int(v):
    try:
        return int(v)
    except Exception:
        return None


def parse_summary(gid: int, s: dict) -> list[tuple]:
    comp = s["header"]["competitions"][0]
    teams = {int(c["team"]["id"]): c for c in comp["competitors"]}
    rows = []
    for side in (s.get("boxscore") or {}).get("players") or []:
        tid = int(side["team"]["id"])
        opp = next((t for t in teams if t != tid), None)
        home = int(teams.get(tid, {}).get("homeAway") == "home")
        st = side["statistics"][0]
        keys = st.get("keys") or []
        for a in st.get("athletes") or []:
            ath = a.get("athlete") or {}
            if not ath.get("id"):          # exhibition opponents (international clubs) carry no athlete id
                continue
            vals = dict(zip(keys, a.get("stats") or []))
            dnp = int(bool(a.get("didNotPlay")) or not a.get("stats"))
            fgm, fga = _split(vals.get("fieldGoalsMade-fieldGoalsAttempted", ""))
            tpm, tpa = _split(vals.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted", ""))
            ftm, fta = _split(vals.get("freeThrowsMade-freeThrowsAttempted", ""))
            mn = vals.get("minutes")
            rows.append((gid, int(ath["id"]), ath.get("displayName"), tid, opp, home, int(bool(a.get("starter"))), dnp,
                         a.get("reason"), float(mn) if mn not in (None, "", "--") else None, _int(vals.get("points")),
                         fgm, fga, tpm, tpa, ftm, fta, _int(vals.get("offensiveRebounds")), _int(vals.get("defensiveRebounds")),
                         _int(vals.get("rebounds")), _int(vals.get("assists")), _int(vals.get("steals")),
                         _int(vals.get("blocks")), _int(vals.get("turnovers")), _int(vals.get("fouls")),
                         _int(str(vals.get("plusMinus", "")).replace("+", ""))))
    return rows


def ingest_day(con, day: dt.date) -> tuple[int, int]:
    sb = _get(f"{SITE}/scoreboard?dates={day:%Y%m%d}&limit=50")
    n_games = n_rows = 0
    for ev in sb.get("events") or []:
        gid = int(ev["id"])
        comp = ev["competitions"][0]
        final = int(comp["status"]["type"].get("completed", False))
        have = con.execute("SELECT final FROM game WHERE game_id=?", (gid,)).fetchone()
        if have and have[0]:
            continue
        cs = {c["homeAway"]: c for c in comp["competitors"]}
        con.execute("INSERT OR REPLACE INTO game VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (gid, day.isoformat(), ev.get("date"), (ev.get("season") or {}).get("type"),
                     int(cs["home"]["team"]["id"]), int(cs["away"]["team"]["id"]), _int(cs["home"].get("score")),
                     _int(cs["away"].get("score")), final, db.now()))
        if final:
            rows = parse_summary(gid, _get(f"{SITE}/summary?event={gid}"))
            con.executemany("INSERT OR REPLACE INTO player_game VALUES (" + ",".join("?" * 26) + ")", rows)
            n_rows += len(rows)
        n_games += 1
    con.commit()
    return n_games, n_rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["boxscores"])
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", default=dt.date.today().isoformat())
    a = ap.parse_args(argv)
    con = db.connect()
    con.executescript(SCHEMA)
    d, end = dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end)
    tg = tr = 0
    while d <= end:
        g, r = ingest_day(con, d)
        tg, tr = tg + g, tr + r
        if g:
            print(f"{d}: {g} games, {r} player rows", flush=True)
        d += dt.timedelta(days=1)
    print(f"done: {tg} games, {tr} player rows")


if __name__ == "__main__":
    main()
