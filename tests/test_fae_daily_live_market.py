import unittest

from web_app import _daily_live_market_payload


class DailyLiveMarketPayloadTests(unittest.TestCase):
    def test_exposes_current_handicap_ratio_without_changing_snapshot(self):
        payload = _daily_live_market_payload({
            "betting_ratio": {
                "fetched_at": "2026-09-08T13:16:12",
                "handicap": {
                    "draw_support_rate": 32.0,
                },
            },
            "hi_handicap_value": -1,
            "hi_current_home_odds": "3.75",
            "hi_current_draw_odds": "3.70",
            "hi_current_away_odds": "1.69",
        })

        self.assertEqual(
            payload["betting_ratio"]["handicap"]["draw_support_rate"],
            32.0,
        )
        self.assertEqual(
            payload["sporttery_handicap"]["current"],
            ["3.75", "3.70", "1.69"],
        )


if __name__ == "__main__":
    unittest.main()
