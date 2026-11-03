"""Independent AI-approved draw pool and normalized 3-match 2/3-leg tickets."""

from copy import deepcopy
from itertools import combinations
from math import prod

from fae_schedule import parse_generated_at, parse_match_datetime
from .engine import FootballAIEngine


DRAW_TRIAL_POLICY = {
    "enabled": True,
    "version": "ai-draw-23-v1",
    "min_odds": 2.5,
    "max_odds": 5.0,
    "min_draw_probability": 28.0,
    "min_handicap_draw_probability": 25.0,
    "min_expected_return": 1.0,
    "stake_units": 1.0,
    "pass_counts": [2, 3],
    "matches": 3,
}


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def draw_candidates(audit, rows, policy, now, published_at=None):
    """A secondary draw is allowed only with explicit AI focus approval."""
    match = audit['raw_snapshot']
    result = audit.get('model_result') or {}
    analysis = result.get('analysis') or {}
    focus = result.get('draw_focus') or {}
    draw_policy = policy['draw_focus']
    research = policy.get('selection_mode') == 'daily_research_v1'
    candidates = []
    for row in rows:
        selection = row['selection']
        if selection not in {'平局', '让平'}:
            continue
        item = deepcopy(row)
        approved = focus.get('ordinary_draw' if selection == '平局' else 'handicap_draw') or {}
        reasons = []
        if not result.get('ai_verified') or not result.get('draw_focus_valid'):
            reasons.append(result.get('draw_focus_error') or '缺少AI独立平/让平评估，不用本地规则替代')
        elif not approved.get('eligible'):
            reasons.append('AI未列入本玩法候选：' + str(approved.get('reason') or '证据不足'))
        if selection == '让平' and not row.get('handicap'):
            reasons.append('让球数为0或缺失，与普通平重复，不另占一场')
        if not draw_policy['min_odds'] <= row['odds'] <= draw_policy['max_odds']:
            reasons.append('赔率不在平/让平实验区间2.50～5.00')
        minimum = draw_policy['min_draw_probability'] if selection == '平局' else draw_policy['min_handicap_draw_probability']
        if row.get('probability') is None or row['probability'] < minimum:
            reasons.append(f'本玩法融合估计低于{minimum:g}%')
        if row.get('estimated_return') is None or row['estimated_return'] < draw_policy['min_expected_return']:
            reasons.append('本玩法未校准模型期望小于1.00')
        total = _number(match.get('ou_current_total'))
        if (FootballAIEngine._handicap_value(match.get('asian_current_handicap')) is None
                or total is None or not 0 < total <= 8
                or any(_number(match.get(field)) is None for field in (
                    'asian_current_home_odds', 'asian_current_away_odds',
                    'ou_current_over_odds', 'ou_current_under_odds'))):
            reasons.append('亚盘或大小球数据缺失/异常')
        ratio = match.get('betting_ratio') or {}
        ratio_time = parse_generated_at(ratio.get('fetched_at'))
        market = ratio.get(row['market']) or {}
        rates = [_number(market.get(side + '_support_rate')) for side in ('home', 'draw', 'away')]
        if (not ratio_time or not 0 <= (now - ratio_time).total_seconds() <= policy['max_market_age_minutes'] * 60
                or any(rate is None or not 0 <= rate <= 100 for rate in rates)
                or not 98 <= sum(rate or 0 for rate in rates) <= 102
                or row.get('heat_deviation_pp') is None):
            reasons.append('本玩法投注比例缺失、异常或过期')
        elif row['heat_deviation_pp'] > policy['max_heat_deviation_pp']:
            reasons.append('本玩法投注偏离超过+10个百分点')
        if (selection == '让平' and market.get('handicap_value') is not None
                and _number(market['handicap_value']) != row['handicap']):
            reasons.append('让球投注比例与当前让球数不一致')
        if (analysis.get('risk') or {}).get('dangerous'):
            reasons.append('AI标记严重数据风险')
        kickoff = parse_match_datetime(match)
        if published_at and (not kickoff or kickoff <= published_at):
            reasons.append('AI完成时已开赛，不补赛前方案')
        soft = {f'本玩法融合估计低于{minimum:g}%', '本玩法未校准模型期望小于1.00', '本玩法投注偏离超过+10个百分点'}
        if research and row.get('probability') is not None and row.get('estimated_return') is not None:
            item['warnings'] = [reason for reason in reasons if reason in soft]
            reasons = [reason for reason in reasons if reason not in soft]
        item.update(eligible=not reasons, rejections=reasons,
                    strict_eligible=not reasons and not item.get('warnings'),
                    ai_reason=str(approved.get('reason') or result.get('draw_focus_error') or ''),
                    scope='draw_focus')
        candidates.append(item)
    return candidates


