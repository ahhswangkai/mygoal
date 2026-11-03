"""Explicit, audited retries; the original trial inputs and decisions stay intact."""

from copy import deepcopy
from datetime import datetime, timedelta
import json
import re
from uuid import uuid4

from fae_schedule import app_timezone, parse_generated_at, parse_match_datetime
from .strategy_trial import build_trial_day, digest, model_fingerprint
from .strategy_trial_ai import TrialAIForecaster, trial_ai_settings, validate_forecast
from .provider import FAEOutputError


class TrialRetryError(ValueError):
    pass


def _audits(job):
    return {row['match_id']: row for row in job['input_snapshot'].get('audit', [])
            if row.get('context') and not row.get('rejections')}


def _all_batches(job):
    yield from (job.get('batches') or {}).values()
    for request in sorted((job.get('manual_retries') or {}).values(), key=lambda row: row['requested_at']):
        yield from (request.get('batches') or {}).values()


def _predictions(job):
    predictions = {}
    for batch in _all_batches(job):
        for match_id, row in (batch.get('forecasts') or {}).items():
            # A valid original forecast may never be replaced by retry fishing.
            if row.get('ai_verified') and not predictions.get(match_id, {}).get('ai_verified'):
                predictions[match_id] = deepcopy(row)
        for match_id in batch.get('match_ids') or []:
            if not predictions.get(match_id, {}).get('ai_verified'):
                predictions[match_id] = {'error': (batch.get('errors') or {}).get(match_id)
                                         or batch.get('error') or 'AI未返回有效研判'}
    return predictions


def _open(match, now, lead_minutes):
    kickoff = parse_match_datetime(match or {})
    return bool(match and match.get('status') in (0, '0') and kickoff
                and kickoff > now + timedelta(minutes=lead_minutes))


