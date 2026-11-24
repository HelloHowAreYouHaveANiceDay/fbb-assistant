# fbb-assistant

Fantasy basketball co-manager for ESPN league **Barchskitball (211696)**, team 1. It's the
basketball sibling of [ff-assistant](https://github.com/HelloHowAreYouHaveANiceDay/ff-assistant).
It runs on haus (Mac) from Hermes cron.

## What it does today (v0)

- `python3 -m fbb.cli lineup [--send] [--quiet] [-v]`: pulls our roster and the NBA schedule for the
  CURRENT scoring period (one day) and projects each man in league scoring. It then picks the
  best legal starting lineup and, with `--send`, writes it to ESPN and re-reads to verify.
  ESPN only accepts lineup writes for the current scoring period (HTTP 409 otherwise), so this
  runs during the day.
- Locked men (game started) never move. IR is never touched. Ties keep the current lineup.
- `python3 -m fbb.cli backup`: consistent SQLite snapshot to `~/Backups/fbb-assistant`, plus the
  `db-backups` GitHub release when `FBB_BACKUP_GH_REPO` is set.

Projection v0 blends ESPN's season projection (0.5), the last 15 days (0.3) and the season to date
(0.2). All three are ESPN `appliedAverage`, which is already in league points. Day-to-day men
get x0.75, OUT men 0. This is a placeholder. The real model is the research track in
`docs/research-plan.md`.

## Transport

All ESPN traffic runs inside the logged-in `bim bro` `espn` session (fetch with `credentials:
'include'`), the same as ff-assistant (D55). This repo never stores cookies or passwords.
There is exactly one write: a `ROSTER` / `EXECUTE` transaction whose items are all `LINEUP`
moves of our own players.

## Store

`data/fbb.db` (SQLite, gitignored). Raw snapshots of every pull are kept write-once, together
with every lineup decision and the per-player projections behind it. Back it up and never `rm` it.

## Tests

`python3 -m unittest discover -s tests`

## League (2027)

12 teams, H2H points, daily lineups, 16 matchup periods, 10-team playoffs, 1 acquisition per
matchup. Starters are PG, SG, SF, PF, C, G, SG/SF and PF/C, with 4 bench and 2 IR. Scoring:
PTS 1, REB 1, AST 1.5, STL 2, BLK 2.5, TO -1, FGM 1, FGA -1, FTM 1, FTA -1, 3PM 0.5,
FF -0.5, TECH -0.5.
