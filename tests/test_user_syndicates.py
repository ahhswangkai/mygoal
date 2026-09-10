import os
import tempfile
import unittest

from user_storage import UserStorage, allocate_cents


class SyndicateStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.storage = UserStorage(os.path.join(self.directory.name, 'users.db'))
        self.user = self.storage.create_user('host', '主持人', 'secret123')

    def tearDown(self):
        self.directory.cleanup()

    def test_largest_remainder_allocation_is_exact(self):
        members = [{'shares': 1}, {'shares': 1}, {'shares': 1}]
        self.assertEqual(allocate_cents(10000, members, 3), [3334, 3333, 3333])

    def test_host_can_reconcile_ticket_difference_and_lock_shares(self):
        plan = self.storage.create_syndicate(
            self.user['id'],
            {
                'title': '周三合买',
                'business_date': '2026-09-09',
                'total_shares': 20,
                'expected_stake': 160,
                'members': [
                    {'name': '主持人', 'shares': 8, 'is_host': True},
                    {'name': '张三', 'shares': 5, 'is_host': False},
                    {'name': '李四', 'shares': 4, 'is_host': False},
                    {'name': '王五', 'shares': 3, 'is_host': False},
                ],
            },
        )
        self.assertEqual(plan['expected_stake'], 160.0)
        self.assertIsNone(plan['actual_stake'])
        self.assertEqual(
            [member['expected_contribution'] for member in plan['members']],
            [64.0, 40.0, 32.0, 24.0],
        )

        reconciled = self.storage.reconcile_syndicate(
            self.user['id'], plan['id'], actual_stake=200
        )
        self.assertEqual(reconciled['difference'], 40.0)
        self.assertEqual(
            [member['actual_contribution'] for member in reconciled['members']],
            [80.0, 50.0, 40.0, 30.0],
        )
        self.assertEqual(
            [member['adjustment'] for member in reconciled['members']],
            [16.0, 10.0, 8.0, 6.0],
        )

        locked = self.storage.lock_syndicate(self.user['id'], plan['id'])
        self.assertEqual(locked['status'], 'locked')
        with self.assertRaisesRegex(ValueError, '份额已经锁定'):
            self.storage.update_syndicate(
                self.user['id'], plan['id'], {
                    'title': '不能再改',
                    'business_date': '2026-09-09',
                    'total_shares': 20,
                    'expected_stake': 160,
                    'members': [
                        {'name': '主持人', 'shares': 20, 'is_host': True},
                    ],
                }
            )

    def test_linked_bet_return_is_distributed_by_locked_share(self):
        bet = self.storage.create_bet(
            self.user['id'],
            {
                'id': 'ticket-1',
                'status': 'pending',
                'multiplier': 1,
                'pass_counts': [1],
                'selected_items': [
                    {
                        'match_id': 'match-1',
                        'pool': 'had',
                        'pool_name': '胜平负',
                        'opt': 'win',
                        'label': '胜',
                        'odd': 2.0,
                        'match_num': '周三001',
                        'league': '测试联赛',
                        'home_team': '主队',
                        'away_team': '客队',
                        'date': '2026-09-09',
                        'time': '20:00',
                        'handicap': 0,
                    }
                ],
                'match_count': 1,
                'option_count': 1,
                'notes': 1,
                'stake': 2,
                'total_odds': 2,
                'max_bonus': 4,
                'description': '1场 · 单关 · 1倍',
                'created_at': '2026-09-09T12:00:00Z',
            },
        )
        plan = self.storage.create_syndicate(
            self.user['id'],
            {
                'title': '票据合买',
                'business_date': '2026-09-09',
                'bet_id': bet['id'],
                'total_shares': 4,
                'expected_stake': 2,
                'members': [
                    {'name': '主持人', 'shares': 3, 'is_host': True},
                    {'name': '成员甲', 'shares': 1, 'is_host': False},
                ],
            },
        )
        self.assertEqual(plan['actual_stake'], 2.0)
        self.storage.lock_syndicate(self.user['id'], plan['id'])
        self.storage.settle_bet(
            bet['id'],
            {
                'status': 'won',
                'actual_return': 8,
                'profit': 6,
                'winning_notes': 1,
                'settled_at': '2026-09-10T01:00:00Z',
                'settlement': {},
            },
        )
        settled = self.storage.get_syndicate(self.user['id'], plan['id'])
        self.assertEqual(settled['status'], 'settled')
        self.assertEqual(settled['actual_return'], 8.0)
        self.assertEqual(
            [member['payout'] for member in settled['members']],
            [6.0, 2.0],
        )
        self.assertEqual(
            [member['profit'] for member in settled['members']],
            [4.5, 1.5],
        )


if __name__ == '__main__':
    unittest.main()
