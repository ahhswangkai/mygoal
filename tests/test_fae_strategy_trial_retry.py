from copy import deepcopy
from datetime import timedelta
from datetime import datetime
from functools import wraps
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from flask import Flask, jsonify, request, session

from db_storage import MongoDBStorage
from football_ai.provider import ArkNarrativeClient, FAEProviderError
from football_ai.strategy_trial import build_trial_day, execute_trial_ai_job, start_trial, trial_report
from football_ai.strategy_trial_ai import TrialAIForecaster
from football_ai.strategy_trial_retry import (
    TrialRetryError, execute_manual_retry, make_retry_request, merge_trial_days, parse_retry_response,
)
from tests.test_fae_strategy_trial import NOW, fixture, fake_engine
from tests.test_fae_strategy_trial_ai import MemoryJobs, response


class RetryMemory(MemoryJobs):
    def __init__(self, snapshot, matches):
        super().__init__(snapshot)
        self.job.update(experiment_id=snapshot['experiment_id'], owner_date=snapshot['owner_date'], manual_retries={})
        self.matches = deepcopy(matches)

    def get_matches(self, **kwargs):
        return deepcopy(self.matches)

    def request_strategy_trial_retry(self, exp, day, request):
        if self.job.get('retry_state', {}).get('status') in ('queued', 'running'):
            return False
        self.job['manual_retries'][request['request_id']] = deepcopy(request)
        self.job['retry_state'] = {key: request[key] for key in ('request_id', 'status', 'batch_count')}
        return True

    def claim_strategy_trial_retry_batch(self, exp, day, rid, index, batch):
        request = self.job['manual_retries'][rid]
        if str(index) in request['batches'] or self.job['retry_state']['request_id'] != rid:
            return False
        request['batches'][str(index)] = deepcopy(batch)
        self.job['retry_state']['status'] = request['status'] = 'running'
        return True

    def finish_strategy_trial_retry_batch(self, exp, day, rid, index, token, batch):
        saved = self.job['manual_retries'][rid]['batches'][str(index)]
        if saved['claim_token'] == token and saved['status'] == 'running':
            self.job['manual_retries'][rid]['batches'][str(index)] = deepcopy(batch)

    def fail_strategy_trial_retry(self, exp, day, rid, message):
        self.job['retry_state'].update(status='failed', error=message)
        self.job['manual_retries'][rid]['status'] = 'failed'

    def complete_strategy_trial_retry(self, exp, day, rid, record, completed_at):
        self.job['retry_result'] = deepcopy(record)
        self.job['manual_retries'][rid].update(status='complete', result=deepcopy(record))
        self.job['retry_state'].update(status='complete', completed_at=completed_at)


