# Projection v2: long history + minutes redistribution. **DID NOT PASS the gate**

**Result: v2 beats v1 on MAE in every test season. The pooled lineup-points edge is positive (+323) but its 95%
interval includes zero ([-31.5, +691.0]), so v2 fails the pre-registered ship rule (`docs/model-v2-prereg.md`,
committed before any test number).** Nothing is wired into `fbb/cli.py`, and v1 stays live. The research code,
the shared stdlib feature code (`fbb/features_v2.py`) and its tests are in this PR so that a later run can
build on them.

Numbers are from the run on 2026-11-24 against the store as of 2026-11-23: box scores 2002-03 .. 2026-11-23,
5PM reports 2021-10-19 .. 2026-11-23. Reproduce:

    python3 research/v1/build.py && python3 research/v2/build.py         # stdlib, ~1 min
    uv run --with numpy python research/v2/evaluate.py                    # dev pick, v1 refit, gate (~6 min)
    uv run --with numpy python research/v2/ablation.py                    # exploratory, not gated

The incumbent was reproduced exactly from `research/v1` (same dev pick C3_full, MAE 6.951 / 6.857 / 6.861,
lineup v1 - v0-pit pooled +802.0 [+258.9, +1,370.1]). These match the re-run numbers quoted for v1 with full
injury data.

## Method

- **Features:** `fbb/features_v2.py` (stdlib; production would share it). `BuilderV2` extends the v1 builder.
  - **v1's 17 terms**, now computed over the full history, so 2023-24 rows have prior-season inputs. v1's own
    dataset starts on 2023-10-24, so its 2023-24 rows have none.
  - **Minutes redistribution.** "Out teammates" are men listed **Out on the 5PM-ET report** for that game (latest
    5PM report dated on or before the game day for that matchup), resolved by name. A man counts only if he
    played for the team in its last 10 games this season and his latest game was for that team. Four terms:
    - `out_min_x_rate`: their summed EWMA minutes x the player's points per minute.
    - `out_role_min_x_rate`: the same sum weighted by role similarity, using reb/(reb+ast) as a position proxy,
      since box scores carry no positions.
    - `out_starter_min_x_rate`: the sum over teammates with the same starter flag.
    - `out_share_x_rate`: out minutes x the player's historical share of freed minutes x rate. The share is the
      slope of his minutes over his EWMA minutes against the minutes of absent rotation mates in past games,
      shrunk.
  - **Seasons before 2021-22 have no reports.** Their training rows use a proxy: rotation teammates absent from
    that game's box score. Report-era rows, dev, test and production use the report only.
- **Model:** the v1 linear least squares, ridge 1.0, floor 0.
- **Candidates (14):** feature set A (v1 terms) or B (A + 4 redistribution terms), crossed with history window
  last 3 / 6 / 10 / all seasons, or all seasons with 0.5^(age/h) weights for h = 2, 4, 8.
- **Dev:** target 2023-24, each candidate fit on seasons up to 2022-23.

### Dev (target 2023-24, n = 26,187 played games)

| window | A (v1 terms) MAE | B (+ redistribution) MAE |
|---|---|---|
| last 3 seasons | 6.827 | **6.800 (chosen)** |
| last 6 | 6.831 | 6.805 |
| last 10 | 6.826 | 6.809 |
| all (2003-04 on) | 6.822 | 6.818 |
| all, half-life 2 seasons | 6.826 | 6.803 |
| all, half-life 4 | 6.825 | 6.809 |
| all, half-life 8 | 6.823 | 6.814 |

v0-pit on the same rows: 6.971.

**History window: last 3 seasons, chosen on dev only.** Old seasons barely matter for the v1 terms: A is flat
within 0.01 across windows, with "all" marginally best. With the redistribution terms, more history and less
recency weighting is monotonically worse (B: 6.800 at 3 seasons, 6.818 with all). Two things push that way:
era drift (pace and 3PA change the points-per-minute scale), and the pre-2021-22 rows that use the box-score
proxy (below), whose out-minutes distribution differs a lot from the report's. With a 3-season window every
test fit (2021-22..2023-24, 22-23..24-25, 23-24..25-26) contains report-era rows only. The proxy is used only
in dev candidates whose window reaches back past 2021-22.