def choose_draw_ticket(candidates, policy, *, baseline=False):
    """Maximize estimated >=2 hits; each fixture occupies at most one leg."""
    draw_policy = policy['draw_focus']
    research = policy.get('selection_mode') == 'daily_research_v1'
    best = {}
    key = 'market_probability' if baseline else 'probability'
    for raw in candidates:
        if raw['selection'] not in {'平局', '让平'} or (raw['selection'] == '让平' and not raw.get('handicap')):
            continue
        if not draw_policy['min_odds'] <= raw['odds'] <= draw_policy['max_odds']:
            continue
        if (not baseline and not raw.get('eligible')) or raw.get(key) is None:
            continue
        row = deepcopy(raw)
        current = best.get(row['match_id'])
        if current is None or (row[key], row['odds'], row['selection']) > (current[key], current['odds'], current['selection']):
            best[row['match_id']] = row
    if len(best) < (2 if research else 3):
        return None
    # P(at least two) is monotone in each independent leg's probability, so
    # the best three distinct fixtures suffice; avoid O(n^3) slate expansion.
    picks = sorted(best.values(), key=lambda row: (-row[key], -row['odds'], row['match_id'], row['selection']))[:3]
    values = [row[key] / 100 for row in picks]
    at_least_two = (values[0] * values[1] if len(picks) == 2 else
                    values[0] * values[1] + values[0] * values[2] + values[1] * values[2] - 2 * prod(values))
    all_three = prod(values) if len(picks) == 3 else None
    lines = []
    line_count = 1 if len(picks) == 2 else 4
    for count in range(2, len(picks) + 1):
        for group in combinations(picks, count):
            lines.append({'picks': deepcopy(list(group)), 'play': f'{count}串1',
                          'combined_odds': round(prod(row['odds'] for row in group), 6),
                          'stake': draw_policy['stake_units'] / line_count, 'status': 'pending'})
    return {'strategy': draw_policy['version'], 'play': '2场2关' if len(picks) == 2 else '3场2、3关',
            'picks': deepcopy(list(picks)), 'lines': lines, 'line_count': line_count,
            'stake': draw_policy['stake_units'], 'status': 'pending',
            'estimated_at_least_two_probability': round(at_least_two * 100, 4),
            'estimated_all_three_probability': round(all_three * 100, 4) if all_three is not None else None,
            'max_return_units': round(sum(line['stake'] * line['combined_odds'] for line in lines), 6),
            'research_only': research,
            'warnings': sorted({warning for pick in picks for warning in pick.get('warnings', [])}),
            'probability_note': '未校准估计、独立近似；有返奖不等于盈利，不保证收益'}


def settle_draw_plan(ticket, results, settle_line):
    if not ticket:
        return None
    output = deepcopy(ticket)
    lines = [settle_line(line, results) for line in ticket['lines']]
    picks = [settle_line({'picks': [pick], 'stake': 1}, results)['picks'][0] for pick in ticket['picks']]
    pending = any(line['status'] == 'pending' for line in lines)
    returns = None if pending else round(sum(line['return_units'] for line in lines), 6)
    status = ('pending' if pending else 'push' if all(line['status'] == 'push' for line in lines)
              else 'hit' if returns > 0 else 'miss')
    output.update(lines=lines, picks=picks, status=status, return_units=returns,
                  profit_units=round(returns - ticket['stake'], 6) if returns is not None else None,
                  hit_leg_count=sum(pick['status'] == 'hit' for pick in picks),
                  return_line_count=sum((line.get('return_units') or 0) > 0 for line in lines),
                  profitable=returns > ticket['stake'] if returns is not None else None)
    return output
