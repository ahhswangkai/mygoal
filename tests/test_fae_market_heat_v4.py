import unittest

from football_ai.daily_analysis import (
    FAEDailyAIAnalyzer,
    build_daily_match_input,
    compact_daily_ai_run,
)


def match_with_market(**overrides):
    match = {
        "match_id": "v4-001",
        "match_number": "周一001",
        "owner_date": "2026-09-07",
        "league": "测试联赛",
        "home_team": "主队",
        "away_team": "客队",
        "euro_initial_win": 1.60,
        "euro_initial_draw": 4.00,
        "euro_initial_lose": 6.00,
        "euro_current_win": 1.60,
        "euro_current_draw": 4.00,
        "euro_current_lose": 6.00,
        "asian_initial_home_odds": 0.90,
        "asian_initial_handicap": "半/一",
        "asian_initial_away_odds": 0.98,
        "asian_current_home_odds": 0.90,
        "asian_current_handicap": "半/一",
        "asian_current_away_odds": 0.98,
        "ou_initial_over_odds": 0.96,
        "ou_initial_total": "2/2.5",
        "ou_initial_under_odds": 0.88,
        "ou_current_over_odds": 0.96,
        "ou_current_total": "2/2.5",
        "ou_current_under_odds": 0.88,
        "hi_handicap_value": -1,
        "hi_current_home_odds": 2.20,
        "hi_current_draw_odds": 3.40,
        "hi_current_away_odds": 2.65,
        "betting_ratio": {
            "source_provider": "vipc",
            "ordinary": {
                "home_support_rate": 72,
                "draw_support_rate": 18,
                "away_support_rate": 10,
            },
        },
    }
    match.update(overrides)
    return match


