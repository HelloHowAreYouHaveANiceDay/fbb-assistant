"""Back up data/fbb.db so losing a machine never loses the store again (tot took ff.db with it).

1. A consistent copy via SQLite's online backup API (safe while the DB is open, WAL included).
2. gzip it into ~/Backups/fbb-assistant/fbb-YYYYmmdd-HHMM.db.gz, keep the newest KEEP.
3. If FBB_BACKUP_GH_REPO is set (owner/repo), also upload it to that repo's `db-backups` release as
   fbb-latest.db.gz (clobbered) plus a dated copy, so the newest snapshot lives off this machine.
"""
from __future__ import annotations

import gzip
import os
import pathlib
import shutil
import sqlite3
import subprocess
import time

from .db import DB_PATH

KEEP = 30
DEST = pathlib.Path.home() / "Backups" / "fbb-assistant"


def backup(db_path: pathlib.Path = DB_PATH) -> str:
    DEST.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M")
    tmp = DEST / f".fbb-{stamp}.db"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(tmp)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    out = DEST / f"fbb-{stamp}.db.gz"
    with open(tmp, "rb") as f, gzip.open(out, "wb") as g:
        shutil.copyfileobj(f, g)
    tmp.unlink()
    olds = sorted(DEST.glob("fbb-*.db.gz"))[:-KEEP]
    for o in olds:
        o.unlink()
    msg = f"backup: {out} ({out.stat().st_size // 1024} KB)"
    repo = os.environ.get("FBB_BACKUP_GH_REPO")
    if repo:
        latest = DEST / "fbb-latest.db.gz"
        shutil.copy(out, latest)
        subprocess.run(["gh", "release", "create", "db-backups", "-R", repo, "--title", "DB backups",
                        "--notes", "Automated fbb.db snapshots."], capture_output=True)
        r = subprocess.run(["gh", "release", "upload", "db-backups", str(latest), str(out), "-R", repo, "--clobber"],
                           capture_output=True, text=True)
        msg += f"; github {repo}: {'ok' if r.returncode == 0 else 'FAILED ' + r.stderr[-200:]}"
    return msg
