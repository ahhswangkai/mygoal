import unittest

from football_ai.skills import (
    baseline_skill_documents,
    build_draw_skill_candidate,
    build_rule_skill_candidate,
    next_patch_version,
)


class FAESkillCandidateTests(unittest.TestCase):
    def setUp(self):
        self.skills = {
            item["skill_id"]: item
            for item in baseline_skill_documents()
        }

    def test_baseline_registry_contains_versioned_active_skills(self):
        self.assertIn("asian-handicap", self.skills)
        self.assertIn("draw-strategy", self.skills)
        self.assertEqual(self.skills["euro-odds"]["version"], "1.0.0")
        self.assertEqual(self.skills["euro-odds"]["status"], "active")
        self.assertEqual(next_patch_version("1.2.9"), "1.2.10")

    def test_rule_candidate_requires_enough_new_samples(self):
        active = self.skills["euro-odds"]

        missing = build_rule_skill_candidate(
            active,
            [{"rule_id": "euro-home-support", "samples": 9, "hits": 9}],
        )

        self.assertIsNone(missing)

        candidate = build_rule_skill_candidate(
            active,
            [{"rule_id": "euro-home-support", "samples": 10, "hits": 9}],
        )

        self.assertEqual(candidate["status"], "validated")
        self.assertEqual(candidate["proposed_version"], "1.0.1")
        self.assertEqual(
            candidate["parameters"]["rule_weights"]["euro-home-support"], 1.05
        )
        self.assertGreater(candidate["evaluation"]["improvement"], 0)

    def test_published_snapshot_prevents_reusing_same_evidence(self):
        active = {
            **self.skills["euro-odds"],
            "learning_snapshot": {"euro-home-support": 10},
        }

        candidate = build_rule_skill_candidate(
            active,
            [{"rule_id": "euro-home-support", "samples": 15, "hits": 14}],
            minimum_new_samples=10,
        )

        self.assertIsNone(candidate)

    def test_low_accuracy_rule_is_proposed_for_lower_weight(self):
        active = self.skills["risk-control"]

        candidate = build_rule_skill_candidate(
            active,
            [{"rule_id": "hot-overheat", "samples": 10, "hits": 4}],
        )

        self.assertEqual(
            candidate["parameters"]["rule_weights"]["hot-overheat"], 0.95
        )
        self.assertEqual(candidate["changes"][0]["action"], "decrease")

    def test_draw_strategy_candidate_uses_roi_and_new_sample_gate(self):
        active = self.skills["draw-strategy"]
        candidate = build_draw_skill_candidate(
            active,
            {
                "平局": {"settled": 12, "hits": 5, "hit_rate": 41.7, "roi": 18},
                "让平": {"settled": 11, "hits": 2, "hit_rate": 18.2, "roi": -14},
            },
        )

        weights = candidate["parameters"]["strategy_weights"]
        self.assertEqual(weights["平局"], 1.05)
        self.assertEqual(weights["让平"], 0.95)
        self.assertTrue(candidate["evaluation"]["passed"])


if __name__ == "__main__":
    unittest.main()
