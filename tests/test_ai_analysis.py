import os
import unittest
from unittest.mock import Mock, patch

from ai_analysis import AIAnalysisService, ArkResponsesClient, SkillLoader


class FakeArkClient:
    configured = True
    model = "test-model"

    def generate(self, prompt):
        self.prompt = prompt
        return (
            """```json
            {
              "result_tendency": "主队不败",
              "confidence": 99,
              "asian_tendency": "主队方向",
              "over_under_tendency": "小球",
              "score_candidates": ["1-0", "1-1", "2-0", "3-0"],
              "evidence": ["主水下降", "盘口下降"],
              "risks": ["盘口与水位方向存在矛盾"],
              "summary": "主队近期表现占优，但盘口支持力度有限，需要防范平局。"
            }
            ```""",
            {"response_id": "test-response", "usage": {"total_tokens": 100}},
        )


class SkillLoaderTests(unittest.TestCase):
    def setUp(self):
        self.loader = SkillLoader()

    def test_selects_only_skills_with_available_data(self):
        context = {
            "match": {"match_id": "1"},
            "analysis": {
                "recent": {"home": [{"score": "2-1"}], "away": []},
                "history": [],
                "standings": [],
                "future": {"home": [], "away": []},
            },
            "movement": {
                "asian": {
                    "handicap": {
                        "initial": "半球",
                        "current": "平手/半球",
                    }
                }
            },
            "prediction": {},
        }

        names = [skill.name for skill in self.loader.select(context)]

        self.assertIn("match-baseline", names)
        self.assertIn("recent-form", names)
        self.assertIn("asian-handicap", names)
        self.assertIn("risk-control", names)
        self.assertIn("final-synthesis", names)
        self.assertNotIn("head-to-head", names)
        self.assertNotIn("over-under", names)
        self.assertNotIn("model-calibration", names)


class AIAnalysisServiceTests(unittest.TestCase):
    def test_cleans_handicap_suffix_and_computes_movement(self):
        service = AIAnalysisService(client=FakeArkClient())
        context = service.build_context({
            "match_id": "1",
            "home_team": "主队",
            "away_team": "客队",
            "asian_initial_handicap": "半球",
            "asian_current_handicap": "平手/半球降",
            "asian_initial_home_odds": "0.98",
            "asian_current_home_odds": "0.82",
        })

        handicap = context["movement"]["asian"]["handicap"]

        self.assertEqual(handicap["current"], "平手/半球")
        self.assertEqual(handicap["direction"], "降盘")
        self.assertEqual(handicap["change"], -0.25)

    def test_generates_validated_analysis_with_selected_skills(self):
        client = FakeArkClient()
        service = AIAnalysisService(client=client)
        context = service.build_context({
            "match_id": "1",
            "home_team": "主队",
            "away_team": "客队",
            "asian_initial_handicap": "半球",
            "asian_current_handicap": "平手/半球降",
            "asian_initial_home_odds": "0.98",
            "asian_current_home_odds": "0.82",
        })

        result = service.generate_from_context(context)

        self.assertEqual(result["match_id"], "1")
        self.assertEqual(result["model"], "test-model")
        self.assertEqual(result["analysis"]["confidence"], 85)
        self.assertEqual(len(result["analysis"]["score_candidates"]), 3)
        self.assertIn("asian-handicap", result["analysis"]["skills"])
        self.assertIn("Skill: asian-handicap", client.prompt)
        self.assertNotIn("Skill: over-under", client.prompt)
        self.assertEqual(
            result["analysis"]["disclaimer"],
            "仅基于现有数据进行分析，不构成投注建议",
        )


class ArkResponsesClientTests(unittest.TestCase):
    @patch("ai_analysis.requests.post")
    def test_coding_gateway_uses_chat_completions(self, post):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "id": "chat-response",
            "choices": [{"message": {"content": "{\"summary\":\"ok\"}"}}],
        }
        post.return_value = response
        with patch.dict(os.environ, {}, clear=False):
            client = ArkResponsesClient(
                api_key="test-key",
                base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
                model="ark-code-latest",
            )

        text, metadata = client.generate("test prompt")

        self.assertEqual(client.api_mode, "chat_completions")
        self.assertEqual(text, "{\"summary\":\"ok\"}")
        self.assertEqual(metadata["response_id"], "chat-response")
        request_args = post.call_args
        self.assertEqual(
            request_args.args[0],
            "https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
        )
        self.assertEqual(
            request_args.kwargs["json"]["messages"][0]["content"],
            "test prompt",
        )


if __name__ == "__main__":
    unittest.main()
