from copy import deepcopy
from datetime import timedelta
import json
import unittest

from football_ai.strategy_trial import (
    TRIAL_POLICY, V3_STRICT_1600_FINGERPRINT, build_trial_day, digest,
    execute_trial_ai_job, reschedule_trial, settle_draw_ticket, start_trial, trial_report,
)
from tests.test_fae_strategy_trial import NOW, fixture
from tests import test_fae_strategy_trial_ai as ai_tests
from tests.test_fae_strategy_trial_ai import MemoryJobs, response


class DailyResearchTests(unittest.TestCase):
    def setUp(self):
        self.base = ai_tests.TrialAITests()
        self.base.setUp()
        self.client, self.forecaster, self.trial = self.base.client, self.base.forecaster, self.base.trial
        self.trial['policy']['selection_mode'] = 'daily_research_v1'
        self.trial['policy_hash'] = digest(self.trial['policy'])

    def forecast(self, mid, **changes):
        return response(mid, primary_selection='让平', probabilities={
            'home_win': 70, 'draw': 20, 'away_win': 10,
            'hhad': {'win': 20, 'draw': 30, 'lose': 50}},
            draw_focus={'ordinary_draw': {'eligible': False, 'reason': '普通平证据不足'},
                        'handicap_draw': {'eligible': True, 'reason': '主队小胜一球路径可以观察'}}, **changes)

    def run_day(self, count=2, rows=None, matches=None):
        self.client.generate.return_value = (json.dumps({'matches': rows or [self.forecast(f'{n:03d}')
            for n in range(1, count + 1)]}), {})
        snapshot = build_trial_day(self.trial, '2026-11-03', matches or [fixture(f'{n:03d}')
            for n in range(1, count + 1)], now=NOW, capture_only=True)
        storage = MemoryJobs(snapshot)
        return execute_trial_ai_job(self.trial, storage.job, storage,
                                    forecaster=self.forecaster, clock=lambda: NOW)

    def test_valid_low_probability_negative_edge_rows_still_create_research_ticket(self):
        day = self.run_day()
        self.assertIsNotNone(day['ticket'])
        self.assertTrue(day['ticket']['research_only'])
        self.assertTrue(all(row['probability'] < 45 and row['estimated_return'] < 1
                            for row in day['ticket']['picks']))
        self.assertTrue(day['ticket']['warnings'])
        self.assertEqual(day['eligible_match_count'], 2)

    def test_outside_three_times_target_displays_actual_odds_without_changing_ai_primary(self):
        day = self.run_day()
        self.assertEqual(day['ticket']['combined_odds'], 12.25)
        self.assertEqual({row['selection'] for row in day['ticket']['picks']}, {'让平'})
        self.assertTrue(any('2.8' in warning for warning in day['ticket']['warnings']))

    def test_exactly_two_draw_candidates_form_one_real_double_not_a_fake_treble(self):
        day = self.run_day()
        ticket = day['draw_ticket']
        self.assertEqual(len(ticket['picks']), 2)
        self.assertEqual(ticket['line_count'], 1)
        self.assertEqual(ticket['play'], '2场2关')
        self.assertEqual(ticket['lines'][0]['stake'], 1)
        self.assertIsNone(ticket['estimated_all_three_probability'])
        settled = settle_draw_ticket(ticket, {mid: {'status': 2, 'score': '1:0'} for mid in ('001', '002')})
        self.assertEqual(settled['return_units'], 12.25)
        self.assertEqual(settled['profit_units'], 11.25)

    def test_three_draw_candidates_keep_three_doubles_and_one_treble(self):
        day = self.run_day(3)
        self.assertEqual(day['draw_ticket']['line_count'], 4)
        self.assertEqual(sum(row['stake'] for row in day['draw_ticket']['lines']), 1)
        self.assertEqual(len(day['draw_ticket']['picks']), 3)

    def test_heat_above_ten_is_visible_warning_not_hidden_rejection(self):
        matches = [fixture(mid) for mid in ('001', '002')]
        matches[0]['betting_ratio']['handicap'].update(home_support_rate=20, draw_support_rate=45, away_support_rate=35)
        day = self.run_day(matches=matches)
        row = next(row for row in day['ticket']['picks'] if row['match_id'] == '001')
        self.assertTrue(any('投注偏离' in reason for reason in row['warnings']))

    def test_missing_data_semantic_errors_and_severe_risk_still_block(self):
        for updates in ({'ou_current_total': None}, {'betting_ratio': {}}, {'status': 1}):
            with self.subTest(updates=updates):
                day = self.run_day(matches=[fixture('001', **updates), fixture('002')])
                self.assertIsNone(day['ticket'])
                self.assertIsNone(day['draw_ticket'])
        day = self.run_day(rows=[self.forecast('001', data_risk=True), self.forecast('002')])
        self.assertIsNone(day['ticket'])
        self.assertIsNone(day['draw_ticket'])
        bad = response('001', probabilities={'home_win': 10, 'draw': 30, 'away_win': 60,
                                            'hhad': {'win': 50, 'draw': 40, 'lose': 10}})
        self.assertIsNone(self.run_day(rows=[bad, self.forecast('002')])['ticket'])

    def test_ai_rejection_cannot_be_promoted_to_draw_candidate_for_daily_coverage(self):
        focus = {'ordinary_draw': {'eligible': False, 'reason': '证据不足'},
                 'handicap_draw': {'eligible': False, 'reason': '一球差路径不足'}}
        rows = [response(mid, primary_selection='主胜', draw_focus=focus) for mid in ('001', '002', '003')]
        self.assertIsNone(self.run_day(3, rows=rows)['draw_ticket'])

    def test_one_fixture_or_duplicate_fixture_cannot_form_daily_double(self):
        self.assertIsNone(self.run_day(count=1)['ticket'])
        self.assertIsNone(self.run_day(matches=[fixture('001'), fixture('001')])['ticket'])

    def test_primary_odds_below_one_point_five_is_still_not_replaced(self):
        rows = [response(mid, primary_selection='主胜') for mid in ('001', '002')]
        day = self.run_day(rows=rows, matches=[fixture(mid, euro_current_win=1.2) for mid in ('001', '002')])
        self.assertIsNone(day['ticket'])

    def test_switch_is_prospective_and_retains_original_inputs_dates_and_policy(self):
        old = start_trial(NOW - timedelta(hours=1), client=self.client)
        old['policy'].pop('selection_mode')
        old['policy_hash'] = digest(old['policy'])
        old['model_fingerprint'] = V3_STRICT_1600_FINGERPRINT
        original = deepcopy(old)
        upgraded = reschedule_trial(old, NOW + timedelta(hours=1))
        self.assertEqual(upgraded['policy']['selection_mode'], 'daily_research_v1')
        self.assertEqual(upgraded['schedule_effective_date'], '2026-11-04')
        self.assertEqual(upgraded['start_date'], old['start_date'])
        self.assertEqual(upgraded['end_date'], old['end_date'])
        self.assertEqual(old, original)

    def test_strict_and_research_financial_statistics_are_not_mixed(self):
        day = self.run_day()
        old = deepcopy(day)
        old['owner_date'] = '2026-11-02'
        old['policy'].pop('selection_mode')
        old['ticket'].update(status='hit', return_units=12.25, profit_units=11.25)
        report = trial_report(self.trial, [old, day], [], now=NOW)
        self.assertEqual(report['previous_mode_days'], 1)
        self.assertEqual(report['summary']['tickets'], 1)
        self.assertEqual(report['summary']['settled'], 0)
        self.assertEqual(len(report['days']), 2)


if __name__ == '__main__':
    unittest.main()
