import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import web_app
from crawler import OkoooAccessVerificationError


def fundamentals(match_id, cached_at=None):
    return {
        'match_id': str(match_id),
        'source': '澳客',
        'cached_at': cached_at or datetime.now(),
        'recent': {'home': [{'score': '1-0'}], 'away': []},
        'history': [],
        'standings': [],
    }


class FakeStorage:
    def __init__(self, matches=None, entries=None):
        self.matches = matches or []
        self.entries = entries or {}
        self.failures = []

    def get_matches(self, filters=None):
        return list(self.matches)

    def get_match_fundamentals(self, match_id):
        return (self.entries.get(str(match_id)) or {}).get('data') or {}

    def get_match_fundamentals_bulk(self, match_ids):
        return {
            str(match_id): (self.entries.get(str(match_id)) or {}).get('data')
            for match_id in match_ids
            if (self.entries.get(str(match_id)) or {}).get('data')
        }

    def get_match_fundamentals_entries_bulk(self, match_ids):
        return {
            str(match_id): self.entries[str(match_id)]
            for match_id in match_ids
            if str(match_id) in self.entries
        }

    def save_match_fundamentals(self, match_id, data):
        payload = dict(data)
        payload['cached_at'] = datetime.now()
        self.entries[str(match_id)] = {
            'match_id': str(match_id),
            'data': payload,
            'failure_count': 0,
        }
        return True

    def save_match_fundamentals_failure(
        self, match_id, error, cooldown_minutes=60
    ):
        self.failures.append((str(match_id), str(error), cooldown_minutes))
        self.entries[str(match_id)] = {
            'match_id': str(match_id),
            'failure_count': 1,
            'next_retry_at': datetime.now() + timedelta(minutes=cooldown_minutes),
        }
        return True


class FundamentalsCacheTests(unittest.TestCase):
    def setUp(self):
        self.original_storage = web_app.mongo_storage
        self.original_blocked_until = web_app.fundamentals_upstream_blocked_until
        web_app.fundamentals_upstream_blocked_until = None

    def tearDown(self):
        web_app.mongo_storage = self.original_storage
        web_app.fundamentals_upstream_blocked_until = self.original_blocked_until

    def test_stale_success_cache_is_served_without_upstream_fetch(self):
        stale = fundamentals('18', datetime.now() - timedelta(days=2))
        web_app.mongo_storage = FakeStorage(entries={
            '18': {'match_id': '18', 'data': stale}
        })
        source = Mock()

        result = web_app._get_match_fundamentals(
            {'match_id': '18'}, source_crawler=source
        )

        self.assertEqual(result['cache_status'], 'stale')
        source.crawl_okooo_fundamentals.assert_not_called()

    def test_daily_loader_never_fans_out_uncached_upstream_requests(self):
        web_app.mongo_storage = FakeStorage(entries={
            '1': {'match_id': '1', 'data': fundamentals('1')}
        })

        result = web_app._load_daily_fundamentals([
            {'match_id': '1'}, {'match_id': '2'}
        ])

        self.assertEqual(set(result), {'1'})

    def test_waf_failure_creates_persistent_and_global_cooldown(self):
        storage = FakeStorage()
        web_app.mongo_storage = storage
        source = Mock()
        source.crawl_okooo_fundamentals.side_effect = (
            OkoooAccessVerificationError('澳客要求滑动访问验证')
        )

        with self.assertRaises(OkoooAccessVerificationError):
            web_app._fetch_and_cache_match_fundamentals(
                {'match_id': '18'}, source_crawler=source
            )

        self.assertEqual(storage.failures[0][0], '18')
        self.assertGreaterEqual(storage.failures[0][2], 60)
        self.assertFalse(web_app._fundamentals_upstream_available())

    @patch.dict(os.environ, {
        'FAE_FUNDAMENTALS_BATCH_SIZE': '2',
        'FAE_FUNDAMENTALS_REQUEST_INTERVAL_SECONDS': '0',
    })
    def test_background_prefetch_only_processes_bounded_batch(self):
        matches = [
            {
                'match_id': str(index),
                'match_time': f'09-05 1{index}:00',
                'match_number': f'周六00{index}',
                'owner_date': '2026-09-05',
                'status': 0,
            }
            for index in range(1, 5)
        ]
        storage = FakeStorage(matches=matches)
        web_app.mongo_storage = storage

        class FakeCrawler:
            calls = []

            def crawl_okooo_fundamentals(self, match):
                self.calls.append(match['match_id'])
                return fundamentals(match['match_id'])

            def close(self):
                return None

        with patch('web_app.FootballCrawler', FakeCrawler):
            result = web_app._run_scheduled_fundamentals_prefetch()

        self.assertEqual(result['fetched'], 2)
        self.assertEqual(FakeCrawler.calls, ['1', '2'])
        self.assertEqual(result['pending'], 2)


if __name__ == '__main__':
    unittest.main()
