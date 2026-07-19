import json
import unittest

from football_ai.daily_analysis import (
    FAEDailyAIAnalyzer,
    build_daily_match_input,
)


class FakeDailyArkClient:
    configured = True
    model = "ark-test"

    def generate(self, prompt):
        self.prompt = prompt
        return json.dumps({
            "daily_summary": {
                "core_conclusion": "让平与普通平局均有候选，异常跳盘场次应回避。",
                "warnings": ["2号比赛大小球跳盘异常"],
                "pools": {
                    "handicap_draw": [{
                        "match_id": "1",
                        "rating": 4.5,
                        "reason": "主胜增强但亚洲盘未充分升深",
                    }],
                    "draw": [{
                        "match_id": "2",
                        "rating": 4,
                        "reason": "欧亚方向背离",
                    }],
                    "away_small_win": [],
                    "avoid": [{
                        "match_id": "2",
                        "reason": "大小球跳档异常",
                    }],
                },
                "recommended_combinations": [{
                    "play": "2串1",
                    "picks": [
                        {"match_id": "1", "selection": "让平"},
                        {"match_id": "2", "selection": "平局"},
                    ],
                    "reason": "混合分散精确比分风险",
                }],
            },
            "matches": [{
                "match_id": "1",
                "direction": "主胜",
                "primary_play": "让平",
                "rating": 4.5,
                "verdict": "主胜方向清晰，但半球未升到一球，优先考虑一球胜。",
                "market_analysis": {
                    "euro": "主胜下降",
                    "asian": "盘口未真正升深",
                    "sporttery": "主让1球让平可选",
                    "total": "2.5稳定",
                    "consistency": "主胜一致但大胜支持不足",
                },
                "evidence": ["主胜1.80降至1.70"],
                "risks": ["缺少首发"],
                "score_candidates": ["1:0", "2:1"],
            }],
        }, ensure_ascii=False), {
            "response_id": "daily-test",
            "usage": {"total_tokens": 500},
        }


def match(match_id, total_initial="2.5", total_current="2.5"):
    return {
        "match_id": match_id,
        "match_number": f"周六20{match_id}",
        "league": "测试联赛",
        "match_time": "2026-07-18 20:00",
        "home_team": f"主队{match_id}",
        "away_team": f"客队{match_id}",
        "euro_initial_win": "1.80",
        "euro_initial_draw": "3.40",
        "euro_initial_lose": "4.20",
        "euro_current_win": "1.70",
        "euro_current_draw": "3.50",
        "euro_current_lose": "4.60",
        "asian_initial_home_odds": "0.88",
        "asian_initial_handicap": "半球",
        "asian_initial_away_odds": "0.96",
        "asian_current_home_odds": "0.82",
        "asian_current_handicap": "半球降",
        "asian_current_away_odds": "1.02",
        "hi_handicap_value": "-1",
        "hi_current_home_odds": "2.30",
        "hi_current_draw_odds": "3.50",
        "hi_current_away_odds": "2.40",
        "ou_initial_total": total_initial,
        "ou_current_total": total_current,
    }


