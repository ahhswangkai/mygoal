import unittest
from unittest.mock import Mock

from db_storage import MongoDBStorage


class DailySnapshotStorageTests(unittest.TestCase):
    def setUp(self):
        self.storage = MongoDBStorage.__new__(MongoDBStorage)
        self.storage.logger = Mock()
        self.storage.fae_daily_ai_runs_collection = Mock()
        self.storage.fae_daily_ai_matches_collection = Mock()

        cursor = Mock()
        cursor.sort.return_value = [{"match_id": "201"}]
        self.storage.fae_daily_ai_matches_collection.find.return_value = cursor
        self.storage.fae_daily_ai_runs_collection.find_one.return_value = {
            "run_id": "latest-run",
            "owner_date": "2026-08-29",
        }

    def test_review_snapshot_can_select_absolute_latest_run(self):
        snapshot = self.storage.get_fae_daily_ai_snapshot(
            "2026-08-29", latest=True
        )

        self.assertEqual(snapshot["run_id"], "latest-run")
        query = self.storage.fae_daily_ai_runs_collection.find_one.call_args.args[0]
        self.assertEqual(query, {"owner_date": "2026-08-29"})

    def test_historical_snapshot_still_requires_pregame_run(self):
        self.storage.get_fae_daily_ai_snapshot("2026-08-29")

        query = self.storage.fae_daily_ai_runs_collection.find_one.call_args.args[0]
        self.assertEqual(query, {
            "owner_date": "2026-08-29",
            "eligible_for_review": True,
        })

    def test_review_date_scan_can_include_incremental_only_days(self):
        self.storage.fae_daily_ai_runs_collection.distinct.return_value = [
            "2026-08-28", "2026-08-29"
        ]

        dates = self.storage.get_fae_daily_ai_snapshot_dates(
            14, eligible_only=False
        )

        self.assertEqual(dates, ["2026-08-28", "2026-08-29"])
        self.storage.fae_daily_ai_runs_collection.distinct.assert_called_once_with(
            "owner_date", {}
        )


if __name__ == "__main__":
    unittest.main()