class MarketHeatV4Test(unittest.TestCase):
    def test_uses_own_no_vig_probability_and_flags_danger_band_heat(self):
        snapshot = build_daily_match_input(match_with_market())
        model = snapshot["market_heat_v4"]

        self.assertTrue(model["available"])
        self.assertAlmostEqual(
            model["outcomes"]["home"]["implied_probability"], 60.0, places=1
        )
        self.assertAlmostEqual(
            model["outcomes"]["home"]["deviation_pp"], 12.0, places=1
        )
        self.assertEqual(model["outcomes"]["home"]["class"], "C")
        self.assertTrue(model["favorite"]["death_warning"])

    def test_super_short_favorite_is_not_mechanically_marked_dead(self):
        snapshot = build_daily_match_input(match_with_market(
            euro_initial_win=1.20,
            euro_initial_draw=6.00,
            euro_initial_lose=12.00,
            euro_current_win=1.20,
            euro_current_draw=6.00,
            euro_current_lose=12.00,
            betting_ratio={
                "source_provider": "vipc",
                "ordinary": {
                    "home_support_rate": 89,
                    "draw_support_rate": 7,
                    "away_support_rate": 4,
                },
            },
        ))
        favorite = snapshot["market_heat_v4"]["favorite"]

        self.assertEqual(favorite["class"], "C")
        self.assertTrue(favorite["is_extreme_1_10_to_1_39"])
        self.assertFalse(favorite["death_warning"])

    def test_draw_requires_healthy_deviation_shallow_asian_and_low_total(self):
        snapshot = build_daily_match_input(match_with_market(
            euro_initial_win=2.45,
            euro_initial_draw=2.95,
            euro_initial_lose=2.85,
            euro_current_win=2.45,
            euro_current_draw=2.95,
            euro_current_lose=2.85,
            asian_initial_handicap="平手",
            asian_current_handicap="平手",
            betting_ratio={
                "source_provider": "vipc",
                "ordinary": {
                    "home_support_rate": 39,
                    "draw_support_rate": 33,
                    "away_support_rate": 28,
                },
            },
        ))
        draw = snapshot["market_heat_v4"]["draw"]

        self.assertTrue(draw["eligible"])
        self.assertEqual(draw["failed_checks"], [])

    def test_overheated_draw_is_not_eligible(self):
        snapshot = build_daily_match_input(match_with_market(
            euro_initial_win=2.45,
            euro_initial_draw=2.95,
            euro_initial_lose=2.85,
            euro_current_win=2.45,
            euro_current_draw=2.95,
            euro_current_lose=2.85,
            asian_initial_handicap="平手",
            asian_current_handicap="平手",
            betting_ratio={
                "source_provider": "vipc",
                "ordinary": {
                    "home_support_rate": 33,
                    "draw_support_rate": 44,
                    "away_support_rate": 23,
                },
            },
        ))
        draw = snapshot["market_heat_v4"]["draw"]

        self.assertFalse(draw["eligible"])
        self.assertIn(
            "draw_deviation_minus2_to_plus5", draw["failed_checks"]
        )

    def test_handicap_draw_needs_preconditions_and_exact_score_path(self):
        snapshot = build_daily_match_input(match_with_market(
            betting_ratio={
                "source_provider": "vipc",
                "ordinary": {
                    "home_support_rate": 63,
                    "draw_support_rate": 23,
                    "away_support_rate": 14,
                },
                "handicap": {
                    "home_support_rate": 43,
                    "draw_support_rate": 34,
                    "away_support_rate": 23,
                },
            },
        ))
        handicap_draw_heat = (
            snapshot["market_heat_v4"]["handicap_market"]
            ["outcomes"]["draw"]
        )
        self.assertEqual(handicap_draw_heat["selection"], "让平")
        self.assertEqual(handicap_draw_heat["support_rate"], 34.0)
        self.assertAlmostEqual(
            handicap_draw_heat["implied_probability"], 26.12, places=2
        )
        self.assertEqual(handicap_draw_heat["class"], "B")
        self.assertTrue(
            snapshot["market_heat_v4"]["handicap_draw"][
                "prerequisites_met"
            ]
        )
        candidate = {
            "selection": "让平",
            "tier": "core",
            "rating": 4.5,
            "score": 82,
            "probability": 29,
            "odds": 3.4,
        }
        passed = FAEDailyAIAnalyzer._apply_market_heat_v4_radar_gate(
            snapshot,
            {"score_candidates": ["2:1", "1:0", "2:0"]},
            candidate,
        )
        failed = FAEDailyAIAnalyzer._apply_market_heat_v4_radar_gate(
            snapshot,
            {"score_candidates": ["2:0", "3:0", "1:1"]},
            candidate,
        )

        self.assertTrue(passed["market_heat_v4_gate"]["passed"])
        self.assertTrue(passed["secondary_only"])
        self.assertFalse(failed["market_heat_v4_gate"]["passed"])
        self.assertEqual(failed["tier"], "watch")
        self.assertFalse(failed["formal_eligible"])

    def test_high_total_shallow_favorite_keeps_one_goal_path(self):
        snapshot = build_daily_match_input(match_with_market(
            ou_initial_over_odds=0.90,
            ou_initial_total="3/3.5",
            ou_initial_under_odds=0.96,
            ou_current_over_odds=0.90,
            ou_current_total="3/3.5",
            ou_current_under_odds=0.96,
            betting_ratio={
                "source_provider": "vipc",
                "ordinary": {
                    "home_support_rate": 64,
                    "draw_support_rate": 22,
                    "away_support_rate": 14,
                },
                "handicap": {
                    "home_support_rate": 45,
                    "draw_support_rate": 34,
                    "away_support_rate": 21,
                },
            },
        ))
        handicap_draw = snapshot["market_heat_v4"]["handicap_draw"]

        self.assertTrue(handicap_draw["prerequisites_met"])
        self.assertFalse(
            handicap_draw["checks"]["total_restrains_margin"]
        )
        self.assertTrue(handicap_draw["checks"]["open_one_goal_path"])
        self.assertTrue(
            handicap_draw["checks"]["exact_margin_market_path"]
        )

    def test_plus_one_flat_asian_supports_away_one_goal_path(self):
        snapshot = build_daily_match_input(match_with_market(
            euro_initial_win=3.20,
            euro_initial_draw=3.25,
            euro_initial_lose=2.20,
            euro_current_win=3.20,
            euro_current_draw=3.25,
            euro_current_lose=2.20,
            asian_initial_handicap="平手",
            asian_current_handicap="平手",
            asian_initial_home_odds=0.94,
            asian_current_home_odds=0.94,
            asian_initial_away_odds=0.94,
            asian_current_away_odds=0.94,
            ou_initial_over_odds=0.94,
            ou_initial_total=2.75,
            ou_initial_under_odds=0.94,
            ou_current_over_odds=0.94,
            ou_current_total=2.75,
            ou_current_under_odds=0.94,
            hi_handicap_value=1,
            hi_current_home_odds=1.75,
            hi_current_draw_odds=3.10,
            hi_current_away_odds=4.20,
            betting_ratio={
                "source_provider": "vipc",
                "ordinary": {
                    "home_support_rate": 25,
                    "draw_support_rate": 27,
                    "away_support_rate": 48,
                },
                "handicap": {
                    "home_support_rate": 35,
                    "draw_support_rate": 34,
                    "away_support_rate": 31,
                },
            },
        ))
        handicap_draw = snapshot["market_heat_v4"]["handicap_draw"]

        self.assertTrue(handicap_draw["prerequisites_met"])
        self.assertFalse(
            handicap_draw["checks"]["asian_depth_half_to_one"]
        )
        self.assertTrue(
            handicap_draw["checks"]["plus_one_balanced_away_path"]
        )

    def test_high_total_path_rejects_overheated_handicap_draw_funds(self):
        snapshot = build_daily_match_input(match_with_market(
            ou_initial_over_odds=0.90,
            ou_initial_total="3/3.5",
            ou_initial_under_odds=0.96,
            ou_current_over_odds=0.90,
            ou_current_total="3/3.5",
            ou_current_under_odds=0.96,
            betting_ratio={
                "source_provider": "vipc",
                "ordinary": {
                    "home_support_rate": 64,
                    "draw_support_rate": 22,
                    "away_support_rate": 14,
                },
                "handicap": {
                    "home_support_rate": 38,
                    "draw_support_rate": 43,
                    "away_support_rate": 19,
                },
            },
        ))
        handicap_draw = snapshot["market_heat_v4"]["handicap_draw"]

        self.assertFalse(handicap_draw["prerequisites_met"])
        self.assertFalse(
            handicap_draw["checks"]["handicap_draw_funds_supported"]
        )
        self.assertFalse(handicap_draw["checks"]["open_one_goal_path"])

    def test_b_class_draw_is_watch_only_and_cannot_enter_ticket(self):
        snapshot = build_daily_match_input(match_with_market(
            euro_initial_win=2.45,
            euro_initial_draw=2.95,
            euro_initial_lose=2.85,
            euro_current_win=2.45,
            euro_current_draw=2.95,
            euro_current_lose=2.85,
            asian_initial_handicap="平手",
            asian_current_handicap="平手",
            betting_ratio={
                "source_provider": "vipc",
                "ordinary": {
                    "home_support_rate": 36,
                    "draw_support_rate": 37,
                    "away_support_rate": 27,
                },
            },
        ))
        self.assertEqual(
            snapshot["market_heat_v4"]["outcomes"]["draw"]["class"],
            "B",
        )
        result = FAEDailyAIAnalyzer._apply_market_heat_v4_radar_gate(
            snapshot,
            {},
            {
                "selection": "平局",
                "tier": "core",
                "rating": 4.0,
                "score": 82,
                "probability": 31,
                "odds": 2.95,
                "guardrail_ticket_eligible": True,
            },
        )

        self.assertFalse(result["market_heat_v4_gate"]["passed"])
        self.assertEqual(result["market_heat_v4_watch"]["class"], "B")
        self.assertTrue(result["market_heat_v4_watch"]["watch_only"])
        self.assertFalse(result["guardrail_ticket_eligible"])

    def test_missing_ratio_does_not_apply_hard_gate_and_compact_keeps_v4(self):
        snapshot = build_daily_match_input(match_with_market(betting_ratio={}))
        self.assertFalse(snapshot["market_heat_v4"]["available"])
        candidate = {
            "selection": "平局",
            "tier": "core",
            "rating": 4.0,
            "score": 80,
            "probability": 30,
            "odds": 3.0,
        }
        unchanged = FAEDailyAIAnalyzer._apply_market_heat_v4_radar_gate(
            snapshot, {}, candidate
        )
        self.assertEqual(unchanged["tier"], "core")

        compact = compact_daily_ai_run({
            "matches": [{
                "match_id": "v4-001",
                "analysis": {},
                "input_snapshot": snapshot,
            }],
        })
        self.assertIn(
            "market_heat_v4", compact["matches"][0]["input_snapshot"]
        )
        self.assertIn(
            "betting_ratio", compact["matches"][0]["input_snapshot"]
        )


if __name__ == "__main__":
    unittest.main()
