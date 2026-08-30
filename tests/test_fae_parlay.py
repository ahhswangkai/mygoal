import unittest

from football_ai.parlay import build_draw_parlays


def ranking_row(match_id, match_number, category, probability, odds, score=68):
    return {
        "match_id": match_id,
        "match_number": match_number,
        "home_team": f"主队{match_id}",
        "away_team": f"客队{match_id}",
        "recommendation": category,
        "probability": probability,
        "odds": odds,
        "score": score,
        "handicap": -1 if category == "让平" else None,
        "risk": {"score": 20},
    }


class DrawParlayTests(unittest.TestCase):
    def test_builds_all_match_choices_and_distinct_2_3_leg_groups(self):
        rankings = {
            "date": "2026-07-18",
            "engine_version": "2.0.0",
            "groups": {
                "平局": [
                    ranking_row(str(i), f"周六20{i}", "平局", 36 + i, 3.2 + i / 10)
                    for i in range(1, 5)
                ],
                "让平": [
                    ranking_row(str(i), f"周六20{i}", "让平", 34 + i, 3.5 + i / 10, 78)
                    for i in range(1, 5)
                ],
            },
        }

        result = build_draw_parlays(rankings)

        self.assertEqual(result["match_count"], 4)
        self.assertEqual(len(result["two_leg"]), 5)
        self.assertEqual(len(result["three_leg"]), 4)
        for combo in result["two_leg"] + result["three_leg"]:
            match_ids = [item["match_id"] for item in combo["picks"]]
            self.assertEqual(len(match_ids), len(set(match_ids)))
            self.assertGreater(combo["combined_odds"], 1)
        self.assertTrue(
            any(
                item["selection_text"].startswith("让平(-1)")
                for item in result["match_recommendations"]
            )
        )


if __name__ == "__main__":
    unittest.main()
