from copy import deepcopy
import ast
from datetime import datetime, timedelta
from functools import wraps
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from flask import Flask, jsonify, request, session
from pymongo.errors import DuplicateKeyError

from db_storage import MongoDBStorage
from fae_schedule import app_timezone
from football_ai.provider import ArkNarrativeClient, FAEProviderError
from football_ai.strategy_trial import digest, start_trial, trial_report
from football_ai.strategy_trial_manual import (
    execute_manual_run, make_manual_run, manual_run_summary, merge_manual_runs,
)
from football_ai.strategy_trial_retry import TrialRetryError
from tests.test_fae_strategy_trial import NOW, fake_engine, fixture
from tests.test_fae_strategy_trial_ai import response


MANUAL_NOW = NOW + timedelta(hours=2)


def fresh_match(mid, **changes):
    row = fixture(mid, updated_at=MANUAL_NOW.isoformat())
    row['betting_ratio']['fetched_at'] = MANUAL_NOW.isoformat()
    row.update(changes)
    return row


class ManualMemory:
    def __init__(self, run, matches):
        self.run = deepcopy(run)
        self.matches = deepcopy(matches)
        self.control = {key: run[key] for key in ('request_id', 'experiment_id', 'owner_date', 'status')}

    def get_strategy_trial_manual_run(self, rid):
        return deepcopy(self.run)

    def get_strategy_trial_manual_control(self, *args):
        return deepcopy(self.control)

    def get_matches(self, **kwargs):
        return deepcopy(self.matches)

    def claim_strategy_trial_manual_batch(self, rid, index, batch):
        if str(index) in self.run['batches'] or self.run['status'] not in ('queued', 'running'):
            return False
        self.run['batches'][str(index)] = deepcopy(batch)
        self.run['status'] = self.control['status'] = 'running'
        return True

    def finish_strategy_trial_manual_batch(self, rid, index, token, batch):
        saved = self.run['batches'][str(index)]
        if saved['claim_token'] == token and saved['status'] == 'running':
            self.run['batches'][str(index)] = deepcopy(batch)
            self.run['completed_batches'] += 1

    def fail_strategy_trial_manual_run(self, rid, error):
        self.run.update(status='failed', error=error)
        self.control.update(status='failed', error=error)

    def complete_strategy_trial_manual_run(self, rid, result, completed_at):
        self.run.update(status='complete', result=deepcopy(result), completed_at=completed_at)
        self.control.update(status='complete', completed_at=completed_at)


