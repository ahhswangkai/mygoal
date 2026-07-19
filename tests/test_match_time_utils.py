import unittest
from datetime import datetime

from match_time_utils import parse_match_datetime, sort_matches_by_datetime


class MatchTimeUtilsTests(unittest.TestCase):
    def test_short_match_time_uses_owner_year(self):
        parsed = parse_match_datetime({
            'owner_date': '2025-12-31',
            'match_time': '12-31 23:25',
        })
        self.assertEqual(parsed, datetime(2025, 12, 31, 23, 25))

    def test_short_match_time_handles_new_year_rollover(self):
        parsed = parse_match_datetime({
            'owner_date': '2025-12-31',
            'match_time': '01-01 00:30',
        })
        self.assertEqual(parsed, datetime(2026, 1, 1, 0, 30))

    def test_results_sort_by_complete_datetime_descending(self):
        matches = [
            {
                'match_id': 'old',
                'owner_date': '2025-12-31',
                'match_time': '12-31 23:25',
            },
            {
                'match_id': 'latest',
                'owner_date': '2026-07-19',
                'match_time': '07-20 03:00',
            },
            {
                'match_id': 'recent',
                'owner_date': '2026-07-19',
                'match_time': '07-19 18:30',
            },
        ]
        ordered = sort_matches_by_datetime(matches, descending=True)
        self.assertEqual(
            [match['match_id'] for match in ordered],
            ['latest', 'recent', 'old'],
        )

    def test_missing_time_is_always_last(self):
        matches = [
            {'match_id': 'missing'},
            {
                'match_id': 'known',
                'owner_date': '2026-07-19',
                'match_time': '07-19 18:30',
            },
        ]
        self.assertEqual(
            sort_matches_by_datetime(matches)[-1]['match_id'],
            'missing',
        )
        self.assertEqual(
            sort_matches_by_datetime(matches, descending=True)[-1]['match_id'],
            'missing',
        )


if __name__ == '__main__':
    unittest.main()
