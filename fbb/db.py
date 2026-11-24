"""The store. SQLite, one file (data/fbb.db, gitignored, backed up -- see fbb/backup.py).

Write-once raw snapshots of everything we pull (so a model can be refit on point-in-time data later),
plus every lineup decision with what was projected, what was sent and what ESPN answered.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import time

DB_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "fbb.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_snapshot (
  id INTEGER PRIMARY KEY, fetched_at TEXT NOT NULL, kind TEXT NOT NULL, season INTEGER,
  league_id INTEGER, scoring_period INTEGER, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS raw_snapshot_kind ON raw_snapshot(kind, season, scoring_period);
CREATE TABLE IF NOT EXISTS lineup_decision (
  id INTEGER PRIMARY KEY, decided_at TEXT NOT NULL, season INTEGER, league_id INTEGER, team_id INTEGER,
  scoring_period INTEGER, model TEXT, projected_before REAL, projected_after REAL,
  moves TEXT, sent INTEGER, status INTEGER, response TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS player_day_projection (
  decided_id INTEGER, player_id INTEGER, name TEXT, scoring_period INTEGER, has_game INTEGER,
  injury TEXT, value REAL, inputs TEXT, slot_before INTEGER, slot_after INTEGER, locked INTEGER);
"""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def connect(path: pathlib.Path | str = DB_PATH) -> sqlite3.Connection:
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=120)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def snapshot(con, kind: str, payload, season=None, league_id=None, scoring_period=None) -> int:
    cur = con.execute("INSERT INTO raw_snapshot(fetched_at,kind,season,league_id,scoring_period,payload) VALUES (?,?,?,?,?,?)",
                      (now(), kind, season, league_id, scoring_period, json.dumps(payload, separators=(",", ":"))))
    con.commit()
    return cur.lastrowid
