from copy import deepcopy
import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from fae_schedule import app_timezone
from football_ai.strategy_trial import (
    TRIAL_POLICY, TRIAL_VERSION, V3_1400_FINGERPRINT, V3_1800_FINGERPRINT, archive_trial_job,
    build_trial_day, decision_time, digest, reschedule_trial, settle_draw_ticket,
    settle_ticket, start_trial, trial_report,
)
from db_storage import MongoDBStorage
from football_ai.strategy_trial_retry import merge_trial_days
from football_ai.strategy_trial_manual import merge_manual_runs


NOW = datetime(2026, 11, 3, 16, tzinfo=app_timezone())


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
        self.trial['policy'].pop('selection_mode', None)
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
            {'status': 1}, {'match_time': ''}, {'match_time': '11-03 16:05'},
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
        storage.get_strategy_trial_ai_jobs.return_value = []
        storage.get_strategy_trial_manual_runs.return_value = []
        storage.get_strategy_trial_manual_controls.return_value = []
        storage.save_strategy_trial_day.side_effect = lambda day: day
        namespace = dict(datetime=Clock, timedelta=timedelta, app_timezone=app_timezone,
                         mongo_storage=storage, TRIAL_VERSION=TRIAL_VERSION,
                         reschedule_trial=reschedule_trial,
                         archive_trial_job=archive_trial_job, execute_trial_ai_job=Mock(),
                         merge_trial_days=merge_trial_days, _run_strategy_trial_manual_retry=Mock(),
                         merge_manual_runs=merge_manual_runs, _run_strategy_trial_manual_now=Mock(),
                         decision_time=decision_time, build_trial_day=Mock(return_value={
                             'decision': 'no_ticket', 'owner_date': '2026-11-03', 'ticket': None, 'baseline_ticket': None}),
                         settle_ticket=settle_ticket, settle_draw_ticket=settle_draw_ticket,
                         app=SimpleNamespace(logger=Mock()))
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
        return namespace, storage

    def test_scheduler_waits_until_16_without_reading_markets(self):
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

    def test_scheduler_does_not_read_retired_versions(self):
        job, storage = self.load_job(NOW)
        job['_run_scheduled_strategy_trial']()
        self.assertEqual({call.args[0] for call in storage.get_strategy_trial.call_args_list}, {TRIAL_VERSION})
        self.assertEqual({call.args[0] for call in storage.get_strategy_trial_days.call_args_list}, {TRIAL_VERSION})

    def test_after_16_does_not_generate_another_decision_at_18(self):
        job, storage = self.load_job(NOW + timedelta(hours=2))
        storage.get_strategy_trial_days.return_value = [{
            'owner_date': '2026-11-03', 'ticket': None, 'baseline_ticket': None}]
        job['_run_scheduled_strategy_trial']()
        job['build_trial_day'].assert_not_called()
        storage.get_matches.assert_not_called()

    def test_existing_18_trial_is_migrated_but_not_backfilled_after_16(self):
        job, storage = self.load_job(NOW + timedelta(hours=1))
        storage.get_strategy_trial.return_value = TrialScheduleMigrationTests().old_trial()
        storage.update_strategy_trial_schedule.side_effect = lambda old, updated: updated
        job['_run_scheduled_strategy_trial']()
        storage.update_strategy_trial_schedule.assert_called_once()
        updated = storage.update_strategy_trial_schedule.call_args.args[1]
        self.assertEqual(updated['policy']['decision_hour'], 16)
        self.assertEqual(updated['schedule_effective_date'], '2026-11-04')
        storage.get_matches.assert_not_called()
        job['build_trial_day'].assert_not_called()
        job['execute_trial_ai_job'].assert_not_called()

    def test_pending_18_job_is_archived_without_paid_retry_or_input_mutation(self):
        job, storage = self.load_job(NOW + timedelta(hours=1))
        old = TrialScheduleMigrationTests().old_trial()
        storage.get_strategy_trial.return_value = reschedule_trial(old, NOW)
        snapshot = {'owner_date': '2026-11-03', 'experiment_id': TRIAL_VERSION,
                    'policy_hash': old['policy_hash'], 'snapshot_hash': 'frozen18',
                    'decision': 'analyzing', 'ticket': None, 'baseline_ticket': None,
                    'draw_ticket': None, 'draw_baseline_ticket': None}
        original = deepcopy(snapshot)
        storage.get_strategy_trial_ai_jobs.return_value = [{'owner_date': '2026-11-03'}]
        storage.get_strategy_trial_ai_job.return_value = {'input_snapshot': snapshot}
        job['_run_scheduled_strategy_trial']()
        archived = storage.save_strategy_trial_day.call_args.args[0]
        self.assertEqual(archived['decision'], 'suspended')
        self.assertEqual(archived['source_snapshot_hash'], 'frozen18')
        self.assertIn('16:00', archived['reason'])
        self.assertEqual(snapshot, original)
        job['execute_trial_ai_job'].assert_not_called()

    def test_scheduler_resumes_explicitly_queued_retry_after_browser_leaves(self):
        job, storage = self.load_job(NOW + timedelta(minutes=20))
        storage.get_strategy_trial.return_value['policy']['llm_enabled'] = True
        storage.get_strategy_trial_days.return_value = [{
            'owner_date': '2026-11-03', 'ticket': None, 'baseline_ticket': None}]
        storage.get_strategy_trial_ai_jobs.return_value = [{
            'owner_date': '2026-11-03', 'retry_state': {'status': 'queued'}}]
        job['_run_scheduled_strategy_trial']()
        job['_run_strategy_trial_manual_retry'].assert_called_once_with('2026-11-03')
        job['execute_trial_ai_job'].assert_not_called()

    def test_scheduler_settles_latest_retry_hash_without_overwriting_original_day(self):
        job, storage = self.load_job(NOW + timedelta(minutes=20))
        storage.get_strategy_trial.return_value['policy']['llm_enabled'] = True
        old = {'owner_date': '2026-11-03', 'ticket': None, 'baseline_ticket': None, 'snapshot_hash': 'original'}
        storage.get_strategy_trial_days.return_value = [old]
        ticket = {'picks': [{'match_id': '001', 'selection': '主胜', 'odds': 1.75},
                            {'match_id': '002', 'selection': '主胜', 'odds': 1.75}], 'stake': 1}
        storage.get_strategy_trial_ai_jobs.return_value = [{
            'owner_date': '2026-11-03', 'retry_state': {'status': 'complete'},
            'retry_result': {**old, 'snapshot_hash': 'retry-result', 'ticket': ticket}}]
        storage.get_matches.return_value = [fixture(mid, status=2, score='2:0') for mid in ('001', '002')]
        job['_run_scheduled_strategy_trial']()
        review = storage.save_strategy_trial_review.call_args.args[0]
        self.assertEqual(review['snapshot_hash'], 'retry-result')
        self.assertEqual(review['ticket']['status'], 'hit')
        self.assertEqual(old['snapshot_hash'], 'original')
        storage.save_strategy_trial_day.assert_not_called()

    def test_scheduler_recovers_manual_run_and_settles_latest_result(self):
        job, storage = self.load_job(NOW + timedelta(hours=2))
        storage.get_strategy_trial.return_value['policy']['llm_enabled'] = True
        old = {'owner_date': '2026-11-03', 'ticket': None, 'baseline_ticket': None,
               'snapshot_hash': 'original', 'published_at': NOW.isoformat()}
        storage.get_strategy_trial_days.return_value = [old]
        ticket = {'picks': [{'match_id': mid, 'selection': '主胜', 'odds': 1.75}
                             for mid in ('001', '002')], 'stake': 1}
        run = {'request_id': 'new-run', 'owner_date': old['owner_date'], 'status': 'queued',
               'requested_at': (NOW + timedelta(hours=1)).isoformat(),
               'result': {**old, 'snapshot_hash': 'manual', 'ticket': ticket,
                          'published_at': (NOW + timedelta(hours=1, minutes=1)).isoformat()}}
        storage.get_strategy_trial_manual_runs.return_value = [run]
        storage.get_matches.return_value = [fixture(mid, status=2, score='2:0') for mid in ('001', '002')]
        job['_run_scheduled_strategy_trial']()
        job['_run_strategy_trial_manual_now'].assert_called_once_with('new-run')
        review = storage.save_strategy_trial_review.call_args.args[0]
        self.assertEqual(review['snapshot_hash'], 'manual')
        self.assertEqual(review['ticket']['status'], 'hit')
        self.assertEqual(old['snapshot_hash'], 'original')
        storage.save_strategy_trial_day.assert_not_called()

    def test_scheduler_recovers_stale_control_after_run_already_finished(self):
        job, storage = self.load_job(NOW + timedelta(hours=2))
        storage.get_strategy_trial.return_value['policy']['llm_enabled'] = True
        storage.get_strategy_trial_days.return_value = [{'owner_date': '2026-11-03', 'ticket': None}]
        storage.get_strategy_trial_manual_controls.return_value = [
            {'request_id': 'completed-but-locked', 'owner_date': '2026-11-03', 'status': 'running'}]
        job['_run_scheduled_strategy_trial']()
        job['_run_strategy_trial_manual_now'].assert_called_once_with('completed-but-locked')