class ManualRunTests(unittest.TestCase):
    def setUp(self):
        self.client = ArkNarrativeClient(api_key='unit-manual-secret', timeout=180,
            max_tokens=6144, thinking='disabled', json_mode=True)
        self.client.max_retries = 0
        self.client.generate = Mock(return_value=(json.dumps({'matches': [response('001'), response('002')]}),
                                                  {'response_id': 'manual-call'}))
        self.trial = start_trial(NOW - timedelta(hours=8), client=self.client)
        self.matches = [fresh_match('001'), fresh_match('002')]
        self.engine_patch = patch('football_ai.strategy_trial.FootballAIEngine', return_value=fake_engine())
        self.engine_patch.start()
        self.addCleanup(self.engine_patch.stop)

    def queue(self, trial=None, matches=None):
        run = make_manual_run(trial or self.trial, matches or self.matches, {}, {'username': 'wangkai'},
                              owner_date=NOW.date().isoformat(), now=MANUAL_NOW)
        return run, ManualMemory(run, matches or self.matches)

    def execute(self, memory, clock=None):
        return execute_manual_run(self.trial, memory.get_strategy_trial_manual_run('rid'), memory,
                                  client=self.client, clock=clock or (lambda: MANUAL_NOW))

    def test_current_snapshot_outside_16_window_does_not_change_fixed_trial(self):
        original = deepcopy(self.trial)
        run, memory = self.queue()
        self.assertEqual(self.trial, original)
        self.assertEqual(run['input_snapshot']['captured_at'], MANUAL_NOW.isoformat())
        self.assertEqual(run['input_snapshot']['regular_scheduled_at'], NOW.isoformat())
        self.assertEqual(run['trial_snapshot']['policy']['decision_hour'], 18)
        self.assertEqual(self.trial['policy']['decision_hour'], 16)
        self.client.generate.assert_not_called()
        result = self.execute(memory)
        self.assertTrue(result['manual_run'])
        self.assertEqual(result['decision'], 'ticket')
        self.assertEqual(result['ai_run']['verified_matches'], 2)
        self.assertEqual(result['ai_run']['failed_matches'], 0)
        self.assertIn('新快照', result['reason'])
        self.assertEqual(memory.run['input_snapshot'], run['input_snapshot'])
        self.assertEqual(self.trial, original)
        self.client.generate.assert_called_once()

    def test_changed_current_odds_are_used_instead_of_old_input(self):
        self.matches[0]['euro_current_win'] = 1.9
        run, memory = self.queue()
        result = self.execute(memory)
        self.assertEqual(result['audit'][0]['raw_snapshot']['euro_current_win'], 1.9)
        prompt = self.client.generate.call_args.args[0]
        self.assertIn('"euro_current_win": 1.9', prompt)
        self.assertEqual(run['canonical_policy_hash'], self.trial['policy_hash'])

    def test_manual_run_allowed_today_when_automatic_enrollment_starts_tomorrow(self):
        self.trial = start_trial(MANUAL_NOW, client=self.client)
        self.trial['schedule_effective_date'] = '2026-11-04'
        old = deepcopy(self.trial)
        run, memory = self.queue()
        result = self.execute(memory)
        self.assertEqual(result['decision'], 'ticket')
        self.assertEqual(run['trial_snapshot']['start_date'], '2026-11-03')
        self.assertEqual(self.trial, old)

    def test_only_unstarted_same_day_matches_with_ten_minutes_lead_are_analyzed(self):
        matches = self.matches + [fresh_match('003', status=1), fresh_match('004', owner_date='2026-11-04'),
            fresh_match('005', match_time='11-03 18:09'), fresh_match('006', match_time=None)]
        run, memory = self.queue(matches=matches)
        self.assertEqual(run['match_count'], 2)
        self.execute(memory)
        prompt = self.client.generate.call_args.args[0]
        payload = json.loads(prompt.split('冻结输入：\n')[1])
        self.assertEqual([row['match_id'] for row in payload['matches']], ['001', '002'])

    def test_not_enough_fresh_matches_rejects_before_paid_call(self):
        for matches in ([fresh_match('001')], [fixture('001'), fixture('002')]):
            with self.assertRaises(TrialRetryError):
                self.queue(matches=matches)
        self.client.generate.assert_not_called()

    def test_past_future_and_expired_dates_are_rejected(self):
        for date in ('2026-11-02', '2026-11-04'):
            with self.assertRaises(TrialRetryError):
                make_manual_run(self.trial, self.matches, {}, {}, owner_date=date, now=MANUAL_NOW)
        self.trial['end_date'] = '2026-11-02'
        with self.assertRaises(TrialRetryError):
            self.queue()
        self.client.generate.assert_not_called()

    def test_invalid_rule_or_weights_block_before_call(self):
        for field in ('policy_hash', 'model_fingerprint', 'weights_hash'):
            trial = deepcopy(self.trial)
            trial[field] = 'changed'
            with self.assertRaises(TrialRetryError):
                self.queue(trial=trial)
        self.client.generate.assert_not_called()

    def test_queue_to_lease_race_neither_executes_nor_fails_request(self):
        _, memory = self.queue()
        memory.control = {}
        self.assertIsNone(self.execute(memory))
        self.assertEqual(memory.run['status'], 'queued')
        self.client.generate.assert_not_called()

    def test_model_configuration_change_after_queue_prevents_payment(self):
        _, memory = self.queue()
        self.client.model = 'new-model'
        self.assertIsNone(self.execute(memory))
        self.assertEqual(memory.run['status'], 'failed')
        self.client.generate.assert_not_called()

    def test_batch_rechecks_live_status_before_paying(self):
        _, memory = self.queue()
        memory.matches[0]['status'] = 1
        result = self.execute(memory)
        self.assertEqual(result['decision'], 'ai_failed')
        self.client.generate.assert_not_called()

    def test_completed_checkpoint_does_not_pay_again_and_partial_rows_are_salvaged(self):
        _, memory = self.queue()
        self.client.generate.return_value = (json.dumps({'matches': [response('001'), response('002'), response('unknown')]}), {})
        self.execute(memory)
        self.assertIsNone(self.execute(memory))
        self.assertEqual(memory.run['result']['ai_run']['verified_matches'], 2)
        self.client.generate.assert_called_once()

    def test_failed_calls_do_not_auto_repay_or_leak_credentials(self):
        _, memory = self.queue()
        self.client.generate.side_effect = FAEProviderError('failed unit-manual-secret')
        self.assertEqual(self.execute(memory)['decision'], 'ai_failed')
        self.assertIsNone(self.execute(memory))
        self.assertNotIn('unit-manual-secret', json.dumps(memory.run))
        self.client.generate.assert_called_once()

    def test_inflight_checkpoint_is_not_stolen_and_stale_one_fails_without_repay(self):
        _, memory = self.queue()
        memory.claim_strategy_trial_manual_batch('', 0, {'status': 'running', 'claim_token': 'other',
            'started_at': MANUAL_NOW.isoformat(), 'match_ids': ['001', '002']})
        self.assertIsNone(self.execute(memory))
        result = self.execute(memory, clock=lambda: MANUAL_NOW + timedelta(minutes=5))
        self.assertEqual(result['decision'], 'ai_failed')
        self.client.generate.assert_not_called()

    def test_finished_after_kickoff_does_not_publish_pregame_ticket(self):
        _, memory = self.queue()
        times = [MANUAL_NOW]
        def generate(prompt):
            times[0] += timedelta(hours=4)
            return json.dumps({'matches': [response('001'), response('002')]}), {}
        self.client.generate.side_effect = generate
        self.assertIsNone(self.execute(memory, clock=lambda: times[0])['ticket'])

    def test_many_matches_are_limited_to_ten_per_batch(self):
        run, memory = self.queue(matches=[fresh_match(f'{n:03d}') for n in range(1, 22)])
        def generate(prompt):
            rows = json.loads(prompt.split('冻结输入：\n')[1])['matches']
            self.assertLessEqual(len(rows), 10)
            return json.dumps({'matches': [response(row['match_id']) for row in rows]}), {}
        self.client.generate.side_effect = generate
        self.assertEqual(run['batch_count'], 3)
        self.assertEqual(self.execute(memory)['ai_run']['verified_matches'], 21)
        self.assertEqual(self.client.generate.call_count, 3)

    def test_latest_completed_publication_wins_without_mutating_history(self):
        run, memory = self.queue()
        result = self.execute(memory)
        old = {**deepcopy(result), 'snapshot_hash': 'old', 'manual_run': False, 'published_at': NOW.isoformat()}
        original = deepcopy(old)
        merged = merge_manual_runs([old], [memory.run], [memory.control])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['snapshot_hash'], result['snapshot_hash'])
        self.assertEqual(old, original)
        self.assertEqual(trial_report(self.trial, merged, [], now=MANUAL_NOW)['summary']['tickets'], 1)
        newer_auto = {**old, 'published_at': (MANUAL_NOW + timedelta(minutes=1)).isoformat()}
        self.assertEqual(merge_manual_runs([newer_auto], [memory.run])[0]['snapshot_hash'], 'old')

    def test_queued_and_failed_runs_do_not_hide_completed_day(self):
        run, _ = self.queue()
        day = {'owner_date': run['owner_date'], 'published_at': NOW.isoformat(), 'snapshot_hash': 'old'}
        for status in ('queued', 'running', 'failed', 'rejected'):
            self.assertEqual(merge_manual_runs([day], [{**run, 'status': status}])[0], day)
        self.assertEqual(merge_manual_runs([], [run])[0]['decision'], 'analyzing')
        self.assertEqual(merge_manual_runs([], [{**run, 'status': 'failed'}])[0]['decision'], 'ai_failed')

    def test_history_summary_contains_no_prompt_raw_input_or_user(self):
        _, memory = self.queue()
        self.execute(memory)
        summary = manual_run_summary(memory.run)
        self.assertEqual(summary['verified_matches'], 2)
        for key in ('input_snapshot', 'batches', 'trial_snapshot', 'requested_by', 'raw_response'):
            self.assertNotIn(key, summary)


