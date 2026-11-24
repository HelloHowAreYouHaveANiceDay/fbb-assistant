"""Official NBA injury reports (PDF, archived from 2021-22): who was listed Out/Doubtful/
Questionable/Probable/Available before each game, as point-in-time rows.

  python3 -m fbb.injury --start 2021-10-19 --end 2026-11-23 [--hour 05PM]
One report per game day. The default is the 5PM ET edition, which is published before every tip
and lines up with when our lineup job decides.

Parsing uses `bim --read-only pdf text` (positioned words). There are two layouts:
- older reports are a ROTATED page with whole cells as phrases: the row is x and the column is y;
- 2026-27 reports are upright and word by word, with wrapped reasons offset above/below the row.
Both become (row, col) coordinates. The column edges come from the header (first page, reused
on later pages). Every line with a status word anchors a row, other words attach to the nearest
anchor, and date/time/matchup/team carry forward.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import tempfile
import urllib.error
import urllib.request

from . import db

BASE = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{d}_{h}.pdf"
COLS = ["date", "time", "matchup", "team", "player", "status", "reason"]
STATUSES = {"Out", "Doubtful", "Questionable", "Probable", "Available"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS injury_report (
  report_date TEXT, report_hour TEXT, game_date TEXT, game_time TEXT, matchup TEXT, team TEXT,
  player TEXT, status TEXT, reason TEXT, PRIMARY KEY (report_date, report_hour, matchup, player));
CREATE TABLE IF NOT EXISTS injury_report_fetch (report_date TEXT, report_hour TEXT, url TEXT, http INTEGER,
  rows INTEGER, fetched_at TEXT, PRIMARY KEY (report_date, report_hour));
"""


def _env():
    e = dict(os.environ)
    e["PATH"] = f"{pathlib.Path.home()}/.bim-cli:" + e.get("PATH", "")
    return e


def fetch(day: dt.date, hour: str) -> tuple[str, int, bytes | None]:
    hh, ap = hour[:2], hour[2:]
    url, code = "", 0
    for h in (f"{hh}_00{ap}", hour):          # 2026-27 uses _HH_MMPM; older editions _HHPM
        url = BASE.format(d=day.isoformat(), h=h)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30) as r:
                return url, 200, r.read()
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception:
            code = 0
    return url, code, None


def words(pdf: bytes) -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf)
        path = f.name
    try:
        p = subprocess.run(["bim", "--read-only", "pdf", "text", path], capture_output=True, text=True, env=_env(), timeout=180)
        return json.loads(p.stdout)
    finally:
        os.unlink(path)


def _header(pw: list[dict]):
    """(rotated, {col: left edge}) from a page's header, or None."""
    t = {w["text"]: w for w in pw}
    if "Player Name" in t and "Current Status" in t:            # rotated phrase layout
        g = sorted((w for w in pw if w["text"] in ("Game Date", "Game Time", "Matchup", "Team", "Player Name",
                                                    "Current Status", "Reason")), key=lambda w: w["y"])
        names = {"Game Date": "date", "Game Time": "time", "Matchup": "matchup", "Team": "team",
                 "Player Name": "player", "Current Status": "status", "Reason": "reason"}
        return True, {names[w["text"]]: w["y"] - 3 for w in g}, g[0]["x"]
    if "Player" in t and "Current" in t and "Matchup" in t:     # upright word layout
        hy = t["Player"]["y"]
        games = sorted(w["x"] for w in pw if w["text"] == "Game" and abs(w["y"] - hy) < 2)
        edges = {"date": -1e9, "time": (games[1] - 5) if len(games) > 1 else t["Matchup"]["x"] - 60,
                 "matchup": t["Matchup"]["x"] - 5, "team": t["Team"]["x"] - 5, "player": t["Player"]["x"] - 5,
                 "status": t["Current"]["x"] - 5, "reason": t["Reason"]["x"] - 5}
        return False, edges, hy
    return None


def parse(ws: list[dict]) -> list[dict]:
    rows: list[dict] = []
    carry = {c: "" for c in ("date", "time", "matchup", "team")}
    layout = None
    for page in sorted({w["page"] for w in ws}):
        pw = [w for w in ws if w["page"] == page]
        h = _header(pw)
        if h:
            layout = h
        if not layout:
            continue
        rotated, edges, hpos = layout
        pts = []
        for w in pw:
            if rotated:
                r, c = w["x"], w["y"]
                if h and r <= hpos + 1:
                    continue
            else:
                r, c = -w["y"], w["x"]
                if (h and w["y"] >= hpos - 1) or abs(w.get("fontSize") or 10) > 15 or w["y"] < 60:
                    continue
            if w["text"].startswith("Page") or "\x00" in w["text"]:
                continue
            col = max((k for k, x0 in edges.items() if c >= x0), key=lambda k: edges[k], default="date")
            pts.append((r, c, col, w["text"]))
        # rows that carry a status, plus "NOT YET SUBMITTED" rows (a team listed with no players yet),
        # so a group label never glues onto the previous team's last player
        anchors = sorted({r for r, c, col, t in pts if col == "status" and t.split()[0] in STATUSES}
                         | {r for r, c, col, t in pts if col in ("reason", "player", "status") and "SUBMITTED" in t})
        pts = [p for p in pts if not (p[2] == "player" and p[3].isdigit())]
        if not anchors:
            continue
        cells = {a: {k: [] for k in COLS} for a in anchors}
        for r, c, col, t in sorted(pts):
            a = min(anchors, key=lambda x: abs(x - r))
            if col in ("date", "time", "matchup", "team") and r < a - 1:
                a2 = [x for x in anchors if x >= r]      # a group label sits on/above its first row
                a = a2[0] if a2 else a
            cells[a][col].append((c, r, t))
        for a in anchors:
            cell = {k: " ".join(t for _, _, t in sorted(v, key=lambda z: (z[1], z[0]))) for k, v in cells[a].items()}
            for k in carry:
                if cell[k]:
                    carry[k] = cell[k]
            if not cell["player"] or not cell["status"]:
                continue
            rows.append({**carry, "player": cell["player"], "status": cell["status"].split()[0], "reason": cell["reason"]})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", default=dt.date.today().isoformat())
    ap.add_argument("--hour", default="05PM")
    a = ap.parse_args(argv)
    con = db.connect()
    con.executescript(SCHEMA)
    d, end = dt.date.fromisoformat(a.start), dt.date.fromisoformat(a.end)
    while d <= end:
        iso = d.isoformat()
        have = con.execute("SELECT http FROM injury_report_fetch WHERE report_date=? AND report_hour=?", (iso, a.hour)).fetchone()
        if d.month in (7, 8, 9) or (have and have[0] in (200, 403, 404)):   # off-season / already tried
            d += dt.timedelta(days=1)
            continue
        url, code, pdf = fetch(d, a.hour)
        try:
            rows = parse(words(pdf)) if pdf else []
        except Exception as e:
            print(f"{iso}: parse failed {str(e)[:80]}", flush=True)
            rows, code = [], -1
        con.executemany("INSERT OR REPLACE INTO injury_report VALUES (?,?,?,?,?,?,?,?,?)",
                        [(iso, a.hour, r["date"], r["time"], r["matchup"], r["team"], r["player"], r["status"], r["reason"]) for r in rows])
        con.execute("INSERT OR REPLACE INTO injury_report_fetch VALUES (?,?,?,?,?,?)", (iso, a.hour, url, code, len(rows), db.now()))
        con.commit()
        print(f"{iso} {a.hour}: http {code}, {len(rows)} rows", flush=True)
        d += dt.timedelta(days=1)


if __name__ == "__main__":
    main()
