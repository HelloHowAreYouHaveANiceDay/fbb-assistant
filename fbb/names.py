"""Official-report name join (stdlib): 'Last, First' -> player_game 'First Last'."""
from __future__ import annotations

import re
import unicodedata

SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'`]", "", s)
    s = re.sub(r"[^a-z ]+", " ", s)
    return " ".join(w for w in s.split() if w not in SUFFIXES)


def report_to_first_last(player: str) -> str:
    if "," in player:
        last, first = player.split(",", 1)
        return f"{first.strip()} {last.strip()}"
    return player.strip()


def key(name: str, from_report: bool = False) -> str:
    return norm(report_to_first_last(name) if from_report else name)
