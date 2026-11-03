from copy import deepcopy
from datetime import timedelta
import json
import unittest
from unittest.mock import Mock

from football_ai.provider import ArkNarrativeClient, FAEProviderError
from football_ai.strategy_trial import (
    build_trial_day, execute_trial_ai_job, model_fingerprint, start_trial,
)
from football_ai.strategy_trial_ai import TrialAIForecaster, trial_ai_settings
from tests.test_fae_strategy_trial import NOW, fixture, fake_engine


def response(match_id, **changes):
    row = {'match_id': match_id, 'primary_selection': '主胜', 'probabilities': {
        'home_win': 75, 'draw': 15, 'away_win': 10, 'hhad': {'win': 25, 'draw': 30, 'lose': 45}},
        'reason': '近期状态与盘口支持主队，但伤停缺失构成反证。', 'risks': ['首发未确认'], 'data_risk': False}
    row.update(changes)
    return row


class MemoryJobs:
    def __init__(self, snapshot):
        self.job = {'input_snapshot': deepcopy(snapshot), 'batches': {}}

    def get_strategy_trial_ai_job(self, *args):
        return deepcopy(self.job)

    def claim_strategy_trial_ai_batch(self, exp, day, index, batch):
        key = str(index)
        if key in self.job['batches']:
            return False
        self.job['batches'][key] = deepcopy(batch)
        return True

    def finish_strategy_trial_ai_batch(self, exp, day, index, token, batch):
        key = str(index)
        if self.job['batches'][key]['claim_token'] == token and self.job['batches'][key]['status'] == 'running':
            self.job['batches'][key] = deepcopy(batch)


