# Model v2: pre-registration (written before any test-season number was computed)

Incumbent: **v1** (live since PR #1). Challenger: **v2**, one candidate picked on the dev season below.
v0-pit is reported for context only.

## Data
Regular-season NBA box scores 2002-03 .. 2026-27 to date (`game.season_type=2`, team ids <= 30; games
with no `player_game` rows are skipped and listed). Official 5PM-ET injury reports 2021-22 on.
League rosters: `raw_snapshot` kind `hist_rosters` (ESPN season label = ending year).

## The two models
- **v1 (incumbent)**: exactly `research/v1/build.py` + the `research/v1/evaluate.py` fit, unchanged, run on
  the current store: C3 feature set, rolling folds fit on 2023-24 .. S-1 (2024-25 <- 23-24; 2025-26 <- 23-25;
  2026-27 <- 23-26).
- **v2**: same linear least squares, ridge 1.0 on non-intercept terms, prediction floored at 0, features
  from `fbb/features_v2.py` (v1's features computed with the long history, plus the terms below).

### New features (minutes redistribution)
For player p on team T in a game on date D, "Out teammates" = men listed **Out** on the official 5PM-ET report
for that game (latest 5PM report dated <= D listing the matchup), resolved by name, who played for T in T's
last 10 games this season and whose latest game was for T. All inputs are known before tip.
- `out_min_x_rate`: sum of the Out teammates' EWMA minutes x p's EWMA points-per-minute.
- `out_role_min_x_rate`: the same sum weighted by role similarity, max(0, 1 - |r_p - r_q| / 0.5) with
  r = reb / (reb + ast) over this and last season (box scores carry no positions).
- `out_starter_min_x_rate`: the same sum over Out teammates with the same starter flag as p (start share >= 0.5
  over the last 5 games).
- `out_share_x_rate`: out_min x p's historical share of freed minutes x rate. The share is the slope through 0 of
  (his minutes - his pre-game EWMA minutes) on the EWMA minutes of rotation teammates absent from that past
  game's box score, this and last season, shrunk by adding 2000 to the denominator.

**Seasons before 2021-22 have no reports.** For TRAINING rows in those seasons only, "Out" is replaced by a proxy:
rotation teammates with no box-score row in that game (inactive). Report-era rows (2021-22 on), the dev season,
the test seasons and production use the report only. The proxy/report agreement on 2021-22+ is reported.

## Candidates (14) and the dev season (the only place choices are made)
- Feature set: **A** = v1's 17 terms; **B** = A + the 4 terms above.
- History: training window of the last **3 / 6 / 10** seasons before the target season, or **all** seasons from
  2003-04 (2002-03 has no prior-season history, so its rows are not fitted), unweighted; or all seasons with
  season-recency weights 0.5^(age / h), age = (target season - 1) - fit season, **h = 2, 4, 8** seasons.
- Dev: target = **2023-24** (whole regular season), each candidate fit on seasons <= 2022-23 per its window.
  Pick the lowest dev MAE (played games, min > 0, features available). Ties within 0.001 go to A, then to the
  shorter / more heavily weighted window. The dev table is reported in full.

## Test (rolling origin, fit only on seasons before the test season)
| test season | v2 fit on | v1 fit on |
|---|---|---|
| 2024-25 | chosen window ending 2023-24 | 2023-24 |
| 2025-26 | chosen window ending 2024-25 | 2023-24 .. 2024-25 |
| 2026-27 to date | chosen window ending 2025-26 | 2023-24 .. 2025-26 |

## Metrics
1. **Accuracy.** Regular-season player-games with min > 0 where BOTH v1 and v2 have features (identical rows).
   MAE, RMSE, bias, and MAE by actual-minutes tier (<15, 15-25, 25-32, 32+).
2. **Lineup points** (the research/v1 method, unchanged): 2024-25 (ESPN 2025) and 2025-26 (ESPN 2026); every
   league team's hist_rosters roster fixed; every regular-season day `fbb.lineup.optimise` on each model's
   values (0 with no game; 0 if listed Out on that day's 5PM report, both models, `research/v1` `injury_out`);
   starters scored by ACTUAL league points. Paired diff = v2 - v1. 95% bootstrap over ISO weeks within season,
   2000 reps, seed 7; pooled and per season. Oracle and random reported.
   Secondary (not gated): oracle availability (a man who did not play is valued 0 by both models).

## Ship rule
v2 ships only if BOTH hold:
- v2 MAE < v1 MAE in EACH test season (2024-25, 2025-26, 2026-27 to date), and
- the pooled primary lineup paired diff (v2 - v1) is > 0 with its 95% interval excluding zero.

Otherwise the PR carries the research and the writeup marked "did not pass", and nothing is wired into
`fbb/cli.py`.
