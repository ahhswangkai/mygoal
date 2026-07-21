import os
import unittest
from unittest.mock import Mock, patch

from football_ai import ArkNarrativeClient, FAEReviewEngine, FootballAIEngine


class FakeArkClient:
    configured = True
    model = "test-model"

    def generate(self, prompt):
        self.prompt = prompt
        return (
            """```json
            {
              "evidence": ["欧赔与亚盘方向一致", "近期状态支持主队"],
              "risks": ["缺少伤停和首发数据"],
              "summary": "FAE 核心模型倾向主队，但数据完整度有限，应保留平局风险。"
            }
            ```""",
            {"response_id": "test-response", "usage": {"total_tokens": 100}},
        )


class DisabledArkClient:
    configured = False
    model = "test-model"

    def generate(self, prompt):
        raise AssertionError("deterministic core must not call provider")


def sample_match():
    return {
        "match_id": "1",
        "owner_date": "2026-07-18",
        "match_number": "周六201",
        "league": "世界杯",
        "home_team": "主队",
        "away_team": "客队",
        "handicap": -1,
        "euro_initial_win": "1.92",
        "euro_initial_draw": "3.45",
        "euro_initial_lose": "4.20",
        "euro_current_win": "1.72",
        "euro_current_draw": "3.65",
        "euro_current_lose": "4.80",
        "asian_initial_handicap": "半球",
        "asian_current_handicap": "一球升",
        "asian_initial_home_odds": "0.88",
        "asian_current_home_odds": "0.92",
        "asian_initial_away_odds": "0.98",
        "asian_current_away_odds": "0.94",
        "ou_initial_total": "2.5",
        "ou_current_total": "2.75↑",
        "ou_initial_over_odds": "0.90",
        "ou_current_over_odds": "0.86",
        "ou_initial_under_odds": "0.92",
        "ou_current_under_odds": "0.98",
    }


def source_analysis():
    return {
        "source": "test",
        "recent": {
            "home": [
                {"home_team": "主队", "away_team": "甲", "score": "2:0"},
                {"home_team": "乙", "away_team": "主队", "score": "1:2"},
                {"home_team": "主队", "away_team": "丙", "score": "1:1"},
            ],
            "away": [
                {"home_team": "客队", "away_team": "甲", "score": "0:1"},
                {"home_team": "乙", "away_team": "客队", "score": "2:1"},
                {"home_team": "客队", "away_team": "丙", "score": "1:1"},
            ],
        },
        "history": [
            {"home_team": "主队", "away_team": "客队", "score": "2:1"},
            {"home_team": "客队", "away_team": "主队", "score": "0:1"},
            {"home_team": "主队", "away_team": "客队", "score": "1:1"},
        ],
        "standings": [{"team": "主队", "rank": 1}, {"team": "客队", "rank": 7}],
    }


