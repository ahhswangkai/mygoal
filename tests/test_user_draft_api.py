import os
import tempfile
import unittest
from unittest.mock import patch

import web_app
from user_storage import UserStorage


class UserDraftApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.original_storage = web_app.user_storage
        web_app.user_storage = UserStorage(
            os.path.join(self.directory.name, 'users.db')
        )
        self.user = web_app.user_storage.create_user(
            'draft-api-user', '草稿接口', 'secret123'
        )
        web_app.app.config.update(TESTING=True)

    def tearDown(self):
        web_app.user_storage = self.original_storage
        self.directory.cleanup()

    @staticmethod
    def payload(match_date='2026-09-04'):
        return {
            'selected_items': [{
                'matchId': '1001',
                'pool': 'hhad',
                'opt': 'draw',
                'label': '平',
                'odd': 3.55,
                'match': {
                    'num': '周五001',
                    'league': '测试联赛',
                    'homeTeam': '主队',
                    'awayTeam': '客队',
                    'date': match_date,
                    'time': '20:00',
                    'handicap': -1,
                },
            }],
            'pass_counts': [1],
            'multiplier': 1,
        }

    @patch('web_app._calculator_draft_started', return_value=False)
    @patch('web_app._calculator_business_date', return_value='2026-09-04')
    def test_create_list_and_delete_draft(self, _business_date, _started):
        with web_app.app.test_request_context(
            '/api/user/drafts', method='POST', json=self.payload()
        ):
            web_app.session['user_id'] = self.user['id']
            created, status = web_app.create_user_draft()
        self.assertEqual(status, 201)
        draft_id = created.get_json()['data']['id']

        with web_app.app.test_request_context('/api/user/drafts'):
            web_app.session['user_id'] = self.user['id']
            listed = web_app.list_user_drafts()
        self.assertEqual(listed.get_json()['count'], 1)
        self.assertEqual(listed.get_json()['data'][0]['id'], draft_id)

        with web_app.app.test_request_context(
            '/api/user/drafts/{}'.format(draft_id), method='DELETE'
        ):
            web_app.session['user_id'] = self.user['id']
            deleted = web_app.delete_user_draft(draft_id)
        self.assertTrue(deleted.get_json()['success'])

        with web_app.app.test_request_context('/api/user/drafts'):
            web_app.session['user_id'] = self.user['id']
            listed = web_app.list_user_drafts()
        self.assertEqual(listed.get_json()['count'], 0)

    @patch('web_app._calculator_draft_started', return_value=False)
    @patch('web_app._calculator_business_date', return_value='2026-09-04')
    def test_rejects_non_current_match_date(self, _business_date, _started):
        with web_app.app.test_request_context(
            '/api/user/drafts',
            method='POST',
            json=self.payload('2026-09-03'),
        ):
            web_app.session['user_id'] = self.user['id']
            response, status = web_app.create_user_draft()
        self.assertEqual(status, 400)
        self.assertIn('只保留当天比赛', response.get_json()['message'])

    @patch('web_app._calculator_business_date', return_value='2026-09-04')
    def test_started_match_is_rejected(self, _business_date):
        with patch('web_app._calculator_draft_started', return_value=True):
            with web_app.app.test_request_context(
                '/api/user/drafts', method='POST', json=self.payload()
            ):
                web_app.session['user_id'] = self.user['id']
                response, status = web_app.create_user_draft()
        self.assertEqual(status, 400)
        self.assertIn('已经开赛', response.get_json()['message'])

    @patch('web_app._calculator_business_date', return_value='2026-09-04')
    def test_list_removes_draft_when_a_match_has_started(self, _business_date):
        saved = web_app.user_storage.save_draft(
            self.user['id'],
            {
                'id': 'started-draft',
                'selected_items': [{
                    'match_id': '1001',
                    'date': '2026-09-04',
                    'time': '20:00',
                    'pool': 'hhad',
                    'opt': 'draw',
                    'odd': 3.55,
                }],
                'pass_counts': [1],
                'multiplier': 1,
                'match_count': 1,
                'option_count': 1,
            },
            '2026-09-04',
        )
        self.assertEqual(saved['id'], 'started-draft')

        with patch('web_app._calculator_draft_started', return_value=True):
            with web_app.app.test_request_context('/api/user/drafts'):
                web_app.session['user_id'] = self.user['id']
                response = web_app.list_user_drafts()

        self.assertEqual(response.get_json()['count'], 0)
        self.assertEqual(web_app.user_storage.list_drafts(self.user['id']), [])


if __name__ == '__main__':
    unittest.main()
