import sqlite3
import unittest

from fbb.features_v1 import design
from fbb.features_v2 import NEW_TERMS, TEAM_IDS, BuilderV2, design_v2, report_out_rows

NAMES = {10: "Tre Guard", 11: "Big Center", 12: "Bench Big", 20: "Other Guy"}
BOX = ["pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "reb", "ast", "stl", "blk", "tov"]


def row(pid, team, opp, home, pts=10, mins=30.0, dnp=0, starter=1, reb=5, ast=2, name=None):
    r = {k: 0 for k in BOX}
    r.update(player_id=pid, team_id=team, opp_team_id=opp, home=home, starter=starter, dnp=dnp,
             min=0.0 if dnp else mins, pts=0 if dnp else pts, reb=reb, ast=ast, name=name or NAMES.get(pid, "Some One"))
    return r


def game(h, a, rows):
    return {"home": h, "away": a, "rows": rows}


class FeaturesV2Tests(unittest.TestCase):
    def setUp(self):
        self.b = BuilderV2()
        for d in ["2025-11-01", "2025-11-03", "2025-11-05"]:
            self.b.add_day(d, [game(1, 2, [row(10, 1, 2, 1, mins=30, reb=2, ast=8), row(11, 1, 2, 1, mins=34, reb=10, ast=1),
                                           row(12, 1, 2, 1, mins=20, starter=0, reb=9, ast=1), row(20, 2, 1, 0)])])

    def test_design_extends_v1(self):
        f = self.b.features(12, 1, 2, 1, "2025-11-07", out={11})
        x = design_v2(f)
        self.assertEqual(set(x) - set(design(f)), set(NEW_TERMS))

    def test_out_teammate_minutes(self):
        f = self.b.features(12, 1, 2, 1, "2025-11-07", out={11})
        self.assertEqual(f["out_n"], 1)
        self.assertAlmostEqual(f["out_min"], 34.0)
        self.assertGreater(f["out_role_min"], 30.0)          # both bigs
        self.assertEqual(f["out_starter_min"], 0.0)          # 12 comes off the bench, 11 starts
        g = self.b.features(10, 1, 2, 1, "2025-11-07", out={11})
        self.assertLess(g["out_role_min"], f["out_role_min"])  # guard vs big
        self.assertAlmostEqual(g["out_starter_min"], 34.0)
        # not a teammate / himself / no report -> nothing
        self.assertEqual(self.b.features(12, 1, 2, 1, "2025-11-07", out={20, 12})["out_n"], 0)
        self.assertEqual(self.b.features(12, 1, 2, 1, "2025-11-07")["out_min"], 0.0)

    def test_freed_share_learns_from_absences(self):
        # 11 absent from the box score: 12 plays 34 minutes instead of ~20
        for d in ["2025-11-07", "2025-11-09"]:
            self.b.add_day(d, [game(1, 2, [row(10, 1, 2, 1), row(12, 1, 2, 1, mins=34), row(20, 2, 1, 0)])])
        self.assertGreater(self.b.share_of(12, 2026), 0.0)
        self.assertEqual(self.b.share_of(20, 2026), 0.0)

    def test_resolve_report_names(self):
        outs = self.b.resolve_out([("Atlanta Hawks", "Center, Big"), ("- Two- Way", "Big, Bench"), ("Atlanta Hawks", "Nobody, X")])
        self.assertEqual(outs, {1: {11, 12}})
        self.assertEqual(TEAM_IDS["Atlanta Hawks"], 1)
        self.assertEqual(len(set(TEAM_IDS.values())), 30)

    def test_report_rows_latest_report_wins(self):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE injury_report (report_date, report_hour, game_date, game_time, matchup, team, player, status, reason)")
        con.executemany("INSERT INTO injury_report VALUES (?,?,?,?,?,?,?,?,?)", [
            ("2025-11-06", "05PM", "11/07/2025", "", "ATL@BOS", "Atlanta Hawks", "A, B", "Out", ""),
            ("2025-11-07", "05PM", "11/07/2025", "", "ATL@BOS", "Atlanta Hawks", "A, B", "Questionable", ""),
            ("2025-11-07", "05PM", "11/07/2025", "", "ATL@BOS", "Atlanta Hawks", "C, D", "Out", ""),
            ("2025-11-06", "05PM", "11/07/2025", "", "MIA@NYK", "Miami Heat", "E, F", "Out", ""),
            ("2025-11-07", "05PM", "11/08/2025", "", "LAL@GSW", "Los Angeles Lakers", "G, H", "Out", "")])
        rows, found = report_out_rows(con, "2025-11-07")
        self.assertTrue(found)
        self.assertEqual(sorted(rows), [("Atlanta Hawks", "C, D"), ("Miami Heat", "E, F")])
        self.assertEqual(report_out_rows(con, "2025-11-06"), ([], False))

    def test_prune_keeps_two_seasons(self):
        self.b.prune(2028)
        self.assertEqual(self.b.pl, {})
        self.b.prune(2027)


if __name__ == "__main__":
    unittest.main()
