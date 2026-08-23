import os
import tempfile
import unittest

from calculator_math import calculate_max_bonus, calculate_notes
from bet_settlement import (
    available_bet_results,
    candidate_result_dates,
    grade_item,
    merge_database_results,
    merge_rescheduled_void_results,
    settle_bet,
)
from user_storage import UserStorage


def result(match_id, full='2:1', half='1:0', status='2'):
    return {
        'matchId': match_id,
        'matchResultStatus': status,
        'sectionsNo1': half,
        'sectionsNo999': full,
        'poolStatus': 'Payout',
    }


def item(match_id, pool, opt, odd=2.0, handicap=0):
    return {
        'match_id': str(match_id),
        'pool': pool,
        'pool_name': pool,
        'opt': opt,
        'label': opt,
        'odd': odd,
        'match_num': '周五201',
        'date': '2026-07-17',
        'handicap': handicap,
    }


class ItemGradingTests(unittest.TestCase):
    def test_result_dates_include_business_date_next_day(self):
        self.assertEqual(
            candidate_result_dates(['2026-07-16']),
            ['2026-07-16', '2026-07-17'],
        )

    def test_all_supported_pools(self):
        match_result = result('1', full='2:1', half='1:0')
        winning_items = [
            item('1', 'had', 'win'),
            item('1', 'hhad', 'draw', handicap=-1),
            item('1', 'score', '2:1'),
            item('1', 'goals', '3'),
            item('1', 'hafu', '胜胜'),
        ]
        for selected in winning_items:
            with self.subTest(pool=selected['pool']):
                self.assertEqual(grade_item(selected, match_result), 'win')

        self.assertEqual(grade_item(item('1', 'had', 'draw'), match_result), 'lose')
        self.assertEqual(
            grade_item(item('1', 'hhad', 'lose', handicap=-1), match_result),
            'lose',
        )

    def test_other_score_and_seven_plus(self):
        self.assertEqual(
            grade_item(item('1', 'score', '胜其他'), result('1', full='6:2')),
            'win',
        )
        self.assertEqual(
            grade_item(item('1', 'score', '平其他'), result('1', full='4:4')),
            'win',
        )
        self.assertEqual(
            grade_item(item('1', 'score', '负其他'), result('1', full='2:6')),
            'win',
        )
        self.assertEqual(
            grade_item(item('1', 'goals', '7+'), result('1', full='5:2')),
            'win',
        )

    def test_unfinished_result_stays_pending(self):
        self.assertEqual(
            grade_item(item('1', 'had', 'win'), result('1', status='1')),
            'pending',
        )

    def test_cancelled_result_is_refunded(self):
        cancelled = result('1', full='')
        cancelled['poolStatus'] = 'Cancel'
        self.assertEqual(grade_item(item('1', 'had', 'win'), cancelled), 'void')


