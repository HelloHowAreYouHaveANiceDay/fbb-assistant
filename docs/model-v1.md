# Projection v1: method, backtest and the gate

**Result: v1 passes the pre-registered gate (`docs/model-v1-prereg.md`) and is wired in as the default
projector (`MODEL = "v1"` in `fbb/cli.py`, `FBB_MODEL=v0` forces the old one).** Its accuracy edge
over the baseline is real but modest. Most of the gated lineup-points edge comes from
availability, not projection quality; see "How to read the lineup number" before quoting it.

Numbers are from the run on 2026-11-23 (store as of ~22:30 PST; the injury-report loader was still
running, see coverage). Reproduce with:

    python3 research/v1/build.py                              # point-in-time dataset, stdlib
    uv run --with numpy python research/v1/evaluate.py        # fit, gate, writes data/model_v1.json

## Baseline: v0-pit (the substitution, stated plainly)

Production v0 is 0.5 x ESPN season projection + 0.3 x last-15-days + 0.2 x season-to-date. ESPN keeps
only the FINAL season projection, which has seen the season, so that component can't be used in a
backtest without leakage. **The baseline here is therefore v0-pit**: the same blend minus ESPN,
0.6 x last-15-days + 0.4 x season-to-date (league points, >= 3 games each, missing parts drop out
and the weights renormalise). With neither part available it uses the prior-season avg (>= 3 games),
then whatever current-season games exist. **v1 has NOT been compared with ESPN's projection.**
`player_day_projection` and `snapshot-players` now record it point-in-time, so that comparison
becomes possible once a season of snapshots exists.

## Method

`fbb/features_v1.py` (stdlib; the backtest and production share it) builds features from regular-season
box scores strictly before the game date:

- Player history: EWMA of league points and minutes (half-life 6 games, last 40 games; last season's
  games carry half weight), per-minute rate, season-to-date avg, last-15-days avg, prior-season avg,
  last-3-games minutes, starter share over the last 5.
- Shrinkage: a `1/(1+games this season) x (prior - EWMA)` term pulls early-season estimates toward last season.
- Schedule: back-to-back, 3+ days rest, home.
- Availability history: team games missed since his last appearance (`log1p(missed) x EWMA`, plus a
  back-from-absence flag).
- Usage redistribution: summed EWMA minutes of teammates (>= 12 mpg) who played in the team's previous
  10 games but sat its last one, x his rate. This is known before tip. DNPs from today's game are never
  used.
- Opponent: season-to-date fantasy points allowed and pace (possessions), relative to the league and
  shrunk with 10 team-games of prior. Also own-team pace.

The model is linear least squares (ridge 1.0 on non-intercept terms) on 17 terms, mostly interactions
with the EWMA (e.g. `opp_fp_rel x EWMA`), so it behaves like minutes x rate x context.
The prediction is floored at 0. The coefficients are in `data/model_v1.json` (fit on 2023-24..2025-26).

Not done: a separate minutes model, position-specific defense (`player_game` has no positions), the
injury report as a feature (no coverage for most training seasons), and P(play). Availability in
production stays v0's rule: ESPN OUT/SUSPENSION/IR -> 0, DAY_TO_DAY -> x0.75.

### Model selection (dev split only: fit 2023-24 through Jan, validate Feb-Apr 2024; n = 11,005)

| model | MAE | RMSE | bias |
|---|---|---|---|
| v0-pit | 6.979 | 9.014 | -0.430 |
| C1 history only | 6.975 | 8.903 | -0.112 |
| C2 + minutes/starter | 6.922 | 8.855 | -0.085 |
| **C3 full (chosen)** | **6.887** | **8.810** | -0.098 |

## Accuracy (rolling origin; fit only on earlier seasons; played games with prior history)

| test season (fit on) | n | v1 MAE | v0-pit MAE | v1 RMSE | v0-pit RMSE | v1 bias | v0-pit bias |
|---|---|---|---|---|---|---|---|
| 2024-25 (23-24) | 26,109 | **6.951** | 6.992 | **8.874** | 9.029 | +0.106 | -0.334 |
| 2025-26 (23-25) | 26,447 | **6.857** | 6.987 | **8.758** | 9.008 | +0.071 | -0.233 |
| 2026-27 to 11-22 (23-26) | 5,689 | **6.861** | 7.058 | **8.769** | 9.080 | +0.757 | +0.171 |

By actual minutes (MAE v1 / v0-pit; bias v1 / v0-pit):

| season | <15 min | 15-25 | 25-32 | 32+ |
|---|---|---|---|---|
| 2024-25 | 5.668 / **5.138** (bias +4.36 / +3.28) | **6.366** / 6.546 | **7.397** / 7.635 | **8.447** / 8.698 (bias -3.96 / -3.83) |
| 2025-26 | 5.580 / **5.256** (+4.35 / +3.48) | **6.257** / 6.484 | **7.363** / 7.709 | **8.502** / 8.729 (-4.23 / -3.94) |
| 2026-27 | 5.714 / **5.556** (+4.52 / +3.62) | **6.383** / 6.652 | **7.438** / 7.845 | **8.359** / 8.653 (-3.35 / -3.44) |

Tier counts are in `research/v1/out/results.json`. v1 is worse in the < 15-minute tier: it predicts
toward a man's usual output on nights his minutes collapse. Those are not lineup-relevant games.

## Lineup points

Every regular-season day, each of the 12 league teams' END-OF-SEASON roster (hist_rosters) is
treated as fixed. The optimiser (`fbb.lineup.optimise`, the production slot counts) runs on each
model's values, and the starters are scored by actual league points. A man counts as having a game
when his current NBA team plays. Value is 0 with no game and 0 if he is listed Out on that day's
official 5PM report. The oracle uses actual points. Random gives a random value to every man with a
game (mean of 5 seeds). The interval is a 95% bootstrap over ISO weeks within season (2000 reps).
2026-27 has no end-of-season rosters, so there's no lineup test for it.

