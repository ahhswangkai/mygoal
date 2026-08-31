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
    single_play=None,
    single_probability=None,
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
            "single_play": single_play,
            "single_probability": single_probability,
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

    def test_all_match_single_uses_probability_play_not_value_play(self):
        snapshot = {
            **self.snapshot,
            "matches": [source(
                "216",
                "让负",
                single_play="主胜",
                single_probability=58.4,
                handicap=-1,
            )],
            "daily_summary": {"recommended_combinations": []},
        }
        results = {
            "216": {"status": 2, "home_score": 2, "away_score": 0},
        }

        review = FAEDailyAIReviewEngine().review(snapshot, results)
        row = review["match_results"][0]

        self.assertEqual(row["selection"], "主胜")
        self.assertEqual(row["status"], "hit")
        self.assertEqual(row["value_selection"], "让负")
        self.assertEqual(row["single_probability"], 58.4)

    def test_settles_two_option_coverage_for_handicap_hedge(self):
        match = source(
            "208",
            "让平",
            secondary_play="让负",
            handicap_play="让平",
            handicap=-1,
            hhad=(2.8, 3.5, 2.0),
        )
        match["analysis"]["two_option_recommendation"] = {
            "actionable": True,
            "recommendation_level": "core",
            "daily_rank": 1,
            "selections": ["让平", "让负"],
            "coverage_score": 82.5,
            "rank_score": 78.0,
            "ai_verified": True,
        }
        snapshot = {
            **self.snapshot,
            "matches": [match],
            "daily_summary": {"recommended_combinations": []},
        }
        results = {
            "208": {"status": 2, "home_score": 0, "away_score": 0},
        }

        review = FAEDailyAIReviewEngine().review(snapshot, results)
        row = review["two_option_results"][0]

        self.assertEqual(row["selection"], "让平 / 让负")
        self.assertTrue(row["formal_two_option"])
        self.assertEqual(row["recommendation_level"], "core")
        self.assertEqual(row["daily_rank"], 1)
        self.assertEqual(row["coverage_score"], 82.5)
        self.assertEqual(row["status"], "hit")
        self.assertEqual(row["hit_selection"], "让负")
        self.assertEqual(row["hit_odds"], 2.0)
        self.assertEqual(review["summary"]["two_option"]["handicap"]["hits"], 1)
        self.assertEqual(
            review["summary"]["two_option"]["handicap"]["hit_rate"], 100.0
        )
        overall = review["summary"]["two_option"]["overall"]
        self.assertEqual(review["summary"]["two_option"]["raw_rows"], 2)
        self.assertEqual(review["summary"]["two_option"]["unique_matches"], 1)
        self.assertEqual(overall["total"], 1)
        self.assertEqual(overall["equal_stake"], 2.0)
        self.assertEqual(overall["equal_stake_return"], 2.0)
        self.assertEqual(overall["equal_stake_roi"], 0.0)

    def test_total_goals_are_not_emitted_as_two_option_coverage(self):
        snapshot = {
            **self.snapshot,
            "matches": [source(
                "209", "大球", secondary_play="小球"
            )],
            "daily_summary": {"recommended_combinations": []},
        }
        results = {
            "209": {"status": 2, "home_score": 2, "away_score": 1},
        }

        review = FAEDailyAIReviewEngine().review(snapshot, results)

        self.assertEqual(review["two_option_results"], [])
        self.assertEqual(
            review["summary"]["two_option"]["overall"]["total"], 0
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

    def test_settles_formal_all_play_pool_independently_from_no_bet(self):
        pick = source(
            "205", "平局", euro=(1.80, 3.50, 4.20), no_bet=True,
            single_play="主胜",
        )
        pick["analysis"]["official_bet_recommendation"] = {
            "actionable": True,
            "selection": "主胜",
            "daily_rank": 1,
            "probability": 55,
            "model_probability": 56,
            "market_probability": 53,
            "model_expected_return": 1.008,
            "model_market_edge": 3,
            "value_score": 64,
            "bet_score": 70,
            "market_confidence": 76,
            "rank_score": 61,
            "reason": "通过正式池门槛",
        }
        snapshot = {
            **self.snapshot,
            "matches": [pick],
            "daily_summary": {"recommended_combinations": []},
        }
        results = {
            "205": {"status": 2, "home_score": 2, "away_score": 1},
        }

        review = FAEDailyAIReviewEngine().review(snapshot, results)

        self.assertEqual(review["match_results"][0]["status"], "skipped")
        self.assertEqual(len(review["official_bet_results"]), 1)
        self.assertEqual(
            review["official_bet_results"][0]["status"], "hit"
        )
        self.assertEqual(review["summary"]["official_bets"]["hits"], 1)
        self.assertEqual(review["summary"]["official_bets"]["roi"], 80.0)

    def test_settles_formal_parlay_as_one_ticket(self):
        first = source(
            "205", "主胜", euro=(1.75, 3.60, 4.50), single_play="主胜",
        )
        second = source(
            "206", "客胜", euro=(4.40, 3.50, 1.70), single_play="客胜",
        )
        for rank, (pick, selection, role) in enumerate((
            (first, "主胜", "第1腿"),
            (second, "客胜", "第2腿"),
        ), 1):
            pick["analysis"]["official_bet_recommendation"] = {
                "actionable": True,
                "selection": selection,
                "daily_rank": rank,
                "parlay_role": role,
                "ticket_id": "formal-target-3-205-206",
                "combined_odds": 2.975,
                "strategy_version": "ark-aligned-target-3-parlay-v2",
                "strategy_source": "fae-ark-target-3-parlay",
            }
        snapshot = {
            **self.snapshot,
            "matches": [first, second],
            "daily_summary": {"recommended_combinations": []},
        }
        results = {
            "205": {"status": 2, "home_score": 2, "away_score": 1},
            "206": {"status": 2, "home_score": 0, "away_score": 1},
        }

        review = FAEDailyAIReviewEngine().review(snapshot, results)

        self.assertEqual(len(review["official_bet_results"]), 2)
        self.assertEqual(len(review["official_parlay_results"]), 1)
        ticket = review["official_parlay_results"][0]
        self.assertEqual(ticket["status"], "hit")
        self.assertEqual(ticket["odds"], 2.975)
        self.assertEqual(review["summary"]["official_parlays"]["hits"], 1)
        self.assertEqual(review["summary"]["official_parlays"]["settled"], 1)

    def test_settles_published_high_confidence_single_pool(self):
        pick = source("205", "平局", euro=(1.80, 3.50, 4.20))
        snapshot = {
            **self.snapshot,
            "matches": [pick],
            "daily_summary": {
                "recommended_combinations": [],
                "supervised_shadow": {
                    "high_confidence_single": [{
                        "match_id": "205",
                        "selection": "主胜",
                        "daily_rank": 1,
                        "probability": 62,
                        "model_probability": 64,
                        "market_probability": 58,
                        "model_market_gap_pp": 12,
                        "value_edge": 11.6,
                        "policy_status": "active",
                        "reason": "通过独立验证门禁",
                    }],
                },
            },
        }
        results = {
            "205": {"status": 2, "home_score": 2, "away_score": 1},
        }

        review = FAEDailyAIReviewEngine().review(snapshot, results)

        self.assertEqual(len(review["high_confidence_single_results"]), 1)
        self.assertEqual(
            review["high_confidence_single_results"][0]["status"], "hit"
        )
        self.assertEqual(
            review["summary"]["high_confidence_singles"]["hit_rate"],
            100.0,
        )

    def test_settles_draw_two_three_and_two_leg_tickets_separately(self):
        snapshot = dict(self.snapshot)
        snapshot["daily_summary"] = {
            "recommended_combinations": [],
            "draw_parlay_tickets": {
                "two_three": {
                    "key": "draw-two-three",
                    "title": "平/让平 3场2、3关",
                    "play": "3场2、3关",
                    "structure": "1平+2让平",
                    "picks": [
                        {"match_id": "201", "selection": "平局", "odds": 3.2},
                        {"match_id": "202", "selection": "让平", "odds": 3.4},
                        {"match_id": "214", "selection": "让平", "odds": 3.85},
                    ],
                    "lines": [
                        {
                            "key": "pair-1", "play": "2串1",
                            "pick_refs": [
                                {"match_id": "201", "selection": "平局"},
                                {"match_id": "202", "selection": "让平"},
                            ],
                            "combined_odds": 10.88,
                        },
                        {
                            "key": "pair-2", "play": "2串1",
                            "pick_refs": [
                                {"match_id": "201", "selection": "平局"},
                                {"match_id": "214", "selection": "让平"},
                            ],
                            "combined_odds": 12.32,
                        },
                        {
                            "key": "pair-3", "play": "2串1",
                            "pick_refs": [
                                {"match_id": "202", "selection": "让平"},
                                {"match_id": "214", "selection": "让平"},
                            ],
                            "combined_odds": 13.09,
                        },
                        {
                            "key": "triple-1", "play": "3串1",
                            "pick_refs": [
                                {"match_id": "201", "selection": "平局"},
                                {"match_id": "202", "selection": "让平"},
                                {"match_id": "214", "selection": "让平"},
                            ],
                            "combined_odds": 41.89,
                        },
                    ],
                },
                "two_leg": {
                    "key": "draw-two-leg",
                    "title": "平/让平二串一",
                    "play": "2串1",
                    "structure": "1平+1让平",
                    "picks": [
                        {"match_id": "201", "selection": "平局", "odds": 3.2},
                        {"match_id": "202", "selection": "让平", "odds": 3.4},
                    ],
                    "lines": [{
                        "key": "pair-1", "play": "2串1",
                        "pick_refs": [
                            {"match_id": "201", "selection": "平局"},
                            {"match_id": "202", "selection": "让平"},
                        ],
                        "combined_odds": 10.88,
                    }],
                },
            },
        }

        review = FAEDailyAIReviewEngine().review(snapshot, self.results)
        tickets = {row["key"]: row for row in review["draw_ticket_results"]}

        self.assertEqual(tickets["draw-two-three"]["status"], "hit")
        self.assertEqual(
            tickets["draw-two-three"]["summary"]["winning_lines"], 4
        )
        self.assertEqual(
            tickets["draw-two-three"]["summary"]["stake_units"], 4
        )
        self.assertEqual(tickets["draw-two-leg"]["status"], "hit")
        self.assertEqual(
            tickets["draw-two-leg"]["summary"]["return_units"], 10.88
        )

    def test_records_both_draw_ticket_types_when_candidates_are_insufficient(self):
        review = FAEDailyAIReviewEngine().review(
            self.snapshot, self.results
        )
        tickets = {row["key"]: row for row in review["draw_ticket_results"]}

        self.assertEqual(set(tickets), {"draw-two-three", "draw-two-leg"})
        self.assertEqual(
            tickets["draw-two-three"]["status"], "not_generated"
        )
        self.assertEqual(tickets["draw-two-leg"]["status"], "not_generated")


if __name__ == "__main__":
    unittest.main()
