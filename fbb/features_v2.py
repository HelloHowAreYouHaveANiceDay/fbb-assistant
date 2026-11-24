"""Point-in-time features for projection v2 (stdlib only; the backtest and production share this code).

v2 = the v1 feature set (`fbb.features_v1`) plus MINUTES REDISTRIBUTION from teammates listed Out on the
official 5PM-ET injury report for that game (published before tip, so no leakage).

`BuilderV2` is a `features_v1.Builder` that also keeps, per player:
  - a role ratio reb / (reb + ast) (big vs ball-handler; box scores carry no positions),
  - his historical share of freed minutes: over his past games, the slope (through 0) of
    (minutes - his EWMA minutes before the game) on the EWMA minutes of recent rotation teammates who
    were ABSENT from that game's box score (shrunk with SHARE_LAMBDA). Past games only, so legal.
  - a name index so report rows ('Last, First') resolve to player ids.

`features(pid, team, opp, home, date, out=set_of_pids)` returns the v1 features plus the out-teammate
features. `out` is the set of pids listed Out for the game (production and the 2021-22+ backtest: the
5PM report; seasons before 2021-22 have no reports, so the TRAINING data uses "teammate absent from
the box score" as a proxy -- see research/v2/build.py. Production always uses the report.)

Only teammates who played for this team in its last RECENT_GAMES games this season count: men out for
weeks are already reflected in everyone's recent minutes.
"""
from __future__ import annotations

import sqlite3

from .features_v1 import Builder, design, played, season_of
from .names import key

RECENT_GAMES = 10       # team games: a teammate's minutes count as "freed" only if he played in these
SHARE_LAMBDA = 2000.0   # shrinkage (in freed-minutes^2) of the historical freed-minutes share toward 0
ROLE_DEFAULT = 0.6      # reb / (reb + ast) when unknown
STARTER_SHARE = 0.5     # start share over the last 5 games that makes a man a "starter"

# Official report team names -> ESPN pro team ids.
TEAM_IDS = {
    "Atlanta Hawks": 1, "Boston Celtics": 2, "New Orleans Pelicans": 3, "Chicago Bulls": 4,
    "Cleveland Cavaliers": 5, "Dallas Mavericks": 6, "Denver Nuggets": 7, "Detroit Pistons": 8,
    "Golden State Warriors": 9, "Houston Rockets": 10, "Indiana Pacers": 11, "LA Clippers": 12,
    "Los Angeles Lakers": 13, "Miami Heat": 14, "Milwaukee Bucks": 15, "Minnesota Timberwolves": 16,
    "Brooklyn Nets": 17, "New York Knicks": 18, "Orlando Magic": 19, "Philadelphia 76ers": 20,
    "Phoenix Suns": 21, "Portland Trail Blazers": 22, "Sacramento Kings": 23, "San Antonio Spurs": 24,
    "Oklahoma City Thunder": 25, "Utah Jazz": 26, "Washington Wizards": 27, "Toronto Raptors": 28,
    "Memphis Grizzlies": 29, "Charlotte Hornets": 30,
}

NEW_TERMS = ["out_min_x_rate", "out_role_min_x_rate", "out_starter_min_x_rate", "out_share_x_rate"]