class ManualRetryTests(unittest.TestCase):
    def setUp(self):
        self.client = ArkNarrativeClient(api_key='retry-secret', timeout=180, max_tokens=6144,
                                         thinking='disabled', json_mode=True)
        self.client.max_retries = 0
        self.client.generate = Mock(return_value=('not-json', {'response_id': 'original'}))
        self.forecaster = TrialAIForecaster(self.client)
        self.trial = start_trial(NOW - timedelta(hours=8), client=self.client)
        self.matches = [fixture('001'), fixture('002')]
        snapshot = build_trial_day(self.trial, NOW.date().isoformat(), self.matches,
                                   now=NOW, engine=fake_engine(), capture_only=True)
        self.storage = RetryMemory(snapshot, self.matches)
        self.day = execute_trial_ai_job(self.trial, self.storage.job, self.storage,
                                        forecaster=self.forecaster, clock=lambda: NOW)
        self.client.generate.reset_mock()
        self.client.generate.return_value = (json.dumps({'matches': [response('001'), response('002')]}), {'response_id': 'retry'})

    def queue(self, now=NOW, day=None):
        request = make_retry_request(self.trial, self.storage.job, day or self.day,
                                     self.storage.matches, {'username': 'wangkai'}, now=now)
        self.assertTrue(self.storage.request_strategy_trial_retry(self.trial['experiment_id'], self.day['owner_date'], request))
        return request

    def run_retry(self, now=NOW):
        return execute_manual_retry(self.trial, self.storage.get_strategy_trial_ai_job(), self.storage,
                                     forecaster=self.forecaster, clock=lambda: now)

    def test_explicit_retry_calls_provider_and_keeps_original_audit_unchanged(self):
        original = deepcopy(self.storage.job)
        old_day = deepcopy(self.day)
        request = self.queue()
        result = self.run_retry()
        self.assertTrue(result['manual_retry'])
        self.assertEqual(result['decision'], 'ticket')
        self.assertEqual(result['ai_run']['verified_matches'], 2)
        self.assertEqual(result['ai_run']['failed_matches'], 0)
        self.assertEqual(result['source_snapshot_hash'], original['input_snapshot']['snapshot_hash'])
        self.assertEqual(self.storage.job['batches'], original['batches'])
        self.assertEqual(self.storage.job['input_snapshot'], original['input_snapshot'])
        self.assertEqual(self.day, old_day)
        self.assertIn('result', self.storage.job['manual_retries'][request['request_id']])
        self.client.generate.assert_called_once()

    def test_double_click_is_rejected_before_another_call(self):
        self.queue()
        with self.assertRaises(TrialRetryError):
            self.queue()
        self.client.generate.assert_not_called()

    def test_no_failed_match_is_retried_after_success(self):
        self.queue()
        result = self.run_retry()
        with self.assertRaises(TrialRetryError):
            self.queue(day=result)
        self.client.generate.assert_called_once()

    def test_already_verified_forecast_is_not_requested_again(self):
        self.storage.job['batches']['0']['forecasts'] = {'001': {'ai_verified': True}}
        request = self.queue()
        self.assertEqual(request['match_ids'], ['002'])
        self.run_retry()
        prompt = self.client.generate.call_args.args[0]
        payload = json.loads(prompt.split('冻结输入：\n')[1])
        self.assertEqual([row['match_id'] for row in payload['matches']], ['002'])
        self.assertEqual(self.storage.job['batches']['0']['forecasts']['001'], {'ai_verified': True})

    def test_completed_requests_do_not_pay_again_when_scheduler_resumes(self):
        self.queue()
        self.run_retry()
        self.assertIsNone(self.run_retry())
        self.client.generate.assert_called_once()

    def test_partial_failure_only_retries_failed_ids_on_next_manual_request(self):
        self.queue()
        self.client.generate.return_value = (json.dumps({'matches': [response('001')]}), {})
        result = self.run_retry()
        self.assertEqual(result['ai_run']['failed_matches'], 1)
        request = self.queue(day=result)
        self.assertEqual(request['match_ids'], ['002'])

    def test_live_started_match_is_excluded_but_others_continue(self):
        self.storage.matches[0]['status'] = 1
        request = self.queue()
        self.assertEqual(request['match_ids'], ['002'])
        self.assertEqual(request['skipped'][0]['match_id'], '001')
        self.storage.matches[1]['status'] = 1
        result = self.run_retry()
        self.assertEqual(result['decision'], 'ai_failed')
        self.client.generate.assert_not_called()

    def test_past_or_near_kickoff_rejects_manual_request(self):
        for now in (NOW + timedelta(hours=5), NOW + timedelta(hours=4, minutes=51)):
            with self.assertRaises(TrialRetryError):
                self.queue(now=now)
        self.client.generate.assert_not_called()

    def test_provider_failure_redacts_secret_and_never_auto_retries(self):
        self.queue()
        self.client.generate.side_effect = FAEProviderError('failed retry-secret')
        result = self.run_retry()
        self.assertEqual(result['decision'], 'ai_failed')
        self.assertNotIn('retry-secret', json.dumps(self.storage.job))
        self.run_retry()
        self.client.generate.assert_called_once()

    def test_settings_change_blocks_before_any_paid_call(self):
        self.queue()
        self.client.model = 'changed'
        self.assertIsNone(self.run_retry())
        self.assertEqual(self.storage.job['retry_state']['status'], 'failed')
        self.client.generate.assert_not_called()

    def test_inflight_claim_is_not_stolen_or_repaid(self):
        request = self.queue()
        self.storage.claim_strategy_trial_retry_batch('', '', request['request_id'], 0,
            {'status': 'running', 'claim_token': 'other', 'started_at': NOW.isoformat(), 'match_ids': request['match_ids']})
        self.assertIsNone(self.run_retry())
        self.client.generate.assert_not_called()
        result = self.run_retry(now=NOW + timedelta(minutes=5))
        self.assertEqual(result['decision'], 'ai_failed')
        self.client.generate.assert_not_called()

    def test_published_ticket_is_frozen_even_if_new_candidates_arrive(self):
        self.day['ticket'] = {'picks': [{'match_id': 'original'}], 'combined_odds': 3.123, 'stake': 1}
        self.day['draw_ticket'] = {'picks': [{'match_id': 'draw-original'}], 'stake': 1}
        original = deepcopy(self.day)
        self.queue()
        result = self.run_retry()
        self.assertEqual(result['ticket'], original['ticket'])
        self.assertEqual(result['draw_ticket'], original['draw_ticket'])

    def test_report_and_settlement_choose_one_latest_revision_not_duplicate_days(self):
        self.queue()
        result = self.run_retry()
        merged = merge_trial_days([self.day], [self.storage.job])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['snapshot_hash'], result['snapshot_hash'])
        self.assertEqual(merged[0]['retry_state']['status'], 'complete')
        report = trial_report(self.trial, merged, [], now=NOW)
        self.assertEqual(report['summary']['tickets'], 1)
        self.assertNotEqual(self.day['snapshot_hash'], result['snapshot_hash'])

    def test_input_snapshot_does_not_refresh_to_new_odds(self):
        self.storage.matches[0]['euro_current_win'] = 9.9
        self.queue()
        result = self.run_retry()
        self.assertEqual(result['audit'][0]['raw_snapshot']['euro_current_win'], 1.75)
        self.assertEqual(result['captured_at'], self.storage.job['input_snapshot']['captured_at'])

    def test_retry_finishing_after_kickoff_is_not_a_pregame_forecast(self):
        self.queue()
        clock = [NOW]
        def generate(prompt):
            clock[0] = NOW + timedelta(hours=6)
            return json.dumps({'matches': [response('001'), response('002')]}), {}
        self.client.generate.side_effect = generate
        result = execute_manual_retry(self.trial, self.storage.get_strategy_trial_ai_job(), self.storage,
                                       forecaster=self.forecaster, clock=lambda: clock[0])
        self.assertIsNone(result['ticket'])
        self.assertEqual(result['ai_run']['verified_matches'], 0)

    def test_changed_weights_block_retry_without_payment(self):
        self.trial['rule_weights']['data-quality'] = 9
        with self.assertRaises(TrialRetryError):
            self.queue()
        self.client.generate.assert_not_called()

    def test_many_matches_are_retried_in_batches_of_at_most_ten(self):
        self.matches = [fixture(f'{index:03d}') for index in range(1, 22)]
        snapshot = build_trial_day(self.trial, NOW.date().isoformat(), self.matches,
                                   now=NOW, engine=fake_engine(), capture_only=True)
        self.storage = RetryMemory(snapshot, self.matches)
        self.client.generate.return_value = ('not-json', {})
        self.day = execute_trial_ai_job(self.trial, self.storage.job, self.storage,
                                        forecaster=self.forecaster, clock=lambda: NOW)
        self.client.generate.reset_mock()
        def generate(prompt):
            values = json.loads(prompt.split('冻结输入：\n')[1])['matches']
            self.assertLessEqual(len(values), 10)
            return json.dumps({'matches': [response(row['match_id']) for row in values]}), {}
        self.client.generate.side_effect = generate
        self.queue()
        result = self.run_retry()
        self.assertEqual(self.client.generate.call_count, 3)
        self.assertEqual(result['ai_run']['verified_matches'], 21)


