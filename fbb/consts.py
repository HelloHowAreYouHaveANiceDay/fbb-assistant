"""Constants for ESPN fantasy basketball (fba)."""

# lineupSlotId -> label
SLOTS = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C", 5: "G", 6: "F", 7: "SG/SF", 8: "G/F", 9: "PF/C",
         10: "F/C", 11: "UT", 12: "BE", 13: "IR"}
BENCH, IR = 12, 13

# statId -> label (ESPN fba)
STATS = {0: "PTS", 1: "BLK", 2: "STL", 3: "AST", 4: "OREB", 5: "DREB", 6: "REB", 7: "EJ", 8: "FF", 9: "PF",
         10: "TECH", 11: "TO", 12: "DQ", 13: "FGM", 14: "FGA", 15: "FTM", 16: "FTA", 17: "3PM", 18: "3PA",
         19: "FG%", 20: "FT%", 21: "3P%", 37: "DD", 38: "TD", 39: "QD", 40: "MIN", 41: "GS", 42: "GP"}

# stat split ids on player.stats[]: statSourceId 0 = actual, 1 = ESPN projection
SPLIT_SEASON, SPLIT_LAST7, SPLIT_LAST15, SPLIT_LAST30, SPLIT_GAME = 0, 1, 2, 3, 5

OUT_STATUSES = {"OUT", "SUSPENSION", "INJURY_RESERVE"}
