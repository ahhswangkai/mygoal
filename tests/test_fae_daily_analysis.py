import json
import unittest

from football_ai.daily_analysis import (
    DAILY_AI_MAX_BATCH_SIZE,
    FAEDailyAIAnalyzer,
    build_daily_match_input,
    compact_daily_ai_run,
)
from football_ai.provider import FAEOutputError, FAEProviderError


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


class PartialBatchArkClient:
    configured = True
    model = "ark-partial-test"

    def __init__(self):
        self.detail_batch_sizes = []

    def generate(self, prompt):
        if "全日总编" in prompt:
            return json.dumps({
                "daily_summary": {
                    "core_conclusion": "已合并成功批次。",
                    "warnings": [],
                    "pools": {},
                    "recommended_combinations": [],
                },
            }, ensure_ascii=False), {"response_id": "synthesis"}

        marker = (
            "# 当日比赛输入\n"
            if "# 当日比赛输入\n" in prompt
            else "# 比赛输入\n"
        )
        payload = json.loads(prompt.split(marker, 1)[1])
        rows = payload if isinstance(payload, list) else [payload]
        self.detail_batch_sizes.append(len(rows))
        if len(self.detail_batch_sizes) == 2:
            raise FAEProviderError("模拟第二批超时")
        return json.dumps({
            "daily_summary": {},
            "matches": [
                {
                    "match_id": str(item["match_id"]),
                    "direction": "主胜",
                    "primary_play": "主胜",
                    "secondary_play": "平局",
                    "rating": 3,
                }
                for item in rows
            ],
        }, ensure_ascii=False), {"response_id": "detail-test"}


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
    @staticmethod
    def radar_match(
        match_id,
        *,
        secondary="观望",
        handicap_play="让负",
        ordinary_value=-5.0,
        handicap_value=-12.0,
        primary="主胜",
    ):
        categories = [
            {
                "label": "平局",
                "probability": 27,
                "bet_score": 61,
                "score": 61,
                "odds": 3.5,
                "market_implied_probability": 26,
                "expected_return": 0.95,
                "no_bet": ordinary_value < 0,
            },
            {
                "label": "让平",
                "probability": 26,
                "bet_score": 60,
                "score": 60,
                "odds": 3.4,
                "market_implied_probability": 28,
                "expected_return": 0.88,
                "no_bet": handicap_value < 0,
            },
        ]
        return {
            "match_id": str(match_id),
            "match_number": f"周四{match_id}",
            "analysis": {
                "primary_play": primary,
                "secondary_play": secondary,
                "handicap_play": handicap_play,
                "predicted_result": primary,
                "no_bet": True,
            },
            "input_snapshot": {
                "asian": {"current": [0.88, "半球", 0.96]},
                "sporttery_handicap": {
                    "value": -1,
                    "current": [2.4, 3.4, 2.2],
                },
                "current_asian_risk": {
                    "pattern_ids": ["water_drop_without_deepen"],
                },
                "fae_core": {
                    "recommendation": {
                        "market_confidence": {
                            "score": 76,
                            "level": "高",
                        },
                        "category_scores": categories,
                    },
                },
                "historical_goal_margin_model": {
                    "ordinary_draw": {
                        "eligible_for_adjustment": True,
                        "historical_probability": 30.0,
                        "market_probability": 26.0,
                        "blended_probability": 27.2,
                        "effective_sample": 62.0,
                        "confidence": "中",
                        "odds": 3.5,
                        "value_edge": ordinary_value,
                    },
                    "handicap_draw": {
                        "eligible_for_adjustment": True,
                        "target_goal_difference": 1,
                        "historical_probability": 28.0,
                        "market_probability": 27.0,
                        "blended_probability": 27.4,
                        "effective_sample": 58.0,
                        "confidence": "中",
                        "odds": 3.4,
                        "value_edge": handicap_value,
                    },
                },
            },
        }

    def test_same_day_rerun_retains_started_pregame_judgements(self):
        current = {
            "daily_summary": {
                "warnings": ["本轮市场提醒"],
                "pools": {"handicap_draw": [{"match_id": "2"}]},
                "recommended_combinations": [],
            },
            "matches": [{
                "match_id": "2",
                "match_number": "周二202",
                "match_time": "07-21 19:00",
            }],
        }
        retained = [{
            "match_id": "1",
            "match_number": "周二201",
            "match_time": "07-21 18:00",
            "run_id": "old-run",
            "status_at_prediction": 0,
            "current_status": 1,
            "analysis": {"primary_play": "让平"},
        }]

        result = FAEDailyAIAnalyzer.merge_retained_matches(current, retained)

        self.assertEqual(result["match_count"], 2)
        self.assertEqual(result["analyzed_match_count"], 1)
        self.assertEqual(result["retained_match_count"], 1)
        self.assertEqual(
            [item["match_id"] for item in result["matches"]], ["1", "2"]
        )
        old = result["matches"][0]
        self.assertTrue(old["retained_from_pregame"])
        self.assertEqual(old["retained_from_run_id"], "old-run")
        self.assertEqual(old["status_at_prediction"], 0)
        self.assertEqual(
            result["daily_summary"]["pools"]["handicap_draw"],
            [{"match_id": "2"}],
        )
        self.assertIn(
            "周二201保留原赛前研判",
            result["daily_summary"]["warnings"][-1],
        )

    def test_builds_clean_five_market_input_and_flags_total_jump(self):
        source = match("2", "2.5", "3.5")
        source["hi_handicap_value"] = None
        source["handicap"] = "-1"
        row = build_daily_match_input(source)

        self.assertEqual(row["asian"]["current"][1], "半球")
        self.assertTrue(any("大小球盘口" in item for item in row["data_warnings"]))
        self.assertEqual(row["sporttery_handicap"]["value"], -1)
        self.assertNotIn("竞彩让球数缺失", row["data_warnings"])
        self.assertIn(
            "water_drop_without_deepen",
            row["current_asian_risk"]["pattern_ids"],
        )

    def test_includes_league_history_profile_in_match_input(self):
        profile = {
            "league": "测试联赛",
            "sample_size": 120,
            "confidence": "高",
            "eligible_for_adjustment": True,
            "hidden_signals": ["平局率较全库高4.5个百分点"],
        }
        row = build_daily_match_input(
            match("2"),
            league_profile=profile,
        )

        self.assertEqual(row["league_history_profile"], profile)

    def test_includes_match_specific_goal_margin_model(self):
        model = {
            "version": "goal-margin-similarity-v1",
            "ordinary_draw": {
                "effective_sample": 58.2,
                "blended_probability": 29.4,
                "eligible_for_adjustment": True,
            },
            "handicap_draw": {
                "target_goal_difference": 1,
                "effective_sample": 61.0,
                "blended_probability": 27.1,
                "eligible_for_adjustment": True,
            },
        }

        row = build_daily_match_input(
            match("2"),
            goal_margin_model=model,
        )

        self.assertEqual(row["historical_goal_margin_model"], model)

    def test_similar_history_conservatively_calibrates_draw_value(self):
        source = {
            "fae_core": {
                "recommendation": {
                    "market_confidence": {"score": 66},
                },
            },
            "historical_goal_margin_model": {
                "ordinary_draw": {
                    "eligible_for_adjustment": True,
                    "blended_probability": 27.0,
                    "effective_sample": 64.0,
                    "credibility_weight": 0.24,
                    "signal": "历史低于市场",
                },
            },
        }
        profile = {
            "label": "平局",
            "probability": 42,
            "prediction_score": 74,
            "odds": 3.2,
            "market_implied_probability": 30.0,
            "value_score": 80,
            "bet_score": 78,
            "no_bet": False,
            "no_bet_reasons": [],
        }

        adjusted = FAEDailyAIAnalyzer._historical_adjusted_profile(
            source, profile
        )

        self.assertTrue(adjusted["historical_calibration"]["applied"])
        self.assertLess(adjusted["probability"], 42)
        self.assertLess(adjusted["bet_score"], 78)

    def test_draw_radar_keeps_secondary_draw_as_watch_despite_no_bet(self):
        row = self.radar_match(
            "202",
            secondary="平局",
            ordinary_value=-4.1,
        )

        analyzed = FAEDailyAIAnalyzer.apply_draw_radar([row])[0]
        candidate = analyzed["analysis"]["draw_radar"]["ordinary_draw"]
        summary = FAEDailyAIAnalyzer.attach_draw_radar_summary(
            {}, [analyzed]
        )

        self.assertEqual(candidate["tier"], "watch")
        self.assertLess(candidate["odds_value"], 0)
        self.assertIn("同市场防选", candidate["role_signals"])
        self.assertEqual(
            summary["draw_radar"]["ordinary_draw"][0]["match_id"], "202"
        )

    def test_draw_radar_keeps_secondary_handicap_draw_as_watch(self):
        row = self.radar_match(
            "205",
            secondary="让平",
            handicap_play="让负",
            handicap_value=-14.3,
            primary="让负",
        )

        analyzed = FAEDailyAIAnalyzer.apply_draw_radar([row])[0]
        candidate = analyzed["analysis"]["draw_radar"]["handicap_draw"]

        self.assertEqual(candidate["tier"], "watch")
        self.assertEqual(candidate["definition"], "主队恰好赢1球")
        self.assertLessEqual(candidate["rating"], 3.5)
        self.assertIn("仅列观察", candidate["reason"])

    def test_draw_radar_hard_veto_downgrades_negative_value_core(self):
        candidate = FAEDailyAIAnalyzer._apply_draw_radar_candidate_guard({
            "match_id": "201",
            "selection": "平局",
            "tier": "core",
            "rating": 4.5,
            "odds": 3.4,
            "odds_value": -0.1,
            "draw_odds_band_signal": {},
            "reason": "达到独立核心门槛。",
        })

        self.assertEqual(candidate["tier"], "watch")
        self.assertFalse(candidate["formal_eligible"])
        self.assertTrue(any(
            "赔率价值为负" in reason
            for reason in candidate["official_veto_reasons"]
        ))

    def test_draw_radar_hard_veto_respects_block_official_signal(self):
        candidate = FAEDailyAIAnalyzer._apply_draw_radar_candidate_guard({
            "match_id": "202",
            "selection": "平局",
            "tier": "core",
            "rating": 4.5,
            "odds": 3.2,
            "odds_value": 8.0,
            "draw_odds_band_signal": {"block_official": True},
            "reason": "达到独立核心门槛。",
        })

        self.assertEqual(candidate["tier"], "watch")
        self.assertTrue(any(
            "区间规则明确禁止" in reason
            for reason in candidate["official_veto_reasons"]
        ))

    def test_handicap_draw_350_to_399_is_weekly_risk_watch_only(self):
        candidate = FAEDailyAIAnalyzer._apply_draw_radar_candidate_guard({
            "match_id": "203",
            "selection": "让平",
            "tier": "core",
            "rating": 5.0,
            "odds": 3.55,
            "odds_value": 12.0,
            "draw_odds_band_signal": {},
            "reason": "达到独立核心门槛。",
        })

        self.assertEqual(candidate["tier"], "watch")
        self.assertLessEqual(candidate["rating"], 3.5)
        self.assertTrue(any(
            "3.50–3.99" in reason
            for reason in candidate["official_veto_reasons"]
        ))

    def test_observation_radar_cannot_override_existing_formal_pick(self):
        candidate = FAEDailyAIAnalyzer._apply_draw_radar_candidate_guard({
            "match_id": "204",
            "selection": "让平",
            "tier": "watch",
            "rating": 3.5,
            "score": 88,
            "probability": 30,
            "odds": 3.4,
            "odds_value": 10.0,
            "effective_sample": 100,
            "risk_pattern_ids": [],
            "draw_odds_band_signal": {
                "kind": "backtested_league_one_goal_value",
                "official_score_min": 84,
            },
            "reason": "仅列观察。",
        })
        row = {
            "match_id": "204",
            "analysis": {
                "primary_play": "让平",
                "decision": "可考虑",
                "no_bet": False,
                "rating": 4.5,
                "draw_radar": {"handicap_draw": candidate},
                "market_confidence": {"score": 80},
            },
            "input_snapshot": {},
        }

        result = (
            FAEDailyAIAnalyzer.apply_draw_radar_recommendation_overrides(
                [row]
            )[0]["analysis"]
        )

        self.assertTrue(result["no_bet"])
        self.assertEqual(result["decision"], "观察")
        self.assertEqual(result["formal_veto"]["selection"], "让平")

    def test_draw_radar_summary_only_keeps_top_three_per_market(self):
        matches = []
        for index in range(5):
            matches.append({
                "analysis": {
                    "draw_radar": {
                        "ordinary_draw": {
                            "match_id": str(index),
                            "tier": "watch",
                            "score": 60 + index,
                            "probability": 25,
                        },
                        "handicap_draw": {
                            "match_id": str(index),
                            "tier": "watch",
                            "score": 70 + index,
                            "probability": 25,
                        },
                    },
                },
            })

        radar = FAEDailyAIAnalyzer.attach_draw_radar_summary(
            {}, matches
        )["draw_radar"]

        self.assertEqual(
            [row["match_id"] for row in radar["ordinary_draw"]],
            ["4", "3", "2"],
        )
        self.assertEqual(
            [row["match_id"] for row in radar["handicap_draw"]],
            ["4", "3", "2"],
        )

    def test_summary_promotion_removes_hard_vetoed_formal_pool_row(self):
        summary = {
            "pools": {
                "draw": [],
                "handicap_draw": [{
                    "match_id": "205",
                    "selection": "让平",
                    "rating": 4.5,
                }],
                "avoid": [],
            },
        }
        matches = [{
            "match_id": "205",
            "analysis": {
                "primary_play": "让平",
                "no_bet": True,
                "draw_radar": {
                    "handicap_draw": {
                        "match_id": "205",
                        "selection": "让平",
                        "tier": "watch",
                    },
                },
            },
        }]

        result = FAEDailyAIAnalyzer.promote_draw_radar_recommendations(
            summary, matches
        )

        self.assertEqual(result["pools"]["handicap_draw"], [])

    def test_only_positive_value_core_radar_rows_can_form_combinations(self):
        draw = self.radar_match(
            "202",
            secondary="平局",
            ordinary_value=6.0,
            primary="平局",
        )
        handicap = self.radar_match(
            "205",
            secondary="让平",
            handicap_play="让平",
            handicap_value=8.0,
            primary="让平",
        )
        handicap_profile = (
            handicap["input_snapshot"]["fae_core"]["recommendation"]
            ["category_scores"][1]
        )
        handicap_profile["odds"] = 4.0
        handicap_profile["expected_return"] = 1.096
        handicap["input_snapshot"]["historical_goal_margin_model"][
            "handicap_draw"
        ]["odds"] = 4.0
        rows = FAEDailyAIAnalyzer.apply_draw_radar([draw, handicap])
        summary = FAEDailyAIAnalyzer.attach_draw_radar_summary(
            {
                "pools": {
                    "draw": [],
                    "handicap_draw": [],
                    "avoid": [],
                },
                "recommended_combinations": [],
            },
            rows,
        )

        combinations = FAEDailyAIAnalyzer._ensure_mixed_combinations(
            summary
        )

        self.assertEqual(len(combinations), 1)
        self.assertEqual(combinations[0]["play"], "2串1")
        self.assertEqual(
            {pick["selection"] for pick in combinations[0]["picks"]},
            {"平局", "让平"},
        )

    def test_includes_available_500_fundamentals_without_false_missing_warning(self):
        source_analysis = {
            "source": "500彩票网",
            "source_url": "https://odds.500.com/fenxi/shuju-2.shtml",
            "teams": ["主队", "客队"],
            "recent": {
                "home": [{
                    "home_team": "主队", "away_team": "甲队",
                    "score": "2:0", "date": "26-07-10",
                }],
                "away": [{
                    "home_team": "乙队", "away_team": "客队",
                    "score": "1:2", "date": "26-07-11",
                }],
            },
            "history": [{
                "home_team": "主队", "away_team": "客队",
                "score": "1:1", "date": "25-07-11",
            }],
            "team_rankings": {
                "home": {"team": "主队", "league_rank": "联赛2"},
                "away": {"team": "客队", "league_rank": "联赛5"},
            },
            "future": {
                "home": [{"home_team": "主队", "away_team": "丙队"}],
                "away": [{"home_team": "丁队", "away_team": "客队"}],
            },
            "injuries": {
                "status": "no_listed_players",
                "home": {"injured": [], "suspended": []},
                "away": {"injured": [], "suspended": []},
            },
            "lineups": {
                "status": "predicted",
                "label": "500彩票网预计阵容（非官方确认首发）",
                "home": {
                    "team": "主队",
                    "starters": [{"number": "1", "name": "主门将"}],
                },
                "away": {
                    "team": "客队",
                    "starters": [{"number": "9", "name": "客前锋"}],
                },
            },
        }

        row = build_daily_match_input(
            match("2"),
            source_analysis=source_analysis,
        )

        self.assertEqual(row["missing_fundamentals"], [])
        self.assertEqual(
            row["fundamentals"]["recent"]["away"][0]["away_team"], "客队"
        )
        self.assertEqual(
            row["fundamentals"]["lineups"]["status"], "predicted"
        )
        self.assertEqual(row["rank"], {"home": "联赛2", "away": "联赛5"})
        self.assertIn("不是官方确认首发", row["fundamentals"]["note"])

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
        self.assertIn("联赛历史画像", client.prompt)
        self.assertIn("大球、小球只能写入market_analysis.total", client.prompt)
        self.assertEqual(result["review_memory"]["memory_hash"], "memory-1")

    def test_daily_analysis_caps_batches_at_ten_and_keeps_partial_success(self):
        client = PartialBatchArkClient()
        analyzer = FAEDailyAIAnalyzer(client)
        rows = [
            build_daily_match_input(match(str(index)))
            for index in range(1, 22)
        ]
        checkpoints = []

        result = analyzer.analyze(
            "2026-07-18",
            rows,
            batch_size=100,
            batch_cache_save=checkpoints.append,
        )

        self.assertEqual(DAILY_AI_MAX_BATCH_SIZE, 10)
        self.assertEqual(client.detail_batch_sizes, [10, 10, 1])
        self.assertEqual(result["batch_count"], 3)
        self.assertEqual(result["completed_batch_count"], 2)
        self.assertEqual(result["failed_batch_count"], 1)
        self.assertEqual(result["ai_analyzed_match_count"], 11)
        self.assertEqual(result["fallback_match_count"], 10)
        self.assertTrue(result["partial_success"])
        failed_rows = [
            item for item in result["matches"]
            if item["analysis_source"] == "fae-core-fallback"
        ]
        self.assertEqual(len(failed_rows), 10)
        self.assertTrue(any(
            "本批暂用FAE核心结论" in warning
            for warning in result["daily_summary"]["warnings"]
        ))
        self.assertEqual(
            len([item for item in checkpoints if item["kind"] == "detail"]),
            2,
        )

    def test_daily_analysis_still_fails_when_every_provider_batch_fails(self):
        class AlwaysFailingClient:
            configured = True
            model = "ark-failing-test"

            @staticmethod
            def generate(_prompt):
                raise FAEProviderError("模拟方舟不可用")

        analyzer = FAEDailyAIAnalyzer(AlwaysFailingClient())
        rows = [
            build_daily_match_input(match(str(index)))
            for index in range(1, 12)
        ]

        with self.assertRaisesRegex(FAEOutputError, "全部2批大模型研判失败"):
            analyzer.analyze("2026-07-18", rows, batch_size=10)

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
        self.assertEqual(analysis["secondary_play"], "让负")
        self.assertTrue(analysis["consistency_guard"]["triggered"])

    def test_handicap_secondary_replaces_letdraw_with_market_backed_cover(self):
        source = {
            "sporttery_handicap": {
                "value": -1,
                "current": [2.50, 3.32, 2.34],
            },
            "fae_core": {
                "probabilities": {
                    "hhad": {"win": 31, "draw": 31, "lose": 38},
                },
            },
        }

        decision = FAEDailyAIAnalyzer._secondary_play_decision(
            source, "让负", "让平"
        )

        self.assertEqual(decision["selection"], "让胜")
        self.assertTrue(decision["changed"])
        self.assertEqual(
            decision["strategy"], "hhad-model-market-coverage-v1"
        )
        self.assertIn("替换原防选让平", decision["reason"])

    def test_handicap_secondary_uses_direction_over_lower_letdraw_probability(self):
        source = {
            "sporttery_handicap": {
                "value": -1,
                "current": [2.95, 3.60, 1.95],
            },
            "fae_core": {
                "probabilities": {
                    "hhad": {"win": 29, "draw": 27, "lose": 44},
                },
            },
        }

        decision = FAEDailyAIAnalyzer._secondary_play_decision(
            source, "让负", "让平"
        )

        self.assertEqual(decision["selection"], "让胜")
        scores = {
            row["selection"]: row["coverage_score"]
            for row in decision["candidates"]
        }
        self.assertGreater(scores["让胜"], scores["让平"])

    def test_handicap_secondary_keeps_letdraw_when_it_really_ranks_second(self):
        source = {
            "sporttery_handicap": {
                "value": -1,
                "current": [1.74, 3.70, 3.51],
            },
            "fae_core": {
                "probabilities": {
                    "hhad": {"win": 42, "draw": 35, "lose": 23},
                },
            },
        }

        decision = FAEDailyAIAnalyzer._secondary_play_decision(
            source, "让胜", "让平"
        )

        self.assertEqual(decision["selection"], "让平")
        self.assertFalse(decision["changed"])

    def test_exact_margin_pick_yields_to_probability_and_shortest_price(self):
        source = build_daily_match_input(match("201"))
        source["fae_core"]["probabilities"] = {
            "hhad": {"win": 42, "draw": 35, "lose": 23}
        }
        source["sporttery_handicap"]["current"] = [1.74, 3.70, 3.51]

        result = FAEDailyAIAnalyzer._normalize_match(source, {
            "primary_play": "让平",
            "secondary_play": "让负",
            "rating": 3.5,
        })
        analysis = result["analysis"]

        self.assertEqual(analysis["model_primary_play"], "让平")
        self.assertEqual(analysis["primary_play"], "让胜")
        self.assertEqual(analysis["secondary_play"], "让平")
        self.assertEqual(
            analysis["secondary_selection_guard"]["strategy"],
            "hhad-model-market-coverage-v1",
        )
        self.assertEqual(
            analysis["consistency_guard"]["guard_type"],
            "exact_margin_market_alignment",
        )

    def test_exact_margin_guard_re_ranks_both_remaining_handicap_outcomes(self):
        source = build_daily_match_input(match("202"))
        source["fae_core"]["probabilities"] = {
            "hhad": {"win": 31, "draw": 31, "lose": 38}
        }
        source["sporttery_handicap"]["current"] = [2.50, 3.32, 2.34]

        result = FAEDailyAIAnalyzer._normalize_match(source, {
            "primary_play": "让平",
            "secondary_play": "让胜",
            "rating": 3.5,
        })
        analysis = result["analysis"]

        self.assertEqual(analysis["model_primary_play"], "让平")
        self.assertEqual(analysis["primary_play"], "让负")
        self.assertEqual(analysis["secondary_play"], "让胜")
        self.assertTrue(analysis["consistency_guard"]["triggered"])
        self.assertEqual(
            analysis["secondary_selection_guard"]["selection"], "让胜"
        )
        self.assertEqual(
            analysis["consistency_guard"]["guard_type"],
            "exact_margin_market_alignment",
        )

    def test_low_total_does_not_keep_draw_ahead_of_strong_home_direction(self):
        source = {
            "match_id": "013",
            "match_number": "周六013",
            "league": "葡超",
            "euro": {
                "initial": [1.98, 3.10, 3.32],
                "current": [1.86, 3.00, 3.87],
            },
            "asian": {
                "initial": [0.92, "平手", 0.94],
                "current": [0.84, "半球", 1.04],
            },
            "sporttery_handicap": {
                "value": -1,
                "current": [2.55, 3.10, 2.25],
            },
            "total": {
                "initial": [0.90, 2.50, 0.92],
                "current": [1.02, 2.25, 0.80],
            },
            "fae_core": {
                "probabilities": {
                    "home_win": 52,
                    "draw": 31,
                    "away_win": 17,
                },
            },
        }

        analysis = FAEDailyAIAnalyzer._normalize_match(source, {
            "primary_play": "平局",
            "secondary_play": "主胜",
            "rating": 3.5,
        })["analysis"]

        self.assertEqual(analysis["primary_play"], "主胜")
        self.assertEqual(analysis["secondary_play"], "平局")
        self.assertTrue(
            analysis["directional_precision_guard"]["triggered"]
        )
        self.assertIn("低总球只压低比分", analysis["verdict"])

    def test_level_asian_low_water_promotes_clear_away_direction_over_draw(self):
        source = {
            "match_id": "014",
            "match_number": "周六014",
            "league": "瑞典超",
            "euro": {
                "initial": [2.30, 3.22, 2.61],
                "current": [2.62, 3.12, 2.34],
            },
            "asian": {
                "initial": [0.99, "平手", 0.89],
                "current": [1.07, "平手", 0.81],
            },
            "sporttery_handicap": {
                "value": 1,
                "current": [1.62, 3.55, 4.20],
            },
            "total": {
                "initial": [0.92, 2.50, 0.90],
                "current": [0.94, 2.50, 0.88],
            },
            "fae_core": {
                "probabilities": {
                    "home_win": 30,
                    "draw": 31,
                    "away_win": 39,
                },
            },
        }

        primary, guard = FAEDailyAIAnalyzer._directional_precision_guard(
            source, "平局"
        )

        self.assertEqual(primary, "客胜")
        self.assertEqual(guard["secondary_selection"], "平局")
        self.assertTrue(guard["triggered"])

    def test_true_deepen_high_total_promotes_cover_over_exact_one_goal(self):
        source = {
            "match_id": "016",
            "match_number": "周六016",
            "league": "荷甲",
            "euro": {
                "initial": [1.43, 4.15, 5.30],
                "current": [1.39, 4.35, 5.55],
            },
            "asian": {
                "initial": [0.88, "0.75", 1.00],
                "current": [0.88, "1", 1.00],
            },
            "sporttery_handicap": {
                "value": -1,
                "current": [2.18, 3.40, 2.50],
            },
            "total": {
                "initial": [0.90, 2.75, 0.92],
                "current": [0.88, 3.00, 0.94],
            },
            "fae_core": {
                "probabilities": {
                    "home_win": 67,
                    "draw": 20,
                    "away_win": 13,
                    "hhad": {"win": 41, "draw": 34, "lose": 25},
                },
            },
        }

        analysis = FAEDailyAIAnalyzer._normalize_match(source, {
            "primary_play": "让平",
            "secondary_play": "让胜",
            "rating": 4,
        })["analysis"]

        self.assertEqual(analysis["primary_play"], "让胜")
        self.assertEqual(analysis["secondary_play"], "让平")
        self.assertTrue(
            analysis["directional_precision_guard"]["triggered"]
        )
        self.assertIn("穿盘证据强于恰好赢1球", analysis["verdict"])

    def test_extreme_favorite_deepening_is_not_misread_as_exact_margin(self):
        source = {
            "euro": {
                "initial": [1.40, 4.20, 5.65],
                "current": [1.26, 4.90, 7.60],
            },
            "asian": {
                "initial": [0.83, "1", 1.05],
                "current": [1.04, "1.25", 0.84],
            },
            "sporttery_handicap": {
                "value": -1,
                "current": [1.82, 3.60, 3.85],
            },
            "total": {
                "initial": [0.90, 3.00, 0.92],
                "current": [0.88, 3.00, 0.94],
            },
            "current_asian_risk": {
                "pattern_ids": ["upper_water_rise"],
            },
            "fae_core": {
                "probabilities": {
                    "home_win": 73,
                    "draw": 17,
                    "away_win": 10,
                    "hhad": {"win": 43, "draw": 33, "lose": 24},
                },
            },
        }

        primary, guard = FAEDailyAIAnalyzer._directional_precision_guard(
            source, "让平"
        )

        self.assertEqual(primary, "让胜")
        self.assertEqual(guard["secondary_selection"], "让平")
        self.assertTrue(guard["triggered"])

    def test_favorite_odds_retreat_does_not_displace_valid_draw(self):
        source = {
            "euro": {
                "initial": [3.60, 3.45, 1.78],
                "current": [3.30, 3.34, 1.90],
            },
            "asian": {
                "initial": [0.88, "受半球", 0.96],
                "current": [0.90, "受平半", 0.94],
            },
            "total": {
                "initial": [0.92, 2.50, 0.90],
                "current": [1.00, 2.25, 0.82],
            },
            "fae_core": {
                "probabilities": {
                    "home_win": 26,
                    "draw": 34,
                    "away_win": 40,
                },
            },
        }

        primary, guard = FAEDailyAIAnalyzer._directional_precision_guard(
            source, "平局"
        )

        self.assertEqual(primary, "平局")
        self.assertFalse(guard["triggered"])

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

    def test_league_water_pattern_is_auditable_in_calibration(self):
        source = build_daily_match_input(match("205"))
        source["current_asian_risk"] = {
            "favorite_side": "home",
            "pattern_ids": ["water_drop_without_deepen"],
        }
        source["league_history_profile"] = {
            "eligible_for_adjustment": True,
            "asian_risk_patterns": {
                "patterns": {
                    "water_drop_without_deepen": {
                        "label": "降水不升盘",
                        "sample": 24,
                        "not_cover_rate": 70.8,
                    }
                }
            },
        }
        rows = [{
            "match_id": "205",
            "analysis": {
                "primary_play": "让胜",
                "rating": 4.5,
                "model_rating": 4.5,
                "risks": [],
            },
            "input_snapshot": source,
        }]

        analysis = (
            FAEDailyAIAnalyzer.calibrate_daily_matches(rows)[0]["analysis"]
        )

        self.assertEqual(
            analysis["league_asian_risk_evidence"][0]["pattern_id"],
            "water_drop_without_deepen",
        )
        self.assertTrue(any(
            "联赛降水不升盘模式历史不穿率70.8%" in item
            for item in analysis["risks"]
        ))
        self.assertTrue(any(
            "联赛历史高样本风险匹配" in item
            for item in analysis["rating_adjustments"]
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

    def test_summary_cannot_override_final_play_or_no_bet_decision(self):
        summary = {
            "pools": {
                "handicap_lose": [{
                    "match_id": "207",
                    "rating": 4,
                    "reason": "风险模式提示主队可能不穿盘",
                }],
                "avoid": [{
                    "match_id": "207",
                    "rating": 3.5,
                    "reason": "建议观察",
                }],
            }
        }
        matches = [{
            "match_id": "207",
            "match_number": "周二207",
            "analysis": {
                "primary_play": "主胜",
                "secondary_play": "平局",
                "handicap_play": "让胜",
                "predicted_result": "主胜",
                "decision": "可考虑",
                "no_bet": False,
                "rating": 4,
            },
            "input_snapshot": {
                "fae_core": {
                    "recommendation": {
                        "category_scores": [{
                            "label": "让负",
                            "no_bet": True,
                            "no_bet_reasons": ["赔率价值不足"],
                        }]
                    }
                }
            },
        }]

        aligned = FAEDailyAIAnalyzer.align_summary_ratings(
            summary, matches
        )

        self.assertEqual(aligned["pools"]["handicap_lose"], [])
        self.assertEqual(aligned["pools"]["avoid"], [])
        self.assertEqual(len(aligned["pools"]["core"]), 1)
        self.assertEqual(aligned["pools"]["core"][0]["match_id"], "207")
        self.assertEqual(aligned["pools"]["core"][0]["selection"], "主胜")
        self.assertEqual(
            aligned["pools"]["core"][0]["handicap_play"], "让胜"
        )

    def test_core_pool_excludes_no_bet_matches(self):
        summary = {"pools": {}}
        matches = [{
            "match_id": "201",
            "match_number": "周二201",
            "analysis": {
                "primary_play": "客胜",
                "handicap_play": "让负",
                "no_bet": True,
                "rating": 4,
            },
        }]

        aligned = FAEDailyAIAnalyzer.align_summary_ratings(
            summary, matches
        )

        self.assertEqual(aligned["pools"]["core"], [])

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

    def test_compact_daily_run_keeps_list_fields_and_drops_prompt_inputs(self):
        source = {
            "run_id": "run-1",
            "input_hash": "secret-large-hash",
            "provider_meta": {"usage": {"prompt_tokens": 1000}},
            "daily_summary": {"core_conclusion": "今日结论"},
            "matches": [{
                "match_id": "201",
                "match_number": "周六201",
                "home_team": "主队",
                "away_team": "客队",
                "analysis": {
                    "primary_play": "让平",
                    "secondary_play": "让负",
                    "verdict": "模型结论",
                    "historical_odds_rules": ["large-audit-only-field"],
                },
                "input_snapshot": {
                    "euro": {"current": [1.5, 3.8, 5.5]},
                    "fundamentals": {"large": "payload"},
                    "historical_goal_margin_model": {"version": "v1"},
                    "fae_core": {"recommendation": {"category_scores": [{
                        "label": "让平",
                        "odds": 3.5,
                        "bet_score": 72,
                        "no_bet": False,
                        "no_bet_reasons": ["audit-only"],
                    }] }},
                },
            }],
        }

        compact = compact_daily_ai_run(source)

        self.assertTrue(compact["compact"])
        self.assertNotIn("provider_meta", compact)
        self.assertNotIn("input_hash", compact)
        row = compact["matches"][0]
        self.assertEqual(row["analysis"]["primary_play"], "让平")
        self.assertNotIn("historical_odds_rules", row["analysis"])
        self.assertNotIn("fundamentals", row["input_snapshot"])
        self.assertEqual(
            row["input_snapshot"]["fae_core"]["recommendation"]
            ["category_scores"][0],
            {
                "label": "让平",
                "odds": 3.5,
                "bet_score": 72,
                "no_bet": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
