import unittest

from fbb.lineup import Man, moves_for, optimise, total

# PG, SG, SF, PF, C, G, SG/SF, PF/C starters; 4 bench, 2 IR
COUNTS = {"0": 1, "1": 1, "2": 1, "3": 1, "4": 1, "5": 1, "6": 0, "7": 1, "8": 0, "9": 1, "10": 0, "11": 0, "12": 4, "13": 2}


def m(pid, slot, elig, value, locked=False):
    return Man(pid=pid, name=f"p{pid}", slot=slot, eligible=elig, locked=locked, value=value)


class LineupTests(unittest.TestCase):
    def test_benched_player_with_game_replaces_idle_starter(self):
        men = [m(1, 4, [4, 9, 12], 0.0), m(2, 12, [4, 9, 12], 20.0)]
        t = optimise(men, COUNTS)
        self.assertEqual(t[2] in (4, 9), True)
        self.assertEqual(total(men, t), 20.0)

    def test_no_churn_when_nothing_gains(self):
        men = [m(1, 0, [0, 5, 12], 0.0), m(2, 12, [0, 5, 12], 0.0)]
        self.assertEqual(moves_for(men, optimise(men, COUNTS)), [])

    def test_locked_man_never_moves_and_keeps_slot(self):
        men = [m(1, 4, [4, 9, 12], 5.0, locked=True), m(2, 12, [4, 12], 40.0)]
        t = optimise(men, COUNTS)
        self.assertEqual(t[1], 4)
        self.assertEqual(t[2], 12)  # only C-eligible and C is held by a locked man

    def test_ir_untouched(self):
        men = [m(1, 13, [4, 12, 13], 30.0)]
        self.assertEqual(moves_for(men, optimise(men, COUNTS)), [])

    def test_flex_reassignment_maximises_total(self):
        # A is PG/G, B is PG only: B must take PG and A the G slot
        men = [m(1, 0, [0, 5, 12], 20.0), m(2, 12, [0, 12], 18.0)]
        t = optimise(men, COUNTS)
        self.assertEqual((t[1], t[2]), (5, 0))
        self.assertAlmostEqual(total(men, t), 38.0)


if __name__ == "__main__":
    unittest.main()
