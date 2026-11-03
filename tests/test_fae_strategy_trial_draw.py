from copy import deepcopy
from datetime import timedelta
import json
import unittest

from football_ai.strategy_trial import (
    TRIAL_POLICY, archive_trial_job, choose_draw_ticket, execute_trial_ai_job,
    settle_draw_ticket, start_trial, trial_report,
)
from tests.test_fae_strategy_trial import NOW
from tests import test_fae_strategy_trial_ai as ai_tests
from tests.test_fae_strategy_trial_ai import MemoryJobs, response


def pick(match_id, selection='平局', **updates):
    value = {'match_id': match_id, 'match_number': f'周二{match_id}',
             'selection': selection, 'selection_text': selection,
             'handicap': -1 if selection == '让平' else None,
             'odds': 3.0, 'probability': 40, 'market_probability': 30,
             'eligible': True, 'analysis_source': 'volcengine-ark'}
    value.update(updates)
    return value


class DrawTrialTicketTests(unittest.TestCase):
    def ticket(self):
        return choose_draw_ticket([pick('001'), pick('002'), pick('003', '让平')], TRIAL_POLICY)

    def test_three_matches_expand_to_three_doubles_and_one_treble(self):
        ticket = self.ticket()
        self.assertEqual([len(line['picks']) for line in ticket['lines']], [2, 2, 2, 3])
        self.assertEqual(sum(line['stake'] for line in ticket['lines']), 1)
        self.assertEqual(ticket['line_count'], 4)
        self.assertEqual(ticket['max_return_units'], (9 + 9 + 9 + 27) / 4)

    def test_same_fixture_draw_and_handicap_draw_cannot_count_twice(self):
        self.assertIsNone(choose_draw_ticket([pick('001'), pick('001', '让平'), pick('002')], TRIAL_POLICY))
        ticket = choose_draw_ticket([pick('001'), pick('001', '让平', probability=45), pick('002'), pick('003')], TRIAL_POLICY)
        self.assertEqual(len({p['match_id'] for p in ticket['picks']}), 3)
        self.assertEqual(ticket['picks'][0]['selection'], '让平')

    def test_unapproved_or_zero_handicap_alias_does_not_make_a_third_match(self):
        for third in (pick('003', eligible=False), pick('003', '让平', handicap=0), pick('003', odds=7)):
            self.assertIsNone(choose_draw_ticket([pick('001'), pick('002'), third], TRIAL_POLICY))

    def test_exactly_two_hits_pay_only_the_winning_double(self):
        result = settle_draw_ticket(self.ticket(), {
            '001': {'status': 2, 'score': '1:1'}, '002': {'status': 2, 'score': '0:0'},
            '003': {'status': 2, 'score': '1:1'},
        })
        self.assertEqual(result['hit_leg_count'], 2)
        self.assertEqual(result['return_line_count'], 1)
        self.assertEqual(result['return_units'], 9 / 4)
        self.assertEqual(result['profit_units'], 1.25)

    def test_three_hits_pay_all_four_lines(self):
        result = settle_draw_ticket(self.ticket(), {
            '001': {'status': 2, 'score': '1:1'}, '002': {'status': 2, 'score': '0:0'},
            '003': {'status': 2, 'score': '2:1'},
        })
        self.assertEqual(result['return_line_count'], 4)
        self.assertEqual(result['return_units'], 13.5)
        self.assertEqual(result['profit_units'], 12.5)

    def test_partial_results_remain_pending_and_original_prices_are_immutable(self):
        ticket = self.ticket()
        original = deepcopy(ticket)
        result = settle_draw_ticket(ticket, {'001': {'status': 2, 'score': '1:1'}, '002': {'status': 1},
                                             '003': {'status': 2, 'score': '2:1'}})
        self.assertEqual(result['status'], 'pending')
        self.assertIsNone(result['profit_units'])
        self.assertEqual(ticket, original)

    def test_void_can_return_money_without_profit(self):
        result = settle_draw_ticket(self.ticket(), {'001': {'status': 2, 'score': '1:1'},
                                                    '002': {'status': 6}, '003': {'status': 2, 'score': '1:1'}})
        self.assertEqual(result['status'], 'hit')
        self.assertEqual(result['return_units'], .75)
        self.assertEqual(result['profit_units'], -.25)
        self.assertFalse(result['profitable'])

    def test_all_void_returns_stake(self):
        result = settle_draw_ticket(self.ticket(), {key: {'status': 6} for key in ('001', '002', '003')})
        self.assertEqual(result['status'], 'push')
        self.assertEqual(result['return_units'], 1)
        self.assertEqual(result['profit_units'], 0)

    def test_archiving_pending_old_job_preserves_inputs_without_inventing_ticket(self):
        snapshot = {'owner_date': '2026-11-03', 'snapshot_hash': 'old', 'decision': 'analyzing',
                    'ticket': None, 'baseline_ticket': {'frozen': 'prices'},
                    'audit': [{'raw_snapshot': {'odds': 3.5}}]}
        original = deepcopy(snapshot)
        archived = archive_trial_job(snapshot, NOW)
        self.assertEqual(archived['decision'], 'suspended')
        self.assertIsNone(archived['ticket'])
        self.assertEqual(archived['baseline_ticket'], snapshot['baseline_ticket'])
        self.assertEqual(snapshot, original)
        self.assertEqual(archived['source_snapshot_hash'], 'old')

    def test_draw_statistics_are_independent_of_three_times_double(self):
        ticket = settle_draw_ticket(self.ticket(), {
            '001': {'status': 2, 'score': '1:1'}, '002': {'status': 2, 'score': '0:0'},
            '003': {'status': 2, 'score': '2:1'},
        })
        day = {'owner_date': '2026-11-03', 'snapshot_hash': 'frozen', 'decision': 'no_ticket',
               'ticket': None, 'draw_ticket': ticket, 'draw_decision': 'ticket'}
        report = trial_report(start_trial(NOW - timedelta(hours=1)), [day], [], now=NOW)
        self.assertEqual(report['summary']['tickets'], 0)
        self.assertEqual(report['draw_summary']['settled'], 1)
        self.assertEqual(report['draw_summary']['roi'], 1250)