class BetSettlementTests(unittest.TestCase):
    def test_available_results_mark_only_completed_legs(self):
        first = item('sporttery-1', 'had', 'win', 2.0)
        first['date'] = '2026-07-19'
        first['match_num'] = '周日201'
        second = item('sporttery-2', 'had', 'draw', 3.2)
        second['date'] = '2026-07-20'
        second['match_num'] = '周一201'
        result_index = {}
        merge_database_results(result_index, [{
            'match_id': '500-match-1',
            'owner_date': first['date'],
            'match_number': first['match_num'],
            'status': 2,
            'home_score': '2',
            'away_score': '1',
            'home_half_score': '1',
            'away_half_score': '0',
        }])

        available = available_bet_results(
            {'selected_items': [first, second]},
            result_index,
        )

        self.assertEqual(len(available), 1)
        self.assertEqual(available[0]['match_id'], 'sporttery-1')
        self.assertEqual(available[0]['full_score'], '2:1')
        self.assertEqual(available[0]['item_results'][0]['result'], 'win')

    def test_database_result_settles_by_date_number_with_half_score(self):
        selected = item('sporttery-1', 'hafu', '平胜', 3.2)
        selected['date'] = '2026-07-19'
        selected['match_num'] = '周日205'
        result_index = {}
        merge_database_results(result_index, [{
            'match_id': '500-match-1',
            'owner_date': '2026-07-19',
            'match_number': '周日205',
            'status': 2,
            'home_score': '2',
            'away_score': '1',
            'home_half_score': '0',
            'away_half_score': '0',
        }])

        settled = settle_bet({
            'selected_items': [selected],
            'pass_counts': [1],
            'multiplier': 1,
            'stake': 2,
        }, result_index)

        self.assertEqual(settled['status'], 'won')
        self.assertEqual(
            settled['settlement']['matches'][0]['half_score'], '0:0'
        )
        self.assertEqual(
            settled['settlement']['matches'][0]['result_source'], 'mongodb'
        )

    def test_database_result_without_half_score_waits_for_half_full_bet(self):
        selected = item('sporttery-1', 'hafu', '平胜', 3.2)
        result_index = {}
        merge_database_results(result_index, [{
            'match_id': '500-match-1',
            'owner_date': selected['date'],
            'match_number': selected['match_num'],
            'status': 2,
            'home_score': '2',
            'away_score': '1',
        }])
        self.assertIsNone(settle_bet({
            'selected_items': [selected],
            'pass_counts': [1],
            'multiplier': 1,
            'stake': 2,
        }, result_index))

    def test_two_and_three_pass_bonus_sums_each_ticket(self):
        selected = [
            item('1', 'hhad', 'draw', 2.98),
            item('2', 'hhad', 'draw', 3.40),
            item('3', 'hhad', 'draw', 3.65),
        ]
        self.assertEqual(calculate_notes(selected, [2, 3]), 4)
        self.assertEqual(calculate_max_bonus(selected, [2, 3], 1), 140.79)

    def test_max_bonus_uses_best_mutually_exclusive_option_per_match(self):
        selected = [
            item('1', 'had', 'win', 2.0),
            item('1', 'had', 'draw', 3.0),
            item('2', 'had', 'lose', 4.0),
        ]
        self.assertEqual(calculate_notes(selected, [2]), 2)
        self.assertEqual(calculate_max_bonus(selected, [2], 1), 24.0)

    def test_rescheduled_match_uses_odds_one(self):
        bet = {
            'selected_items': [
                item('1', 'had', 'win', 2.12),
                item('2', 'had', 'win', 1.81),
                item('3', 'had', 'draw', 3.4),
            ],
            'pass_counts': [2, 3],
            'multiplier': 1,
            'stake': 8,
        }
        for selected, number in zip(bet['selected_items'], ('周四206', '周四205', '周四208')):
            selected['date'] = '2026-07-16'
            selected['match_num'] = number
        result_index = {
            '1': result('1', full='1:0'),
            '2': result('2', full='2:1'),
        }
        merge_rescheduled_void_results(
            result_index,
            [bet],
            [{
                'owner_date': '2026-07-16',
                'match_number': '周四208',
                'status': 6,
            }],
        )

        settled = settle_bet(bet, result_index)
        self.assertEqual(settled['status'], 'won')
        self.assertEqual(settled['winning_notes'], 4)
        self.assertEqual(settled['actual_return'], 23.2)
        self.assertEqual(settled['profit'], 15.2)
        void_match = settled['settlement']['matches'][2]
        self.assertTrue(void_match['is_void'])
        self.assertEqual(void_match['item_results'][0]['result'], 'void')

    def test_parlay_uses_pass_counts_options_and_multiplier(self):
        bet = {
            'selected_items': [
                item('1', 'had', 'win', 2.0),
                item('1', 'had', 'draw', 3.0),
                item('2', 'had', 'lose', 1.5),
            ],
            'pass_counts': [2],
            'multiplier': 2,
            'stake': 8,
        }
        settled = settle_bet(
            bet,
            {'1': result('1', full='2:1'), '2': result('2', full='0:1')},
        )
        self.assertEqual(settled['winning_notes'], 1)
        self.assertEqual(settled['actual_return'], 12.0)
        self.assertEqual(settled['profit'], 4.0)
        self.assertEqual(settled['status'], 'won')

    def test_partial_return_can_still_be_net_loss(self):
        bet = {
            'selected_items': [
                item('1', 'had', 'win', 1.5),
                item('2', 'had', 'win', 1.5),
                item('3', 'had', 'win', 1.5),
            ],
            'pass_counts': [2],
            'multiplier': 1,
            'stake': 6,
        }
        settled = settle_bet(
            bet,
            {
                '1': result('1', full='1:0'),
                '2': result('2', full='0:1'),
                '3': result('3', full='1:0'),
            },
        )
        self.assertEqual(settled['actual_return'], 4.5)
        self.assertEqual(settled['profit'], -1.5)
        self.assertEqual(settled['status'], 'lost')

    def test_each_winning_note_is_rounded_before_sum(self):
        bet = {
            'selected_items': [
                item('1', 'had', 'win', 1.11),
                item('2', 'had', 'win', 1.11),
                item('3', 'had', 'win', 1.11),
            ],
            'pass_counts': [2],
            'multiplier': 1,
            'stake': 6,
        }
        settled = settle_bet(
            bet,
            {
                '1': result('1', full='1:0'),
                '2': result('2', full='1:0'),
                '3': result('3', full='1:0'),
            },
        )
        self.assertEqual(settled['winning_notes'], 3)
        self.assertEqual(settled['actual_return'], 7.38)
        self.assertEqual(settled['profit'], 1.38)

    def test_missing_match_does_not_settle(self):
        bet = {
            'selected_items': [item('1', 'had', 'win')],
            'pass_counts': [1],
            'multiplier': 1,
            'stake': 2,
        }
        self.assertIsNone(settle_bet(bet, {}))


