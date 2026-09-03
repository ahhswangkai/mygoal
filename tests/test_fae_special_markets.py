import unittest

from football_ai.special_markets import (
    build_special_market_analysis,
    parse_calculator_payload,
    settle_special_markets,
)


def calculator_payload():
    return {
        "success": True,
        "value": {
            "matchInfoList": [{
                "subMatchList": [{
                    "matchId": "calc-1",
                    "matchNumStr": "周二003",
                    "ttg": {
                        **{f"s{i}": value for i, value in enumerate(
                            (18, 7, 4, 3.5, 5, 8, 13, 20)
                        )},
                        **{f"s{i}f": -1 if i == 2 else 0 for i in range(8)},
                        "updateDate": "2026-09-01",
                        "updateTime": "12:30:00",
                    },
                    "hafu": {
                        "hh": 2.5, "hd": 15, "ha": 35,
                        "dh": 4.5, "dd": 6, "da": 10,
                        "ah": 24, "ad": 14, "aa": 6.5,
                        "hhf": 1,
                        "updateDate": "2026-09-01",
                        "updateTime": "12:31:00",
                    },
                }]
            }]
        },
    }


class SpecialMarketTests(unittest.TestCase):
    def test_parses_calculator_flags_and_update_time(self):
        snapshots = parse_calculator_payload(calculator_payload())
        row = snapshots["周二003"]

        self.assertEqual(row["calculator_match_id"], "calc-1")
        self.assertEqual(row["total_goals"]["odds"]["7+"], 20.0)
        self.assertEqual(row["total_goals"]["flags"]["2"], -1)
        self.assertEqual(
            row["half_full"]["updated_at"], "2026-09-01 12:31:00"
        )
        self.assertEqual(row["half_full"]["flags"]["胜胜"], 1)

    def test_builds_two_ranked_options_for_each_market(self):
        snapshot = parse_calculator_payload(calculator_payload())["周二003"]
        result = build_special_market_analysis(snapshot, {
            "match_number": "周二003",
            "euro": {"current": [1.75, 3.5, 4.4]},
            "total": {"current": [1.82, 2.75, 2.02]},
        })

        self.assertTrue(result["total_goals"]["available"])
        self.assertTrue(result["half_full"]["available"])
        self.assertEqual(len(result["total_goals"]["options"]), 8)
        self.assertEqual(len(result["half_full"]["options"]), 9)
        self.assertGreaterEqual(
            result["total_goals"]["primary"]["model_probability"],
            result["total_goals"]["secondary"]["model_probability"],
        )

    def test_total_goals_low_regime_uses_lower_tail_as_secondary(self):
        snapshot = parse_calculator_payload(calculator_payload())["周二003"]
        snapshot["total_goals"]["odds"] = {
            "0": 12.0, "1": 4.35, "2": 3.0, "3": 3.9,
            "4": 7.0, "5": 14.0, "6": 25.0, "7+": 39.0,
        }
        result = build_special_market_analysis(snapshot, {
            "euro": {"current": [1.6, 3.4, 4.75]},
            "asian": {"current": [0.95, "半球/一球", 0.86]},
            "total": {"current": [0.82, 2.25, 1.0]},
        })["total_goals"]

        self.assertEqual(result["regime"], "low")
        self.assertEqual(result["primary"]["selection"], "2")
        self.assertEqual(result["secondary"]["selection"], "1")
        self.assertTrue(result["actionable"])
        self.assertFalse(result["baseline_only"])

    def test_total_goals_high_regime_uses_upper_tail_as_secondary(self):
        snapshot = parse_calculator_payload(calculator_payload())["周二003"]
        result = build_special_market_analysis(snapshot, {
            "euro": {"current": [1.75, 3.5, 4.4]},
            "asian": {"current": [0.9, "一球", 0.95]},
            "total": {"current": [0.82, 3.25, 1.02]},
        })["total_goals"]

        self.assertEqual(result["regime"], "high")
        self.assertEqual(result["primary"]["selection"], "3")
        self.assertEqual(result["secondary"]["selection"], "4")

    def test_total_goals_normal_two_three_pair_is_market_baseline(self):
        snapshot = parse_calculator_payload(calculator_payload())["周二003"]
        result = build_special_market_analysis(snapshot, {
            "euro": {"current": [1.75, 3.5, 4.4]},
            "asian": {"current": [0.85, "半球", 1.0]},
            "total": {"current": [0.92, 2.5, 0.93]},
        })["total_goals"]

        self.assertEqual(result["regime"], "standard")
        self.assertTrue(result["baseline_only"])
        self.assertFalse(result["actionable"])
        self.assertEqual(result["recommendation_status"], "市场基线")

    def test_total_goals_requires_complete_asian_total_market(self):
        snapshot = parse_calculator_payload(calculator_payload())["周二003"]
        result = build_special_market_analysis(snapshot, {
            "euro": {"current": [1.75, 3.5, 4.4]},
            "total": {"current": [None, None, None]},
        })["total_goals"]

        self.assertFalse(result["available"])
        self.assertFalse(result["data_complete"])
        self.assertTrue(result["calculator_available"])
        self.assertFalse(result["actionable"])
        self.assertEqual(result["recommendation_status"], "数据不足")
        self.assertNotIn("primary", result)
        self.assertNotIn("secondary", result)
        self.assertIn("亚洲大小球", result["reason"])

    def test_total_goals_requires_both_asian_total_water_prices(self):
        snapshot = parse_calculator_payload(calculator_payload())["周二003"]
        result = build_special_market_analysis(snapshot, {
            "total": {"current": [0.88, 2.75, None]},
        })["total_goals"]

        self.assertFalse(result["available"])
        self.assertFalse(result["actionable"])
        self.assertEqual(result["options"], [])

    def test_half_full_strong_direction_keeps_both_paths_aligned(self):
        snapshot = parse_calculator_payload(calculator_payload())["周二003"]
        result = build_special_market_analysis(snapshot, {
            "euro": {"current": [1.5, 3.8, 5.5]},
            "asian": {"current": [0.82, "半球/一球", 1.04]},
            "total": {"current": [0.9, 2.75, 0.95]},
        })["half_full"]

        self.assertEqual(result["direction_profile"]["tier"], "strong")
        self.assertTrue(result["primary"]["selection"].endswith("胜"))
        self.assertTrue(result["secondary"]["selection"].endswith("胜"))
        self.assertTrue(result["actionable"])

    def test_half_full_balanced_low_total_promotes_draw_draw(self):
        snapshot = parse_calculator_payload(calculator_payload())["周二003"]
        result = build_special_market_analysis(snapshot, {
            "euro": {"current": [2.45, 3.16, 2.47]},
            "asian": {"current": [0.89, "平手", 0.96]},
            "total": {"current": [1.01, 2.5, 0.84]},
        })["half_full"]

        self.assertEqual(result["direction_profile"]["tier"], "balanced")
        self.assertEqual(result["primary"]["selection"], "平平")
        self.assertFalse(result["actionable"])
        self.assertEqual(result["recommendation_status"], "均势观察")

    def test_settles_total_goals_and_half_full_from_saved_snapshot(self):
        snapshot = parse_calculator_payload(calculator_payload())["周二003"]
        special = build_special_market_analysis(snapshot, {
            "match_number": "周二003",
            "euro": {"current": [1.75, 3.5, 4.4]},
            "total": {"current": [1.82, 2.75, 2.02]},
        })
        special["total_goals"]["primary"] = {
            "selection": "3", "odds": 3.5, "model_probability": 26,
        }
        special["total_goals"]["secondary"] = {
            "selection": "2", "odds": 4, "model_probability": 23,
        }
        special["half_full"]["primary"] = {
            "selection": "平胜", "odds": 4.5, "model_probability": 31,
        }
        special["half_full"]["secondary"] = {
            "selection": "胜胜", "odds": 2.5, "model_probability": 28,
        }
        source = {
            "match_id": "1",
            "match_number": "周二003",
            "analysis": {"special_markets": special},
        }
        rows = settle_special_markets(source, {
            "status": 2,
            "home_score": 2,
            "away_score": 1,
            "home_half_score": 0,
            "away_half_score": 0,
        })
        by_key = {row["market_key"]: row for row in rows}

        self.assertEqual(by_key["total_goals"]["actual_selection"], "3")
        self.assertEqual(by_key["total_goals"]["primary_status"], "hit")
        self.assertEqual(by_key["half_full"]["actual_selection"], "平胜")
        self.assertEqual(by_key["half_full"]["coverage_status"], "hit")
        self.assertEqual(by_key["half_full"]["half_score"], "0:0")


if __name__ == "__main__":
    unittest.main()
