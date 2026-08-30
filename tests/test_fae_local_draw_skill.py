import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills/fae-draw-handicap-draw/scripts/local_select.py"
)
SPEC = importlib.util.spec_from_file_location(
    "fae_local_draw_skill", SCRIPT_PATH
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class LocalDrawSkillMemoryTests(unittest.TestCase):
    def test_validated_risk_memory_applies_small_capped_penalty(self):
        memory = {
            "memory_hash": "memory-1",
            "validated_patterns": [{
                "scope": "asian",
                "target": "未升深风险",
                "action": "increase",
                "suggested_delta": 0.05,
                "observed_days": 2,
                "evidence_matches": 12,
                "reason": "降水未升深的热门方向需要降权",
                "status": "historically-validated-memory",
            }],
        }
        candidate = {
            "match_id": "1",
            "selection": "让平",
            "tier": "core",
            "probability": 32.0,
            "odds": 3.5,
            "odds_value": 12.0,
            "score": 85.0,
            "risk_pattern_ids": ["water_drop_without_deepen"],
            "draw_odds_band_signal": {},
            "reason": "降水但没有真实升深。",
        }
        rows = [{
            "analysis": {
                "draw_radar": {
                    "ordinary_draw": {},
                    "handicap_draw": candidate,
                }
            }
        }]

        result = MODULE.apply_review_memory_to_rows(rows, memory)
        adjusted = result[0]["analysis"]["draw_radar"]["handicap_draw"]

        self.assertEqual(adjusted["probability"], 31.0)
        self.assertEqual(adjusted["score"], 83.0)
        self.assertEqual(
            adjusted["review_memory"]["probability_adjustment_pp"], -1.0
        )
        self.assertEqual(
            adjusted["review_memory"]["score_adjustment"], -2.0
        )

    def test_unvalidated_observation_never_changes_candidate(self):
        memory = {
            "memory_hash": "memory-2",
            "validated_patterns": [],
            "recent_observations": [{
                "date": "2026-08-29",
                "status": "unvalidated-observation",
                "binding": False,
            }],
        }
        candidate = {
            "match_id": "2",
            "selection": "平局",
            "tier": "watch",
            "probability": 30.0,
            "odds": 3.0,
            "odds_value": -10.0,
            "score": 75.0,
        }
        rows = [{
            "analysis": {
                "draw_radar": {
                    "ordinary_draw": candidate,
                    "handicap_draw": {},
                }
            }
        }]

        result = MODULE.apply_review_memory_to_rows(rows, memory)
        adjusted = result[0]["analysis"]["draw_radar"]["ordinary_draw"]

        self.assertEqual(adjusted["probability"], 30.0)
        self.assertEqual(adjusted["score"], 75.0)
        self.assertEqual(
            adjusted["review_memory"]["matched_validated_patterns"], []
        )

    def test_memory_input_supports_offline_snapshot(self):
        payload = {
            "version": "review-memory-v2-sample-governance",
            "before_date": "2026-08-30",
            "review_days": 7,
            "validated_patterns": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            memory = MODULE.load_review_memory(
                "2026-08-30",
                "https://example.invalid",
                1.0,
                memory_input=str(path),
            )

        self.assertEqual(memory["review_days"], 7)
        self.assertEqual(memory["local_source"], "input")


if __name__ == "__main__":
    unittest.main()
