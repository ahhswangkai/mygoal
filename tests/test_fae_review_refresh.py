"""Exercise review scheduling without starting the app or external services."""

import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call


def load_review_jobs():
    source = Path(__file__).resolve().parents[1] / "web_app.py"
    tree = ast.parse(source.read_text())
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"_run_scheduled_fae_reviews", "_start_scheduler"}
    ]
    namespace = {"datetime": datetime, "timedelta": timedelta}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
    return namespace


class ReviewRefreshTests(unittest.TestCase):
    def test_one_failed_date_does_not_stop_other_reviews_or_draw_settlement(self):
        jobs = load_review_jobs()
        storage = Mock()
        storage.get_fae_daily_ai_snapshot_dates.return_value = ["2026-11-01", "2026-11-02"]
        storage.get_fae_draw_snapshot_dates.return_value = ["2026-11-01"]
        review = Mock(side_effect=[RuntimeError("one bad snapshot"), {"run_id": "older"}])
        draw_review = Mock()
        push = Mock()
        jobs.update({"mongo_storage": storage, "_review_fae_daily_ai": review,
                     "_review_fae_draw_plan": draw_review, "_push_wecom_ai_review": push,
                     "app": SimpleNamespace(logger=Mock())})

        result = jobs["_run_scheduled_fae_reviews"]()

        self.assertEqual(result, {"reviewed": 1, "failed": ["2026-11-02"]})
        self.assertEqual(review.call_args_list, [
            call("2026-11-02", run_ai=False), call("2026-11-01", run_ai=False),
        ])
        draw_review.assert_called_once_with("2026-11-01")
        push.assert_not_called()

    def test_deep_review_remains_separate_from_free_settlement(self):
        jobs = load_review_jobs()
        storage = Mock()
        storage.get_fae_daily_ai_snapshot_dates.return_value = ["2026-11-02"]
        review = Mock(return_value={"run_id": "latest"})
        push = Mock()
        jobs.update({"mongo_storage": storage, "_review_fae_daily_ai": review,
                     "_push_wecom_ai_review": push, "app": SimpleNamespace(logger=Mock())})

        jobs["_run_scheduled_fae_reviews"](run_ai=True)

        review.assert_called_once_with("2026-11-02", run_ai=True)
        storage.get_fae_draw_snapshot_dates.assert_not_called()
        push.assert_called_once_with({"run_id": "latest"})

    def test_scheduler_registers_review_jobs_independently_of_crawling(self):
        jobs = load_review_jobs()
        scheduler = Mock()
        jobs.update({
            "scheduler": None, "BackgroundScheduler": Mock(return_value=scheduler),
            "CronTrigger": lambda **kwargs: kwargs,
            "os": SimpleNamespace(getenv=lambda key, default=None: default),
            "_env_enabled": lambda key, default=False: default,
            "print": Mock(),
        })
        for name in ("_crawl_latest", "_run_scheduled_fundamentals_prefetch",
                     "_settle_pending_calculator_bets", "_run_scheduled_fae_prematch_ai",
                     "_run_scheduled_fae_shadow_backtest", "_run_scheduled_fae_supervised_training"):
            jobs[name] = Mock()
        jobs["_start_scheduler"]()

        registered = {item.kwargs["id"]: item for item in scheduler.add_job.call_args_list}
        settlement = registered["fae_review_settlement"]
        deep = registered["fae_ai_deep_review"]
        self.assertIs(settlement.args[0], jobs["_run_scheduled_fae_reviews"])
        self.assertEqual(settlement.args[1], {"minute": "*/5"})
        self.assertEqual(settlement.kwargs["max_instances"], 1)
        self.assertEqual(deep.kwargs["kwargs"], {"run_ai": True})
        self.assertNotEqual(settlement.args[0], registered["crawl_every_15m"].args[0])
        scheduler.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