class TrialScheduleMigrationTests(unittest.TestCase):
    def old_trial(self, hour=18):
        trial = start_trial(NOW - timedelta(hours=8))
        trial['policy'].pop('selection_mode', None)
        trial['policy']['decision_hour'] = hour
        trial['policy_hash'] = digest(trial['policy'])
        trial['model_fingerprint'] = {18: V3_1800_FINGERPRINT, 14: V3_1400_FINGERPRINT}[hour]
        return trial

    def test_existing_v3_moves_to_16_without_resetting_dates_or_history(self):
        old = self.old_trial()
        original = deepcopy(old)
        updated = reschedule_trial(old, NOW - timedelta(minutes=1))
        self.assertEqual(updated['policy']['decision_hour'], 16)
        self.assertEqual(updated['schedule_effective_date'], '2026-11-03')
        self.assertEqual(updated['start_date'], old['start_date'])
        self.assertEqual(updated['end_date'], old['end_date'])
        self.assertEqual(updated['schedule_history'][0]['previous_policy_hash'], old['policy_hash'])
        self.assertEqual(updated['policy_hash'], digest(TRIAL_POLICY))
        self.assertEqual(old, original)
        self.assertIsNone(reschedule_trial(updated, NOW))

    def test_at_or_after_16_only_takes_effect_tomorrow(self):
        for now in (NOW, NOW + timedelta(hours=4)):
            self.assertEqual(reschedule_trial(self.old_trial(), now)['schedule_effective_date'], '2026-11-04')

    def test_14_schedule_moves_to_16_today_even_when_old_cutoff_has_passed(self):
        old = self.old_trial(hour=14)
        old['schedule_effective_date'] = '2026-11-04'
        old['schedule_history'] = [{'previous_policy_hash': 'original18', 'effective_date': '2026-11-04'}]
        updated = reschedule_trial(old, NOW - timedelta(hours=1))
        self.assertEqual(updated['policy']['decision_hour'], 16)
        self.assertEqual(updated['schedule_effective_date'], '2026-11-03')
        self.assertEqual(updated['schedule_history'][0], old['schedule_history'][0])
        self.assertEqual(updated['schedule_history'][-1]['previous_policy']['decision_hour'], 14)
        self.assertEqual(updated['start_date'], old['start_date'])
        self.assertEqual(updated['end_date'], old['end_date'])

    def test_unknown_implementation_rules_or_version_are_not_silently_accepted(self):
        for field, value in (('model_fingerprint', 'unknown-code'), ('policy_hash', 'bad-hash'),
                             ('weights_hash', 'bad-weights'), ('experiment_id', 'retired')):
            old = self.old_trial()
            old[field] = value
            self.assertIsNone(reschedule_trial(old, NOW))
        old = self.old_trial()
        old['policy']['min_leg_odds'] = 1.1
        old['policy_hash'] = digest(old['policy'])
        self.assertIsNone(reschedule_trial(old, NOW))

    def test_schedule_write_is_atomic_and_does_not_touch_days_or_jobs(self):
        storage = MongoDBStorage.__new__(MongoDBStorage)
        storage.fae_strategy_trials_collection = Mock()
        old = self.old_trial()
        updated = reschedule_trial(old, NOW)
        storage.fae_strategy_trials_collection.find_one.return_value = updated
        self.assertEqual(storage.update_strategy_trial_schedule(old, updated), updated)
        filters, update = storage.fae_strategy_trials_collection.update_one.call_args.args
        self.assertEqual(filters['policy_hash'], old['policy_hash'])
        self.assertEqual(filters['model_fingerprint'], V3_1800_FINGERPRINT)
        self.assertNotIn('start_date', update['$set'])
        self.assertNotIn('end_date', update['$set'])
        self.assertNotIn('$setOnInsert', update)