class FootballAIEngineTests(unittest.TestCase):
    def test_cleans_handicap_suffix_and_computes_movement(self):
        service = FootballAIEngine(client=DisabledArkClient())
        match = sample_match()
        match["asian_current_handicap"] = "平手/半球降"
        context = service.build_context(match)

        handicap = context["markets"]["movement"]["asian"]["handicap"]

        self.assertEqual(handicap["current"], "平手/半球")
        self.assertEqual(handicap["direction"], "降盘")
        self.assertEqual(handicap["change"], -0.25)

    def test_generates_deterministic_fae_core_without_provider(self):
        service = FootballAIEngine(client=DisabledArkClient())
        context = service.build_context(sample_match(), source_analysis())

        result = service.generate_from_context(context, use_ai=False)
        analysis = result["analysis"]

        self.assertEqual(result["match_id"], "1")
        self.assertEqual(result["engine"]["code"], "FAE")
        self.assertEqual(result["provider"], "fae-core")
        self.assertEqual(len(analysis["score_candidates"]), 3)
        self.assertEqual(
            analysis["probabilities"]["home_win"]
            + analysis["probabilities"]["draw"]
            + analysis["probabilities"]["away_win"],
            100,
        )
        self.assertFalse(analysis["probability_basis"]["calibrated"])
        self.assertAlmostEqual(
            sum(
                analysis["probability_basis"]["market_implied_no_vig"].values()
            ),
            100,
            delta=0.2,
        )
        self.assertIn("handicap", analysis["dimension_scores"])
        self.assertIn("recommendation", analysis)
        category_labels = {
            item["label"] for item in analysis["recommendation"]["category_scores"]
        }
        self.assertIn("平局", category_labels)
        self.assertIn("让平", category_labels)
        self.assertIn("value_score", analysis["recommendation"])
        self.assertIn("bet_score", analysis["recommendation"])
        self.assertIn("market_confidence", analysis["recommendation"])
        self.assertIn("decision", analysis["recommendation"])
        self.assertTrue(all(
            "value_score" in item and "no_bet" in item
            for item in analysis["recommendation"]["category_scores"]
        ))
        self.assertIn("F", [item["code"] for item in analysis["market_types"]])
        self.assertAlmostEqual(
            analysis["probabilities"]["hhad"]["lose"],
            analysis["probabilities"]["draw"]
            + analysis["probabilities"]["away_win"],
            delta=2,
        )
        primary = analysis["recommendation"]["primary"]
        first_scores = [tuple(map(int, score.split(":"))) for score in analysis["score_candidates"][:2]]
        if primary == "主胜":
            self.assertTrue(all(home > away for home, away in first_scores))
        elif primary == "平局":
            self.assertTrue(all(home == away for home, away in first_scores))
        elif primary == "客胜":
            self.assertTrue(all(home < away for home, away in first_scores))

    def test_provider_only_writes_narrative(self):
        client = FakeArkClient()
        service = FootballAIEngine(client=client)
        context = service.build_context(sample_match(), source_analysis())

        result = service.generate_from_context(context)

        self.assertEqual(result["model"], "test-model")
        self.assertEqual(result["provider_meta"]["mode"], "ark-narrative")
        self.assertEqual(result["analysis"]["evidence"][0], "欧赔与亚盘方向一致")
        self.assertIn("FAE 核心结果", client.prompt)
        self.assertNotIn("Skill:", client.prompt)

    def test_records_active_skill_versions_in_each_analysis(self):
        service = FootballAIEngine(client=DisabledArkClient())
        context = service.build_context(sample_match(), source_analysis())
        active_skills = [{
            "skill_id": "asian-handicap",
            "label": "亚洲盘口",
            "version": "1.2.0",
            "guidance": "测试规则",
        }]

        result = service.generate_from_context(
            context, use_ai=False, active_skills=active_skills
        )

        self.assertEqual(
            result["skill_versions"]["asian-handicap"], "1.2.0"
        )
        self.assertEqual(result["skills"][0]["guidance"], "测试规则")

    def test_detects_recent_data_contamination(self):
        service = FootballAIEngine(client=DisabledArkClient())
        source = source_analysis()
        source["recent"]["away"] = [
            {"home_team": "主队", "away_team": "其他队", "score": "2:0"},
            {"home_team": "其他队", "away_team": "主队", "score": "0:1"},
            {"home_team": "主队", "away_team": "第三队", "score": "3:1"},
        ]

        context = service.build_context(sample_match(), source)

        self.assertEqual(context["fundamentals"]["away_form"]["valid_matches"], 0)
        self.assertTrue(any("混入" in item for item in context["data_quality"]["issues"]))

    def test_expected_lineup_and_empty_injury_section_remain_partial_data(self):
        service = FootballAIEngine(client=DisabledArkClient())
        source = source_analysis()
        source.update({
            "teams": ["主队", "客队"],
            "injuries": {
                "status": "no_listed_players",
                "home": {"injured": [], "suspended": []},
                "away": {"injured": [], "suspended": []},
            },
            "lineups": {
                "status": "predicted",
                "home": {"starters": [{"name": "主队球员"}]},
                "away": {"starters": [{"name": "客队球员"}]},
            },
        })

        context = service.build_context(sample_match(), source)
        result = service.generate_from_context(context, use_ai=False)
        injuries = result["analysis"]["dimension_scores"]["injuries"]

        self.assertEqual(injuries["data_status"], "partial")
        self.assertTrue(any(
            "非官方确认首发" in issue
            for issue in context["data_quality"]["issues"]
        ))
        self.assertIn("尚非官方确认", injuries["tendency"])

    def test_away_favorite_deepening_is_not_mislabeled_as_drop(self):
        service = FootballAIEngine(client=DisabledArkClient())
        match = sample_match()
        match.update({
            "league": "测试联赛",
            "euro_initial_win": "5.00",
            "euro_current_win": "5.20",
            "euro_initial_lose": "1.55",
            "euro_current_lose": "1.48",
            "asian_initial_handicap": "受半球",
            "asian_current_handicap": "受一球",
        })

        context = service.build_context(match)
        result = service.generate_from_context(context, use_ai=False)
        signal_ids = {
            item["rule_id"] for item in result["core"]["rule_signals"]
        }

        self.assertEqual(
            context["markets"]["movement"]["asian"]["handicap"]["direction"],
            "升盘",
        )
        self.assertNotIn("handicap-drop", signal_ids)
        self.assertNotIn("euro-asian-divergence", signal_ids)

    def test_away_favorite_retreat_triggers_euro_asian_divergence(self):
        service = FootballAIEngine(client=DisabledArkClient())
        match = sample_match()
        match.update({
            "league": "测试联赛",
            "euro_initial_win": "5.00",
            "euro_current_win": "5.20",
            "euro_initial_lose": "1.50",
            "euro_current_lose": "1.46",
            "asian_initial_handicap": "受一球",
            "asian_current_handicap": "受半球",
            "asian_current_home_odds": "1.30",
            "asian_current_away_odds": "0.57",
        })

        context = service.build_context(match)
        result = service.generate_from_context(context, use_ai=False)
        signal_ids = {
            item["rule_id"] for item in result["core"]["rule_signals"]
        }

        self.assertEqual(
            context["markets"]["movement"]["asian"]["handicap"]["direction"],
            "降盘",
        )
        self.assertIn("handicap-drop", signal_ids)
        self.assertIn("euro-asian-divergence", signal_ids)
        self.assertIn("market-data-anomaly", signal_ids)
        recommendation = result["analysis"]["recommendation"]
        self.assertTrue(recommendation["no_bet"])
        self.assertEqual(recommendation["decision"], "不下注")
        self.assertLess(recommendation["market_confidence"]["score"], 50)