class ManualRunStorageTests(unittest.TestCase):
    def setUp(self):
        self.storage = MongoDBStorage.__new__(MongoDBStorage)
        for name in ('fae_strategy_trial_manual_runs_collection', 'fae_strategy_trial_manual_controls_collection'):
            setattr(self.storage, name, Mock())
        self.runs = self.storage.fae_strategy_trial_manual_runs_collection
        self.controls = self.storage.fae_strategy_trial_manual_controls_collection
        self.controls.update_one.return_value.modified_count = 1
        self.runs.update_one.return_value.modified_count = 1
        self.run = {'request_id': 'rid', 'experiment_id': 'v3', 'owner_date': '2026-11-03',
            'status': 'queued', 'requested_at': MANUAL_NOW.isoformat(), 'match_count': 2, 'batch_count': 1}

    def test_run_is_persisted_before_unique_control_lease_is_acquired(self):
        order = []
        self.runs.insert_one.side_effect = lambda *args: order.append('persist')
        self.controls.update_one.side_effect = lambda *args, **kwargs: order.append('lease') or SimpleNamespace(modified_count=1)
        self.assertTrue(self.storage.request_strategy_trial_manual_run(self.run))
        self.assertEqual(order, ['persist', 'lease'])
        query, update = self.controls.update_one.call_args.args
        self.assertEqual(query['_id'], 'v3:2026-11-03')
        self.assertEqual(query['status'], {'$nin': ['queued', 'running']})
        self.assertIn('error', update['$unset'])

    def test_concurrent_control_upsert_is_rejected_without_overwriting_first_run(self):
        self.controls.update_one.side_effect = DuplicateKeyError('active control')
        self.assertFalse(self.storage.request_strategy_trial_manual_run(self.run))
        query, update = self.runs.update_one.call_args.args
        self.assertEqual(query['_id'], 'rid')
        self.assertEqual(update['$set']['status'], 'rejected')

    def test_claim_and_finish_use_atomic_batch_token(self):
        self.assertTrue(self.storage.claim_strategy_trial_manual_batch('rid', 0, {}))
        query = self.runs.update_one.call_args.args[0]
        self.assertEqual(query['batches.0'], {'$exists': False})
        self.storage.finish_strategy_trial_manual_batch('rid', 0, 'token', {})
        query, update = self.runs.update_one.call_args.args
        self.assertEqual(query['batches.0.claim_token'], 'token')
        self.assertEqual(query['batches.0.status'], 'running')
        self.assertEqual(update['$inc'], {'completed_batches': 1})

    def test_duplicate_finish_or_completion_cannot_advance_control_twice(self):
        self.runs.update_one.return_value.modified_count = 0
        self.storage.finish_strategy_trial_manual_batch('rid', 0, 'token', {})
        self.storage.complete_strategy_trial_manual_run('rid', {'ai_run': {}}, MANUAL_NOW.isoformat())
        self.controls.update_one.assert_not_called()

    def test_public_projection_excludes_prompt_and_runtime_credentials(self):
        self.runs.find.return_value.sort.return_value = []
        self.storage.get_strategy_trial_manual_runs('v3', pending=True)
        query, projection = self.runs.find.call_args.args
        self.assertEqual(query['status'], {'$in': ['queued', 'running']})
        for key in ('batches', 'trial_snapshot', 'input_snapshot.audit', 'result.audit', 'requested_by'):
            self.assertEqual(projection[key], 0)

    def test_terminal_run_repairs_stale_control_without_touching_a_new_request(self):
        self.storage.reconcile_strategy_trial_manual_control({**self.run, 'status': 'complete',
            'completed_at': MANUAL_NOW.isoformat(), 'result': {'ai_run': {'verified_matches': 2, 'failed_matches': 0}}})
        query, update = self.controls.update_one.call_args.args
        self.assertEqual(query, {'request_id': 'rid', 'status': {'$in': ['queued', 'running']}})
        self.assertEqual(update['$set']['status'], 'complete')
        self.assertEqual(update['$set']['verified_matches'], 2)
        self.runs.update_one.assert_not_called()


class ManualRunRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask('trial-run-now-tests')
        self.app.secret_key = 'unit-only'
        self.storage, self.admin, self.thread = Mock(), Mock(return_value={'username': 'wangkai'}), Mock()
        self.storage.get_strategy_trial.return_value = {'ai_settings': {'version': 'unit'}}
        self.storage.get_strategy_trial_manual_control.return_value = None
        self.storage.get_strategy_trial_ai_job.return_value = None
        self.storage.get_strategy_trial_days.return_value = []
        self.storage.get_matches.return_value = []
        self.storage.get_match_fundamentals_bulk.return_value = {}
        self.storage.request_strategy_trial_manual_run.return_value = True
        self.prepare = Mock(return_value={'request_id': 'rid', 'match_count': 4})
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return MANUAL_NOW
        source = Path(__file__).resolve().parents[1] / 'web_app.py'
        nodes = [node for node in ast.parse(source.read_text()).body if isinstance(node, ast.FunctionDef)
                 and node.name in ('fae_admin_required', 'run_fae_strategy_trial_now')]
        self.namespace = dict(app=self.app, jsonify=jsonify, request=request, session=session,
            wraps=wraps, datetime=Clock, app_timezone=app_timezone, user_storage=Mock(),
            _fae_admin_user=self.admin, TRIAL_VERSION='v3', mongo_storage=self.storage,
            make_trial_ai_client=lambda: SimpleNamespace(configured=True),
            trial_ai_settings=lambda client: {'version': 'unit'}, TrialRetryError=TrialRetryError,
            make_manual_run=self.prepare, threading=SimpleNamespace(Thread=self.thread),
            _run_strategy_trial_manual_now=Mock())
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), self.namespace)
        self.logged_in = False

    def send(self, **changes):
        payload = {'date': '2026-11-03', 'confirm_api_cost': True}
        payload.update(changes)
        with self.app.test_request_context('/api/fae/strategy-trial/run-now', method='POST', json=payload):
            if self.logged_in:
                session['user_id'] = 1
            return self.app.make_response(self.namespace['run_fae_strategy_trial_now']())

    def test_admin_and_explicit_cost_confirmation_are_required(self):
        self.assertEqual(self.send().status_code, 401)
        self.logged_in = True
        self.admin.return_value = None
        self.assertEqual(self.send().status_code, 403)
        self.admin.return_value = {'username': 'wangkai'}
        for value in (False, 'true', None):
            self.assertEqual(self.send(confirm_api_cost=value).status_code, 422)
        self.prepare.assert_not_called()

    def test_only_today_can_be_manually_run(self):
        self.logged_in = True
        for date in ('2026-11-02', '2026-11-04', 'bad'):
            self.assertEqual(self.send(date=date).status_code, 422)
        self.prepare.assert_not_called()

    def test_request_returns_202_after_saving_and_starting_background_worker(self):
        self.logged_in = True
        self.assertEqual(self.send().status_code, 202)
        self.storage.request_strategy_trial_manual_run.assert_called_once_with(self.prepare.return_value)
        self.thread.return_value.start.assert_called_once()
        self.assertEqual(self.prepare.call_args.kwargs['now'], MANUAL_NOW)

    def test_concurrent_active_or_atomic_rejected_requests_do_not_start_worker(self):
        self.logged_in = True
        self.storage.get_strategy_trial_manual_control.return_value = {'status': 'running'}
        self.assertEqual(self.send().status_code, 409)
        self.prepare.assert_not_called()
        self.storage.get_strategy_trial_manual_control.return_value = None
        self.storage.request_strategy_trial_manual_run.return_value = False
        self.assertEqual(self.send().status_code, 409)
        self.thread.assert_not_called()

    def test_active_automatic_job_and_legacy_retry_block_duplicate_cost(self):
        self.logged_in = True
        self.storage.get_strategy_trial_ai_job.return_value = {'input_snapshot': {}}
        self.assertEqual(self.send().status_code, 409)
        self.storage.get_strategy_trial_days.return_value = [{'owner_date': '2026-11-03'}]
        self.storage.get_strategy_trial_ai_job.return_value = {'retry_state': {'status': 'queued'}}
        self.assertEqual(self.send().status_code, 409)
        self.prepare.assert_not_called()

    def test_latest_rules_can_run_even_with_an_old_day_archive(self):
        self.logged_in = True
        self.storage.get_strategy_trial_days.return_value = [{'owner_date': '2026-11-03', 'policy_hash': 'old'}]
        self.storage.get_strategy_trial_ai_job.return_value = {'retry_state': {'status': 'complete'}}
        self.assertEqual(self.send().status_code, 202)

    def test_insufficient_candidates_does_not_enqueue_or_pay(self):
        self.logged_in = True
        self.prepare.side_effect = TrialRetryError('不足两场')
        result = self.send()
        self.assertEqual(result.status_code, 409)
        self.assertIn('不足两场', result.json['message'])
        self.storage.request_strategy_trial_manual_run.assert_not_called()
        self.thread.assert_not_called()

    def test_thread_start_failure_keeps_persisted_queue_for_scheduler_recovery(self):
        self.logged_in = True
        self.thread.return_value.start.side_effect = RuntimeError('thread unavailable')
        with self.assertLogs(self.app.logger, level='ERROR'):
            self.assertEqual(self.send().status_code, 202)
        self.storage.request_strategy_trial_manual_run.assert_called_once()


if __name__ == '__main__':
    unittest.main()