def make_retry_request(trial, job, day, live_matches, user, *, now=None):
    current = now or datetime.now(app_timezone())
    if not job or not day or day.get('decision') == 'analyzing':
        raise TrialRetryError('首次研判尚未完成或没有赛前留档，请等待完成后再重试')
    if (job.get('retry_state') or {}).get('status') in ('queued', 'running'):
        raise TrialRetryError('失败研判正在重试，请勿重复提交')
    snapshot = job['input_snapshot']
    if (not trial['policy'].get('llm_enabled')
            or snapshot.get('policy_hash') != trial['policy_hash']
            or digest(trial['policy']) != trial['policy_hash']
            or digest(trial['rule_weights']) != trial['weights_hash']
            or snapshot.get('model_fingerprint') != trial['model_fingerprint']
            or trial['model_fingerprint'] != model_fingerprint()):
        raise TrialRetryError('规则或模型实现与原留档不一致，不能重试原任务')
    timeout = trial['ai_settings']['timeout'] + 60
    for batch in _all_batches(job):
        started = parse_generated_at(batch.get('started_at'))
        if batch.get('status') == 'running' and (not started or current < started + timedelta(seconds=timeout)):
            raise TrialRetryError('仍有调用未结束，暂不能再次付费重试')
    predictions = _predictions(job)
    matches = {str(row['match_id']): row for row in live_matches}
    targets, skipped = [], []
    for match_id, audit in _audits(job).items():
        if predictions.get(match_id, {}).get('ai_verified'):
            continue
        if (_open(audit['raw_snapshot'], current, trial['policy']['min_lead_minutes'])
                and _open(matches.get(match_id), current, trial['policy']['min_lead_minutes'])):
            targets.append(match_id)
        else:
            skipped.append({'match_id': match_id, 'match_number': audit.get('match_number'),
                            'reason': '已开赛、临近开赛或当前比赛状态无法确认'})
    if not targets:
        raise TrialRetryError('没有可重试的失败场次：有效结果不重复调用，已开赛或距开赛不足10分钟不重跑')
    return {'request_id': uuid4().hex, 'status': 'queued', 'requested_at': current.isoformat(),
            'requested_by': str(user.get('username') or user.get('id') or ''),
            'match_ids': sorted(targets), 'skipped': skipped, 'batches': {},
            'input_snapshot_hash': snapshot['snapshot_hash'],
            'base_result_hash': day['snapshot_hash'],
            'base_ticket': deepcopy(day.get('ticket')),
            'base_draw_ticket': deepcopy(day.get('draw_ticket')),
            'first_published_at': day.get('first_published_at') or day.get('published_at'),
            'batch_count': (len(targets) + 9) // 10}


def parse_retry_response(result, audits):
    """Quarantine only the bad ID; never map by position, team or round number."""
    output = {**result, 'forecasts': {}, 'errors': {}, 'id_warnings': []}
    expected = {row['match_id']: row['raw_snapshot'] for row in audits}
    try:
        payload = json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', result['raw_response'].strip()))
        rows = payload.get('matches')
        if not isinstance(rows, list):
            raise ValueError('AI未返回matches数组')
        by_id = {}
        for row in rows:
            match_id = str(row.get('match_id') or '') if isinstance(row, dict) else ''
            if match_id not in expected:
                output['id_warnings'].append('忽略未知或无效比赛ID：' + match_id[:80])
                continue
            by_id.setdefault(match_id, []).append(row)
        for match_id, match in expected.items():
            try:
                values = by_id.get(match_id, [])
                if len(values) != 1:
                    raise ValueError('AI重复本场比赛ID' if values else 'AI遗漏本场比赛ID')
                output['forecasts'][match_id] = validate_forecast(values[0], match)
            except (ValueError, TypeError, AttributeError, FAEOutputError) as exc:
                output['errors'][match_id] = str(exc)
    except (ValueError, TypeError, AttributeError, FAEOutputError) as exc:
        output['errors'] = {match_id: 'AI输出无效：' + str(exc) for match_id in expected}
    return output


def merge_trial_days(days, jobs):
    """Display/settle the latest completed retry, without editing original days."""
    output = {day['owner_date']: deepcopy(day) for day in days}
    for job in jobs:
        owner_date = job['owner_date']
        result = job.get('retry_result')
        if result:
            output[owner_date] = deepcopy(result)
        elif owner_date not in output:
            output[owner_date] = deepcopy(job['input_snapshot'])
        if job.get('retry_state'):
            output[owner_date]['retry_state'] = deepcopy(job['retry_state'])
    return sorted(output.values(), key=lambda day: day['owner_date'])


def execute_manual_retry(trial, job, storage, *, forecaster=None, clock=None):
    """Batch claims precede payment. Crashed in-flight calls are never repaid."""
    now = clock or (lambda: datetime.now(app_timezone()))
    state = job.get('retry_state') or {}
    request_id = state.get('request_id')
    if state.get('status') not in ('queued', 'running'):
        return None
    request = job['manual_retries'][request_id]
    owner_date = job['owner_date']
    experiment_id = trial['experiment_id']
    forecaster = forecaster or TrialAIForecaster()
    settings = trial['ai_settings']
    if (trial_ai_settings(forecaster.client) != settings or model_fingerprint() != trial['model_fingerprint']
            or job['input_snapshot'].get('policy_hash') != trial['policy_hash']
            or job['input_snapshot'].get('model_fingerprint') != trial['model_fingerprint']
            or digest(trial['policy']) != trial['policy_hash']
            or digest(trial['rule_weights']) != trial['weights_hash']
            or job['input_snapshot']['snapshot_hash'] != request['input_snapshot_hash']):
        storage.fail_strategy_trial_retry(experiment_id, owner_date, request_id, '模型配置或原始快照已变化，未调用AI')
        return None
    audits = _audits(job)
    targets = request['match_ids']
    for index, offset in enumerate(range(0, len(targets), 10)):
        fresh = storage.get_strategy_trial_ai_job(experiment_id, owner_date)
        saved = fresh['manual_retries'][request_id]['batches'].get(str(index))
        if saved:
            if saved['status'] == 'running':
                started = parse_generated_at(saved.get('started_at'))
                if not started or now() < started + timedelta(seconds=settings['timeout'] + 60):
                    return None
                storage.finish_strategy_trial_retry_batch(experiment_id, owner_date, request_id, index,
                    saved['claim_token'], {**saved, 'status': 'failed', 'error': '调用中断或超时，不自动重复付费',
                                            'completed_at': now().isoformat()})
            continue
        match_ids = targets[offset:offset + 10]
        live = {str(row['match_id']): row for row in storage.get_matches(filters={'owner_date': owner_date})}
        active = [audits[mid] for mid in match_ids
                  if _open(live.get(mid), now(), trial['policy']['min_lead_minutes'])
                  and _open(audits[mid]['raw_snapshot'], now(), trial['policy']['min_lead_minutes'])]
        active_ids = {row['match_id'] for row in active}
        blocked = {mid: '重试时已开赛、临近开赛或当前状态无法确认' for mid in match_ids if mid not in active_ids}
        batch = {'claim_token': uuid4().hex, 'status': 'running', 'started_at': now().isoformat(),
                 'match_ids': match_ids, 'called_match_ids': sorted(active_ids)}
        if not storage.claim_strategy_trial_retry_batch(experiment_id, owner_date, request_id, index, batch):
            return None
        try:
            if active:
                prompt = ('格式复核：match_id必须逐字复制以下内部ID字符串，不要改为竞彩场次号、球队ID或数组序号：'
                          + json.dumps(sorted(active_ids), ensure_ascii=False) + '\n'
                          + forecaster.prompt(owner_date, job['input_snapshot']['captured_at'], active))
                batch.update(prompt=prompt, prompt_hash=digest(prompt))
                result = parse_retry_response(forecaster.run(prompt, active, settings), active)
                batch.update(result)
                for audit in active:
                    mid = audit['match_id']
                    if (parse_match_datetime(audit['raw_snapshot']) or now()) <= now():
                        batch.get('forecasts', {}).pop(mid, None)
                        batch.setdefault('errors', {})[mid] = '重试完成时已开赛，不补写赛前研判'
            batch.setdefault('errors', {}).update(blocked)
            batch.update(status='complete', completed_at=now().isoformat())
        except Exception as exc:
            error = str(exc)
            key = getattr(forecaster.client, 'api_key', '')
            if isinstance(key, str) and key:
                error = error.replace(key, '[redacted]')
            batch.update(status='failed', error=error[:500], completed_at=now().isoformat())
        storage.finish_strategy_trial_retry_batch(experiment_id, owner_date, request_id, index,
                                                  batch['claim_token'], batch)
    fresh = storage.get_strategy_trial_ai_job(experiment_id, owner_date)
    request = fresh['manual_retries'][request_id]
    predictions = _predictions(fresh)
    snapshot = fresh['input_snapshot']
    published_at = now()
    record = build_trial_day(trial, owner_date, [row['raw_snapshot'] for row in snapshot['audit']],
        {row['match_id']: row.get('fundamentals_snapshot') or {} for row in snapshot['audit']},
        now=parse_generated_at(snapshot['captured_at']), ai_predictions=predictions, published_at=published_at)
    for key in ('baseline_ticket', 'draw_baseline_ticket'):
        record[key] = deepcopy(snapshot.get(key))
    if request.get('base_ticket'):
        record.update(ticket=deepcopy(request['base_ticket']), decision='ticket', reason='保留已发布的二串一；仅补充失败场次研判')
    if request.get('base_draw_ticket'):
        record.update(draw_ticket=deepcopy(request['base_draw_ticket']), draw_decision='ticket', draw_reason='保留已发布的平/让平方案；仅补充失败场次研判')
    verified = sum(bool(value.get('ai_verified')) for value in predictions.values())
    if not verified:
        record.update(decision='ai_failed', reason='手动重试后仍未获得有效火山研判',
                      draw_decision='ai_failed', draw_reason='手动重试后仍未获得有效火山研判')
    all_batches = list(_all_batches(fresh))
    record.update(first_published_at=request.get('first_published_at'),
                  source_snapshot_hash=snapshot['snapshot_hash'], manual_retry=True,
                  retry_request_id=request_id, retried_at=published_at.isoformat())
    record['ai_run'] = {
        'model': settings['model'], 'prompt_version': settings['prompt_version'], 'prompt_hash': settings['prompt_hash'],
        'batch_count': len(all_batches), 'completed_batches': sum(b['status'] == 'complete' for b in all_batches),
        'failed_batches': sum(b['status'] == 'failed' for b in all_batches), 'verified_matches': verified,
        'failed_matches': sum(not predictions.get(mid, {}).get('ai_verified') for mid in audits),
        'draw_assessed_matches': sum(bool(value.get('draw_focus_valid')) for value in predictions.values()),
        'manual_retry_count': len(fresh.get('manual_retries') or {}), 'batch_audit_hash': digest(all_batches),
    }
    record['snapshot_hash'] = digest({key: value for key, value in record.items() if key != 'snapshot_hash'})
    storage.complete_strategy_trial_retry(experiment_id, owner_date, request_id, record, published_at.isoformat())
    return record
