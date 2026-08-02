import unittest

from football_ai.daily_review import (
    FAEDailyAIReviewEngine,
    aggregate_daily_ai_reviews,
)


def source(
    match_id,
    selection,
    *,
    euro=(2.0, 3.2, 4.0),
    handicap=-1,
    hhad=(2.5, 3.4, 2.1),
    rating=4,
    guarded=False,
    no_bet=False,
    handicap_play=None,
    secondary_play=None,
    historical_calibration=None,
):
    return {
        "match_id": str(match_id),
        "match_number": f"周六{match_id}",
        "league": "测试联赛",
        "home_team": f"主队{match_id}",
        "away_team": f"客队{match_id}",
        "analysis": {
            "primary_play": selection,
            "secondary_play": secondary_play,
            "handicap_play": handicap_play,
            "model_primary_play": "让平" if guarded else selection,
            "rating": rating,
            "no_bet": no_bet,
            "no_bet_reasons": ["盘口可信度偏低"] if no_bet else [],
            "consistency_guard": {
                "triggered": guarded,
                "model_selection": "让平" if guarded else selection,
                "effective_selection": selection,
            },
            "historical_calibration": historical_calibration or {},
        },
        "input_snapshot": {
            "euro": {"current": list(euro)},
            "sporttery_handicap": {
                "value": handicap,
                "current": list(hhad),
            },
            "total": {"current": [1.9, 2.5, 1.9]},
        },
    }


class DailyAIReviewTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "owner_date": "2026-07-18",
            "run_id": "run-1",
            "engine_version": "2.0.0",
            "model": "ark-code-latest",
            "matches": [
                source("201", "平局"),
                source("202", "让平"),
                source(
                    "214", "让负", handicap=-2,
                    hhad=(2.54, 3.85, 2.10), guarded=True,
                ),
                source("215", "主胜"),
            ],
            "daily_summary": {
                "recommended_combinations": [
                    {
                        "play": "2串1",
                        "picks": [
                            {"match_id": "201", "selection": "平局"},
                            {"match_id": "202", "selection": "让平"},
                        ],
                        "reason": "两个命中样本",
                    },
                    {
                        "play": "3串1",
                        "picks": [
                            {"match_id": "201", "selection": "平局"},
                            {"match_id": "202", "selection": "让平"},
                            {"match_id": "214", "selection": "让负"},
                        ],
                        "reason": "包含一个未中样本",
                    },
                ]
            },
        }
        self.results = {
            "201": {"status": 2, "home_score": 1, "away_score": 1},
            "202": {"status": 2, "home_score": 2, "away_score": 1},
            "214": {"status": 2, "home_score": 2, "away_score": 0},
            "215": {"status": 0},
        }

    def test_settles_ai_singles_combos_odds_and_guard_conflicts(self):
        review = FAEDailyAIReviewEngine().review(
            self.snapshot, self.results
        )

        rows = {
            item["match_id"]: item for item in review["match_results"]
        }
        self.assertEqual(rows["201"]["status"], "hit")
        self.assertEqual(rows["201"]["return"], 3.2)
        self.assertEqual(rows["202"]["selection_text"], "让平(-1)")
        self.assertEqual(rows["202"]["status"], "hit")
        self.assertEqual(rows["214"]["status"], "miss")
        self.assertTrue(rows["214"]["guardrail_triggered"])
        self.assertEqual(rows["214"]["model_selection"], "让平")
        self.assertEqual(rows["215"]["status"], "pending")

        self.assertEqual(review["combo_results"][0]["status"], "hit")
        self.assertEqual(
            review["combo_results"][0]["combined_odds"], 10.88
        )
        self.assertEqual(review["combo_results"][1]["status"], "miss")
        self.assertEqual(review["summary"]["guardrail_conflicts"], 1)
        self.assertFalse(review["completed"])

    def test_aggregates_primary_ai_review_statistics(self):
        review = FAEDailyAIReviewEngine().review(
            self.snapshot, self.results
        )

        stats = aggregate_daily_ai_reviews(
            [review], {"平局": {"weight": 1.0}}
        )

        self.assertEqual(stats["primary_source"], "fae-daily-ai")
        self.assertEqual(stats["singles"]["settled"], 3)
        self.assertEqual(stats["singles"]["hits"], 2)
        self.assertEqual(stats["by_play"]["2串1"]["hits"], 1)
        self.assertEqual(stats["guardrail_conflicts"], 1)

    def test_settles_two_option_coverage_for_handicap_hedge(self):
        snapshot = {
            **self.snapshot,
            "matches": [
                source(
                    "208",
                    "让平",
                    secondary_play="让负",
                    handicap_play="让平",
                    handicap=-1,
                    hhad=(2.8, 3.5, 2.0),
                ),
            ],
            "daily_summary": {"recommended_combinations": []},
        }
        results = {
            "208": {"status": 2, "home_score": 0, "away_score": 0},
        }

        review = FAEDailyAIReviewEngine().review(snapshot, results)
        row = review["two_option_results"][0]

        self.assertEqual(row["selection"], "让平 / 让负")
        self.assertEqual(row["status"], "hit")
        self.assertEqual(row["hit_selection"], "让负")
        self.assertEqual(review["summary"]["two_option"]["handicap"]["hits"], 1)
        self.assertEqual(
            review["summary"]["two_option"]["handicap"]["hit_rate"], 100.0
        )

    def test_aggregates_history_calibration_brier_score(self):
        reviews = [{
            "owner_date": f"2026-07-{10 + index:02d}",
            "summary": {"singles": {"settled": 1}},
            "match_results": [{
                "match_id": str(index),
                "selection": "平局",
                "status": "hit" if index % 3 == 0 else "miss",
                "historical_calibration": {
                    "applied": True,
                    "core_probability": 45,
                    "calibrated_probability": 34,
                },
            }],
            "handicap_results": [],
            "combo_results": [],
            "conflicts": [],
        } for index in range(30)]

        stats = aggregate_daily_ai_reviews(reviews)
        calibration = stats["history_calibration"]["ordinary_draw"]

        self.assertEqual(calibration["sample"], 30)
        self.assertEqual(calibration["review_days"], 30)
        self.assertGreater(calibration["brier_improvement"], 0)
        self.assertTrue(calibration["validated"])

    def test_no_bet_match_is_observed_but_excluded_from_roi(self):
        snapshot = {
            **self.snapshot,
            "matches": [source("203", "客胜", no_bet=True)],
            "daily_summary": {"recommended_combinations": []},
        }
        results = {
            "203": {"status": 2, "home_score": 0, "away_score": 1},
        }

        review = FAEDailyAIReviewEngine().review(snapshot, results)
        row = review["match_results"][0]

        self.assertEqual(row["status"], "skipped")
        self.assertEqual(row["observation_status"], "hit")
        self.assertTrue(row["no_bet"])
        self.assertEqual(review["summary"]["singles"]["settled"], 0)

    def test_settles_handicap_reference_separately_from_primary_pick(self):
        snapshot = {
            **self.snapshot,
            "matches": [
                source(
                    "205", "主胜", handicap=-1,
                    hhad=(2.55, 3.15, 2.25), handicap_play="让胜",
                ),
                source(
                    "207", "主胜", handicap=-1,
                    hhad=(2.90, 3.22, 2.11), handicap_play="让胜",
                ),
            ],
            "daily_summary": {"recommended_combinations": []},
        }
        results = {
            "205": {"status": 2, "home_score": 1, "away_score": 0},
            "207": {"status": 2, "home_score": 4, "away_score": 0},
        }

        review = FAEDailyAIReviewEngine().review(snapshot, results)
        primary = {
            item["match_id"]: item for item in review["match_results"]
        }
        handicap = {
            item["match_id"]: item for item in review["handicap_results"]
        }

        self.assertEqual(primary["205"]["status"], "hit")
        self.assertEqual(handicap["205"]["selection_text"], "让胜(-1)")
        self.assertEqual(handicap["205"]["status"], "miss")
        self.assertEqual(handicap["207"]["status"], "hit")
        self.assertEqual(review["summary"]["handicap"]["settled"], 2)
        self.assertEqual(review["summary"]["handicap"]["hits"], 1)
        self.assertEqual(review["summary"]["handicap"]["hit_rate"], 50.0)

    def test_settles_draw_radar_core_and_watch_rows_separately(self):
        ordinary = source("202", "主胜")
        ordinary["analysis"]["draw_radar"] = {
            "ordinary_draw": {
                "tier": "watch",
                "rating": 3.5,
                "score": 64,
                "probability": 27.2,
                "market_probability": 26.0,
                "odds_value": -4.1,
                "effective_sample": 62,
                "reason": "副选平局，仅列观察。",
            },
            "handicap_draw": {"tier": "exclude"},
        }
        handicap = source("205", "主胜", handicap=-1)
        handicap["analysis"]["draw_radar"] = {
            "ordinary_draw": {"tier": "exclude"},
            "handicap_draw": {
                "tier": "core",
                "rating": 4,
                "score": 74,
                "probability": 28.4,
                "market_probability": 26.0,
                "odds_value": 4.5,
                "effective_sample": 70,
                "reason": "让平达到核心门槛。",
            },
        }
        snapshot = {
            **self.snapshot,
            "matches": [ordinary, handicap],
            "daily_summary": {"recommended_combinations": []},
        }
        results = {
            "202": {"status": 2, "home_score": 0, "away_score": 0},
            "205": {"status": 2, "home_score": 1, "away_score": 0},
        }

        review = FAEDailyAIReviewEngine().review(snapshot, results)
        rows = {
            (item["match_id"], item["selection"]): item
            for item in review["draw_radar_results"]
        }

        self.assertEqual(rows[("202", "平局")]["status"], "hit")
        self.assertEqual(rows[("202", "平局")]["tier"], "watch")
        self.assertFalse(rows[("202", "平局")]["official_bet"])
        self.assertEqual(rows[("205", "让平")]["status"], "hit")
        self.assertTrue(rows[("205", "让平")]["official_bet"])
        self.assertEqual(
            review["summary"]["draw_radar"]["core"]["hits"], 1
        )
        self.assertEqual(
            review["summary"]["draw_radar"]["watch"]["hits"], 1
        )


if __name__ == "__main__":
    unittest.main()