class DrawTrialAITests(unittest.TestCase):
    def setUp(self):
        self.case = ai_tests.TrialAITests()
        self.case.setUp()
        self.client, self.forecaster, self.trial = self.case.client, self.case.forecaster, self.case.trial

    def capture(self, count):
        return self.case.capture(count)

    def test_draw_plan_can_use_ai_secondary_focus_without_changing_primary(self):
        def forecast(match_id):
            return response(match_id, primary_selection='主胜', probabilities={
                'home_win': 40, 'draw': 50, 'away_win': 10, 'hhad': {'win': 10, 'draw': 20, 'lose': 70}},
                draw_focus={'ordinary_draw': {'eligible': True, 'reason': '浅盘且低比分支持胶着'},
                            'handicap_draw': {'eligible': False, 'reason': '一球差路径不明确'}})
        self.client.generate.return_value = (json.dumps({'matches': [forecast(f'{i:03d}') for i in range(1, 4)]}), {})
        storage = MemoryJobs(self.capture(3))
        result = execute_trial_ai_job(self.trial, storage.job, storage, forecaster=self.forecaster, clock=lambda: NOW)
        self.assertIsNone(result['ticket'])
        self.assertIsNotNone(result['draw_ticket'])
        self.assertEqual({row['selection'] for row in result['draw_ticket']['picks']}, {'平局'})
        self.assertEqual({row['primary']['selection'] for row in result['audit']}, {'主胜'})
        self.client.generate.assert_called_once()

    def test_missing_ai_focus_never_becomes_a_local_draw_recommendation(self):
        storage = MemoryJobs(self.capture(3))
        self.client.generate.return_value = (json.dumps({'matches': [response(f'{i:03d}') for i in range(1, 4)]}), {})
        result = execute_trial_ai_job(self.trial, storage.job, storage, forecaster=self.forecaster, clock=lambda: NOW)
        self.assertIsNone(result['draw_ticket'])
        self.assertEqual(result['ai_run']['draw_assessed_matches'], 0)

    def test_handicap_draw_is_independent_of_no_primary_and_uses_its_own_market(self):
        rows = [response(f'{i:03d}', primary_selection=None, probabilities={
            'home_win': 70, 'draw': 20, 'away_win': 10, 'hhad': {'win': 20, 'draw': 50, 'lose': 30}},
            draw_focus={'ordinary_draw': {'eligible': False, 'reason': '主胜方向更强'},
                        'handicap_draw': {'eligible': True, 'reason': '主队恰好赢1球路径清楚'}}) for i in range(1, 4)]
        self.client.generate.return_value = (json.dumps({'matches': rows}), {})
        storage = MemoryJobs(self.capture(3))
        result = execute_trial_ai_job(self.trial, storage.job, storage, forecaster=self.forecaster, clock=lambda: NOW)
        self.assertIsNone(result['ticket'])
        self.assertEqual({pick['selection'] for pick in result['draw_ticket']['picks']}, {'让平'})
        self.assertTrue(all(pick['handicap'] == -1 for pick in result['draw_ticket']['picks']))

    def test_AI_explicit_rejection_is_kept_even_with_high_draw_probability(self):
        self.test_draw_plan_can_use_ai_secondary_focus_without_changing_primary()
        # New independent job, with the same probabilities but AI says no.
        payload = json.loads(self.client.generate.return_value[0])
        for row in payload['matches']:
            row['draw_focus']['ordinary_draw']['eligible'] = False
        self.client.generate.return_value = (json.dumps(payload), {})
        storage = MemoryJobs(self.capture(3))
        result = execute_trial_ai_job(self.trial, storage.job, storage, forecaster=self.forecaster, clock=lambda: NOW)
        self.assertIsNone(result['draw_ticket'])
        self.assertTrue(all(not row['eligible'] for row in result['draw_candidates']))


if __name__ == '__main__':
    unittest.main()