## Accuracy (rolling origin, identical rows: played, both models have features; 0 rows dropped)

| test season | n | **v2** MAE | v1 MAE | v0-pit MAE | v2 RMSE | v1 RMSE | v2 bias | v1 bias |
|---|---|---|---|---|---|---|---|---|
| 2024-25 (v2 fit 21-22..23-24) | 26,109 | **6.795** | 6.951 | 6.992 | **8.721** | 8.874 | +0.043 | +0.106 |
| 2025-26 (v2 fit 22-23..24-25) | 26,447 | **6.798** | 6.857 | 6.987 | **8.698** | 8.758 | +0.092 | +0.071 |
| 2026-27 to 11-23 (v2 fit 23-24..25-26) | 5,689 | **6.802** | 6.861 | 7.058 | **8.717** | 8.769 | +0.548 | +0.757 |

MAE by actual minutes (v2 / v1):

| season | <15 | 15-25 | 25-32 | 32+ |
|---|---|---|---|---|
| 2024-25 | **5.259** / 5.668 | **6.312** / 6.366 | **7.350** / 7.397 | **8.315** / 8.447 |
| 2025-26 | **5.418** / 5.580 | 6.263 / **6.257** | **7.354** / 7.363 | **8.400** / 8.502 |
| 2026-27 | **5.497** / 5.714 | **6.352** / 6.383 | 7.443 / **7.438** | 8.384 / **8.359** |

MAE gate: ✔ in all three seasons.

**Where the MAE gain comes from** (exploratory ablation, not pre-registered, `research/v2/ablation.py`):

| season | v1 | A, last 3 (v1 terms, long-history features) | **B, last 3 (= v2)** | B, last 10 | B, all |
|---|---|---|---|---|---|
| 2024-25 | 6.951 | 6.845 | **6.795** | 6.808 | 6.824 |
| 2025-26 | 6.857 | 6.836 | **6.798** | 6.807 | 6.820 |
| 2026-27 | 6.861 | 6.852 | **6.802** | 6.810 | 6.838 |

- Refitting v1's terms with complete feature history is worth about 0.01-0.1. The large 2024-25 gap exists
  mostly because v1's only training season had no prior-season features.
- The report-based redistribution terms add a steady 0.04-0.05 MAE per season.
- Fitted coefficients are stable across folds: out_min_x_rate ~ +0.032, out_role ~ +0.035,
  out_starter ~ -0.03, out_share ~ +0.24 to 0.28.

## Lineup points (research/v1 method, unchanged)

hist_rosters fixed, daily `fbb.lineup.optimise`, actual points. Out on the 5PM report scores 0 for both models.
The interval is a 95% bootstrap over ISO weeks.

### Primary (gated)

| season | v2 | v1 | v0-pit | oracle | random | **v2 - v1 [95% CI]** | teams v2 > v1 |
|---|---|---|---|---|---|---|---|
| 2024-25 | 223,609.5 | 223,339.5 | 222,896.0 | 229,074.0 | 207,559.2 | +270.0 [-26.5, +586.0] | 8/12 |
| 2025-26 | 220,954.0 | 220,901.0 | 220,542.5 | 225,637.5 | 202,987.1 | +53.0 [-177.5, +282.5] | 6/12 (2 tied) |
| **pooled** | | | | | | **+323.0 [-31.5, +691.0]** ✘ | 14/24 |