class RetryParserTests(unittest.TestCase):
    def parse(self, rows):
        audits = [{'match_id': mid, 'raw_snapshot': fixture(mid)} for mid in ('001', '002')]
        return parse_retry_response({'raw_response': json.dumps({'matches': rows}), 'provider_meta': {}}, audits)

    def test_unknown_row_does_not_poison_legitimate_results(self):
        result = self.parse([response('001'), response('002'), response('unknown')])
        self.assertEqual(set(result['forecasts']), {'001', '002'})
        self.assertTrue(result['id_warnings'])

    def test_duplicate_id_only_rejects_that_match(self):
        result = self.parse([response('001'), response('001'), response('002')])
        self.assertEqual(set(result['forecasts']), {'002'})
        self.assertIn('重复', result['errors']['001'])

    def test_round_number_is_not_guessed_as_internal_id(self):
        result = self.parse([response('周二001'), response('002')])
        self.assertNotIn('001', result['forecasts'])
        self.assertIn('001', result['errors'])

    def test_probability_validation_is_still_enforced_per_match(self):
        bad = response('001', probabilities={'home_win': 120, 'draw': 1, 'away_win': -21})
        result = self.parse([bad, response('002')])
        self.assertEqual(set(result['forecasts']), {'002'})
        self.assertIn('001', result['errors'])


