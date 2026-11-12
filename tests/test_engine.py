"""
Antigravity Comprehensive Test Suite
------------------------------------
Automated regression tests covering grading, settlement, devigging math,
name normalization, payout tables, slip downgrade logic, and database integrity.
"""

import os
import json
import unittest
from typing import Dict, Any

import paper_tracker
import auto_bot
import slip_card_generator


class TestLegGrading(unittest.TestCase):
    def test_over_grading(self):
        self.assertEqual(paper_tracker.grade_leg("OVER", 19.5, 20.0), "HIT")
        self.assertEqual(paper_tracker.grade_leg("OVER", 19.5, 19.0), "MISS")
        self.assertEqual(paper_tracker.grade_leg("OVER", 20.0, 20.0), "PUSH")

    def test_under_grading(self):
        self.assertEqual(paper_tracker.grade_leg("UNDER", 5.5, 4.0), "HIT")
        self.assertEqual(paper_tracker.grade_leg("UNDER", 5.5, 6.0), "MISS")
        self.assertEqual(paper_tracker.grade_leg("UNDER", 5.0, 5.0), "PUSH")


class TestSlipGradingAndPayouts(unittest.TestCase):
    def _create_dummy_slip(self, leg_statuses, slip_type="flex_6", stake=10.0):
        legs = []
        for i, st in enumerate(leg_statuses):
            legs.append({
                "leg_id": f"dummy_{i}",
                "player_name": f"Player {i}",
                "choice": "OVER",
                "line": 10.0,
                "status": st,
            })
        return {
            "id": "test_slip",
            "slip_type": slip_type,
            "stake": stake,
            "legs": legs,
            "status": "PENDING",
            "return_payout": 0.0,
            "net_profit": 0.0,
            "payout_multiplier": 0.0,
        }

    def test_flex_6_perfect(self):
        # 6 of 6 HIT: 25x payout
        slip = self._create_dummy_slip(["HIT"] * 6)
        graded = paper_tracker.grade_slip(slip)
        self.assertEqual(graded["status"], "WON")
        self.assertEqual(graded["payout_multiplier"], 25.0)
        self.assertEqual(graded["return_payout"], 250.0)
        self.assertEqual(graded["net_profit"], 240.0)

    def test_flex_6_5_of_6(self):
        # 5 of 6 HIT, 1 MISS: 2x payout
        slip = self._create_dummy_slip(["HIT"] * 5 + ["MISS"])
        graded = paper_tracker.grade_slip(slip)
        self.assertEqual(graded["status"], "WON")
        self.assertEqual(graded["payout_multiplier"], 2.0)
        self.assertEqual(graded["return_payout"], 20.0)
        self.assertEqual(graded["net_profit"], 10.0)

    def test_flex_6_4_of_6(self):
        # 4 of 6 HIT, 2 MISS: 0.4x payout
        slip = self._create_dummy_slip(["HIT"] * 4 + ["MISS"] * 2)
        graded = paper_tracker.grade_slip(slip)
        self.assertEqual(graded["status"], "LOST")
        self.assertEqual(graded["payout_multiplier"], 0.4)
        self.assertEqual(graded["return_payout"], 4.0)
        self.assertEqual(graded["net_profit"], -6.0)

    def test_flex_6_with_1_push_downgrade(self):
        # 6-flex with 1 PUSH and 5 HITS -> downgrades to 5-flex with 5 hits (10x payout)
        slip = self._create_dummy_slip(["HIT"] * 5 + ["PUSH"])
        graded = paper_tracker.grade_slip(slip)
        self.assertEqual(graded["status"], "WON")
        self.assertEqual(graded["payout_multiplier"], 10.0)
        self.assertEqual(graded["return_payout"], 100.0)
        self.assertEqual(graded["net_profit"], 90.0)

    def test_flex_6_with_1_push_and_1_miss(self):
        # 6-flex with 1 PUSH, 4 HITS, 1 MISS -> downgrades to 5-flex with 4 hits (2.0x payout)
        slip = self._create_dummy_slip(["HIT"] * 4 + ["PUSH", "MISS"])
        graded = paper_tracker.grade_slip(slip)
        self.assertEqual(graded["status"], "WON")
        self.assertEqual(graded["payout_multiplier"], 2.0)
        self.assertEqual(graded["return_payout"], 20.0)
        self.assertEqual(graded["net_profit"], 10.0)

    def test_all_pushes_refund(self):
        slip = self._create_dummy_slip(["PUSH"] * 6)
        graded = paper_tracker.grade_slip(slip)
        self.assertEqual(graded["status"], "PUSH")
        self.assertEqual(graded["payout_multiplier"], 1.0)
        self.assertEqual(graded["return_payout"], 10.0)
        self.assertEqual(graded["net_profit"], 0.0)

    def test_pending_leg_keeps_slip_pending(self):
        slip = self._create_dummy_slip(["HIT"] * 5 + ["PENDING"])
        graded = paper_tracker.grade_slip(slip)
        self.assertEqual(graded["status"], "PENDING")


class TestPlayerNormalization(unittest.TestCase):
    def test_accents_and_suffixes(self):
        self.assertEqual(paper_tracker.normalize_player_name("Luka Dončić"), "luka doncic")
        self.assertEqual(paper_tracker.normalize_player_name("Dennis Schröder"), "dennis schroder")
        self.assertEqual(paper_tracker.normalize_player_name("Jaime Jaquez Jr."), "jaime jaquez")
        self.assertEqual(paper_tracker.normalize_player_name("Robert Williams III"), "robert williams")
        self.assertEqual(paper_tracker.normalize_player_name("Trey Murphy  Sr. "), "trey murphy")


