"""Current-rule, new-snapshot manual runs, separate from scheduled trial jobs."""

from copy import deepcopy
from datetime import datetime, timedelta
import json
from uuid import uuid4

from fae_schedule import app_timezone, parse_generated_at, parse_match_datetime
from .strategy_trial import build_trial_day, decision_time, digest, execute_trial_ai_job, model_fingerprint
from .strategy_trial_ai import TrialAIForecaster, trial_ai_settings
from .strategy_trial_retry import TrialRetryError, parse_retry_response


def make_manual_run(trial, matches, fundamentals, user, *, owner_date, now=None):
    current = now or datetime.now(app_timezone())
    if owner_date != current.date().isoformat():
        raise TrialRetryError('立即运行只处理今天的未开赛比赛，不能重跑历史或提前预测其他日期')
    if not trial or current.date().isoformat() > trial['end_date']:
        raise TrialRetryError('试运行未登记或观察期已结束')
    if (not trial['policy'].get('llm_enabled') or model_fingerprint() != trial['model_fingerprint']
            or digest(trial['policy']) != trial['policy_hash']
            or digest(trial['rule_weights']) != trial['weights_hash']):
        raise TrialRetryError('当前规则或模型实现尚未就绪，不能立即运行')
    # Scheduling override belongs exclusively to this manual-run archive. It
    # neither changes the 16:00 enrollment nor pretends this was a 16:00 sample.
    runtime = deepcopy(trial)
    runtime['start_date'] = min(runtime['start_date'], owner_date)
    runtime['policy'].update(decision_hour=current.hour, decision_minute=current.minute)
    runtime['policy_hash'] = digest(runtime['policy'])
    remaining = [match for match in matches if str(match.get('owner_date') or '')[:10] == owner_date
                 and match.get('status') in (0, '0')
                 and (parse_match_datetime(match) or current) > current + timedelta(minutes=trial['policy']['min_lead_minutes'])]
    snapshot = build_trial_day(runtime, owner_date, remaining, fundamentals, now=current, capture_only=True)
    usable = [row for row in snapshot['audit'] if row.get('context') and not row.get('rejections')]
    if snapshot['decision'] != 'analyzing' or len(usable) < 2:
        raise TrialRetryError('当前未开赛且快照有效的比赛不足两场；未调用AI，请等待数据更新后再试')
    request_id = uuid4().hex
    snapshot.update(manual_run=True, manual_run_id=request_id, scheduled_at=current.isoformat(),
                    regular_scheduled_at=decision_time(owner_date, trial['policy']).isoformat(),
                    canonical_policy_hash=trial['policy_hash'])
    snapshot['snapshot_hash'] = digest({key: value for key, value in snapshot.items() if key != 'snapshot_hash'})
    return {'request_id': request_id, 'experiment_id': trial['experiment_id'], 'owner_date': owner_date,
            'status': 'queued', 'requested_at': current.isoformat(),
            'requested_by': str(user.get('username') or user.get('id') or ''),
            'canonical_policy_hash': trial['policy_hash'], 'trial_snapshot': runtime,
            'input_snapshot': snapshot, 'batches': {}, 'match_count': len(usable),
            'batch_count': (len(usable) + 9) // 10, 'completed_batches': 0}


class _RunStorage:
    def __init__(self, storage, request_id):
        self.storage, self.request_id = storage, request_id

    def get_strategy_trial_ai_job(self, *args):
        return self.storage.get_strategy_trial_manual_run(self.request_id)

    def claim_strategy_trial_ai_batch(self, exp, date, index, batch):
        return self.storage.claim_strategy_trial_manual_batch(self.request_id, index, batch)

    def finish_strategy_trial_ai_batch(self, exp, date, index, token, batch):
        self.storage.finish_strategy_trial_manual_batch(self.request_id, index, token, batch)


class _RunForecaster(TrialAIForecaster):
    def __init__(self, client, storage, clock):
        super().__init__(client)
        self.storage, self.clock = storage, clock

    def prompt(self, owner_date, captured_at, audits):
        return ('格式复核：match_id必须逐字复制以下内部ID，不得改为竞彩场次号、球队ID或数组序号：'
                + json.dumps([row['match_id'] for row in audits], ensure_ascii=False) + '\n'
                + super().prompt(owner_date, captured_at, audits))

    def run(self, prompt, audits, settings):
        date = str(audits[0]['raw_snapshot']['owner_date'])[:10]
        live = {str(row['match_id']): row for row in self.storage.get_matches(filters={'owner_date': date})}
        if any(not live.get(row['match_id']) or live[row['match_id']].get('status') not in (0, '0')
               or (parse_match_datetime(live[row['match_id']]) or self.clock()) <= self.clock() for row in audits):
            raise ValueError('批次有已开赛或当前状态无法确认的比赛，不发起付费调用')
        return parse_retry_response(super().run(prompt, audits, settings), audits)


def execute_manual_run(trial, run, storage, *, client=None, clock=None):
    if not run or run.get('status') not in ('queued', 'running'):
        return None
    now = clock or (lambda: datetime.now(app_timezone()))
    from .strategy_trial_ai import make_trial_ai_client
    client = client or make_trial_ai_client()
    request_id = run['request_id']
    control = storage.get_strategy_trial_manual_control(run['experiment_id'], run['owner_date']) or {}
    if control.get('request_id') != request_id or control.get('status') not in ('queued', 'running'):
        # A scheduler can observe the persisted request before its caller has
        # acquired the control lease. Never fail or execute it in that gap.
        return None
    runtime = run['trial_snapshot']
    if (not trial or trial['policy_hash'] != run['canonical_policy_hash']
            or trial['model_fingerprint'] != model_fingerprint()
            or digest(runtime['policy']) != runtime['policy_hash']
            or digest(runtime['rule_weights']) != runtime['weights_hash']
            or trial_ai_settings(client) != runtime['ai_settings']):
        storage.fail_strategy_trial_manual_run(request_id, '当前模型或规则已变化，未调用AI；请按最新配置重新提交')
        return None
    result = execute_trial_ai_job(runtime, run, _RunStorage(storage, request_id),
                                 forecaster=_RunForecaster(client, storage, now), clock=now)
    if result is None:
        return None
    result.update(manual_run=True, manual_run_id=request_id, requested_at=run['requested_at'],
                  scheduled_at=run['requested_at'], regular_scheduled_at=run['input_snapshot']['regular_scheduled_at'],
                  canonical_policy_hash=run['canonical_policy_hash'])
    prefix = f"按当前规则手动运行（{run['requested_at'][11:19]}新快照，非16:00固定任务）："
    result['reason'] = prefix + result['reason']
    result['draw_reason'] = prefix + result.get('draw_reason', '')
    result['ai_run']['failed_matches'] = sum(not row.get('model_result', {}).get('ai_verified')
        for row in result['audit'] if row.get('context'))
    result['snapshot_hash'] = digest({key: value for key, value in result.items() if key != 'snapshot_hash'})
    storage.complete_strategy_trial_manual_run(request_id, result, now().isoformat())
    return result


def merge_manual_runs(days, runs, controls=()):
    """Latest publication wins for display, while every source remains stored."""
    output = {row['owner_date']: deepcopy(row) for row in days}
    for run in sorted(runs, key=lambda row: row['requested_at']):
        result = run.get('result')
        current = output.get(run['owner_date'])
        if result and (not current or (parse_generated_at(result.get('published_at')) or datetime.min.replace(tzinfo=app_timezone()))
                      >= (parse_generated_at(current.get('published_at')) or datetime.min.replace(tzinfo=app_timezone()))):
            output[run['owner_date']] = deepcopy(result)
        elif run.get('status') in ('queued', 'running') and (current is None or not current.get('published_at')):
            output[run['owner_date']] = deepcopy(run['input_snapshot'])
        elif current is None and run.get('status') == 'failed':
            output[run['owner_date']] = {**deepcopy(run['input_snapshot']), 'decision': 'ai_failed',
                                         'reason': run.get('error') or '手动运行失败，未覆盖任何旧记录'}
    for control in controls:
        if control['owner_date'] in output:
            output[control['owner_date']]['manual_run_state'] = deepcopy(control)
    return sorted(output.values(), key=lambda row: row['owner_date'])


def manual_run_summary(run):
    result = run.get('result') or {}
    return {**{key: run.get(key) for key in ('request_id', 'owner_date', 'status', 'requested_at', 'completed_at', 'error')},
            'published_at': result.get('published_at'), 'verified_matches': (result.get('ai_run') or {}).get('verified_matches'),
            'picks': [{key: pick.get(key) for key in ('match_number', 'selection_text', 'odds')}
                      for pick in (result.get('ticket') or {}).get('picks', [])],
            'draw_picks': [{key: pick.get(key) for key in ('match_number', 'selection_text', 'odds')}
                          for pick in (result.get('draw_ticket') or {}).get('picks', [])]}