class FAEReviewEngineTests(unittest.TestCase):
    def test_reviews_recommendation_and_rule_signals(self):
        service = FootballAIEngine(client=DisabledArkClient())
        context = service.build_context(sample_match(), source_analysis())
        analysis = service.generate_from_context(context, use_ai=False)
        primary = analysis["core"]["recommendation"]["primary"]
        match = {**sample_match(), "status": 2, "home_score": 2, "away_score": 1}

        review = FAEReviewEngine().review(analysis, match)

        self.assertEqual(review["result"]["score"], "2:1")
        self.assertEqual(review["prediction"]["primary"], primary)
        self.assertIn(review["prediction"]["result"], {"hit", "miss", "push"})
        self.assertTrue(review["rule_results"])


class ArkNarrativeClientTests(unittest.TestCase):
    @patch("football_ai.provider.requests.post")
    def test_coding_gateway_uses_chat_completions(self, post):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "id": "chat-response",
            "choices": [{"message": {"content": '{"summary":"ok"}'}}],
        }
        post.return_value = response
        with patch.dict(os.environ, {}, clear=False):
            client = ArkNarrativeClient(
                api_key="test-key",
                base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
                model="ark-code-latest",
            )

        text, metadata = client.generate("test prompt")

        self.assertEqual(client.api_mode, "chat_completions")
        self.assertEqual(text, '{"summary":"ok"}')
        self.assertEqual(metadata["response_id"], "chat-response")
        request_args = post.call_args
        self.assertEqual(
            request_args.args[0],
            "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
        )

    @patch("football_ai.provider.requests.post")
    def test_streaming_chat_completions_are_joined(self, post):
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = [
            'data: {"id":"stream-1","choices":[{"delta":{"content":"{\\"summary\\":"}}]}',
            'data: {"id":"stream-1","choices":[{"delta":{"content":"\\"ok\\"}"}}]}',
            'data: {"id":"stream-1","choices":[],"usage":{"total_tokens":12}}',
            "data: [DONE]",
        ]
        post.return_value = response
        client = ArkNarrativeClient(
            api_key="test-key",
            base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
            model="ark-code-latest",
            stream=True,
            max_tokens=2048,
            thinking="disabled",
            json_mode=True,
        )

        text, metadata = client.generate("test prompt")

        self.assertEqual(text, '{"summary":"ok"}')
        self.assertEqual(metadata["response_id"], "stream-1")
        self.assertEqual(metadata["usage"]["total_tokens"], 12)
        payload = post.call_args.kwargs["json"]
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["max_tokens"], 2048)
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["response_format"], {"type": "json_object"})


if __name__ == "__main__":
    unittest.main()
