# Model v1: pre-registration (written before any test-season number was computed)

## Compared
- **v0-pit** (baseline). v0 without its ESPN component, because ESPN keeps only the final season
  projection, which leaks the outcome. So: 0.6 x last-15-days avg + 0.4 x season-to-date avg, league points,
  each input only with >= 3 games played; missing inputs drop out and the weights renormalise.
  With neither input it falls back to the prior-season avg (>= 3 games), and failing that to the
  avg of whatever current-season games exist.
- **v1**. One candidate, picked on the dev split below. The test seasons are never used to choose.

## Dev split (the only place design choices are made)
Fit on 2023-24 regular-season games through 2024-01-31 and validate on 2024-02-01 to the end of
the season. Choose by validation MAE.

## Test (rolling origin, fit only on earlier seasons)
| test season | fit on |
|---|---|
| 2024-25 | 2023-24 |
| 2025-26 | 2023-24, 2024-25 |
| 2026-27 to date | 2023-24 .. 2025-26 |

The 2002-2023 backfill is partial while the loaders run, so it is not used.

## Metrics
1. **Accuracy.** Regular-season player-games with min > 0 where the player has >= 1 earlier
   regular-season game (this season or last). Both models are scored on the identical rows.
   Report MAE, RMSE and bias (pred - actual), overall and by actual-minutes tier (<15, 15-25, 25-32, 32+).
2. **Lineup points.** For each test season with ESPN end-of-season rosters (2024-25 = ESPN 2025,
   2025-26 = ESPN 2026), take every league team's roster as fixed. Every regular-season day, run
   `fbb.lineup.optimise` with each model's values: 0 with no game, 0 if listed Out on that
   day's official 5PM report, otherwise the prediction. Score the starters by their ACTUAL league
   points (a DNP scores 0). Also report an oracle (actual points as the values) and random
   (a random value for every man with a game; mean of 5 seeds).
   Paired diff = v1 - v0-pit total. The interval is a 95% bootstrap over ISO weeks within
   season (2000 reps), on the pooled total across both seasons and for each season.

## Ship rule
v1 ships only if BOTH hold:
- v1 MAE < v0-pit MAE in each test season (2024-25, 2025-26, 2026-27 to date), and
- the pooled lineup-points paired diff is > 0 with its 95% interval excluding zero.

Otherwise the PR carries the research and this writeup, and nothing is wired into `fbb/cli.py`.

Secondary (reported, not gated): lineup points with oracle availability, where both models
value a man who did not play at 0. This isolates projection quality given play.
