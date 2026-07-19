"""竞彩足球计算器的注数与理论最高奖金计算。"""

from itertools import combinations


def _group_odds(selected_items):
    groups = {}
    for item in selected_items or []:
        if not isinstance(item, dict):
            continue
        match_id = str(item.get('match_id') or item.get('matchId') or '').strip()
        if not match_id:
            continue
        try:
            odds = float(item.get('odd'))
        except (TypeError, ValueError):
            continue
        if odds <= 0:
            continue
        groups.setdefault(match_id, []).append(odds)
    return groups


def _valid_pass_counts(pass_counts, match_count):
    values = set()
    for count in pass_counts or []:
        try:
            value = int(count)
        except (TypeError, ValueError):
            continue
        if 1 <= value <= match_count:
            values.add(value)
    return sorted(values)


def calculate_notes(selected_items, pass_counts):
    """计算不同过关方式一共包含多少注。"""
    groups = _group_odds(selected_items)
    option_counts = [len(odds) for odds in groups.values()]
    notes = 0
    for pass_count in _valid_pass_counts(pass_counts, len(option_counts)):
        for combo in combinations(option_counts, pass_count):
            combo_notes = 1
            for count in combo:
                combo_notes *= count
            notes += combo_notes
    return notes


def calculate_max_bonus(selected_items, pass_counts, multiplier=1):
    """计算所有过关票中可同时命中的最高奖金之和。

    同一场选择多个互斥赛果时只可能命中其中一个，因此每场使用已选项
    的最高赔率；每张票先按分取整，再汇总，和赛后结算口径保持一致。
    """
    groups = _group_odds(selected_items)
    max_odds = [max(odds) for odds in groups.values() if odds]
    try:
        multiplier = max(1, int(multiplier))
    except (TypeError, ValueError):
        multiplier = 1

    total = 0.0
    for pass_count in _valid_pass_counts(pass_counts, len(max_odds)):
        for combo in combinations(max_odds, pass_count):
            factor = 1.0
            for odds in combo:
                factor *= odds
            total += round(factor * 2 * multiplier + 1e-9, 2)
    return round(total + 1e-9, 2)