class BuilderV2(Builder):
    def __init__(self):
        super().__init__()
        self.role: dict[tuple, list] = {}      # (pid, season) -> [reb, ast]
        self.share: dict[tuple, list] = {}     # (pid, season) -> [sum delta*M, sum M*M]
        self.by_name: dict[str, set] = {}      # name key -> pids
        self._pool_cache: dict = {}

    # ---- state ----
    def prune(self, season: int) -> None:
        """Drop played-game / team-game history older than season-1 (features never look further back).
        Only for long backtests; production loads two seasons anyway."""
        for pid in list(self.pl):
            h = [g for g in self.pl[pid] if g[1] >= season - 1]
            if h:
                self.pl[pid] = h
            else:
                del self.pl[pid]
        for tid in list(self.team_games):
            self.team_games[tid] = [g for g in self.team_games[tid] if g[1] >= season - 1]
        self._ewm_cache = {}

    def recent_pool(self, team: int, season: int) -> set:
        """Pids who played for `team` in its last RECENT_GAMES games this season and whose latest
        played game was for `team`."""
        ck = (team, season, len(self.team_games.get(team, ())))
        if ck in self._pool_cache:
            return self._pool_cache[ck]
        tg = [g for g in self.team_games.get(team, [])[-RECENT_GAMES:] if g[1] == season]
        pool = set().union(*[g[2] for g in tg]) if tg else set()
        pool = {q for q in pool if self.pl.get(q) and self.pl[q][-1][5] == team}
        self._pool_cache[ck] = pool
        return pool

    def add_day(self, date: str, games: list) -> None:
        season = season_of(date)
        for g in games:
            for tid in (g["home"], g["away"]):
                rows = [r for r in g["rows"] if r["team_id"] == tid]
                present = {r["player_id"] for r in rows}
                freed = 0.0
                for q in self.recent_pool(tid, season) - present:
                    e = self._ewm(q, season)
                    if e:
                        freed += e[1]
                for r in rows:
                    pid = r["player_id"]
                    if r.get("name"):
                        self.by_name.setdefault(key(r["name"]), set()).add(pid)
                    if not played(r):
                        continue
                    ro = self.role.setdefault((pid, season), [0.0, 0.0])
                    ro[0] += r.get("reb") or 0
                    ro[1] += r.get("ast") or 0
                    if freed > 0:
                        e = self._ewm(pid, season)
                        if e:
                            s = self.share.setdefault((pid, season), [0.0, 0.0])
                            s[0] += (float(r["min"]) - e[1]) * freed
                            s[1] += freed * freed
        super().add_day(date, games)
        self._pool_cache = {}

    # ---- helpers ----
    def role_of(self, pid: int, season: int) -> float:
        reb = ast = 0.0
        for s in (season, season - 1):
            v = self.role.get((pid, s))
            if v:
                reb += v[0]; ast += v[1]
        return reb / (reb + ast) if reb + ast > 0 else ROLE_DEFAULT

    def share_of(self, pid: int, season: int) -> float:
        num = den = 0.0
        for s in (season, season - 1):
            v = self.share.get((pid, s))
            if v:
                num += v[0]; den += v[1]
        return num / (den + SHARE_LAMBDA)

    def _starter(self, pid: int, season: int) -> bool:
        h = [g for g in self.pl.get(pid, [])[-5:] if g[1] in (season, season - 1)]
        return bool(h) and sum(g[4] for g in h) / len(h) >= STARTER_SHARE

    def resolve_out(self, out_rows) -> dict:
        """[(team name, 'Last, First'), ...] listed Out -> {team id: set(pid)}. The team comes from the
        report when it parses to one of the 30 names, else from the man's latest box-score team."""
        res: dict = {}
        for team_name, player in out_rows:
            for pid in self.by_name.get(key(player, from_report=True), ()):
                tid = TEAM_IDS.get((team_name or "").strip())
                if tid is None:
                    h = self.pl.get(pid)
                    if not h:
                        continue
                    tid = h[-1][5]
                res.setdefault(tid, set()).add(pid)
        return res

    # ---- features ----
    def out_features(self, pid: int, team: int, date: str, out) -> dict:
        season = season_of(date)
        f = {"out_n": 0, "out_min": 0.0, "out_role_min": 0.0, "out_starter_min": 0.0}
        qs = (set(out) & self.recent_pool(team, season)) - {pid}
        if qs:
            rp, sp = self.role_of(pid, season), self._starter(pid, season)
            for q in qs:
                e = self._ewm(q, season)
                if not e:
                    continue
                m = e[1]
                f["out_n"] += 1
                f["out_min"] += m
                f["out_role_min"] += m * max(0.0, 1.0 - abs(rp - self.role_of(q, season)) / 0.5)
                if self._starter(q, season) == sp:
                    f["out_starter_min"] += m
        f["freed_share"] = self.share_of(pid, season)
        return f

    def features(self, pid: int, team: int, opp: int, home: int, date: str, out=()) -> dict | None:
        f = super().features(pid, team, opp, home, date)
        if f is None:
            return None
        f.update(self.out_features(pid, team, date, out))
        return f


def design_v2(f: dict) -> dict:
    x = design(f)
    rate = f["ewm_rate"]
    x["out_min_x_rate"] = f["out_min"] * rate
    x["out_role_min_x_rate"] = f["out_role_min"] * rate
    x["out_starter_min_x_rate"] = f["out_starter_min"] * rate
    x["out_share_x_rate"] = f["out_min"] * f["freed_share"] * rate
    return x


def predict_v2(coef: dict, f: dict | None) -> float | None:
    if f is None:
        return None
    x = design_v2(f)
    return max(0.0, sum(c * x[k] for k, c in coef.items()))


# ---- official 5PM report (shared by the backtest and production) ----
def _mdy(date: str) -> str:
    return f"{date[5:7]}/{date[8:10]}/{date[:4]}"


def report_out_rows(con: sqlite3.Connection, date: str, hour: str = "05PM") -> tuple[list, bool]:
    """([(team, player)] listed Out for games ON `date`, report_found). Per matchup, the latest
    `hour` report dated on or before the game day wins (a report also lists some next-day games)."""
    rows = con.execute(
        "SELECT report_date, matchup, team, player, status FROM injury_report WHERE report_hour=? AND game_date=? "
        "AND report_date<=?", (hour, _mdy(date), date)).fetchall()
    latest: dict = {}
    for rd, mu, _, _, _ in rows:
        if rd > latest.get(mu, ""):
            latest[mu] = rd
    out = [(t, p) for rd, mu, t, p, st in rows if rd == latest[mu] and st == "Out"]
    return out, bool(rows)
