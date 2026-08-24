import unittest
from datetime import date, timedelta

from football_ai.daily_analysis import FAEDailyAIAnalyzer
from football_ai.supervised import (
    FEATURE_NAMES,
    FAESupervisedBacktestEngine,
    FAESupervisedPredictor,
    FAESupervisedTrainer,
    build_training_example,
    extract_prematch_features,
)


def prematch(match_id, owner_date, *, handicap=-1, league="测试联赛"):
    return {
        "match_id": str(match_id),
        "match_number": f"周六{int(match_id):03d}",
        "owner_date": owner_date,
        "match_time": f"{owner_date} 18:00",
        "league": league,
        "input_snapshot": {
            "euro": {
                "initial": [1.90, 3.30, 4.20],
                "current": [1.80, 3.20, 4.50],
            },
            "asian": {
                "initial": [0.88, -0.5, 0.98],
                "current": [0.94, -0.75, 0.92],
            },
            "sporttery_handicap": {
                "value": handicap,
                "initial": [2.20, 3.50, 2.65],
                "current": [2.30, 3.40, 2.55],
            },
            "total": {
                "initial": [0.90, 2.5, 0.94],
                "current": [0.96, 2.5, 0.88],
            },
            "rank": {"home": 4, "away": 11},
        },
    }


def examples(count=40, *, start="2026-07-01"):
    start_day = date.fromisoformat(start)
    rows = []
    score_cycle = [(1, 1), (2, 1), (1, 0), (0, 1), (2, 2)]
    for index in range(count):
        owner_date = (start_day + timedelta(days=index // 5)).isoformat()
        home_score, away_score = score_cycle[index % len(score_cycle)]
        rows.append(build_training_example(
            prematch(index + 1, owner_date),
            {
                "status": 2,
                "home_score": home_score,
                "away_score": away_score,
            },
            owner_date=owner_date,
        ))
    return rows


class FAESupervisedTests(unittest.TestCase):
    def test_labels_keep_result_out_of_feature_vector(self):
        self.assertIsNone(build_training_example(
            prematch(0, "2026-08-22", handicap=-1),
            {"status": 1, "home_score": 1, "away_score": 0},
            owner_date="2026-08-22",
        ))
        row = build_training_example(
            prematch(1, "2026-08-22", handicap=-1),
            {"status": 2, "home_score": 2, "away_score": 1},
            owner_date="2026-08-22",
        )

        self.assertEqual(len(row["feature_vector"]), len(FEATURE_NAMES))
        self.assertNotIn("home_score", row["features"])
        self.assertNotIn("away_score", row["features"])
        self.assertFalse(row["label"]["ordinary_draw"])
        self.assertTrue(row["label"]["handicap_draw"])
        self.assertEqual(row["label"]["goal_margin"], 1)

        away_row = build_training_example(
            prematch(2, "2026-08-22", handicap=1),
            {"status": 2, "home_score": 0, "away_score": 1},
            owner_date="2026-08-22",
        )
        self.assertTrue(away_row["label"]["handicap_draw"])
        self.assertEqual(away_row["label"]["goal_margin"], -1)

    def test_predictor_uses_frozen_features_and_exact_margin(self):
        artifact = FAESupervisedTrainer().fit(examples(), fast=True)
        predictor = FAESupervisedPredictor(artifact)
        frozen = build_training_example(
            prematch(99, "2026-08-23", handicap=-1),
            {"status": 2, "home_score": 1, "away_score": 0},
            owner_date="2026-08-23",
        )

        result = predictor.predict(
            frozen, owner_date="2026-08-23", daily_match_count=8
        )

        expected_market_draw = extract_prematch_features(
            prematch(99, "2026-08-23"), owner_date="2026-08-23"
        )["market"]["ordinary_draw_probability"] * 100
        self.assertAlmostEqual(
            result["ordinary_draw"]["market_probability"],
            expected_market_draw,
            places=2,
        )
        self.assertEqual(
            result["handicap_draw"]["target_goal_margin"], 1
        )
        self.assertIsNotNone(
            result["handicap_draw"]["conditional_exact_margin_probability"]
        )
        self.assertEqual(result["status"], "shadow")

    def test_weekend_large_pool_only_shrinks_ranking_probability(self):
        artifact = FAESupervisedTrainer().fit(examples(), fast=True)
        predictor = FAESupervisedPredictor(artifact)
        row = prematch(100, "2026-08-22")

        small = predictor.predict(
            row, owner_date="2026-08-22", daily_match_count=8
        )
        large = predictor.predict(
            row, owner_date="2026-08-22", daily_match_count=24
        )

        self.assertEqual(
            small["ordinary_draw"]["probability"],
            large["ordinary_draw"]["probability"],
        )
        self.assertLess(
            large["ordinary_draw"]["ranking_probability"],
            small["ordinary_draw"]["ranking_probability"],
        )
        self.assertGreater(
            large["ordinary_draw"]["candidate_pool_penalty_pp"], 0
        )

    def test_expanding_window_records_leakage_safe_cutoffs(self):
        rows = examples(60)
        days = []
        for owner_date in sorted({row["owner_date"] for row in rows}):
            days.append({
                "owner_date": owner_date,
                "examples": [
                    row for row in rows if row["owner_date"] == owner_date
                ],
            })

        result = FAESupervisedBacktestEngine(
            minimum_train_days=7,
            minimum_train_samples=35,
            retrain_interval_days=3,
        ).build(days)
        report = result["report"]

        self.assertTrue(report["tested_dates"])
        self.assertEqual(
            len(report["tested_dates"]), len(report["training_cutoffs"])
        )
        for cutoff in report["training_cutoffs"]:
            self.assertLess(
                cutoff["training_end_date"], cutoff["test_date"]
            )
        self.assertEqual(report["release_guard"]["status"], "shadow_only")
        self.assertFalse(
            result["model"]["governance"]["may_override_official_recommendations"]
        )

    def test_shadow_summary_is_ranked_but_never_actionable(self):
        matches = []
        for index, probability in enumerate((31, 29, 27, 25), start=1):
            matches.append({
                "match_id": str(index),
                "match_number": f"周六{index:03d}",
                "home_team": f"主{index}",
                "away_team": f"客{index}",
                "league": "测试联赛",
                "input_snapshot": {
                    "supervised_shadow": {
                        "model_id": "shadow-1",
                        "model_version": "test",
                        "status": "shadow",
                        "sample_count": 100,
                        "training_end_date": "2026-08-21",
                        "ordinary_draw": {
                            "probability": probability,
                            "ranking_probability": probability - 1,
                            "market_probability": 28,
                            "value_edge": 2,
                        },
                        "handicap_draw": {
                            "probability": probability - 2,
                            "ranking_probability": probability - 3,
                            "market_probability": 26,
                            "value_edge": 1,
                            "target_goal_margin": 1,
                        },
                    },
                },
            })

        summary = FAEDailyAIAnalyzer.attach_supervised_shadow_summary(
            {}, matches
        )["supervised_shadow"]

        self.assertEqual(len(summary["ordinary_draw"]), 3)
        self.assertEqual(summary["ordinary_draw"][0]["match_id"], "1")
        self.assertTrue(summary["combinations"])
        self.assertFalse(summary["combinations"][0]["actionable"])


if __name__ == "__main__":
    unittest.main()