### Primary (pre-registered gate)

| season | v1 | v0-pit | oracle | random | v1 - v0-pit [95% CI] | teams v1 > v0-pit |
|---|---|---|---|---|---|---|
| 2024-25 | 220,518.5 | 219,008.5 | 229,074.0 | 207,559.2 | +1,510.0 [+670.0, +2,441.0] | 11/12 |
| 2025-26 | 211,060.0 | 208,614.5 | 225,637.5 | 202,987.1 | +2,445.5 [+1,555.9, +3,516.6] | 10/12 |
| **pooled** | | | | | **+3,955.5 [+2,733.8, +5,410.5]** | 21/24 |

Per-team diffs (v1 - v0-pit, season totals): 2024-25 [-1, 24, 48, 54, 78, 106, 110, 152, 170,
236, 238, 297]; 2025-26 [-8, 0, 16, 172, 185, 213, 234, 240, 242, 243, 286, 624].

### Secondary: oracle availability (both models value a man who did not play at 0)

| season | v1 | v0-pit | v1 - v0-pit [95% CI] | teams v1 > v0-pit |
|---|---|---|---|---|
| 2024-25 | 225,026.0 | 224,837.5 | +188.5 [-108.0, +459.1] | 8/12 |
| 2025-26 | 222,304.5 | 222,126.5 | +178.0 [-51.0, +410.5] | 8/12 |
| **pooled** | | | **+366.5 [+12.0, +729.6]** | 16/24 |

### How to read the lineup number

The primary gap (~+165 pts per team-season) is mostly **availability**. v1's games-missed feature
marks men on long absences down. v0-pit has no such signal, and the official report covers only part
of the test window (below), so v0-pit keeps starting injured men whose team plays. Production v0
gets ESPN's OUT status, which closes most of that gap. The oracle-availability comparison isolates
projection quality given play: **+366.5 pooled, about +15 pts per team-season (~0.1 per day)**. Its
interval excludes zero pooled but not in either season alone. Expect the live gain to be at that
scale, plus whatever v1's absence signal adds when ESPN status lags (e.g. men back from long
absences, minutes ramps).

Gate: MAE better in all three test seasons ✔; pooled primary lineup diff > 0 with CI excluding 0 ✔
-> **PASS**. The secondary also passes pooled.

## Data coverage and problems found

- **Box scores:** regular-season NBA games from 2023-10-24 to 2026-11-22: 1,233 / 1,236 / 1,235 / 258
  (2023-24 / 24-25 / 25-26 / 26-27). 11 games have a `game` row but no `player_game` rows (the summary
  failed and was skipped): 401585204, 401585217, 401705090, 401705098, 401705103, 401705104,
  401705183, 401810384, 401810499, 401810506, 401810507. In the backtest, those teams have no game that day.
- **Inactive players have no row.** ESPN box scores list only the active list (~13 per team-game,
  including coach's-decision DNPs). Injured or inactive men are absent, so "a dnp row means on the
  roster" is false for them. `build.py` adds an inactive row (62,374 of them) for every man whose
  latest row this season (or last season, if none yet) was with a team playing that day. Without
  this, the lineup backtest silently gave both models oracle availability for injured men.
- **All-Star weekend games are `season_type` 2** (team ids > 30, e.g. 31/32, 130579-130754). They're
  excluded everywhere (`home_team_id <= 30 AND away_team_id <= 30`).
- **`dnp_reason` is unreliable:** "COACH'S DECISION" sits on 97,080 rows where the man played. 478 rows
  have dnp=0 with min NULL (treated as not played).
- **Injury reports (5PM)** cover 161/161 days of 2023-24, 147/164 of 2024-25, **0/165 of 2025-26** and
  2/34 of 2026-27 at run time. The loader was still running, so re-running moves the primary numbers.
  Name join ('Last, First' -> normalised 'First Last', with Jr./Sr./II-V, accents, periods and the PDF's
  "Gilgeous- Alexander" hyphen break handled): 32,616 report rows from 2023-10-24 on. 32,357 (99.2%)
  match a known player id and 28,497 (87.4%) match a man with a team game that day. Most of the
  rest are two-way or long-term-out men with no box-score row in two seasons (Jaylen Clark, Vlatko
  Cancar, Steven Adams, ...). The join is name-only, not team-checked. 83 of 15,547 Out-listed
  player-days actually played (0.5%: late reversals, G-League returns, possible same-name collisions).
- **2002-2023 backfill** was partial (2002-10 to 2005-12 loaded) and is not used.
- **The first test run had data bugs** (All-Star games included, no inactive rows, traded-player
  duplicate rows). Fixing them did not change the chosen candidate or the feature set. First-run
  numbers also passed: MAE 6.974/7.019, 6.897/7.025, 6.858/7.056; primary pooled +1,080
  [+548, +1,685]; oracle-availability pooled +393.5 [+65, +742].

## Production

- `fbb/project_v1.py` builds the feature state from `data/fbb.db` (this season and last, read-only,
  ~0.15 s) and applies v0's availability rules.
- Per man, it falls back to v0 when v1 has no features: no box-score history, no opponent, a missing
  model file, or a store more than 3 days behind the game date.
- `lineup_decision.model` records `v1` when any man was projected by v1. `player_day_projection.inputs`
  carries `model` per man.
- **Requirement:** the box-score ingest has to keep `data/fbb.db` current. If it falls more than 3 days
  behind, the projector silently becomes v0.
- Rebuild the model with the two commands at the top. Only `numpy` is needed for fitting, and only
  for research. Runtime is stdlib.
