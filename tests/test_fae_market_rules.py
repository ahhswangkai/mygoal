import unittest

from football_ai.daily_analysis import build_daily_match_input
from football_ai.engine import FootballAIEngine
from football_ai.learning import FAEReviewEngine
from football_ai.market_rules import evaluate_historical_market_rules
from football_ai.skills import baseline_skill_documents


class FAEHistoricalMarketRulesTests(unittest.TestCase):
    def test_detects_stable_ordinary_draw_band(self):
        profile = evaluate_historical_market_rules({
            "league": "测试联赛",
            "euro_current_win": "1.78",
            "euro_current_draw": "3.40",
            "euro_current_lose": "4.60",
        })

        ordinary = profile["ordinary_draw"]
        self.assertTrue(ordinary["eligible_for_adjustment"])
        self.assertEqual(ordinary["adjustment_pp"], 2.0)
        self.assertIn(
            "history-draw-favorite-170-189",
            ordinary["matched_rule_ids"],
        )
        self.assertEqual(ordinary["signals"][0]["sample"], 657)

    def test_detects_plus_one_low_handicap_draw_odds(self):
        profile = evaluate_historical_market_rules({
            "league": "测试联赛",
            "euro_current_win": "4.50",
            "euro_current_draw": "3.40",
            "euro_current_lose": "1.68",
            "hi_handicap_value": "1",
            "hi_initial_draw_odds": "3.10",
            "hi_current_draw_odds": "3.05",
        })

        handicap_draw = profile["handicap_draw"]
        self.assertEqual(handicap_draw["adjustment_pp"], 4.0)
        self.assertEqual(handicap_draw["target_goal_difference"], -1)
        self.assertEqual(handicap_draw["definition"], "客队恰好赢1球")
        self.assertIn(
            "history-hhad-plus1-draw-270-319",
            handicap_draw["matched_rule_ids"],
        )

    def test_engine_rule_weight_changes_hhad_draw_probability(self):
        match = {
            "match_id": "rule-test",
            "owner_date": "2026-07-24",
            "home_team": "主队",
            "away_team": "客队",
            "euro_current_win": "4.50",
            "euro_current_draw": "3.40",
            "euro_current_lose": "1.68",
            "hi_handicap_value": "1",
            "hi_initial_home_odds": "2.60",
            "hi_initial_draw_odds": "3.10",
            "hi_initial_away_odds": "2.10",
            "hi_current_home_odds": "2.60",
            "hi_current_draw_odds": "3.05",
            "hi_current_away_odds": "2.10",
            "ou_current_total": "2.5",
        }
        engine = FootballAIEngine()
        context = engine.build_context(match)

        low = engine.generate_from_context(
            context,
            rule_weights={"history-hhad-plus1-draw-270-319": 0.5},
            use_ai=False,
        )
        high = engine.generate_from_context(
            context,
            rule_weights={"history-hhad-plus1-draw-270-319": 1.5},
            use_ai=False,
        )

        self.assertGreater(
            high["core"]["probabilities"]["hhad"]["draw"],
            low["core"]["probabilities"]["hhad"]["draw"],
        )
        self.assertIn(
            "history-hhad-plus1-draw-270-319",
            high["core"]["historical_odds_rules"]["matched_rule_ids"],
        )

    def test_daily_input_exposes_auditable_rules(self):
        rules = evaluate_historical_market_rules({
            "euro_current_win": "1.78",
            "euro_current_draw": "3.40",
            "euro_current_lose": "4.60",
        })
        row = build_daily_match_input(
            {
                "match_id": "201",
                "home_team": "主队",
                "away_team": "客队",
                "euro_current_win": "1.78",
                "euro_current_draw": "3.40",
                "euro_current_lose": "4.60",
            },
            {
                "analysis": {},
                "core": {"historical_odds_rules": rules},
            },
        )

        self.assertEqual(
            row["historical_odds_rules"]["version"],
            "historical-market-rules-v1",
        )
        self.assertIn(
            "history-draw-favorite-170-189",
            row["historical_odds_rules"]["matched_rule_ids"],
        )

    def test_hhad_historical_rule_is_reviewable(self):
        review = FAEReviewEngine().review(
            {
                "match_id": "201",
                "core": {
                    "recommendation": {"primary": "让平"},
                    "rule_signals": [{
                        "rule_id": "history-hhad-plus1-draw-270-319",
                        "market": "hhad",
                        "prediction": "draw",
                        "handicap": 1,
                        "reason": "客队恰好赢1球",
                    }],
                },
            },
            {
                "match_id": "201",
                "home_score": 0,
                "away_score": 1,
                "handicap": 1,
            },
        )

        self.assertTrue(review["rule_results"][0]["hit"])

    def test_skill_registry_contains_historical_market_patterns(self):
        skills = {
            item["skill_id"]: item
            for item in baseline_skill_documents()
        }
        self.assertIn("historical-market-patterns", skills)
        self.assertIn(
            "history-hhad-plus1-draw-270-319",
            skills["historical-market-patterns"]["parameters"]["rule_weights"],
        )


if __name__ == "__main__":
    unittest.main()
