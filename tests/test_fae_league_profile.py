import unittest

from football_ai.league_profile import (
    build_match_goal_margin_models,
    build_league_profiles,
    classify_asian_risk_patterns,
    classify_market_favorite,
    league_aliases,
)


def historical_match(
    owner_date,
    home_score,
    away_score,
    *,
    league="测试联赛",
    home_odds=1.8,
    away_odds=4.0,
    handicap=-1,
    total_line=2.5,
):
    return {
        "league": league,
        "owner_date": owner_date,
        "home_score": home_score,
        "away_score": away_score,
        "euro_current_win": home_odds,
        "euro_current_lose": away_odds,
        "hi_handicap_value": handicap,
        "ou_current_total": total_line,
    }


class LeagueProfileTests(unittest.TestCase):
    def test_known_league_aliases_share_history_pool(self):
        self.assertEqual(
            set(league_aliases("瑞典超")),
            {"瑞典超", "瑞超"},
        )
        self.assertEqual(league_aliases("芬超"), ["芬超"])

    def test_excludes_target_day_and_future_results(self):
        rows = [
            historical_match("2026-07-18", 1, 1),
            historical_match("2026-07-19", 2, 0),
            historical_match("2026-07-20", 3, 0),
        ]
        profile = build_league_profiles(
            {"测试联赛": rows},
            "2026-07-19",
            global_matches=rows,
            minimum_samples=1,
        )["测试联赛"]

        self.assertEqual(profile["sample_size"], 1)
        self.assertEqual(profile["baseline"]["draw_rate"], 100.0)
        self.assertTrue(profile["governance"]["future_matches_excluded"])

    def test_builds_outcome_handicap_and_goal_baselines(self):
        rows = [
            historical_match("2026-07-15", 2, 1),
            historical_match("2026-07-14", 1, 1),
            historical_match("2026-07-13", 0, 1),
            historical_match("2026-07-12", 3, 1),
        ]
        profile = build_league_profiles(
            {"测试联赛": rows},
            "2026-07-19",
            global_matches=rows,
            minimum_samples=2,
        )["测试联赛"]

        self.assertTrue(profile["eligible_for_adjustment"])
        self.assertAlmostEqual(
            profile["baseline"]["home_win_rate"], 50.0, delta=1.0
        )
        self.assertAlmostEqual(
            profile["baseline"]["draw_rate"], 25.0, delta=1.0
        )
        self.assertAlmostEqual(
            profile["baseline"]["avg_total_goals"], 2.5, delta=0.1
        )
        self.assertGreater(
            profile["sporttery_handicap"]["sample"], 0
        )
        self.assertIn("1.51-1.80", profile["favorite_odds_bands"])

    def test_small_sample_cannot_adjust_analysis(self):
        rows = [
            historical_match("2026-07-18", 1, 1),
            historical_match("2026-07-17", 1, 0),
        ]
        profile = build_league_profiles(
            {"测试联赛": rows},
            "2026-07-19",
            global_matches=rows,
            minimum_samples=30,
        )["测试联赛"]

        self.assertFalse(profile["eligible_for_adjustment"])
        self.assertEqual(profile["confidence"], "样本不足")
        self.assertIn("样本不足", profile["hidden_signals"][0])

    def test_recent_matches_receive_more_weight(self):
        rows = [
            historical_match("2026-07-18", 1, 1),
            historical_match("2025-07-18", 2, 0),
        ]
        profile = build_league_profiles(
            {"测试联赛": rows},
            "2026-07-19",
            global_matches=rows,
            half_life_days=90,
            minimum_samples=1,
        )["测试联赛"]

        self.assertGreater(profile["baseline"]["draw_rate"], 90)

    def test_builds_market_surprise_rates_for_clear_favorites(self):
        rows = [
            historical_match("2026-07-18", 2, 0),
            historical_match("2026-07-18", 1, 1),
            historical_match("2026-07-18", 0, 1),
            historical_match(
                "2026-07-18",
                1,
                0,
                home_odds=4.0,
                away_odds=1.8,
                handicap=1,
            ),
        ]
        profile = build_league_profiles(
            {"测试联赛": rows},
            "2026-07-19",
            global_matches=rows,
            minimum_samples=1,
        )["测试联赛"]
        surprise = profile["market_surprise"]

        self.assertEqual(surprise["sample"], 4)
        self.assertEqual(surprise["favorite_fail_rate"], 75.0)
        self.assertEqual(surprise["favorite_draw_rate"], 25.0)
        self.assertEqual(surprise["underdog_win_rate"], 50.0)
        self.assertEqual(surprise["favorite_not_cover_rate"], 75.0)

    def test_classification_drives_auditable_match_list(self):
        upset = classify_market_favorite(historical_match(
            "2026-07-18", 0, 1, home_odds=1.7, away_odds=4.2,
        ))
        balanced = classify_market_favorite(historical_match(
            "2026-07-18", 1, 1, home_odds=2.1, away_odds=2.2,
        ))

        self.assertEqual(upset["favorite_side"], "home")
        self.assertEqual(upset["result_type"], "upset")
        self.assertTrue(upset["favorite_failed"])
        self.assertIsNone(balanced)

    def test_classifies_asian_water_warning_patterns(self):
        row = historical_match(
            "2026-07-18", 1, 1, home_odds=1.65, away_odds=4.8
        )
        row.update({
            "euro_initial_win": 1.75,
            "asian_initial_home_odds": 0.90,
            "asian_initial_handicap": "半球/一球",
            "asian_initial_away_odds": 0.98,
            "asian_current_home_odds": 1.02,
            "asian_current_handicap": "半球",
            "asian_current_away_odds": 0.86,
        })

        risk = classify_asian_risk_patterns(row)

        self.assertTrue(risk["data_complete"])
        self.assertIn("handicap_retreat", risk["pattern_ids"])
        self.assertIn("upper_water_rise", risk["pattern_ids"])
        self.assertIn("euro_asian_divergence", risk["pattern_ids"])
        self.assertNotIn("water_drop_without_deepen", risk["pattern_ids"])

    def test_builds_historical_not_cover_rate_by_water_pattern(self):
        covered = historical_match(
            "2026-07-18", 2, 0, home_odds=1.65, away_odds=4.8
        )
        not_covered = historical_match(
            "2026-07-17", 1, 1, home_odds=1.65, away_odds=4.8
        )
        for row in (covered, not_covered):
            row.update({
                "euro_initial_win": 1.70,
                "asian_initial_home_odds": 0.95,
                "asian_initial_handicap": "半球",
                "asian_initial_away_odds": 0.85,
                "asian_current_home_odds": 0.80,
                "asian_current_handicap": "半球",
                "asian_current_away_odds": 1.00,
            })

        profile = build_league_profiles(
            {"测试联赛": [covered, not_covered]},
            "2026-07-19",
            global_matches=[covered, not_covered],
            minimum_samples=1,
        )["测试联赛"]
        pattern = profile["asian_risk_patterns"]["patterns"][
            "water_drop_without_deepen"
        ]

        self.assertEqual(pattern["sample"], 2)
        self.assertAlmostEqual(pattern["not_cover_rate"], 50.0, delta=1.0)

    def test_goal_margin_model_excludes_target_day_and_future(self):
        current = historical_match(
            "2026-07-19", 0, 0, handicap=-1, home_odds=1.8, away_odds=4.2
        )
        current.update({
            "match_id": "current-1",
            "euro_current_draw": 3.4,
            "hi_current_home_odds": 2.2,
            "hi_current_draw_odds": 3.5,
            "hi_current_away_odds": 2.6,
            "asian_current_handicap": "半球",
        })
        past = [
            {
                **current,
                "match_id": f"past-{index}",
                "owner_date": "2026-07-18",
                "home_score": 1,
                "away_score": 1,
            }
            for index in range(12)
        ]
        future = [{
            **current,
            "match_id": "future",
            "owner_date": "2026-07-19",
            "home_score": 4,
            "away_score": 0,
        }]

        model = build_match_goal_margin_models(
            [current], past + future, "2026-07-19",
            minimum_effective_sample=1,
        )["current-1"]

        self.assertEqual(
            model["ordinary_draw"]["historical_probability"], 100.0
        )
        self.assertTrue(model["governance"]["future_matches_excluded"])

    def test_handicap_draw_uses_exact_current_goal_margin(self):
        current = historical_match(
            "2026-07-19", 0, 0, handicap=-1, home_odds=1.7, away_odds=4.8
        )
        current.update({
            "match_id": "current-2",
            "euro_current_draw": 3.5,
            "hi_current_home_odds": 2.1,
            "hi_current_draw_odds": 3.6,
            "hi_current_away_odds": 2.8,
            "asian_current_handicap": "半球/一球",
        })
        history = []
        for index in range(20):
            history.append({
                **current,
                "match_id": f"history-{index}",
                "owner_date": "2026-07-18",
                "home_score": 2 if index < 15 else 1,
                "away_score": 1,
            })

        model = build_match_goal_margin_models(
            [current], history, "2026-07-19",
            minimum_effective_sample=1,
        )["current-2"]

        self.assertEqual(
            model["handicap_draw"]["target_goal_difference"], 1
        )
        self.assertAlmostEqual(
            model["handicap_draw"]["historical_probability"],
            75.0,
            delta=0.1,
        )
        self.assertEqual(
            model["ordinary_draw"]["historical_probability"], 25.0
        )

    def test_goal_margin_model_refuses_small_effective_sample(self):
        current = historical_match("2026-07-19", 0, 0)
        current.update({
            "match_id": "current-3",
            "euro_current_draw": 3.3,
            "asian_current_handicap": "半球",
        })
        history = [{
            **current,
            "owner_date": "2026-07-18",
            "home_score": 1,
            "away_score": 1,
        }]

        model = build_match_goal_margin_models(
            [current], history, "2026-07-19",
            minimum_effective_sample=10,
        )["current-3"]

        self.assertFalse(
            model["ordinary_draw"]["eligible_for_adjustment"]
        )
        self.assertEqual(model["ordinary_draw"]["signal"], "样本不足")


if __name__ == "__main__":
    unittest.main()