class TrialCurrentVersionRouteTests(unittest.TestCase):
    def route(self, name, args):
        source = Path(__file__).resolve().parents[1] / 'web_app.py'
        node = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == name)
        node.decorator_list = []
        storage = Mock()
        storage.get_strategy_trial.return_value = None
        namespace = dict(mongo_storage=storage, request=SimpleNamespace(args=args),
                         TRIAL_VERSION=TRIAL_VERSION, TRIAL_POLICY=TRIAL_POLICY,
                         datetime=datetime, app_timezone=app_timezone,
                         jsonify=lambda value: value, _fae_admin_user=lambda: None)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
        return namespace[name], storage

    def test_only_v3_is_available_without_looking_up_old_trials(self):
        route, storage = self.route('get_fae_strategy_trial', {})
        result = route()
        self.assertEqual(result['available_experiments'], [TRIAL_VERSION])
        self.assertEqual(result['policy']['decision_hour'], 16)
        storage.get_strategy_trial.assert_called_once_with(TRIAL_VERSION)

    def test_report_and_audit_reject_retired_versions(self):
        for name in ('get_fae_strategy_trial', 'get_fae_strategy_trial_audit'):
            for version in ('paper-two-leg-v1', 'paper-two-leg-ark-v2'):
                route, storage = self.route(name, {'experiment_id': version})
                self.assertEqual(route()[1], 422)
                storage.get_strategy_trial.assert_not_called()
                storage.get_strategy_trial_days.assert_not_called()


if __name__ == '__main__':
    unittest.main()
