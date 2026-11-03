from copy import deepcopy
import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from fae_schedule import app_timezone
from football_ai.strategy_trial import (
    build_trial_day, decision_time, digest, settle_ticket, start_trial, trial_report,
)
from db_storage import MongoDBStorage


NOW = datetime(2026, 11, 3, 18, tzinfo=app_timezone())


def fixture(match_id, **updates):
    row = {
        'match_id': match_id, 'match_number': f'周二{match_id}',
        'home_team': f'主{match_id}', 'away_team': f'客{match_id}',
        'owner_date': '2026-11-03', 'match_time': '11-03 21:00', 'status': 0,
        'updated_at': (NOW - timedelta(minutes=5)).isoformat(),
        'euro_current_win': 1.75, 'euro_current_draw': 3.6, 'euro_current_lose': 5.2,
        'hi_handicap_value': -1,
        'hi_current_home_odds': 3, 'hi_current_draw_odds': 3.5, 'hi_current_away_odds': 1.8,
        'asian_current_handicap': '半球', 'asian_current_home_odds': .90,
        'asian_current_away_odds': .95, 'ou_current_total': 2.5,
        'ou_current_over_odds': .95, 'ou_current_under_odds': .90,
        'betting_ratio': {
            'fetched_at': (NOW - timedelta(minutes=5)).isoformat(),
            'ordinary': {'home_support_rate': 53, 'draw_support_rate': 25, 'away_support_rate': 22},
            'handicap': {'home_support_rate': 25, 'draw_support_rate': 25, 'away_support_rate': 50},
        },
    }
    row.update(updates)
    return row


def fake_engine():
    engine = Mock()
    engine.build_context.side_effect = lambda match, **kwargs: {'match': deepcopy(match), **kwargs}
    engine.generate_from_context.return_value = {'analysis': {
        'probabilities': {'home_win': 75, 'draw': 15, 'away_win': 10,
                          'hhad': {'win': 20, 'draw': 20, 'lose': 60}},
        'risk': {'dangerous': False},
    }}
    return engine


