import unittest

from football_ai.backtest import FAEShadowBacktestEngine


def shadow_match(
    match_id,
    primary,
    secondary,
    probabilities,
    odds,
    *,
    match_time,
):
    source = {
        "sporttery_handicap": {
            "value": -1,
            "current": list(odds),
        },
        "fae_core": {
            "probabilities": {"hhad": probabilities},
            "risk": {"dangerous": False},
        },
    }
    return {
        "match_id": str(match_id),
        "match_number": f"周一{match_id}",
        "match_time": match_time,
        "league": "测试联赛",
        "home_team": f"主队{match_id}",
        "away_team": f"客队{match_id}",
        "analysis_source": "volcengine-ark",
        "analysis": {
            "primary_play": primary,
            "secondary_play": secondary,
            "market_confidence": {"score": 80},
            "no_bet": True,
        },
        "input_snapshot": source,
    }


class FAEShadowBacktestTests(unittest.TestCase):
    def test_candidate_filters_close_coverage_value_trap(self):
        snapshot = {
            "owner_date": "2026-08-20",
            "run_id": "run-shadow-1",
            "matches": [
                shadow_match(
                    "001",
                    "让胜",
                    "让平",
                    {"win": 46, "draw": 32, "lose": 22},
                    (2.10, 3.65, 2.72),
                    match_time="18:00",
                ),
                shadow_match(
                    "002",
                    "让负",
                    "让平",
                    {"win": 31, "draw": 31, "lose": 38},
                    (2.50, 3.32, 2.34),
                    match_time="19:00",
                ),
            ],
        }
        results = {
            # -1 后均为让平；001 被两版覆盖，002 仅候选版过滤。
            "001": {"status": 2, "home_score": 1, "away_score": 0},
            "002": {"status": 2, "home_score": 2, "away_score": 1},
        }

        report = FAEShadowBacktestEngine().build([{
            "snapshot": snapshot,
            "results": results,
        }])

        self.assertEqual(report["baseline"]["settled"], 2)
        self.assertEqual(report["baseline"]["hits"], 1)
        self.assertEqual(report["candidate"]["settled"], 1)
        self.assertEqual(report["candidate"]["hits"], 1)
        self.assertEqual(report["comparison"]["removed_count"], 1)
        self.assertEqual(
            report["comparison"]["removed"][0]["match_id"], "002"
        )
        self.assertEqual(report["release_guard"]["status"], "shadow_only")
        self.assertFalse(report["release_guard"]["can_promote"])
        self.assertEqual(report["validation"]["candidate"]["settled"], 0)

    def test_release_guard_requires_samples_days_roi_and_drawdown(self):
        baseline = {
            "settled": 40,
            "review_days": 8,
            "roi": 4,
            "hit_rate": 90,
            "max_drawdown_units": 4,
        }
        candidate = {
            "settled": 35,
            "review_days": 8,
            "roi": 7,
            "hit_rate": 92,
            "max_drawdown_units": 3,
        }

        guard = FAEShadowBacktestEngine._release_guard(
            baseline, candidate
        )

        self.assertTrue(guard["can_promote"])
        self.assertEqual(guard["status"], "eligible")


if __name__ == "__main__":
    unittest.main()