class UserStorageSettlementTests(unittest.TestCase):
    def test_storage_files_bet_under_dominant_match_date(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = UserStorage(os.path.join(directory, 'users.db'))
            user = storage.create_user('dated', '按比赛日期', 'secret123')
            selected = [
                {**item('1', 'hhad', 'draw', 3.8), 'date': '2026-08-16'},
                {**item('2', 'hhad', 'draw', 3.05), 'date': '2026-08-16'},
                # One malformed OCR date must not move the entire ticket.
                {**item('3', 'hhad', 'draw', 3.5), 'date': '2025-08-17'},
            ]
            bet = {
                'id': 'dated-bet',
                'status': 'pending',
                'multiplier': 11,
                'pass_counts': [2, 3],
                'selected_items': selected,
                'match_count': 3,
                'option_count': 3,
                'notes': 4,
                'stake': 88,
                'total_odds': 40.57,
                'max_bonus': 0,
                'description': '3场 · 2，3关 · 11倍',
                'created_at': '2026-08-23T09:16:35Z',
            }

            saved = storage.create_bet(user['id'], bet)

            self.assertEqual(saved['created_at'], '2026-08-16T09:16:35Z')
            self.assertEqual(storage.get_stats(user['id'])['daily'][0]['date'], '2026-08-16')

    def test_storage_repairs_legacy_inflated_max_bonus(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = os.path.join(directory, 'users.db')
            storage = UserStorage(database_path)
            user = storage.create_user('legacy', '旧记录', 'secret123')
            selected = [
                item('1', 'hhad', 'draw', 2.98),
                item('2', 'hhad', 'draw', 3.40),
                item('3', 'hhad', 'draw', 3.65),
            ]
            bet = {
                'id': 'legacy-bet',
                'status': 'pending',
                'multiplier': 1,
                'pass_counts': [2, 3],
                'selected_items': selected,
                'match_count': 3,
                'option_count': 3,
                'notes': 4,
                'stake': 8,
                'total_odds': 36.99,
                'max_bonus': 295.85,
                'description': '3场 · 2关、3关 · 1倍',
                'created_at': '2026-07-19T10:26:00Z',
            }
            saved = storage.create_bet(user['id'], bet)
            self.assertEqual(saved['max_bonus'], 140.79)

            with storage._connect() as conn:
                conn.execute(
                    'UPDATE calculator_bets SET max_bonus = 295.85 WHERE id = ?',
                    ('legacy-bet',),
                )
            repaired = UserStorage(database_path).list_bets(user['id'])[0]
            self.assertEqual(repaired['max_bonus'], 140.79)

    def test_settlement_is_persisted_and_included_in_stats(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = UserStorage(os.path.join(directory, 'users.db'))
            user = storage.create_user('tester', '测试', 'secret123')
            bet = {
                'id': 'bet-1',
                'status': 'pending',
                'multiplier': 1,
                'pass_counts': [1],
                'selected_items': [item('1', 'had', 'win', 2.0)],
                'match_count': 1,
                'option_count': 1,
                'notes': 1,
                'stake': 2,
                'total_odds': 2,
                'max_bonus': 4,
                'description': '1场 · 单关 · 1倍',
                'created_at': '2026-07-01T00:00:00Z',
            }
            storage.create_bet(user['id'], bet)
            settlement = settle_bet(bet, {'1': result('1', full='1:0')})
            self.assertTrue(storage.settle_bet('bet-1', settlement))
            self.assertFalse(storage.settle_bet('bet-1', settlement))

            saved = storage.list_bets(user['id'])[0]
            self.assertEqual(saved['status'], 'won')
            self.assertEqual(saved['actual_return'], 4.0)
            self.assertEqual(saved['profit'], 2.0)
            self.assertEqual(saved['settlement']['matches'][0]['full_score'], '1:0')

            stats = storage.get_stats(user['id'])
            self.assertEqual(stats['total_return'], 4.0)
            self.assertEqual(stats['net_profit'], 2.0)
            self.assertEqual(stats['settled_bets'], 1)
            self.assertEqual(stats['won_bets'], 1)
            self.assertEqual(stats['win_rate'], 100.0)
            self.assertEqual(stats['daily'][0]['profit'], 2.0)

            previous_month_bet = {
                **bet,
                'id': 'bet-2',
                'status': 'pending',
                'created_at': '2026-06-30T15:59:59Z',
                'selected_items': [
                    {**selected_item, 'date': '2026-06-30'}
                    for selected_item in bet['selected_items']
                ],
            }
            storage.create_bet(user['id'], previous_month_bet)

            self.assertEqual(len(storage.list_bets(user['id'], status='won')), 1)
            self.assertEqual(len(storage.list_bets(user['id'], status='pending')), 1)

            june_stats = storage.get_stats(user['id'], month='2026-06')
            self.assertEqual(june_stats['total_bets'], 1)
            self.assertEqual(june_stats['pending_bets'], 1)
            self.assertEqual(june_stats['net_profit'], 0.0)

            july_stats = storage.get_stats(user['id'], month='2026-07')
            self.assertEqual(july_stats['total_bets'], 1)
            self.assertEqual(july_stats['won_bets'], 1)
            self.assertEqual(july_stats['net_profit'], 2.0)
            self.assertEqual(
                july_stats['available_months'],
                ['2026-07', '2026-06'],
            )


if __name__ == '__main__':
    unittest.main()
