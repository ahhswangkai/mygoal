import unittest

from football_ai.ai_review import FAEAIReviewAnalyzer


class FakeReviewClient:
    configured = True
    model = "ark-code-latest"

    def __init__(self):
        self.calls = 0

    def generate(self, prompt):
        self.calls += 1
        return """{
          "summary": {
            "conclusion": "命中与失误均需要区分结果和决策质量。",
            "what_worked": ["欧赔方向识别有效"],
            "what_failed": ["亚盘未升深的风险权重不足"],
            "risk_patterns": ["热门方向缺少升盘确认"],
            "next_actions": ["积累同类样本后再调整"]
          },
          "market_lessons": {
            "euro": "欧赔方向有效。",
            "asian": "未升深需要提高警惕。",
            "sporttery": "让平必须结合让球数。",
            "total": "大小球仅作辅助。",
            "consistency": "市场背离时降低置信度。"
          },
          "matches": [
            {
              "match_id": "201",
              "verdict": "判断有效",
              "diagnosis": "赛前证据支持平局，最终结果命中。",
              "correct_signals": ["平赔稳定"],
              "missed_signals": [],
              "data_quality_issues": [],
              "counterfactual": "继续等待多场验证。",
              "rule_tags": ["均势盘"]
            },
            {
              "match_id": "999",
              "verdict": "判断失误",
              "diagnosis": "不存在的比赛不应保留。"
            }
          ],
          "combination_review": {
            "conclusion": "组合样本仍少。",
            "good_choices": [],
            "bad_choices": [],
            "construction_advice": ["避免单一玩法过度集中"]
          },
          "learning_candidates": [
            {
              "scope": "asian",
              "target": "未升深风险",
              "action": "increase",
              "delta": 0.8,
              "confidence": "high",
              "minimum_samples": 2,
              "reason": "需要更多关注未升深。",
              "evidence_match_ids": ["201", "999"]
            },
            {
              "scope": "invented",
              "target": "非法范围",
              "action": "increase",
              "delta": 0.1,
              "reason": "应被过滤"
            }
          ]
        }""", {"response_id": "review-1", "usage": {"total_tokens": 100}}


def snapshot():
    return {
        "owner_date": "2026-07-18",
        "run_id": "run-1",
        "model": "ark-code-latest",
        "matches": [{
            "match_id": "201",
            "match_number": "周六201",
            "league": "测试",
            "home_team": "主队",
            "away_team": "客队",
            "analysis": {
                "verdict": "均势盘防平",
                "market_analysis": {"euro": "平赔稳定"},
                "evidence": ["平赔3.20"],
                "risks": [],
                "score_candidates": ["1:1"],
            },
            "input_snapshot": {
                "euro": {"current": [2.5, 3.2, 2.7]},
                "asian": {"current": [0.9, "平手", 0.9]},
                "sporttery_handicap": {
                    "value": -1,
                    "current": [3.5, 3.2, 1.8],
                },
                "total": {"current": [0.9, 2.5, 0.9]},
            },
        }],
    }


def review(status="hit", score="1:1"):
    return {
        "owner_date": "2026-07-18",
        "run_id": "run-1",
        "engine_version": "2.0.0",
        "completed": True,
        "match_results": [{
            "match_id": "201",
            "match_number": "周六201",
            "league": "测试",
            "home_team": "主队",
            "away_team": "客队",
            "selection": "平局",
            "selection_text": "平局",
            "rating": 4,
            "odds": 3.2,
            "status": status,
            "result_score": score,
            "return": 3.2 if status == "hit" else 0,
            "profit": 2.2 if status == "hit" else -1,
        }],
        "combo_results": [],
        "summary": {
            "singles": {"settled": 1, "hits": status == "hit"},
        },
    }


class FAEAIReviewAnalyzerTests(unittest.TestCase):
    def test_generates_normalized_candidate_only_review(self):
        client = FakeReviewClient()
        result = FAEAIReviewAnalyzer(client).analyze(snapshot(), review())

        self.assertEqual(client.calls, 1)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["matches"][0]["match_id"], "201")
        self.assertFalse(result["governance"]["formal_weights_changed"])
        self.assertEqual(len(result["learning_candidates"]), 1)
        candidate = result["learning_candidates"][0]
        self.assertEqual(candidate["delta"], 0.15)
        self.assertEqual(candidate["minimum_samples"], 10)
        self.assertEqual(candidate["evidence_match_ids"], ["201"])

    def test_hash_changes_only_when_review_input_changes(self):
        analyzer = FAEAIReviewAnalyzer(FakeReviewClient())
        first = analyzer.input_hash(snapshot(), review())
        second = analyzer.input_hash(snapshot(), review())
        changed = analyzer.input_hash(
            snapshot(), review(status="miss", score="0:1")
        )

        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)


if __name__ == "__main__":
    unittest.main()