class RetryStorageTests(unittest.TestCase):
    def storage(self):
        storage = MongoDBStorage.__new__(MongoDBStorage)
        storage.fae_strategy_trial_ai_jobs_collection = Mock()
        storage.fae_strategy_trial_ai_jobs_collection.update_one.return_value.modified_count = 1
        return storage

    def test_request_and_batch_claim_are_atomic_and_do_not_upsert(self):
        storage = self.storage()
        request = {'request_id': 'rid', 'status': 'queued', 'requested_at': NOW.isoformat(),
                   'batch_count': 1, 'match_ids': ['001'], 'input_snapshot_hash': 'frozen'}
        self.assertTrue(storage.request_strategy_trial_retry('v3', '2026-11-03', request))
        filters, update = storage.fae_strategy_trial_ai_jobs_collection.update_one.call_args.args
        self.assertEqual(filters['input_snapshot.snapshot_hash'], 'frozen')
        self.assertEqual(filters['retry_state.status'], {'$nin': ['queued', 'running']})
        self.assertNotIn('batches', update['$set'])
        self.assertTrue(storage.claim_strategy_trial_retry_batch('v3', '2026-11-03', 'rid', 0, {}))
        filters = storage.fae_strategy_trial_ai_jobs_collection.update_one.call_args.args[0]
        self.assertEqual(filters['manual_retries.rid.batches.0'], {'$exists': False})

    def test_finish_requires_original_claim_and_completion_keeps_every_attempt(self):
        storage = self.storage()
        storage.finish_strategy_trial_retry_batch('v3', '2026-11-03', 'rid', 0, 'token', {})
        filters = storage.fae_strategy_trial_ai_jobs_collection.update_one.call_args.args[0]
        self.assertEqual(filters['manual_retries.rid.batches.0.claim_token'], 'token')
        storage.complete_strategy_trial_retry('v3', '2026-11-03', 'rid',
            {'ai_run': {'verified_matches': 1, 'failed_matches': 0}}, NOW.isoformat())
        filters, update = storage.fae_strategy_trial_ai_jobs_collection.update_one.call_args.args
        self.assertIn('manual_retries.rid.result', update['$set'])
        self.assertIn('retry_result', update['$set'])
        self.assertEqual(filters['retry_state.request_id'], 'rid')
        self.assertNotIn('input_snapshot', update['$set'])

    def test_public_job_projection_does_not_leak_full_retry_prompts_or_audit(self):
        storage = self.storage()
        storage.fae_strategy_trial_ai_jobs_collection.find.return_value.sort.return_value = []
        storage.get_strategy_trial_ai_jobs('v3', audit=False)
        projection = storage.fae_strategy_trial_ai_jobs_collection.find.call_args.args[1]
        self.assertEqual(projection['manual_retries'], 0)
        self.assertEqual(projection['retry_result.audit'], 0)