class TrialAITests(unittest.TestCase):
    def setUp(self):
        self.client = ArkNarrativeClient(api_key='unit-secret', timeout=180, max_tokens=4096, thinking='disabled', json_mode=True)
        self.client.max_retries = 0
        self.client.generate = Mock(return_value=(json.dumps({'matches': [response('001'), response('002')]}, ensure_ascii=False),
                                                  {'response_id': 'unit-call', 'response_model': 'unit-model'}))
        self.forecaster = TrialAIForecaster(self.client)
        self.trial = start_trial(NOW - timedelta(hours=8), client=self.client)

    def capture(self, count=2):
        engine = fake_engine()
        record = build_trial_day(self.trial, '2026-11-03', [fixture(f'{n:03d}') for n in range(1, count + 1)],
                                 now=NOW, engine=engine, capture_only=True)
        engine.generate_from_context.assert_not_called()
        self.assertEqual(record['decision'], 'analyzing')
        self.assertIsNone(record['ticket'])
        return record

    def test_start_freezes_provider_and_prompt_without_secret(self):
        self.assertTrue(self.trial['policy']['llm_enabled'])
        self.assertEqual(self.trial['ai_settings']['batch_size'], 10)
        self.assertNotIn('unit-secret', json.dumps(self.trial))

    def test_real_client_interface_called_and_ai_primary_is_ticket_source(self):
        snapshot = self.capture()
        storage = MemoryJobs(snapshot)
        result = execute_trial_ai_job(self.trial, storage.job, storage, forecaster=self.forecaster,
                                      clock=lambda: NOW + timedelta(seconds=10))
        self.client.generate.assert_called_once()
        self.assertEqual(result['decision'], 'ticket')
        self.assertEqual(result['ticket']['combined_odds'], 3.0625)
        self.assertTrue(all(p['analysis_source'] == 'volcengine-ark' for p in result['ticket']['picks']))
        self.assertEqual(result['ai_run']['verified_matches'], 2)
        batch = storage.job['batches']['0']
        self.assertIn('冻结输入', batch['prompt'])
        self.assertEqual(batch['provider_meta']['response_id'], 'unit-call')
        self.assertTrue(batch['raw_response'])

    def test_provider_failure_is_saved_and_never_falls_back_or_repays(self):
        snapshot = self.capture()
        storage = MemoryJobs(snapshot)
        self.client.generate.side_effect = FAEProviderError('timeout unit-secret')
        result = execute_trial_ai_job(self.trial, storage.job, storage, forecaster=self.forecaster, clock=lambda: NOW)
        self.assertEqual(result['decision'], 'ai_failed')
        self.assertIsNone(result['ticket'])
        self.assertNotIn('unit-secret', json.dumps(storage.job))
        execute_trial_ai_job(self.trial, storage.job, storage, forecaster=self.forecaster, clock=lambda: NOW)
        self.client.generate.assert_called_once()

    def test_successful_checkpoint_is_not_called_again_on_resume(self):
        storage = MemoryJobs(self.capture())
        execute_trial_ai_job(self.trial, storage.job, storage, forecaster=self.forecaster, clock=lambda: NOW)
        execute_trial_ai_job(self.trial, storage.job, storage, forecaster=self.forecaster, clock=lambda: NOW)
        self.client.generate.assert_called_once()

    def test_unparseable_output_keeps_raw_response_but_no_ticket(self):
        self.client.generate.return_value = ('not-json', {'response_id': 'bad'})
        storage = MemoryJobs(self.capture())
        result = execute_trial_ai_job(self.trial, storage.job, storage, forecaster=self.forecaster, clock=lambda: NOW)
        self.assertEqual(result['decision'], 'ai_failed')
        self.assertEqual(storage.job['batches']['0']['raw_response'], 'not-json')

    def test_each_paid_request_has_at_most_ten_matches(self):
        storage = MemoryJobs(self.capture(21))
        def generate(prompt):
            payload = json.loads(prompt.split('冻结输入：\n')[1])
            self.assertLessEqual(len(payload['matches']), 10)
            return json.dumps({'matches': [response(row['match_id']) for row in payload['matches']]}), {}
        self.client.generate.side_effect = generate
        result = execute_trial_ai_job(self.trial, storage.job, storage, forecaster=self.forecaster, clock=lambda: NOW)
        self.assertEqual(self.client.generate.call_count, 3)
        self.assertEqual(result['ai_run']['verified_matches'], 21)

    def test_model_configuration_change_suspends_before_paid_call(self):
        storage = MemoryJobs(self.capture())
        self.client.model = 'changed-model'
        result = execute_trial_ai_job(self.trial, storage.job, storage, forecaster=self.forecaster, clock=lambda: NOW)
        self.assertEqual(result['decision'], 'suspended')
        self.client.generate.assert_not_called()

    def test_AI_primary_is_not_replaced_by_higher_local_probability(self):
        self.client.generate.return_value = (json.dumps({'matches': [response('001', primary_selection='平局'), response('002')]}), {})
        storage = MemoryJobs(self.capture())
        result = execute_trial_ai_job(self.trial, storage.job, storage, forecaster=self.forecaster, clock=lambda: NOW)
        self.assertIsNone(result['ticket'])
        self.assertEqual(result['audit'][0]['primary']['selection'], '平局')

    def test_semantically_conflicting_probability_is_rejected(self):
        bad = response('001', probabilities={'home_win': 20, 'draw': 40, 'away_win': 40,
                                            'hhad': {'win': 60, 'draw': 20, 'lose': 20}})
        self.client.generate.return_value = (json.dumps({'matches': [bad, response('002')]}), {})
        storage = MemoryJobs(self.capture())
        result = execute_trial_ai_job(self.trial, storage.job, storage, forecaster=self.forecaster, clock=lambda: NOW)
        self.assertEqual(result['ai_run']['verified_matches'], 1)
        self.assertIsNone(result['ticket'])

    def test_ai_completing_after_kickoff_does_not_become_pregame_ticket(self):
        storage = MemoryJobs(self.capture())
        # Simulate a saved, successful batch whose publication was interrupted.
        execute_trial_ai_job(self.trial, storage.job, storage, forecaster=self.forecaster, clock=lambda: NOW)
        result = execute_trial_ai_job(self.trial, storage.job, storage, forecaster=self.forecaster,
                                      clock=lambda: NOW + timedelta(hours=8))
        self.assertIsNone(result['ticket'])

    def test_inflight_claim_is_never_stolen_or_repaid(self):
        storage = MemoryJobs(self.capture())
        storage.job['batches']['0'] = {'status': 'running', 'started_at': NOW.isoformat(), 'claim_token': 'other', 'match_ids': ['001', '002']}
        result = execute_trial_ai_job(self.trial, storage.job, storage, forecaster=self.forecaster, clock=lambda: NOW)
        self.assertIsNone(result)
        self.client.generate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
