import unittest

from football_ai.review_memory import build_review_memory


def review(day, evidence_ids=None, *, target="未升深风险"):
    candidate = {
        "scope": "asian",
        "target": target,
        "action": "increase",
        "delta": 0.05,
        "minimum_samples": 10,
        "reason": "未升深的热门方向需要降低置信度。",
        "evidence_match_ids": evidence_ids or [],
    }
    return {
        "owner_date": day,
        "run_id": f"run-{day}",
        "summary": {
            "singles": {
                "settled": 12,
                "hit_rate": 50,
                "roi": -10,
            }
        },
        "ai_deep_review": {
            "status": "completed",
            "summary": {
                "conclusion": f"{day}复盘结论",
                "what_failed": ["热门方向没有真实升深"],
                "risk_patterns": ["欧赔下降但亚盘未升"],
                "next_actions": ["同类比赛降低置信度"],
            },
            "market_lessons": {
                "asian": "盘口未升深时不要仅凭降水加强推荐。"
            },
            "learning_candidates": [candidate],
        },
    }


class ReviewMemoryTests(unittest.TestCase):
    def test_single_day_is_observation_not_validated_pattern(self):
        memory = build_review_memory(
            [review("2026-07-18", [str(index) for index in range(10)])],
            "2026-07-19",
        )

        self.assertEqual(memory["review_days"], 1)
        self.assertEqual(memory["observation_count"], 1)
        self.assertEqual(memory["validated_pattern_count"], 0)
        self.assertTrue(memory["governance"]["observations_are_non_binding"])
        self.assertFalse(memory["recent_observations"][0]["binding"])
        self.assertTrue(
            memory["governance"]["absolute_exclusions_forbidden"]
        )

    def test_recurring_cross_day_pattern_with_evidence_is_validated(self):
        memory = build_review_memory(
            [
                review("2026-07-17", [f"17-{index}" for index in range(5)]),
                review("2026-07-18", [f"18-{index}" for index in range(5)]),
            ],
            "2026-07-19",
        )

        self.assertEqual(memory["validated_pattern_count"], 1)
        pattern = memory["validated_patterns"][0]
        self.assertEqual(pattern["observed_days"], 2)
        self.assertEqual(pattern["evidence_matches"], 10)
        self.assertEqual(pattern["status"], "historically-validated-memory")

    def test_current_and_future_reviews_are_excluded(self):
        memory = build_review_memory(
            [
                review("2026-07-18", ["old"]),
                review("2026-07-19", ["current"]),
                review("2026-07-20", ["future"]),
            ],
            "2026-07-19",
        )

        self.assertEqual(memory["source_dates"], ["2026-07-18"])
        self.assertTrue(memory["governance"]["future_data_excluded"])


if __name__ == "__main__":
    unittest.main()