class RetryRouteTests(unittest.TestCase):
    def setUp(self):
        fixture_test = ManualRetryTests()
        fixture_test.setUp()
        self.memory = fixture_test.storage
        self.client_model = fixture_test.client
        self.storage = Mock()
        self.storage.get_strategy_trial.return_value = fixture_test.trial
        self.storage.get_strategy_trial_ai_job.side_effect = self.memory.get_strategy_trial_ai_job
        self.storage.get_strategy_trial_days.return_value = [fixture_test.day]
        self.storage.get_matches.side_effect = self.memory.get_matches
        self.storage.request_strategy_trial_retry.side_effect = self.memory.request_strategy_trial_retry
        self.app = Flask('strategy-retry-unit')
        self.app.secret_key = 'unit-session-only'
        self.admin = Mock(return_value={'username': 'wangkai'})
        self.thread = Mock()
        source = Path(__file__).resolve().parents[1] / 'web_app.py'
        nodes = [node for node in ast.parse(source.read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name in ('fae_admin_required', 'retry_fae_strategy_trial')]
        namespace = dict(app=self.app, jsonify=jsonify, request=request, session=session, wraps=wraps,
                         datetime=datetime, user_storage=Mock(), _fae_admin_user=self.admin,
                         TRIAL_VERSION=fixture_test.trial['experiment_id'], mongo_storage=self.storage,
                         make_trial_ai_client=lambda: self.client_model,
                         trial_ai_settings=lambda client: fixture_test.trial['ai_settings'],
                         TrialRetryError=TrialRetryError, threading=SimpleNamespace(Thread=self.thread),
                         make_retry_request=lambda *args: make_retry_request(*args, now=NOW),
                         _run_strategy_trial_manual_retry=Mock())
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
        self.handler = namespace['retry_fae_strategy_trial']
        self.logged_in = False

    def login(self, admin=True):
        self.logged_in = True
        if not admin:
            self.admin.return_value = None

    def send(self, data):
        # Do not couple route tests to old Flask/new Werkzeug cookie-jar internals.
        with self.app.test_request_context('/api/fae/strategy-trial/retry', method='POST', json=data):
            if self.logged_in:
                session['user_id'] = 1
            return self.app.make_response(self.handler())

    def post(self, **changes):
        data = {'date': NOW.date().isoformat(), 'confirm_api_cost': True}
        data.update(changes)
        return self.send(data)

    def test_only_logged_in_admin_can_retry(self):
        self.assertEqual(self.post().status_code, 401)
        self.login(admin=False)
        self.assertEqual(self.post().status_code, 403)
        self.storage.request_strategy_trial_retry.assert_not_called()
        self.thread.assert_not_called()

    def test_explicit_cost_confirmation_and_valid_date_are_required(self):
        self.login()
        for changes in ({'confirm_api_cost': False}, {'confirm_api_cost': 'true'}, {'date': 'not-a-date'}):
            self.assertEqual(self.post(**changes).status_code, 422)
        self.assertEqual(self.send(['bad']).status_code, 422)
        self.thread.assert_not_called()

    def test_request_returns_immediately_and_duplicate_post_does_not_queue_again(self):
        self.login()
        result = self.post()
        self.assertEqual(result.status_code, 202)
        self.assertTrue(result.json['success'])
        self.thread.return_value.start.assert_called_once()
        self.assertEqual(self.post().status_code, 409)
        self.client_model.generate.assert_not_called()

    def test_queue_survives_thread_start_failure_for_scheduler_recovery(self):
        self.login()
        self.thread.return_value.start.side_effect = RuntimeError('thread unavailable')
        with self.assertLogs(self.app.logger, level='ERROR'):
            self.assertEqual(self.post().status_code, 202)
        self.assertEqual(self.memory.job['retry_state']['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
