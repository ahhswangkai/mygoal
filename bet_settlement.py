"""竞彩足球投注判定与体彩官方赛果读取。"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from itertools import combinations

import requests


RESULT_URL = (
    'https://webapi.sporttery.cn/gateway/uniform/football/'
    'getUniformMatchResultV1.qry'
)
HOME_SCORE_OPTIONS = {
    '1:0', '2:0', '2:1', '3:0', '3:1', '3:2',
    '4:0', '4:1', '4:2', '5:0', '5:1', '5:2',
}
DRAW_SCORE_OPTIONS = {'0:0', '1:1', '2:2', '3:3'}
AWAY_SCORE_OPTIONS = {
    '0:1', '0:2', '1:2', '0:3', '1:3', '2:3',
    '0:4', '1:4', '2:4', '0:5', '1:5', '2:5',
}
VOID_MARKERS = ('取消', '无效', '延期', '腰斩', '中断', '弃权')


def candidate_result_dates(dates):
    """体彩业务日期的凌晨比赛，赛果日期可能落在业务日期的次日。"""
    candidates = set()
    for value in dates:
        match_date = str(value or '')[:10]
        if not match_date:
            continue
        candidates.add(match_date)
        try:
            parsed = datetime.strptime(match_date, '%Y-%m-%d')
        except ValueError:
            continue
        candidates.add((parsed + timedelta(days=1)).date().isoformat())
    return sorted(candidates)


def merge_rescheduled_void_results(result_index, bets, rescheduled_matches):
    """把本地已确认改期的比赛补成赔率 1.00 的无效赛果。"""
    rescheduled_index = {
        (
            str(match.get('owner_date') or '')[:10],
            str(match.get('match_number') or ''),
        ): match
        for match in rescheduled_matches
        if int(match.get('status') or 0) == 6
    }
    for bet in bets:
        for item in bet.get('selected_items') or []:
            match_id = str(item.get('match_id') or '')
            if match_id in result_index:
                continue
            fallback_key = (
                str(item.get('date') or '')[:10],
                str(item.get('match_num') or ''),
            )
            if fallback_key not in rescheduled_index:
                continue
            synthetic_result = {
                'matchId': match_id,
                'matchDate': fallback_key[0],
                'matchNumStr': fallback_key[1],
                'matchResultStatus': '2',
                'poolStatus': 'Void',
                'resultStatus': '改期，按赔率1.00计算',
                'sectionsNo1': '',
                'sectionsNo999': '',
            }
            result_index[match_id] = synthetic_result
            result_index[fallback_key] = synthetic_result
    return result_index


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_score(value):
    """把 ``2:1`` 一类比分转为整数元组，非比分返回 None。"""
    text = str(value or '').strip().replace('：', ':')
    parts = text.split(':')
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None


def merge_database_results(result_index, matches):
    """Merge completed MongoDB matches using business date and match number."""
    for match in matches or []:
        try:
            status = int(match.get('status') or 0)
        except (TypeError, ValueError):
            status = 0
        if status != 2:
            continue

        full_score = parse_score(match.get('score'))
        if full_score is None:
            full_score = parse_score('{}:{}'.format(
                match.get('home_score', ''),
                match.get('away_score', ''),
            ))
        if full_score is None:
            continue

        half_score = parse_score(match.get('half_score'))
        if half_score is None:
            half_score = parse_score('{}:{}'.format(
                match.get('home_half_score', ''),
                match.get('away_half_score', ''),
            ))
        match_id = str(match.get('match_id') or '')
        match_date = str(match.get('owner_date') or '')[:10]
        match_number = str(match.get('match_number') or '')
        result = {
            'matchId': match_id,
            'matchDate': match_date,
            'matchNumStr': match_number,
            'matchResultStatus': '2',
            'poolStatus': 'Payout',
            'resultStatus': '数据库完场赛果',
            'sectionsNo1': (
                '{}:{}'.format(*half_score) if half_score is not None else ''
            ),
            'sectionsNo999': '{}:{}'.format(*full_score),
            'resultSource': 'mongodb',
        }
        if match_id:
            result_index[match_id] = result
        if match_date and match_number:
            result_index[(match_date, match_number)] = result
    return result_index


def outcome_name(score):
    if score[0] > score[1]:
        return '胜'
    if score[0] == score[1]:
        return '平'
    return '负'


def is_result_complete(result):
    return str(result.get('matchResultStatus') or '') == '2'


def is_void_result(result):
    text = ' '.join(
        str(result.get(key) or '')
        for key in ('sectionsNo999', 'resultStatus', 'poolStatus')
    )
    lowered = text.lower()
    return (
        any(marker in text for marker in VOID_MARKERS)
        or any(marker in lowered for marker in ('cancel', 'void', 'refund'))
    )


def grade_item(item, result):
    """判定单个投注项，返回 win / lose / void / pending。"""
    if not result or not is_result_complete(result):
        return 'pending'
    if is_void_result(result):
        return 'void'

    full_score = parse_score(result.get('sectionsNo999'))
    if full_score is None:
        return 'pending'

    pool = str(item.get('pool') or '')
    option = str(item.get('opt') or item.get('label') or '').strip()
    full_outcome = outcome_name(full_score)

    if pool == 'had':
        expected = {'win': '胜', 'draw': '平', 'lose': '负'}.get(option, option)
        return 'win' if expected == full_outcome else 'lose'

    if pool == 'hhad':
        handicap = _to_float(item.get('handicap'))
        handicap_score = (full_score[0] + handicap, full_score[1])
        expected = {'win': '胜', 'draw': '平', 'lose': '负'}.get(option, option)
        return 'win' if expected == outcome_name(handicap_score) else 'lose'

    if pool == 'score':
        score_text = '{}:{}'.format(*full_score)
        if option == score_text:
            return 'win'
        if option == '胜其他':
            return 'win' if full_outcome == '胜' and score_text not in HOME_SCORE_OPTIONS else 'lose'
        if option == '平其他':
            return 'win' if full_outcome == '平' and score_text not in DRAW_SCORE_OPTIONS else 'lose'
        if option == '负其他':
            return 'win' if full_outcome == '负' and score_text not in AWAY_SCORE_OPTIONS else 'lose'
        return 'lose'

    if pool == 'goals':
        goals = full_score[0] + full_score[1]
        if option == '7+':
            return 'win' if goals >= 7 else 'lose'
        try:
            return 'win' if int(option) == goals else 'lose'
        except (TypeError, ValueError):
            return 'lose'

    if pool == 'hafu':
        half_score = parse_score(result.get('sectionsNo1'))
        if half_score is None:
            return 'pending'
        return 'win' if option == outcome_name(half_score) + full_outcome else 'lose'

    return 'pending'


def _find_result(item, result_index):
    match_id = str(item.get('match_id') or '')
    result = result_index.get(match_id)
    if result:
        return result
    fallback_key = (
        str(item.get('date') or ''),
        str(item.get('match_num') or ''),
    )
    return result_index.get(fallback_key)


def settle_bet(bet, result_index):
    """按所选关数、选项和倍数计算整单实际返还；赛果不齐时返回 None。"""
    groups = {}
    for item in bet.get('selected_items') or []:
        match_id = str(item.get('match_id') or '')
        groups.setdefault(match_id, []).append(item)
    if not groups:
        return None

    match_settlements = []
    match_winning_factors = []
    for match_id, items in groups.items():
        result = _find_result(items[0], result_index)
        if not result or not is_result_complete(result):
            return None

        item_results = []
        winning_factors = []
        for item in items:
            grade = grade_item(item, result)
            if grade == 'pending':
                return None
            if grade == 'win':
                winning_factors.append(_to_float(item.get('odd')))
            elif grade == 'void':
                winning_factors.append(1.0)
            item_results.append({
                'pool': item.get('pool'),
                'opt': item.get('opt'),
                'result': grade,
            })

        match_winning_factors.append(winning_factors)
        match_settlements.append({
            'match_id': match_id,
            'match_num': items[0].get('match_num') or result.get('matchNumStr') or '',
            'full_score': str(result.get('sectionsNo999') or ''),
            'half_score': str(result.get('sectionsNo1') or ''),
            'result_status': str(result.get('resultStatus') or ''),
            'result_source': str(result.get('resultSource') or 'sporttery'),
            'is_void': is_void_result(result),
            'item_results': item_results,
        })

    payout_factor = 0.0
    actual_return = 0.0
    winning_notes = 0
    multiplier = max(1, int(bet.get('multiplier') or 1))
    pass_counts = sorted(set(int(count) for count in bet.get('pass_counts') or []))
    for pass_count in pass_counts:
        for indexes in combinations(range(len(match_winning_factors)), pass_count):
            product_counts = {1.0: 1}
            for index in indexes:
                next_counts = {}
                for base_factor, base_count in product_counts.items():
                    for option_factor in match_winning_factors[index]:
                        product = round(base_factor * option_factor, 12)
                        next_counts[product] = next_counts.get(product, 0) + base_count
                product_counts = next_counts
                if not product_counts:
                    break
            for factor, count in product_counts.items():
                payout_factor += factor * count
                winning_notes += count
                actual_return += round(factor * 2 * multiplier + 1e-9, 2) * count

    stake = round(_to_float(bet.get('stake')), 2)
    actual_return = round(actual_return + 1e-9, 2)
    profit = round(actual_return - stake, 2)
    if profit > 0.005:
        status = 'won'
    elif profit < -0.005:
        status = 'lost'
    else:
        status = 'draw'

    return {
        'status': status,
        'actual_return': actual_return,
        'profit': profit,
        'winning_notes': winning_notes,
        'settled_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'settlement': {
            'matches': match_settlements,
            'payout_factor': round(payout_factor, 6),
        },
    }


class SportteryResultClient:
    """读取官方赛果；默认忽略系统代理，避免代理出口触发 WAF。"""

    def __init__(self, timeout=15, cache_seconds=120):
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self._cache = {}
        self._lock = threading.Lock()
        self.session = requests.Session()
        self.session.trust_env = os.getenv('SPORTTERY_TRUST_ENV', '').lower() in {
            '1', 'true', 'yes',
        }
        self.session.headers.update({
            'Accept': 'application/json, text/plain, */*',
            'Referer': 'https://www.sporttery.cn/',
            'User-Agent': (
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 Chrome/126.0 Safari/537.36'
            ),
        })

    def fetch_date(self, match_date):
        match_date = str(match_date or '')[:10]
        datetime.strptime(match_date, '%Y-%m-%d')
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(match_date)
            if cached and now - cached[0] < self.cache_seconds:
                return cached[1]

        matches = []
        page_no = 1
        while True:
            response = self.session.get(
                RESULT_URL,
                params={
                    'matchPage': 1,
                    'matchBeginDate': match_date,
                    'matchEndDate': match_date,
                    'leagueId': '',
                    'pageSize': 100,
                    'pageNo': page_no,
                    'isFix': 0,
                    'pcOrWap': 1,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if str(payload.get('errorCode')) != '0':
                raise RuntimeError(payload.get('errorMessage') or '体彩赛果接口返回异常')
            value = payload.get('value') or {}
            matches.extend(value.get('matchResult') or [])
            if page_no >= int(value.get('pages') or 1):
                break
            page_no += 1

        with self._lock:
            self._cache[match_date] = (now, matches)
        return matches

    def build_index(self, dates):
        index = {}
        for match_date in sorted(set(str(date or '')[:10] for date in dates if date)):
            for result in self.fetch_date(match_date):
                match_id = str(result.get('matchId') or '')
                if match_id:
                    index[match_id] = result
                index[(
                    str(result.get('matchDate') or ''),
                    str(result.get('matchNumStr') or ''),
                )] = result
        return index
