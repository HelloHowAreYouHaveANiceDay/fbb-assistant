import json
import sqlite3
import unittest

from fbb.features_v1 import Builder, design, fantasy_points, predict, season_of, v0_pit
from fbb.names import key
from fbb.project_v1 import MODEL_PATH, build_state, is_stale, load_model, project_v1

BOX = ["pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "reb", "ast", "stl", "blk", "tov"]


def row(pid, team, opp, home, pts=10, mins=30.0, dnp=0, starter=1):
    r = {k: 0 for k in BOX}
    r.update(player_id=pid, team_id=team, opp_team_id=opp, home=home, starter=starter, dnp=dnp,
             min=0.0 if dnp else mins, pts=0 if dnp else pts, fga=0, name=f"p{pid}")
    return r


def game(h, a, rows):
    return {"home": h, "away": a, "rows": rows}


class FeatureTests(unittest.TestCase):
    def test_league_scoring_matches_espn(self):
        # Jokic 2026-11-22 box score; ESPN scored it 63.5 in this league
        vals = [37, 14, 22, 1, 3, 8, 10, 5, 13, 18, 11, 1, 1, 3]
        self.assertAlmostEqual(fantasy_points(dict(zip(BOX, vals))), 63.5)

    def test_season_label(self):
        self.assertEqual(season_of("2025-11-01"), 2026)
        self.assertEqual(season_of("2026-03-01"), 2026)

    def test_features_use_only_earlier_days(self):
        b = Builder()
        for i, d in enumerate(["2025-11-01", "2025-11-03", "2025-11-05"]):
            b.add_day(d, [game(1, 2, [row(10, 1, 2, 1, pts=10 * (i + 1)), row(20, 2, 1, 0)])])
        f = b.features(10, 1, 2, 1, "2025-11-06")
        self.assertEqual(f["n_cur"], 3)
        self.assertAlmostEqual(f["season_fp"], 20.0)
        self.assertEqual(f["rest"], 1)
        self.assertEqual(f["b2b"], 1)
        self.assertAlmostEqual(v0_pit(f), 20.0)          # l15 and season both 20
        self.assertIsNone(b.features(99, 1, 2, 1, "2025-11-06"))  # no history

    def test_missed_games_and_missing_teammates(self):
        b = Builder()
        b.add_day("2025-11-01", [game(1, 2, [row(10, 1, 2, 1), row(11, 1, 2, 1, mins=34)])])
        b.add_day("2025-11-03", [game(1, 2, [row(10, 1, 2, 1), row(11, 1, 2, 1, dnp=1)])])
        f = b.features(10, 1, 2, 1, "2025-11-05")
        self.assertEqual(f["missed"], 0)
        self.assertAlmostEqual(f["team_missing_min"], 34.0)
        self.assertEqual(b.features(11, 1, 2, 1, "2025-11-05")["missed"], 1)

    def test_v0_pit_falls_back_to_prior_season(self):
        b = Builder()
        for d in ["2025-03-01", "2025-03-03", "2025-03-05"]:
            b.add_day(d, [game(1, 2, [row(10, 1, 2, 1, pts=30)])])
        f = b.features(10, 1, 2, 1, "2025-10-25")
        self.assertEqual(f["n_cur"], 0)
        self.assertAlmostEqual(v0_pit(f), 30.0)

    def test_report_names_join(self):
        self.assertEqual(key("Holland II, Ronald", from_report=True), key("Ronald Holland II"))
        self.assertEqual(key("Jackson Jr., Jaren", from_report=True), key("Jaren Jackson Jr."))
        self.assertEqual(key("Gilgeous- Alexander, Shai", from_report=True), key("Shai Gilgeous-Alexander"))
        self.assertEqual(key("Jokić, Nikola", from_report=True), key("Nikola Jokic"))


class ProjectorTests(unittest.TestCase):
    def setUp(self):
        self.model = load_model()
        self.assertIsNotNone(self.model, f"missing {MODEL_PATH}")
        self.b = Builder()
        for d in ["2025-11-01", "2025-11-03", "2025-11-05"]:
            self.b.add_day(d, [game(1, 2, [row(10, 1, 2, 1, pts=25), row(20, 2, 1, 0)])])

    def test_artifact_covers_design(self):
        f = self.b.features(10, 1, 2, 1, "2025-11-07")
        self.assertEqual(set(self.model["coef"]), set(design(f)))
        p = predict(self.model["coef"], f)
        self.assertGreater(p, 10)
        self.assertLess(p, 40)

    def test_availability_rules(self):
        args = (self.b, self.model, 10, 1, 2, True, "2025-11-07")
        base = project_v1(*args, True, "ACTIVE")[0]
        self.assertEqual(project_v1(*args, True, "OUT")[0], 0.0)
        self.assertEqual(project_v1(*args, False, "ACTIVE")[0], 0.0)
        self.assertAlmostEqual(project_v1(*args, True, "DAY_TO_DAY")[0], base * 0.75)

    def test_falls_back_when_no_features(self):
        self.assertIsNone(project_v1(self.b, self.model, 99, 1, 2, True, "2025-11-07", True, None))
        self.assertIsNone(project_v1(None, self.model, 10, 1, 2, True, "2025-11-07", True, None))
        self.assertIsNone(project_v1(self.b, None, 10, 1, 2, True, "2025-11-07", True, None))
        self.assertIsNone(project_v1(self.b, self.model, 10, 1, None, True, "2025-11-07", True, None))

    def test_build_state_from_store_is_point_in_time(self):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE game (game_id INTEGER, game_date TEXT, season_type INTEGER, home_team_id INTEGER, away_team_id INTEGER)")
        cols = list(dict.fromkeys(["game_id", "player_id", "name", "team_id", "opp_team_id", "home", "starter", "dnp",
                                   "min", "pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "reb", "ast",
                                   "stl", "blk", "tov"]))
        con.execute(f"CREATE TABLE player_game ({','.join(cols)})")
        for gid, d, pts in [(1, "2025-11-01", 10), (2, "2025-11-03", 20), (3, "2025-11-05", 90), (4, "2026-02-15", 50)]:
            st, a = (2, 31) if gid == 4 else (2, 2)   # gid 4 = All-Star game (team id > 30): ignored
            con.execute("INSERT INTO game VALUES (?,?,?,?,?)", (gid, d, st, 1, a))
            r = row(10, 1, a, 1, pts=pts)
            r["game_id"] = gid
            con.execute(f"INSERT INTO player_game VALUES ({','.join('?' * len(cols))})", [r.get(c, 0) for c in cols])
        b, last = build_state(con, "2025-11-05")
        self.assertEqual(last, "2025-11-03")
        self.assertAlmostEqual(b.features(10, 1, 2, 1, "2025-11-05")["season_fp"], 15.0)
        self.assertFalse(is_stale(last, "2025-11-05"))
        self.assertTrue(is_stale(last, "2025-11-09"))
        b2, last2 = build_state(con, "2026-03-01")
        self.assertEqual(last2, "2025-11-05")


if __name__ == "__main__":
    unittest.main()