class DailyAnalysisTests(unittest.TestCase):
    def test_builds_clean_five_market_input_and_flags_total_jump(self):
        source = match("2", "2.5", "3.5")
        source["hi_handicap_value"] = None
        source["handicap"] = "-1"
        row = build_daily_match_input(source)

        self.assertEqual(row["asian"]["current"][1], "半球")
        self.assertTrue(any("大小球盘口" in item for item in row["data_warnings"]))
        self.assertEqual(row["sporttery_handicap"]["value"], -1)
        self.assertNotIn("竞彩让球数缺失", row["data_warnings"])

    def test_analyzes_whole_batch_and_falls_back_for_missing_match(self):
        client = FakeDailyArkClient()
        analyzer = FAEDailyAIAnalyzer(client)
        rows = [
            build_daily_match_input(match("1")),
            build_daily_match_input(match("2", "2.5", "3.5")),
        ]

        review_memory = {
            "memory_hash": "memory-1",
            "review_days": 1,
            "recent_observations": [{
                "date": "2026-07-17",
                "what_failed": ["热门方向没有真实升深"],
            }],
            "validated_patterns": [],
        }
        result = analyzer.analyze(
            "2026-07-18", rows, review_memory=review_memory
        )

        self.assertEqual(result["match_count"], 2)
        self.assertEqual(result["batch_count"], 1)
        self.assertEqual(
            result["matches"][0]["analysis"]["primary_play"], "让平"
        )
        self.assertEqual(
            result["matches"][1]["analysis_source"], "fae-core-fallback"
        )
        self.assertEqual(
            result["daily_summary"]["recommended_combinations"], []
        )
        self.assertIn("五项检查", client.prompt)
        self.assertIn("不输出隐藏思维链", client.prompt)
        self.assertIn("热门方向没有真实升深", client.prompt)
        self.assertIn("禁止使用历史0%命中区间", client.prompt)
        self.assertEqual(result["review_memory"]["memory_hash"], "memory-1")

    def test_input_hash_is_order_independent(self):
        analyzer = FAEDailyAIAnalyzer(FakeDailyArkClient())
        rows = [
            build_daily_match_input(match("1")),
            build_daily_match_input(match("2")),
        ]

        self.assertEqual(
            analyzer.input_hash("2026-07-18", rows),
            analyzer.input_hash("2026-07-18", reversed(rows)),
        )

    def test_input_hash_changes_when_review_memory_changes(self):
        analyzer = FAEDailyAIAnalyzer(FakeDailyArkClient())
        rows = [build_daily_match_input(match("1"))]

        without_memory = analyzer.input_hash("2026-07-18", rows)
        with_memory = analyzer.input_hash(
            "2026-07-18",
            rows,
            review_memory={
                "memory_hash": "memory-1",
                "recent_observations": [{"date": "2026-07-17"}],
            },
        )

        self.assertNotEqual(without_memory, with_memory)

    def test_parser_repairs_only_trailing_commas(self):
        parsed = FAEDailyAIAnalyzer._extract_json(
            '```json\n{"match":{"match_id":"1",},}\n```'
        )

        self.assertEqual(parsed["match"]["match_id"], "1")

    def test_parser_repairs_model_missing_comma(self):
        parsed = FAEDailyAIAnalyzer._extract_json(
            '{"match":{"match_id":"1" "rating":4}}'
        )

        self.assertEqual(parsed["match"]["rating"], 4)

    def test_strong_handicap_conflict_uses_auditable_guardrail(self):
        source = build_daily_match_input(match("214"))
        source["fae_core"]["probabilities"] = {
            "hhad": {"win": 19, "draw": 17, "lose": 64}
        }
        generated = {
            "primary_play": "让平",
            "rating": 4,
            "risks": [],
        }

        result = FAEDailyAIAnalyzer._normalize_match(source, generated)
        analysis = result["analysis"]

        self.assertEqual(analysis["model_primary_play"], "让平")
        self.assertEqual(analysis["primary_play"], "让负")
        self.assertTrue(analysis["consistency_guard"]["triggered"])
        self.assertEqual(
            analysis["consistency_guard"]["probability_gap"], 47
        )

    def test_medium_handicap_conflict_with_large_return_gap_is_guarded(self):
        source = build_daily_match_input(match("201"))
        source["fae_core"]["probabilities"] = {
            "hhad": {"win": 52, "draw": 24, "lose": 24}
        }
        source["sporttery_handicap"]["current"] = [2.13, 3.05, 3.01]

        result = FAEDailyAIAnalyzer._normalize_match(source, {
            "primary_play": "让负",
            "secondary_play": "客胜",
            "rating": 3.5,
        })
        analysis = result["analysis"]

        self.assertEqual(analysis["model_primary_play"], "让负")
        self.assertEqual(analysis["primary_play"], "让胜")
        self.assertEqual(analysis["secondary_play"], "让平")
        self.assertTrue(analysis["consistency_guard"]["triggered"])

    def test_summary_guard_removes_conflicting_letdraw_pick(self):
        matches = [{
            "match_id": "214",
            "match_number": "周六214",
            "analysis": {
                "primary_play": "让负",
                "consistency_guard": {
                    "triggered": True,
                    "effective_selection": "让负",
                },
            },
        }]
        summary = {
            "warnings": [],
            "pools": {
                "handicap_draw": [{"match_id": "214"}],
                "draw": [],
            },
            "recommended_combinations": [{
                "play": "2串1",
                "picks": [
                    {"match_id": "214", "selection": "让平"},
                    {"match_id": "2", "selection": "平局"},
                ],
            }],
        }

        guarded = FAEDailyAIAnalyzer._apply_summary_guard(summary, matches)

        self.assertEqual(guarded["pools"]["handicap_draw"], [])
        self.assertEqual(guarded["recommended_combinations"], [])
        self.assertIn("周六214", guarded["warnings"][0])

    def test_handicap_reference_is_compatible_with_away_win(self):
        source = {
            "sporttery_handicap": {
                "value": 1,
                "current": [2.13, 3.05, 3.01],
            },
            "fae_core": {
                "probabilities": {
                    "hhad": {"win": 52, "draw": 24, "lose": 24},
                },
            },
        }

        selection = FAEDailyAIAnalyzer._handicap_play(source, "客胜")

        self.assertEqual(selection, "让平")

    def test_handicap_reference_excludes_letlose_for_home_win_minus_one(self):
        source = {
            "sporttery_handicap": {
                "value": -1,
                "current": [2.6, 3.3, 2.1],
            },
            "fae_core": {
                "probabilities": {
                    "hhad": {"win": 25, "draw": 24, "lose": 51},
                },
            },
        }

        selection = FAEDailyAIAnalyzer._handicap_play(source, "主胜")

        self.assertEqual(selection, "让胜")

    def test_secondary_play_stays_in_same_market_as_primary(self):
        source = {
            "fae_core": {
                "probabilities": {
                    "home_win": 18,
                    "draw": 22,
                    "away_win": 60,
                    "hhad": {"win": 54, "draw": 23, "lose": 23},
                },
            },
        }

        selection = FAEDailyAIAnalyzer._secondary_play(
            source,
            "客胜",
            "让胜",
        )

        self.assertEqual(selection, "平局")

    def test_mixed_combinations_require_both_picks_to_reach_threshold(self):
        summary = {
            "pools": {
                "handicap_draw": [{"match_id": "203", "rating": 3}],
                "draw": [{"match_id": "104", "rating": 3}],
                "avoid": [],
            },
            "recommended_combinations": [{
                "play": "2串1",
                "picks": [
                    {"match_id": "203", "selection": "让平"},
                    {"match_id": "104", "selection": "平局"},
                ],
            }],
        }

        combinations = FAEDailyAIAnalyzer._ensure_mixed_combinations(summary)

        self.assertEqual(combinations, [])

    def test_mixed_combinations_exclude_avoid_pool_matches(self):
        summary = {
            "pools": {
                "handicap_draw": [{"match_id": "203", "rating": 4}],
                "draw": [{"match_id": "104", "rating": 4}],
                "avoid": [{"match_id": "203", "rating": 4}],
            },
            "recommended_combinations": [],
        }

        combinations = FAEDailyAIAnalyzer._ensure_mixed_combinations(summary)

        self.assertEqual(combinations, [])

    def test_no_bet_matches_are_removed_from_pools_and_combinations(self):
        summary = {
            "warnings": [],
            "pools": {
                "handicap_draw": [{"match_id": "203", "rating": 4}],
                "draw": [{"match_id": "104", "rating": 4}],
                "avoid": [],
            },
            "recommended_combinations": [{
                "play": "2串1",
                "picks": [
                    {"match_id": "203", "selection": "让平"},
                    {"match_id": "104", "selection": "平局"},
                ],
            }],
        }
        matches = [{
            "match_id": "203",
            "match_number": "周日203",
            "analysis": {
                "no_bet": True,
                "rating": 2.5,
                "no_bet_reasons": ["欧亚背离", "盘口可信度偏低"],
            },
        }]

        guarded = FAEDailyAIAnalyzer._apply_no_bet_summary(
            summary, matches
        )

        self.assertEqual(guarded["pools"]["handicap_draw"], [])
        self.assertEqual(guarded["recommended_combinations"], [])
        self.assertEqual(
            guarded["pools"]["avoid"][0]["match_id"], "203"
        )
        self.assertIn("不下注", guarded["pools"]["avoid"][0]["reason"])

    def test_value_guard_prefers_materially_stronger_bettable_play(self):
        source = {
            "fae_core": {
                "recommendation": {
                    "category_scores": [
                        {
                            "label": "主胜",
                            "bet_score": 52,
                            "value_score": 48,
                            "no_bet": True,
                        },
                        {
                            "label": "让平",
                            "bet_score": 78,
                            "value_score": 84,
                            "no_bet": False,
                        },
                    ],
                },
            },
        }

        selection, guard = FAEDailyAIAnalyzer._value_selection_guard(
            source, "主胜"
        )

        self.assertEqual(selection, "让平")
        self.assertTrue(guard["triggered"])

    def test_value_guard_keeps_best_direction_when_every_play_is_no_bet(self):
        source = {
            "fae_core": {
                "recommendation": {
                    "category_scores": [
                        {
                            "label": "客胜",
                            "bet_score": 58,
                            "prediction_score": 72,
                            "no_bet": True,
                        },
                        {
                            "label": "让平",
                            "bet_score": 49,
                            "prediction_score": 55,
                            "no_bet": True,
                        },
                    ],
                },
            },
        }

        selection, guard = FAEDailyAIAnalyzer._value_selection_guard(
            source, "观望"
        )

        self.assertEqual(selection, "客胜")
        self.assertTrue(guard["triggered"])
        self.assertTrue(guard["no_bet_only"])

    def test_summary_prose_uses_match_number_instead_of_raw_id(self):
        summary = {
            "core_conclusion": "1373171客胜方向较强",
            "warnings": ["1373171存在退盘风险"],
            "pools": {
                "avoid": [{
                    "match_id": "1373171",
                    "reason": "1373171市场存在矛盾",
                }]
            },
            "recommended_combinations": [{
                "play": "2串1",
                "picks": [{"match_id": "1373171", "selection": "平局"}],
                "reason": "1373171不适合作胆",
            }],
        }
        matches = [{
            "match_id": "1373171",
            "match_number": "周日201",
        }]

        humanized = FAEDailyAIAnalyzer._humanize_summary_match_ids(
            summary, matches
        )

        self.assertEqual(
            humanized["core_conclusion"], "周日201客胜方向较强"
        )
        self.assertIn("周日201", humanized["warnings"][0])
        self.assertEqual(
            humanized["pools"]["avoid"][0]["match_id"], "1373171"
        )
        self.assertIn(
            "周日201",
            humanized["recommended_combinations"][0]["reason"],
        )

    def test_separates_handicap_lose_from_away_small_win_pool(self):
        summary = {
            "pools": {
                "away_small_win": [
                    {
                        "match_id": "1362707",
                        "reason": "竞彩主队让2球，让负处于低位",
                    },
                    {
                        "match_id": "1362705",
                        "reason": "客胜方向明确，预计客队一球小胜",
                    },
                ]
            }
        }
        matches = [
            {
                "match_id": "1362707",
                "analysis": {
                    "direction": "主胜",
                    "primary_play": "主胜",
                },
                "input_snapshot": {
                    "fae_core": {
                        "probabilities": {
                            "home_win": 79,
                            "away_win": 7,
                            "hhad": {
                                "win": 18,
                                "draw": 17,
                                "lose": 64,
                            },
                        }
                    }
                },
            },
            {
                "match_id": "1362705",
                "analysis": {
                    "direction": "客胜",
                    "primary_play": "客胜",
                },
                "input_snapshot": {
                    "fae_core": {
                        "probabilities": {
                            "home_win": 22,
                            "away_win": 56,
                            "hhad": {
                                "win": 54,
                                "draw": 27,
                                "lose": 19,
                            },
                        }
                    }
                },
            },
        ]

        normalized = FAEDailyAIAnalyzer.normalize_summary_pool_semantics(
            summary, matches
        )
        pools = normalized["pools"]

        self.assertEqual(
            [item["match_id"] for item in pools["handicap_lose"]],
            ["1362707"],
        )
        self.assertEqual(
            [item["match_id"] for item in pools["away_small_win"]],
            ["1362705"],
        )

    def test_unvalidated_memory_cannot_create_absolute_exclusion(self):
        summary = {
            "core_conclusion": (
                "周日201让胜可选，但受昨日让平0/3失败模式影响，"
                "让平落入高危区间，不建议纳入让平组合。"
                "严禁纳入让平玩法，均落入历史0%命中区间。"
            ),
            "warnings": [
                "昨日让平0/3全败，本日让平全部排除",
                "该组合历史失误率高，本日同类场次需降权",
                "周日204大小球跳档异常",
            ],
            "pools": {
                "avoid": [{
                    "match_id": "201",
                    "reason": "让平3.15落入历史0%命中高危区间，严禁切入",
                }]
            },
        }
        memory = {
            "validated_pattern_count": 0,
            "recent_observations": [{"date": "2026-07-18"}],
        }

        normalized = (
            FAEDailyAIAnalyzer.normalize_summary_memory_governance(
                summary, memory
            )
        )

        self.assertNotIn("严禁纳入", normalized["core_conclusion"])
        self.assertNotIn("历史0%命中区间", normalized["core_conclusion"])
        self.assertIn("单日0%或100%", normalized["core_conclusion"])
        self.assertFalse(any(
            "全部排除" in item for item in normalized["warnings"]
        ))
        self.assertFalse(any(
            "历史0%" in item for item in normalized["warnings"]
        ))
        self.assertFalse(any(
            "需降权" in item for item in normalized["warnings"]
        ))
        self.assertIn("周日204大小球跳档异常", normalized["warnings"])
        self.assertNotIn(
            "历史0%",
            normalized["pools"]["avoid"][0]["reason"],
        )

    def test_unvalidated_memory_is_removed_from_match_narrative(self):
        matches = [{
            "match_id": "201",
            "analysis": {
                "verdict": "当日盘口支持客队。昨日K1命中经验形成共振。",
                "evidence": ["历史复盘显示同类模式有效"],
                "risks": ["历史上频繁反转", "缺少首发数据"],
            },
        }]

        normalized = FAEDailyAIAnalyzer.normalize_match_memory_governance(
            matches,
            {"validated_pattern_count": 0},
        )
        analysis = normalized[0]["analysis"]

        self.assertEqual(analysis["verdict"], "当日盘口支持客队。")
        self.assertEqual(analysis["evidence"], [])
        self.assertIn("缺少首发数据", analysis["risks"])
        self.assertTrue(any(
            "不直接改变本场推荐" in item for item in analysis["risks"]
        ))

    def test_probability_language_is_idempotent(self):
        self.assertEqual(
            FAEDailyAIAnalyzer._label_probability_language(
                "FAE平局概率22%，主胜概率约62%"
            ),
            "FAE估算平局概率22%（未校准），"
            "FAE估算主胜概率约62%（未校准）",
        )

    def test_rating_calibration_downgrades_divergence_and_anomaly(self):
        source = build_daily_match_input(match("206"))
        source["euro"] = {
            "initial": [5.15, 4.0, 1.46],
            "current": [5.15, 4.0, 1.46],
        }
        source["asian"] = {
            "initial": [0.93, "受一球", 0.85],
            "current": [1.30, "受半球", 0.57],
        }
        source["fae_core"] = {
            **(source.get("fae_core") or {}),
            "overall_score": 82,
            "probabilities": {
                "home_win": 17,
                "draw": 20,
                "away_win": 63,
            },
            "risk": {"level": "中", "dangerous": False},
        }
        rows = [{
            "match_id": "206",
            "analysis": {
                "primary_play": "客胜",
                "rating": 5,
                "model_rating": 5,
                "risks": [],
            },
            "input_snapshot": source,
        }]

        calibrated = FAEDailyAIAnalyzer.calibrate_daily_matches(rows)[0]
        analysis = calibrated["analysis"]

        self.assertEqual(analysis["rating"], 2.5)
        self.assertTrue(analysis["no_bet"])
        self.assertEqual(analysis["decision"], "不下注")
        self.assertEqual(analysis["secondary_play"], "平局")
        self.assertTrue(any(
            "欧亚背离" in item for item in analysis["rating_adjustments"]
        ))
        self.assertTrue(any(
            "极端水位" in item for item in analysis["rating_adjustments"]
        ))

    def test_summary_marks_secondary_pool_direction_as_defensive(self):
        summary = {
            "pools": {
                "draw": [{
                    "match_id": "104",
                    "rating": 4.5,
                    "reason": "平局概率29%，强强对话防平",
                }]
            }
        }
        matches = [{
            "match_id": "104",
            "analysis": {
                "primary_play": "主胜",
                "secondary_play": "平局",
                "rating": 3,
            },
        }]

        aligned = FAEDailyAIAnalyzer.align_summary_ratings(summary, matches)
        row = aligned["pools"]["draw"][0]

        self.assertEqual(row["rating"], 3)
        self.assertEqual(row["role"], "防选")
        self.assertIn("FAE估算平局概率29%（未校准）", row["reason"])

    def test_stale_memory_avoidance_is_removed_without_current_risk(self):
        summary = {
            "core_conclusion": (
                "周日201方向一般。综合建议构建周日201与周日202的2串1。"
            ),
            "pools": {
                "avoid": [{
                    "match_id": "201",
                    "rating": 3,
                    "reason": "让平赔率落入历史0%命中区间，严禁切入",
                }]
            },
            "recommended_combinations": [],
        }
        matches = [{
            "match_id": "201",
            "analysis": {
                "primary_play": "主胜",
                "secondary_play": "平局",
                "rating": 3.5,
                "rating_adjustments": ["基本面缺失较多，不允许评为五星"],
            },
        }]

        aligned = FAEDailyAIAnalyzer.align_summary_ratings(summary, matches)

        self.assertEqual(aligned["pools"]["avoid"], [])
        self.assertNotIn("2串1", aligned["core_conclusion"])


if __name__ == "__main__":
    unittest.main()