class StrategyTrialTests(unittest.TestCase):
    def setUp(self):
        self.trial = start_trial(NOW - timedelta(hours=8))
        # Preserve the original deterministic tests as explicit v1 regressions.
        self.trial['policy']['llm_enabled'] = False
        self.trial['policy_hash'] = digest(self.trial['policy'])
        self.trial['model_fingerprint'] = 'fixed'

    def build(self, matches=None, **kwargs):
        return build_trial_day(
            self.trial, '2026-11-03', matches if matches is not None else [fixture('001'), fixture('002')],
            now=kwargs.pop('now', NOW), engine=kwargs.pop('engine', fake_engine()),
            fingerprint=kwargs.pop('fingerprint', 'fixed'), **kwargs)

    def test_enrolling_after_cutoff_starts_tomorrow(self):
        self.assertEqual(start_trial(NOW)['start_date'], '2026-11-04')
        self.assertEqual(self.trial['start_date'], '2026-11-03')
        self.assertEqual(self.trial['end_date'], '2026-12-02')

    def test_captures_one_fixed_unit_ticket_with_full_audit_and_baseline(self):
        engine = fake_engine()
        result = self.build(engine=engine)
        self.assertEqual(result['decision'], 'ticket')
        self.assertEqual(result['ticket']['combined_odds'], 3.0625)
        self.assertEqual(result['ticket']['stake'], 1)
        self.assertEqual(len(result['audit']), 2)
        self.assertTrue(result['baseline_ticket'])
        self.assertEqual(len(result['snapshot_hash']), 64)
        for call in engine.generate_from_context.call_args_list:
            self.assertFalse(call.kwargs['use_ai'])
            self.assertEqual(call.kwargs['rule_weights'], self.trial['rule_weights'])

    def test_cannot_capture_before_the_fixed_time(self):
        with self.assertRaises(ValueError):
            self.build(now=NOW - timedelta(seconds=1))

    def test_missed_window_never_backfills(self):
        engine = fake_engine()
        result = self.build(now=NOW + timedelta(minutes=5), engine=engine)
        self.assertEqual(result['decision'], 'missed')
        self.assertIsNone(result['ticket'])
        engine.generate_from_context.assert_not_called()

    def test_code_or_policy_changes_suspend_picks(self):
        self.assertEqual(self.build(fingerprint='changed')['decision'], 'suspended')
        self.trial['policy']['min_leg_odds'] = 1.1
        self.assertEqual(self.build()['decision'], 'suspended')

    def test_frozen_weights_cannot_be_changed_during_experiment(self):
        self.trial['rule_weights']['euro-home-support'] = 1.5
        self.assertEqual(self.build()['decision'], 'suspended')

    def test_real_engine_smoke_does_not_need_llm_or_historical_ai_run(self):
        from football_ai.engine import FootballAIEngine
        engine = FootballAIEngine(client=Mock(configured=True, model='test'))
        result = self.build(engine=engine)
        engine.client.generate.assert_not_called()
        self.assertEqual(len(result['audit']), 2)
        self.assertTrue(result['audit'][0]['model_result']['analysis']['probabilities'])

    def test_live_unknown_future_stale_or_near_kickoff_are_excluded(self):
        for updates in (
            {'status': 1}, {'match_time': ''}, {'match_time': '11-03 18:05'},
            {'updated_at': (NOW + timedelta(minutes=1)).isoformat()},
            {'updated_at': (NOW - timedelta(minutes=61)).isoformat()},
            {'updated_at': None}, {'owner_date': '2026-11-02'},
        ):
            with self.subTest(updates=updates):
                result = self.build([fixture('001', **updates), fixture('002')])
                self.assertIsNone(result['ticket'])

    def test_missing_total_or_ratio_is_not_replaced_by_default_data(self):
        for updates in ({'ou_current_total': None}, {'betting_ratio': {}},
                        {'hi_current_draw_odds': None}, {'ou_current_total': 22.5},
                        {'asian_current_handicap': 'unknown'}, {'ou_current_over_odds': None}):
            result = self.build([fixture('001', **updates), fixture('002')])
            self.assertIsNone(result['ticket'])
            self.assertTrue(result['excluded_matches'])

    def test_overheated_primary_is_not_replaced_by_other_outcome(self):
        match = fixture('001')
        match['betting_ratio']['ordinary']['home_support_rate'] = 90
        match['betting_ratio']['ordinary']['draw_support_rate'] = 5
        match['betting_ratio']['ordinary']['away_support_rate'] = 5
        result = self.build([match, fixture('002')])
        self.assertIsNone(result['ticket'])
        self.assertEqual(result['audit'][0]['primary']['selection'], '主胜')
        self.assertIn('主选投注偏离超过+10个百分点', result['excluded_matches'][0]['reasons'])

    def test_two_qualifying_legs_outside_price_range_do_not_force_a_ticket(self):
        result = self.build([fixture('001', euro_current_win=2.0), fixture('002', euro_current_win=2.0)])
        self.assertIsNone(result['ticket'])
        self.assertEqual(result['eligible_match_count'], 2)

    def test_duplicate_fixture_cannot_form_a_two_leg_ticket(self):
        result = self.build([fixture('001'), fixture('001')])
        self.assertIsNone(result['ticket'])

    def test_future_fundamental_results_are_removed_without_mutating_cache(self):
        fundamentals = {'001': {'recent': {'home': [
            {'date': '2026-11-01'}, {'date': '2026-11-04'}, {'date': '2026-11-03'}]}}}
        engine = fake_engine()
        result = self.build(fundamentals=fundamentals, engine=engine)
        source = result['audit'][0]['context']['source_analysis']
        self.assertEqual(source['recent']['home'], [{'date': '2026-11-01'}])
        self.assertEqual(len(fundamentals['001']['recent']['home']), 3)

    def test_settlement_uses_frozen_prices_and_waits_for_both_results(self):
        record = self.build()
        original = deepcopy(record)
        results = {'001': {'status': 2, 'score': '2:0', 'euro_current_win': 9.9},
                   '002': {'status': 1, 'score': '0:0'}}
        result = settle_ticket(record['ticket'], results)
        self.assertEqual(result['status'], 'pending')
        self.assertIsNone(result['profit_units'])
        results['002'] = {'status': 2, 'score': '1:0'}
        result = settle_ticket(record['ticket'], results)
        self.assertEqual(result['status'], 'hit')
        self.assertEqual(result['return_units'], 3.0625)
        self.assertEqual(result['profit_units'], 2.0625)
        self.assertEqual(record, original)

    def test_one_void_leg_reduces_ticket_odds_instead_of_paying_full_product(self):
        ticket = self.build()['ticket']
        result = settle_ticket(ticket, {'001': {'status': 6}, '002': {'status': 2, 'score': '1:0'}})
        self.assertEqual(result['return_units'], 1.75)
        result = settle_ticket(ticket, {'001': {'status': 6}, '002': {'status': 6}})
        self.assertEqual(result['status'], 'push')
        self.assertEqual(result['profit_units'], 0)

    def test_handicap_settlement_uses_home_signed_line(self):
        ticket = self.build()['ticket']
        ticket['picks'][0].update(selection='让平', handicap=1)
        result = settle_ticket(ticket, {'001': {'status': 2, 'score': '0:1'},
                                        '002': {'status': 2, 'score': '2:1'}})
        self.assertEqual(result['status'], 'hit')

    def test_report_counts_tickets_not_legs_and_keeps_skips_and_drawdown(self):
        first = self.build()
        second = deepcopy(first)
        second['owner_date'] = '2026-11-04'
        third = deepcopy(first)
        third.update(owner_date='2026-11-05', decision='no_ticket', ticket=None, baseline_ticket=None)
        reviews = []
        for day, score in ((first, '2:1'), (second, '0:1')):
            review = {'owner_date': day['owner_date'], 'snapshot_hash': day['snapshot_hash']}
            review['ticket'] = settle_ticket(day['ticket'], {'001': {'status': 2, 'score': score}, '002': {'status': 2, 'score': '1:0'}})
            review['baseline_ticket'] = review['ticket']
            reviews.append(review)
        report = trial_report(self.trial, [second, first, third], reviews, now=NOW)
        self.assertEqual(report['summary']['settled'], 2)
        self.assertEqual(report['summary']['hit_rate'], 50)
        self.assertEqual(report['summary']['roi'], 53.12)
        self.assertEqual(report['summary']['max_drawdown_units'], 1)
        self.assertEqual(report['no_ticket_days'], 1)
        self.assertNotIn('audit', report['days'][0])
        self.assertIsNotNone(report['summary']['hit_rate_interval_95'])

    def test_report_rejects_outcome_for_another_snapshot(self):
        day = self.build()
        report = trial_report(self.trial, [day], [{
            'owner_date': day['owner_date'], 'snapshot_hash': 'wrong', 'ticket': None,
        }], now=NOW)
        self.assertEqual(report['summary']['pending'], 1)

    def test_bootstrap_interval_is_repeatable_and_does_not_publish_strategy(self):
        day = self.build()
        days, reviews = [], []
        for index in range(6):
            row = deepcopy(day)
            row['owner_date'] = f'2026-11-{index + 3:02d}'
            days.append(row)
            ticket = settle_ticket(row['ticket'], {'001': {'status': 2, 'score': '1:0' if index % 2 else '0:1'}, '002': {'status': 2, 'score': '1:0'}})
            reviews.append({'owner_date': row['owner_date'], 'snapshot_hash': row['snapshot_hash'], 'ticket': ticket})
        report = trial_report(self.trial, days, reviews, now=NOW)
        self.assertIsNotNone(report['summary']['roi_interval_95'])
        self.assertEqual(report['summary']['roi_interval_95'], trial_report(self.trial, days, reviews, now=NOW)['summary']['roi_interval_95'])
        self.assertIn('尚未证明盈利', report['verdict'])


