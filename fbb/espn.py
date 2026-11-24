"""ESPN fantasy basketball over the logged-in `bim bro` `espn` session.

Every request runs INSIDE the bro browser tab (fetch with credentials: 'include'), so this repo never
holds ESPN cookies or a password. Reads go to lm-api-reads, the ONE write (a lineup change) goes to
the league transactions URL with type ROSTER / executionType EXECUTE / items LINEUP only.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess

READS = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba"
WRITES = "https://lm-api-writes.fantasy.espn.com/apis/v3/games/fba"

HOME = pathlib.Path.home()


class NotLoggedIn(RuntimeError):
    pass


def _env() -> dict:
    env = dict(os.environ)
    env["PATH"] = f"{HOME}/.bim-cli:{HOME}/.local/bin:" + env.get("PATH", "/usr/bin:/bin")
    env.setdefault("BRO_SITES_DIR", str(HOME / ".local/share/bro/sites"))
    env.setdefault("BRO_BROWSER_CHANNEL", "chromium")
    return env


def bro_eval(js: str, timeout: int = 90):
    """Evaluate JS in the espn bro tab, return its value. Starts the session if it is down."""
    env = _env()
    lst = json.loads(subprocess.run(["bim", "bro", "session", "list"], env=env, capture_output=True,
                                    text=True, timeout=60).stdout or "{}")
    if not any(s.get("site") == "espn" and s.get("alive") for s in lst.get("sessions", [])):
        subprocess.run(["bim", "bro", "session", "launch", "--site", "espn", "--detach"], env=env,
                       capture_output=True, text=True, timeout=120)
    p = subprocess.run(["bim", "bro", "page", "exec", "--site", "espn", "--tab", "espn.com", "--action", "eval",
                        "--arg", js], env=env, capture_output=True, text=True, timeout=timeout)
    out = (p.stdout or "").strip()
    try:
        d = json.loads(out[out.index("{"):])
    except (ValueError, json.JSONDecodeError):
        raise RuntimeError(f"bim bro eval failed: {(p.stderr or out)[-300:]}")
    if d.get("ok") is False:
        raise RuntimeError(f"bim bro eval failed: {d.get('error')}")
    return d.get("value")


def _fetch(url: str, method: str = "GET", body: str | None = None, headers: dict | None = None):
    init = {"credentials": "include", "method": method, "headers": {"accept": "application/json", **(headers or {})}}
    if body is not None:
        init["body"] = body
        init["headers"]["content-type"] = "application/json"
    js = (f"(async()=>{{const r=await fetch({json.dumps(url)},{json.dumps(init)});"
          f"return JSON.stringify({{status:r.status,body:await r.text()}})}})()")
    r = json.loads(bro_eval(js))
    if r["status"] == 401:
        raise NotLoggedIn("bro espn session is logged out -- re-run the ESPN login (bim bro espn)")
    return r["status"], r["body"]


def get(url: str, headers: dict | None = None) -> dict:
    status, body = _fetch(url, headers=headers)
    if status != 200:
        raise RuntimeError(f"{status} for {url}: {body[:200]}")
    return json.loads(body)


def league(season: int, league_id: int, views: list[str], **params) -> dict:
    q = "&".join([f"view={v}" for v in views] + [f"{k}={v}" for k, v in params.items()])
    return get(f"{READS}/seasons/{season}/segments/0/leagues/{league_id}?{q}")


def pro_schedule(season: int) -> dict:
    return get(f"{READS}/seasons/{season}?view=proTeamSchedules_wl")


def players(season: int, league_id: int, scoring_period: int, player_ids: list[int]) -> dict:
    flt = {"players": {"filterIds": {"value": player_ids}, "filterStatsForTopScoringPeriodIds": {
        "value": 2, "additionalValue": [f"00{season}", f"10{season}", f"01{season}", f"02{season}", f"03{season}"]}}}
    return get(f"{READS}/seasons/{season}/segments/0/leagues/{league_id}?view=kona_player_info"
               f"&scoringPeriodId={scoring_period}", headers={"x-fantasy-filter": json.dumps(flt)})


def set_lineup(season: int, league_id: int, team_id: int, scoring_period: int, moves: list[dict]) -> tuple[int, str]:
    """POST one ROSTER/EXECUTE with LINEUP items. Only our own men between our own slots."""
    if not moves:
        raise ValueError("no moves")
    for m in moves:
        assert set(m) == {"playerId", "fromLineupSlotId", "toLineupSlotId"}, m
    body = {"isLeagueManager": False, "teamId": int(team_id), "type": "ROSTER", "scoringPeriodId": int(scoring_period),
            "executionType": "EXECUTE",
            "items": [{"playerId": int(m["playerId"]), "type": "LINEUP", "fromLineupSlotId": int(m["fromLineupSlotId"]),
                       "toLineupSlotId": int(m["toLineupSlotId"])} for m in moves]}
    url = f"{WRITES}/seasons/{season}/segments/0/leagues/{league_id}/transactions/"
    return _fetch(url, method="POST", body=json.dumps(body))


def my_swid() -> str:
    return bro_eval("decodeURIComponent((document.cookie.match(/SWID=([^;]+)/)||[])[1]||'')")
