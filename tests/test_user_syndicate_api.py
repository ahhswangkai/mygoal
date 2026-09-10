import os
import tempfile
import unittest
from unittest.mock import patch

import web_app
from user_storage import UserStorage


class UserSyndicateApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_storage = web_app.user_storage
        web_app.user_storage = UserStorage(
            os.path.join(self.directory.name, 'users.db')
        )
        web_app.user_storage.create_user('host-api', '王主持', 'secret123')
        web_app.app.config.update(TESTING=True)
        self.client = web_app.app.test_client()
        self.client.post('/api/auth/login', json={
            'username': 'host-api', 'password': 'secret123'
        })

    def tearDown(self):
        web_app.user_storage = self.original_storage
        self.directory.cleanup()

    @staticmethod
    def payload():
        return {
            'title': '欧冠合买',
            'business_date': '2026-09-09',
            'total_shares': 10,
            'expected_stake': 160,
            'members': [
                {'name': '伪造主持人', 'shares': 6, 'is_host': True},
                {'name': '成员甲', 'shares': 4, 'is_host': False},
            ],
        }

    def test_create_reconcile_lock_and_list(self):
        created = self.client.post('/api/user/syndicates', json=self.payload())
        self.assertEqual(created.status_code, 201)
        plan = created.get_json()['data']
        self.assertEqual(plan['members'][0]['name'], '王主持')

        reconciled = self.client.post(
            '/api/user/syndicates/{}/reconcile'.format(plan['id']),
            json={'actual_stake': 200, 'bet_id': None},
        )
        self.assertEqual(reconciled.status_code, 200)
        self.assertEqual(reconciled.get_json()['data']['difference'], 40.0)

        locked = self.client.post(
            '/api/user/syndicates/{}/lock'.format(plan['id']), json={}
        )
        self.assertEqual(locked.status_code, 200)
        self.assertEqual(locked.get_json()['data']['status'], 'locked')

        with patch('web_app._settle_pending_calculator_bets'):
            listed = self.client.get('/api/user/syndicates?date=2026-09-09')
        payload = listed.get_json()
        self.assertEqual(payload['data'][0]['id'], plan['id'])
        self.assertEqual(payload['summary']['actual_stake'], 200.0)
        self.assertEqual(payload['summary']['members'][0]['name'], '王主持')

    def test_rejects_incomplete_share_assignment(self):
        payload = self.payload()
        payload['members'][1]['shares'] = 3
        response = self.client.post('/api/user/syndicates', json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn('必须等于总份数', response.get_json()['message'])


if __name__ == '__main__':
    unittest.main()
