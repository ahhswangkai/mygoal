import os
import tempfile
import unittest

from user_storage import UserStorage


def draft(draft_id, match_date, options=None):
    selected = options or [{
        'match_id': '1001',
        'pool': 'hhad',
        'pool_name': '让球胜平负',
        'opt': 'draw',
        'label': '平',
        'odd': 3.55,
        'match_num': '周五001',
        'league': '测试联赛',
        'home_team': '主队',
        'away_team': '客队',
        'date': match_date,
        'time': '20:00',
        'handicap': -1,
    }]
    return {
        'id': draft_id,
        'match_date': match_date,
        'selected_items': selected,
        'pass_counts': [1],
        'multiplier': 1,
        'match_count': 1,
        'option_count': len(selected),
    }


class UserDraftStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.storage = UserStorage(
            os.path.join(self.directory.name, 'users.db')
        )
        self.user = self.storage.create_user(
            'draft-user', '草稿测试', 'secret123'
        )

    def tearDown(self):
        self.directory.cleanup()

    def test_save_list_and_delete_current_day_draft(self):
        saved = self.storage.save_draft(
            self.user['id'], draft('draft-1', '2026-09-04'), '2026-09-04'
        )
        self.assertFalse(saved['deduplicated'])

        records = self.storage.list_drafts(self.user['id'], '2026-09-04')
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['selected_items'][0]['match_num'], '周五001')
        self.assertTrue(self.storage.delete_draft(self.user['id'], 'draft-1'))
        self.assertEqual(self.storage.list_drafts(self.user['id'], '2026-09-04'), [])

    def test_identical_draft_is_deduplicated(self):
        first = self.storage.save_draft(
            self.user['id'], draft('draft-1', '2026-09-04'), '2026-09-04'
        )
        refreshed = draft('draft-2', '2026-09-04')
        refreshed['selected_items'][0]['odd'] = 3.62
        second = self.storage.save_draft(
            self.user['id'], refreshed, '2026-09-04'
        )

        self.assertEqual(first['id'], second['id'])
        self.assertTrue(second['deduplicated'])
        self.assertEqual(second['selected_items'][0]['odd'], 3.62)
        self.assertEqual(len(self.storage.list_drafts(self.user['id'], '2026-09-04')), 1)

    def test_new_programme_date_does_not_purge_unstarted_drafts(self):
        self.storage.save_draft(
            self.user['id'], draft('old', '2026-09-04'), '2026-09-04'
        )
        self.storage.save_draft(
            self.user['id'], draft('new', '2026-09-05'), '2026-09-05'
        )

        records = self.storage.list_drafts(self.user['id'])
        self.assertEqual(
            {record['id'] for record in records},
            {'old', 'new'},
        )

    def test_bulk_delete_started_drafts(self):
        self.storage.save_draft(
            self.user['id'], draft('started', '2026-09-04'), '2026-09-04'
        )
        self.storage.save_draft(
            self.user['id'], draft('waiting', '2026-09-05'), '2026-09-05'
        )

        self.assertEqual(
            self.storage.delete_drafts(self.user['id'], ['started']),
            1,
        )
        records = self.storage.list_drafts(self.user['id'])
        self.assertEqual([record['id'] for record in records], ['waiting'])

    def test_update_draft_replaces_original_plan(self):
        self.storage.save_draft(
            self.user['id'], draft('editable', '2026-09-04'), '2026-09-04'
        )
        changed = draft('ignored', '2026-09-04')
        changed['selected_items'][0]['opt'] = 'lose'
        changed['selected_items'][0]['label'] = '负'
        changed['selected_items'][0]['odd'] = 2.15
        changed['multiplier'] = 20

        updated = self.storage.update_draft(
            self.user['id'], 'editable', changed, '2026-09-04'
        )

        self.assertEqual(updated['id'], 'editable')
        self.assertTrue(updated['updated'])
        self.assertEqual(updated['selected_items'][0]['opt'], 'lose')
        self.assertEqual(updated['multiplier'], 20)
        self.assertEqual(len(self.storage.list_drafts(self.user['id'])), 1)

    def test_update_draft_cannot_modify_another_users_plan(self):
        self.storage.save_draft(
            self.user['id'], draft('private', '2026-09-04'), '2026-09-04'
        )
        other = self.storage.create_user(
            'another-draft-user', '其他用户', 'secret123'
        )

        updated = self.storage.update_draft(
            other['id'],
            'private',
            draft('ignored', '2026-09-04'),
            '2026-09-04',
        )

        self.assertIsNone(updated)


if __name__ == '__main__':
    unittest.main()
