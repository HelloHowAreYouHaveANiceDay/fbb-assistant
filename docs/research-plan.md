# Research plan: where the lineup edge is

## The ff lesson

In ff-assistant, the edge came from projecting in **our league's scoring** rather than
consuming ESPN's default-weighted view. Our book disagreed with the room's consensus where the
league's scoring made it wrong, and independent values beat shared blind spots (project page,
2026-09-01).

## Why basketball scoring makes the same move promising

Barchskitball's weights are far from ESPN's default points league (PTS 1, 3PM 1, FGA -1, FGM 2,
FTA -1, FTM 1, REB 1, AST 2, STL 4, BLK 4, TO -2). Ours:

- BLK 2.5 and STL 2 (default is 4 each), so stocks are worth roughly half as much.
- AST 1.5 (default 2) and TO -1 (default -2), so ball-handlers lose less to turnovers.
- 3PM 0.5 (default 1) and FGM 1 (default 2), so the shooting-efficiency term is smaller.

Anything that ranks or values players on default scoring or category value, like ESPN's player
rater, rankings and other managers' intuition, is mispriced by those gaps. That matters for
waiver adds and trades more than for daily lineups, where ESPN already applies our scoring to
its projected stat lines.

## Daily lineup edges to measure (in order)

1. **Just playing the games.** Start every man with a game; tonight's lineup (period 35) left
   ~65 projected points on the bench because nothing was setting it. That is the floor.
2. **Per-game projection quality.** Today's v0 is a blend of ESPN's projection, last 15 days and
   season to date. Replace it with a model on box scores from the NBA's public stats
   (nba_api/stats.nba.com or a mirror):
   minutes x per-minute rates in league points, with rest, back-to-back, opponent pace and
   defense, and teammate injuries (usage redistribution). Gate it the same way ff gates models:
   nested CV by season, CRPS/RMSE against the ESPN projection, and lineup points against the
   ESPN-projection lineup.
3. **Injury and late-scratch risk.** DTD x0.75 is a guess. Fit P(plays | status, time to tip).
   Re-run the job right before each tip window so a late scratch gets swapped (hourly cron
   already does this for men not yet locked).
4. **Streaming with the one-add-per-matchup limit.** Weekly game counts make an extra game
   worth ~25-40 pts. Value a pickup as its added projected starts across the matchup in league
   points.
5. **The games-played cap.** `lineupSlotStatLimits` shows a GP limit (4.57/period, slot 15). Check
   whether it binds. If it does, the objective becomes points per game started, not total
   points.

## Data we keep from day one

`raw_snapshot` stores our roster, settings and projections at decision time, and
`player_day_projection` stores what we projected versus (later) what happened. That's the
point-in-time record every backtest above needs, and it's why the DB is backed up.
