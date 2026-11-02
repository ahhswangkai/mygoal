import unittest
from copy import deepcopy
from datetime import date, timedelta
from unittest.mock import Mock, patch

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
    def test_old_pattern_survives_recent_seven_review_days(self):
        older = [
            review("2026-05-05", [f"old-a-{i}" for i in range(5)], target="长期亚盘风险"),
            review("2026-05-06", [f"old-b-{i}" for i in range(5)], target="长期亚盘风险"),
        ]
        recent = [
            review((date(2026, 10, 23) + timedelta(days=i)).isoformat(),
                   [f"new-{i}"], target=f"短期观察{i}")
            for i in range(10)
        ]
        memory = build_review_memory(older + recent, "2026-11-02")

        self.assertEqual(memory["history_review_days"], 12)
        self.assertEqual(memory["recent_review_days"], 7)
        self.assertEqual(memory["observation_count"], 3)
        self.assertEqual(memory["history_range"]["first_date"], "2026-05-05")
        self.assertNotIn("2026-05-05", memory["source_dates"])
        self.assertEqual(memory["validated_patterns"][0]["target"], "长期亚盘风险")
        self.assertFalse(memory["validated_patterns"][0]["predictive_validation"])

    def test_memory_strength_decays_but_old_evidence_is_retained(self):
        rows = [review("2026-07-17", [f"a-{i}" for i in range(5)]),
                review("2026-07-18", [f"b-{i}" for i in range(5)])]
        fresh = build_review_memory(rows, "2026-07-19")
        aged = build_review_memory(rows, "2026-10-17")
        fresh_pattern = fresh["validated_patterns"][0]
        aged_pattern = aged["validated_patterns"][0]
        self.assertEqual(aged_pattern["evidence_matches"], 10)
        self.assertLess(aged_pattern["effective_evidence"], fresh_pattern["effective_evidence"])
        self.assertLess(aged_pattern["suggested_delta"], fresh_pattern["suggested_delta"])

        dormant = build_review_memory(rows, "2027-06-01")
        self.assertEqual(dormant["validated_pattern_count"], 0)
        self.assertEqual(dormant["dormant_pattern_count"], 1)
        self.assertEqual(dormant["dormant_patterns"][0]["evidence_matches"], 10)

    def test_recent_opposing_evidence_suspends_strong_old_pattern(self):
        rows = [review("2026-08-01", [f"a-{i}" for i in range(50)]),
                review("2026-08-02", [f"b-{i}" for i in range(50)])]
        for day, prefix in (("2026-11-01", "c"), ("2026-11-02", "d")):
            opposing = review(day, [f"{prefix}-{i}" for i in range(5)])
            opposing["ai_deep_review"]["learning_candidates"][0].update(
                action="decrease", delta=-0.05
            )
            rows.append(opposing)
        memory = build_review_memory(rows, "2026-11-03", window_days=2)
        self.assertEqual(memory["validated_pattern_count"], 0)
        self.assertEqual(memory["contested_pattern_count"], 2)
        self.assertTrue(all(row["usage"] == "reference-only" for row in memory["contested_patterns"]))

    def test_reruns_and_repeated_fixture_evidence_cannot_inflate_patterns(self):
        ids = [str(i) for i in range(10)]
        rows = [review("2026-11-01", ids), review("2026-11-02", ids)]
        rows.append(deepcopy(rows[-1]))
        memory = build_review_memory(rows, "2026-11-03")
        self.assertEqual(memory["review_days"], 2)
        self.assertEqual(memory["validated_pattern_count"], 0)

    def test_unknown_fixture_ids_and_stale_diagnoses_do_not_validate_memory(self):
        rows = [review("2026-11-01", [str(i) for i in range(10)]),
                review("2026-11-02", [str(i + 10) for i in range(10)])]
        for row in rows:
            row["match_results"] = [{"match_id": "real-match"}]
        self.assertEqual(build_review_memory(rows, "2026-11-03")["validated_pattern_count"], 0)
        rows[0]["ai_deep_review_stale"] = True
        self.assertEqual(build_review_memory(rows, "2026-11-03")["review_days"], 1)

    def test_pending_latest_review_does_not_fall_back_to_older_diagnosis(self):
        previous = review("2026-11-01")
        previous["reviewed_at"] = "2026-11-02T01:00:00Z"
        latest = {**previous, "reviewed_at": "2026-11-02T02:00:00Z", "ai_deep_review": {}}
        memory = build_review_memory([previous, latest], "2026-11-03")
        self.assertEqual(memory["history_review_days"], 0)

    def test_lifetime_performance_uses_all_days_and_labels_recent_strength(self):
        rows = [review("2026-07-01"), review("2026-11-01")]
        for row, hits, payout in ((rows[0], 2, 5), (rows[1], 8, 20)):
            row["summary"]["singles"] = {
                "settled": 10, "hits": hits, "stake": 10, "return": payout,
            }
        memory = build_review_memory(rows, "2026-11-02", window_days=1)
        metric = memory["long_term_performance"]["singles"]
        self.assertEqual(metric["settled"], 20)
        self.assertEqual(metric["hit_rate"], 50)
        self.assertEqual(metric["roi"], 25)
        self.assertGreater(metric["weighted_hit_rate"], 50)
        self.assertEqual(memory["memory_hash"], build_review_memory(list(reversed(rows)), "2026-11-02", window_days=1)["memory_hash"])

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


class ReviewMemoryStorageTests(unittest.TestCase):
    def storage(self):
        from db_storage import MongoDBStorage
        storage = MongoDBStorage.__new__(MongoDBStorage)
        storage.logger = Mock()
        storage._latest_fae_daily_ai_reviews = Mock(return_value=[
            review("2026-07-01", [f"a-{i}" for i in range(5)]),
            review("2026-11-01", [f"b-{i}" for i in range(5)]),
        ])
        storage.fae_review_memories_collection = Mock()
        return storage

    def test_storage_loads_unlimited_history_and_persists_as_of_memory(self):
        storage = self.storage()
        with patch.dict("os.environ", {"FAE_REVIEW_MEMORY_DAYS": "7"}):
            memory = storage.get_fae_review_memory("2026-11-02")
        query = storage._latest_fae_daily_ai_reviews.call_args
        self.assertNotIn("limit", query.kwargs)
        self.assertEqual(query.args[0], {"owner_date": {"$lt": "2026-11-02"}})
        self.assertEqual(memory["history_review_days"], 2)
        saved = storage.fae_review_memories_collection.update_one.call_args
        self.assertEqual(saved.args[0], {"memory_hash": memory["memory_hash"]})
        self.assertEqual(saved.args[1]["$set"]["before_date"], "2026-11-02")

    def test_cache_write_failure_does_not_remove_historical_context(self):
        storage = self.storage()
        storage.fae_review_memories_collection.update_one.side_effect = RuntimeError("read-only cache")
        memory = storage.get_fae_review_memory("2026-11-02")
        self.assertEqual(memory["history_review_days"], 2)
        storage.logger.warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
