import unittest

from wecom_notifier import (
    WeComNotifier,
    delivery_key,
    format_daily_ai_message,
    format_live_alert_message,
    format_review_message,
)


class FakeResponse:
    status_code = 200

    def json(self):
        return {"errcode": 0, "errmsg": "ok"}


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse()


class WeComNotifierTests(unittest.TestCase):
    def test_rejects_missing_or_non_wecom_webhook(self):
        self.assertFalse(WeComNotifier("").configured)
        self.assertFalse(
            WeComNotifier("https://example.com/hook?key=secret").configured
        )

    def test_sends_markdown_and_checks_errcode(self):
        session = FakeSession()
        notifier = WeComNotifier(
            "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test-key",
            session=session,
        )

        result = notifier.send_markdown("测试消息")

        self.assertTrue(result["success"])
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(
            session.calls[0][1]["json"]["msgtype"], "markdown"
        )

    def test_daily_message_uses_match_number_not_raw_id(self):
        message = format_daily_ai_message({
            "owner_date": "2026-07-20",
            "generated_at": "2026-07-20T11:15:31+00:00",
            "match_count": 1,
            "daily_summary": {"core_conclusion": "测试结论"},
            "matches": [{
                "match_id": "1362704",
                "match_number": "周一001",
                "analysis": {
                    "primary_play": "让平",
                    "rating": 4,
                    "odds": 3.5,
                    "no_bet": False,
                },
            }],
        })

        self.assertIn("周一001", message)
        self.assertNotIn("1362704", message)
        self.assertIn("研判时间：2026-07-20 19:15（北京时间）", message)
        self.assertEqual(
            delivery_key("daily_ai", "run-1"),
            delivery_key("daily_ai", "run-1"),
        )

    def test_review_message_contains_settlement_and_deep_review(self):
        message = format_review_message({
            "owner_date": "2026-07-19",
            "summary": {"singles": {"settled": 4, "hits": 3}},
            "ai_deep_review": {
                "summary": {
                    "conclusion": "市场一致性筛选有效。",
                    "what_failed": ["退盘场次评级偏高"],
                    "next_actions": ["继续降低欧亚背离场次权重"],
                },
            },
        })

        self.assertIn("3/4", message)
        self.assertIn("75%", message)
        self.assertIn("退盘场次评级偏高", message)

    def test_live_alert_message_is_human_readable(self):
        message = format_live_alert_message([{
            "match_id": "1362704",
            "match_number": "周一001",
            "match_time": "2026-07-20 19:30",
            "home_team": "主队",
            "away_team": "客队",
            "euro_current_draw": 3.2,
            "hi_handicap_value": "平手",
        }])

        self.assertIn("周一001", message)
        self.assertIn("19:30", message)
        self.assertNotIn("1362704", message)


if __name__ == "__main__":
    unittest.main()