class TrialStorageTests(unittest.TestCase):
    def test_decisions_and_experiment_are_insert_only(self):
        storage = MongoDBStorage.__new__(MongoDBStorage)
        storage.fae_strategy_trials_collection = Mock()
        storage.fae_strategy_trial_days_collection = Mock()
        trial = start_trial(NOW)
        storage.start_strategy_trial(trial)
        update = storage.fae_strategy_trials_collection.update_one.call_args.args[1]
        self.assertEqual(set(update), {'$setOnInsert'})
        storage.save_strategy_trial_day({'experiment_id': 'x', 'owner_date': '2026-11-03', 'ticket': None})
        update = storage.fae_strategy_trial_days_collection.update_one.call_args.args[1]
        self.assertEqual(set(update), {'$setOnInsert'})


class TrialSchedulingTests(unittest.TestCase):
    def load_job(self, now):
        source = Path(__file__).resolve().parents[1] / 'web_app.py'
        node = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == '_run_scheduled_strategy_trial')
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now
        storage = Mock()
        trial = start_trial(NOW - timedelta(hours=8))
        trial['policy']['llm_enabled'] = False
        storage.get_strategy_trial.return_value = trial
        storage.get_strategy_trial_days.return_value = []
        storage.get_matches.return_value = [fixture('001'), fixture('002')]
        storage.get_match_fundamentals_bulk.return_value = {}
        storage.save_strategy_trial_day.side_effect = lambda day: day
        namespace = dict(datetime=Clock, timedelta=timedelta, app_timezone=app_timezone,
                         mongo_storage=storage, TRIAL_VERSION='paper-two-leg-v1', HISTORICAL_TRIAL_VERSIONS=('legacy',),
                         decision_time=decision_time, build_trial_day=Mock(return_value={
                             'decision': 'no_ticket', 'owner_date': '2026-11-03', 'ticket': None, 'baseline_ticket': None}),
                         settle_ticket=settle_ticket, app=SimpleNamespace(logger=Mock()))
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
        return namespace, storage

    def test_scheduler_waits_until_18_without_reading_markets(self):
        job, storage = self.load_job(NOW - timedelta(minutes=1))
        job['_run_scheduled_strategy_trial']()
        storage.get_matches.assert_not_called()
        job['build_trial_day'].assert_not_called()

    def test_scheduler_captures_exactly_one_decision_in_window(self):
        job, storage = self.load_job(NOW)
        job['_run_scheduled_strategy_trial']()
        storage.save_strategy_trial_day.assert_called_once()
        job['build_trial_day'].assert_called_once()
        storage.get_matches.assert_called_once_with(filters={'owner_date': '2026-11-03'})

    def test_missed_day_does_not_read_new_market_data(self):
        job, storage = self.load_job(NOW + timedelta(minutes=10))
        job['_run_scheduled_strategy_trial']()
        storage.get_matches.assert_not_called()
        self.assertEqual(job['build_trial_day'].call_args.args[2], [])

    def test_already_frozen_day_is_not_reselected(self):
        job, storage = self.load_job(NOW)
        storage.get_strategy_trial_days.return_value = [{
            'owner_date': '2026-11-03', 'ticket': None, 'baseline_ticket': None}]
        job['_run_scheduled_strategy_trial']()
        job['build_trial_day'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