class TestDevigMath(unittest.TestCase):
    def test_even_devig(self):
        to, tu = auto_bot.oddsjam_devig(-110, -110)
        self.assertAlmostEqual(to, 50.0, places=1)
        self.assertAlmostEqual(tu, 50.0, places=1)

    def test_skewed_devig(self):
        to, tu = auto_bot.oddsjam_devig(-140, 115)
        self.assertTrue(to > tu)
        self.assertAlmostEqual(to + tu, 100.0, places=1)


class TestGoblinDemonFiltering(unittest.TestCase):
    def test_adjusted_odds_filtered(self):
        raw_pp = {
            "data": [
                {
                    "attributes": {
                        "adjusted_odds": True,  # Goblin/Demon flag
                        "odds_type": "standard",
                        "stat_type": "Points",
                        "line_score": 15.5,
                        "allowed_wager_types": ["under_or_over"],
                    },
                    "relationships": {
                        "duration": {"data": {"id": "11"}},
                        "league": {"data": {"id": "7"}},
                        "new_player": {"data": {"id": "p1"}},
                    },
                },
                {
                    "attributes": {
                        "adjusted_odds": False,
                        "odds_type": "standard",
                        "stat_type": "Points",
                        "line_score": 20.5,
                        "allowed_wager_types": ["under_or_over"],
                    },
                    "relationships": {
                        "duration": {"data": {"id": "11"}},
                        "league": {"data": {"id": "7"}},
                        "new_player": {"data": {"id": "p2"}},
                    },
                },
            ],
            "included": [
                {"id": "7", "type": "league", "attributes": {"name": "NBA"}},
                {"id": "p1", "type": "new_player", "attributes": {"name": "Goblin Player"}},
                {"id": "p2", "type": "new_player", "attributes": {"name": "Standard Player"}},
            ],
        }
        fd_props = [
            {
                "Player": "Standard Player",
                "Game": "BOS @ NYK",
                "Stat": "Points",
                "Line": 20.5,
                "Over Odds": -140,
                "Under Odds": 110,
            },
            {
                "Player": "Goblin Player",
                "Game": "BOS @ NYK",
                "Stat": "Points",
                "Line": 15.5,
                "Over Odds": -200,
                "Under Odds": 160,
            },
        ]
        candidates = auto_bot.scan_and_evaluate_props(raw_pp, fd_props, min_win_pct=50.0)
        player_names = [c["player_name"] for c in candidates]
        self.assertIn("Standard Player", player_names)
        self.assertNotIn("Goblin Player", player_names)


class TestDatabaseIntegrity(unittest.TestCase):
    def test_baseline_db_integrity(self):
        with open("paper_bets.json") as f:
            db = json.load(f)
        slips = db.get("slips", [])
        self.assertGreaterEqual(len(slips), 11, "Expected at least 11 slips in baseline database")

        total_staked = 0.0
        total_returned = 0.0
        for s in slips:
            self.assertIn("id", s)
            self.assertIn("status", s)
            self.assertIn(s["status"], ["WON", "LOST", "PUSH", "PENDING"])
            self.assertEqual(len(s.get("legs", [])), 6, f"Slip {s['id']} does not have 6 legs")
            total_staked += float(s.get("stake", 0.0))
            total_returned += float(s.get("return_payout", 0.0))

        rep = paper_tracker.generate_performance_report(slips)
        self.assertEqual(rep["total_slips"], len(slips))
        self.assertAlmostEqual(rep["total_staked"], total_staked, places=2)
        self.assertAlmostEqual(rep["total_returned"], total_returned, places=2)

    def test_volume_db_integrity(self):
        with open("paper_bets_volume.json") as f:
            db = json.load(f)
        slips = db.get("slips", [])
        self.assertGreaterEqual(len(slips), 6, "Expected at least 6 slips in volume database")
        for s in slips:
            self.assertIn("id", s)
            self.assertIn("status", s)
            self.assertEqual(len(s.get("legs", [])), 6)


class TestSlipCardGenerator(unittest.TestCase):
    def test_card_renders_successfully(self):
        with open("paper_bets.json") as f:
            db = json.load(f)
        slip = db["slips"][0]
        # Test rendering without throwing any exception
        card_img = slip_card_generator.render_prizepicks_slip(slip, player_images={}, output_path=None)
        self.assertIsNotNone(card_img)
        self.assertEqual(card_img.size, (860, 1007))


class TestLineupBuilder(unittest.TestCase):
    def test_multi_game_constraint(self):
        # 6 players from the exact same game must fail min_games=2
        candidates = [
            {"player_name": f"P{i}", "game": "BOS @ NYK", "stat_type": "Points", "line": 15.5, "choice": "OVER", "win_pct": 60.0}
            for i in range(6)
        ]
        lineup = auto_bot.select_best_lineup(candidates, target_size=6, min_games=2)
        self.assertIsNone(lineup, "Lineup with only 1 game should be rejected")

        # 5 players from game 1, 1 from game 2 should pass
        candidates.append({"player_name": "P6", "game": "LAL @ GSW", "stat_type": "Points", "line": 20.5, "choice": "OVER", "win_pct": 59.0})
        lineup = auto_bot.select_best_lineup(candidates, target_size=6, min_games=2)
        self.assertIsNotNone(lineup)
        self.assertEqual(len(lineup), 6)
        games = set(p["game"] for p in lineup)
        self.assertGreaterEqual(len(games), 2)


if __name__ == "__main__":
    unittest.main()