- Per-team diffs, 2024-25: [-19.5, -9.5, -7.5, -1.5, 4.5, 5.0, 12.5, 15.5, 16.0, 77.5, 78.5, 98.5].
- Per-team diffs, 2025-26: [-43.5, -22.0, -17.0, -7.0, 0, 0, 2.0, 3.5, 10.5, 16.5, 54.0, 56.0].
- For context, pooled v1 - v0-pit = +802.0 [+258.9, +1,370.1] (the incumbent's edge, reproduced), and
  v2 - v0-pit = +1,125.0 [+657.0, +1,622.5].

### Secondary: oracle availability (not gated)

| season | v2 - v1 [95% CI] |
|---|---|
| 2024-25 | +226.5 [-30.1, +508.6] |
| 2025-26 | +21.5 [-156.5, +205.0] |
| pooled | +248.0 [-71.6, +576.5] |

Gate: MAE ✔ (3/3); pooled lineup CI excludes zero ✘. **FAIL.**

How to read it: the accuracy gain is real but small, about 0.06 points per player-game in 2025-26 and 2026-27.
Only a fraction of it lands on lineup-decision margins (start/sit between men with games). The point estimate
is about +13 points per team-season, and 24 team-seasons cannot separate that from zero. 2025-26, the season
with the smaller MAE gain, contributes almost nothing.

## Data problems found

- **Box scores:** 76 regular-season games have a `game` row but no `player_game` rows, and are skipped.
  Most are in 2020-21 (33) and 2021-22 (11); the rest are scattered: 2024-25 5, 2025-26 4, 2023-24 2, and
  1-3 in most older seasons. Per-season counts are in `research/v2/out/coverage.json`.
- **Box-score format drifts by era**, which breaks the "absent from the box score" proxy:
  - In 2006-2012 ESPN lists ~14.4 men per team-game, apparently including inactives. In other years it lists ~12.5-13.
  - 2009-2012 rows never set `dnp` (always 0, with min 0/NULL). `played()` still works because it requires min > 0.
  - Effect: the proxy sees fewer absences in old seasons. Pre-2021-22 rows average 13.9 proxy out-minutes
    (non-zero on 51% of rows). On report-era rows the proxy averages 43.0 (90% non-zero) against the report's
    36.2 (80%), with correlation 0.80 between proxy and report. The proxy is a poor stand-in before ~2013. This
    is one reason long windows lost on dev.
- **Report join:**
  - 5PM Out rows that resolve to a known name: 98% (2021-22) to 87% (2026-27).
  - Out rows that resolve to a man in a playing team's last-10-game rotation: about 50%. The rest are long-term,
    two-way or G-League men, who by design add no freed minutes.
  - Report team name vs the man's actual box-score team: 3,395 same, 25 different (mid-season trades).
  - The report `team` column holds parse junk on a few rows (e.g. "- Two- Way", "SUBMITTED - ..."). Those rows
    fall back to the man's latest box-score team.
  - A 5PM report also lists some next-day games: 31k of 99k rows have game_date = report_date + 1. The feature
    uses the latest report for each matchup. The lineup backtest uses research/v1's `injury_out` unchanged,
    which unions both.
  - Lineup-backtest join (2023-24 on): 63,051 rows, 62,340 with a known name, 57,301 joined to a man with a
    team game that day.
- The v2 dataset also adds synthesised inactive rows for 2023-24. v1 couldn't, because it had no 2022-23
  history. Lineup seasons 2024-25 and 2025-26 are row-identical between the models: no prediction was missing
  on either side.

## If someone ships a v2 later

`fbb/features_v2.py` is production-ready stdlib, with tests in `tests/test_features_v2.py`. It includes
`report_out_rows()` for the store's `injury_report` table and `BuilderV2.resolve_out()`. A projector would mirror
`project_v1.py`.

One caveat: the hourly lineup job runs 07:00-18:00 PT, but the report loader runs at 14:20 PT. Before then,
today's 5PM report is not in the store and the redistribution terms would be 0. Earlier runs would also need a
fallback, either the previous report or the noon edition.

`research/v2/out/model_v2.json` holds the would-be artifact (B, last 3, fit on 2023-24..2025-26). It is NOT
copied to `data/`.
