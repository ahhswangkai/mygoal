import unittest

from football_ai.draw_review import FAEDrawReviewEngine, aggregate_draw_reviews


class DrawReviewTests(unittest.TestCase):
    def test_settles_draw_handicap_draw_and_combinations(self):
        picks = [
            {
                "match_id": "1", "selection": "平局", "odds": 3.2,
                "match_number": "周六201",
            },
            {
                "match_id": "2", "selection": "让平", "handicap": -1,
                "odds": 3.5, "match_number": "周六202",
            },
            {
                "match_id": "3", "selection": "平局", "odds": 3.1,
                "match_number": "周六203",
            },
            {
                "match_id": "4", "selection": "让平", "handicap": 1,
                "odds": 3.3, "match_number": "周六204",
            },
        ]
        snapshot = {
            "date": "2026-07-18",
            "engine_version": "2.0.0",
            "snapshot_hash": "test-snapshot",
            "generated_at": "2026-07-18T00:00:00Z",
            "match_recommendations": picks,
            "two_leg": [
                {
                    "play": "2串1", "legs": 2,
                    "picks": picks[:2], "combined_odds": 11.2,
                },
                {
                    "play": "2串1", "legs": 2,
                    "picks": [picks[0], picks[2]], "combined_odds": 9.92,
                },
            ],
            "three_leg": [{
                "play": "3串1", "legs": 3,
                "picks": [picks[0], picks[1], picks[3]],
                "combined_odds": 36.96,
            }],
        }
        matches = {
            "1": {"status": 2, "home_score": 1, "away_score": 1},
            "2": {"status": 2, "home_score": 2, "away_score": 1},
            "3": {"status": 2, "home_score": 1, "away_score": 0},
            "4": {"status": 0, "home_score": None, "away_score": None},
        }

        review = FAEDrawReviewEngine().review(snapshot, matches)

        self.assertFalse(review["completed"])
        self.assertEqual(review["summary"]["singles"]["settled"], 3)
        self.assertEqual(review["summary"]["singles"]["hits"], 2)
        self.assertEqual(review["summary"]["by_selection"]["让平"]["hits"], 1)
        self.assertEqual(review["summary"]["by_play"]["2串1"]["settled"], 2)
        self.assertEqual(review["summary"]["by_play"]["2串1"]["hits"], 1)
        self.assertEqual(review["summary"]["by_play"]["3串1"]["pending"], 1)
        self.assertEqual(review["combo_results"][0]["return"], 11.2)

        stats = aggregate_draw_reviews([review], {"平局": {"weight": 1.0}})
        self.assertEqual(stats["reviewed_days"], 1)
        self.assertEqual(stats["singles"]["hits"], 2)


if __name__ == "__main__":
    unittest.main()
