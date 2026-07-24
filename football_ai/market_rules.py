"""Auditable historical odds-band rules for ordinary and handicap draws.

The constants in this module come from a fixed replay of completed matches.
They are deliberately conservative probability adjustments, not the raw
in-sample edge and never deterministic betting instructions.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


HISTORICAL_MARKET_RULES_VERSION = "historical-market-rules-v1"
HISTORICAL_MARKET_RULES_WINDOW = "2025-08-01~2026-07-23"


ORDINARY_DRAW_LEAGUE_PRIORS = {
    "葡超": {
        "adjustment_pp": 1.0, "sample": 169, "hit_rate": 29.6,
        "market_probability": 22.9, "roi": 12.5,
    },
    "荷甲": {
        "adjustment_pp": 1.0, "sample": 182, "hit_rate": 28.6,
        "market_probability": 21.9, "roi": 13.0,
    },
    "K1联赛": {
        "adjustment_pp": 0.5, "sample": 132, "hit_rate": 31.1,
        "market_probability": 27.3, "roi": 1.8,
    },
    "英超": {
        "adjustment_pp": 0.5, "sample": 325, "hit_rate": 27.4,
        "market_probability": 24.2, "roi": 0.9,
    },
    "欧罗巴": {
        "adjustment_pp": -1.5, "sample": 154, "hit_rate": 16.9,
        "market_probability": 23.6, "roi": -39.8,
    },
    "欧冠": {
        "adjustment_pp": -0.75, "sample": 190, "hit_rate": 18.4,
        "market_probability": 21.3, "roi": -28.1,
    },
    "挪超": {
        "adjustment_pp": -0.75, "sample": 123, "hit_rate": 18.7,
        "market_probability": 21.4, "roi": -29.1,
    },
}


HANDICAP_DRAW_LEAGUE_PRIORS = {
    "欧罗巴": {
        "adjustment_pp": 1.0, "sample": 139, "hit_rate": 30.9,
        "market_probability": 25.0, "roi": 12.5,
    },
    "意甲": {
        "adjustment_pp": 0.75, "sample": 322, "hit_rate": 29.5,
        "market_probability": 25.9, "roi": 1.5,
    },
    "法甲": {
        "adjustment_pp": 0.5, "sample": 183, "hit_rate": 28.4,
        "market_probability": 24.4, "roi": 2.2,
    },
    "瑞典超": {
        "adjustment_pp": -1.5, "sample": 114, "hit_rate": 17.5,
        "market_probability": 24.2, "roi": -38.2,
    },
    "英超": {
        "adjustment_pp": -1.0, "sample": 310, "hit_rate": 21.3,
        "market_probability": 24.3, "roi": -22.2,
    },
    "葡超": {
        "adjustment_pp": -1.0, "sample": 129, "hit_rate": 22.5,
        "market_probability": 25.9, "roi": -25.0,
    },
    "澳超": {
        "adjustment_pp": -1.0, "sample": 132, "hit_rate": 19.7,
        "market_probability": 23.1, "roi": -24.7,
    },
}


LEAGUE_ALIASES = {
    "瑞超": "瑞典超",
    "韩职": "K1联赛",
    "韩国K1联赛": "K1联赛",
    "韩K联": "K1联赛",
    "欧联": "欧罗巴",
    "欧联杯": "欧罗巴",
}


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> Optional[int]:
    number = _number(value)
    if number is None or abs(number - round(number)) > 0.01:
        return None
    return int(round(number))


def _weight(rule_id: str, weights: Optional[Dict[str, float]]) -> float:
    try:
        return max(0.5, min(1.5, float((weights or {}).get(rule_id, 1.0))))
    except (TypeError, ValueError):
        return 1.0


def _signal(
    rule_id: str,
    selection: str,
    adjustment_pp: float,
    sample: int,
    hit_rate: float,
    market_probability: float,
    roi: float,
    reason: str,
    *,
    confidence: str,
    weights: Optional[Dict[str, float]],
    risk: bool = False,
) -> Dict[str, Any]:
    weight = _weight(rule_id, weights)
    return {
        "rule_id": rule_id,
        "selection": selection,
        "adjustment_pp": round(adjustment_pp * weight, 2),
        "base_adjustment_pp": round(adjustment_pp, 2),
        "weight": round(weight, 3),
        "sample": int(sample),
        "hit_rate": round(hit_rate, 1),
        "market_probability": round(market_probability, 1),
        "roi": round(roi, 1),
        "confidence": confidence,
        "reason": reason,
        "risk": bool(risk),
    }


def _league_name(value: Any) -> str:
    name = str(value or "").strip()
    return LEAGUE_ALIASES.get(name, name)


def _profile(signals: List[Dict[str, Any]], lower: float, upper: float) -> Dict[str, Any]:
    adjustment = max(
        lower,
        min(upper, sum(float(item.get("adjustment_pp") or 0) for item in signals)),
    )
    return {
        "eligible_for_adjustment": bool(signals),
        "adjustment_pp": round(adjustment, 2),
        "signals": signals,
        "matched_rule_ids": [item["rule_id"] for item in signals],
    }


def evaluate_historical_market_rules(
    match: Dict[str, Any],
    rule_weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Match one pre-game snapshot against the fixed historical rule set."""
    ordinary: List[Dict[str, Any]] = []
    handicap_draw: List[Dict[str, Any]] = []
    favorite_risks: List[Dict[str, Any]] = []

    home_odds = _number(match.get("euro_current_win"))
    draw_odds = _number(match.get("euro_current_draw"))
    away_odds = _number(match.get("euro_current_lose"))
    euro = [home_odds, draw_odds, away_odds]
    favorite_side = None
    favorite_odds = None
    if all(value is not None and value > 1 for value in euro):
        favorite_index = min(range(3), key=lambda index: euro[index])
        favorite_side = ("home", "draw", "away")[favorite_index]
        favorite_odds = euro[favorite_index]

        if 1.70 <= favorite_odds < 1.90:
            ordinary.append(_signal(
                "history-draw-favorite-170-189",
                "平局", 2.0, 657, 30.1, 26.2, 1.2,
                "最低欧赔1.70-1.89：历史平局率30.1%，市场去水概率26.2%，前后期方向一致",
                confidence="高", weights=rule_weights,
            ))
        elif favorite_odds < 1.50:
            ordinary.append(_signal(
                "history-draw-strong-favorite-filter",
                "平局", -1.0, 1494, 17.0, 18.0, -16.5,
                "最低欧赔低于1.50：强弱悬殊样本的平局价值偏低",
                confidence="中", weights=rule_weights, risk=True,
            ))

        if draw_odds is not None and 4.00 <= draw_odds < 5.00:
            ordinary.append(_signal(
                "history-draw-odds-400-499-filter",
                "平局", -1.0, 798, 17.7, 20.3, -23.0,
                "平赔4.00-4.99：历史平局率17.7%，低于市场去水概率20.3%",
                confidence="中", weights=rule_weights, risk=True,
            ))

        if (
            favorite_side == "away"
            and favorite_odds is not None
            and 1.50 <= favorite_odds < 2.10
        ):
            if favorite_odds < 1.80:
                sample, hit_rate, market_probability, roi = 366, 46.7, 54.2, -24.1
                band = "1.50-1.79"
            else:
                sample, hit_rate, market_probability, roi = 429, 41.3, 45.6, -19.7
                band = "1.80-2.09"
            favorite_risks.append(_signal(
                "history-away-favorite-150-209-risk",
                "客胜", 0.0, sample, hit_rate, market_probability, roi,
                f"客胜赔率{band}历史命中低于市场预期，禁止仅凭低赔作为重点胆",
                confidence="高", weights=rule_weights, risk=True,
            ))

    league = _league_name(match.get("league"))
    ordinary_league = ORDINARY_DRAW_LEAGUE_PRIORS.get(league)
    if ordinary_league:
        ordinary.append(_signal(
            "history-draw-league-prior",
            "平局", ordinary_league["adjustment_pp"],
            ordinary_league["sample"], ordinary_league["hit_rate"],
            ordinary_league["market_probability"], ordinary_league["roi"],
            f"{league}普通平局历史条件频率相对市场的低权重先验",
            confidence="中", weights=rule_weights,
            risk=ordinary_league["adjustment_pp"] < 0,
        ))

    handicap = _integer(
        match.get("hi_handicap_value")
        if match.get("hi_handicap_value") not in (None, "")
        else match.get("handicap")
    )
    initial_hhad_draw = _number(match.get("hi_initial_draw_odds"))
    current_hhad_draw = _number(match.get("hi_current_draw_odds"))
    if handicap == 1 and current_hhad_draw is not None and 2.70 <= current_hhad_draw < 3.20:
        handicap_draw.append(_signal(
            "history-hhad-plus1-draw-270-319",
            "让平", 4.0, 109, 38.5, 28.9, 18.4,
            "主队+1且让平赔2.70-3.19：对应客队恰好赢1球，前后期历史方向一致",
            confidence="高", weights=rule_weights,
        ))
    if handicap == -1 and current_hhad_draw is not None and 3.20 <= current_hhad_draw < 4.00:
        handicap_draw.append(_signal(
            "history-hhad-minus1-draw-320-399",
            "让平", 0.75, 1915, 26.7, 24.9, -5.5,
            "主队-1且让平赔3.20-3.99：正好赢1球概率略高于市场，但历史ROI仍为负",
            confidence="中", weights=rule_weights,
        ))
    if (
        initial_hhad_draw is not None
        and current_hhad_draw is not None
        and 0.03 <= current_hhad_draw - initial_hhad_draw < 0.10
        and 3.20 <= current_hhad_draw < 3.80
    ):
        handicap_draw.append(_signal(
            "history-hhad-draw-small-rise",
            "让平", 1.5, 248, 33.1, 25.0, 17.3,
            "让平赔小升0.03-0.09且即时3.20-3.79：历史命中高于市场，近期仅作辅助加分",
            confidence="中", weights=rule_weights,
        ))
    if current_hhad_draw is not None and 4.00 <= current_hhad_draw < 5.00:
        handicap_draw.append(_signal(
            "history-hhad-draw-400-499-filter",
            "让平", -1.0, 985, 19.3, 21.0, -18.7,
            "让平赔4.00-4.99历史表现偏弱，不能因高赔率直接升级",
            confidence="中", weights=rule_weights, risk=True,
        ))

    hhad_league = HANDICAP_DRAW_LEAGUE_PRIORS.get(league)
    if hhad_league:
        handicap_draw.append(_signal(
            "history-hhad-draw-league-prior",
            "让平", hhad_league["adjustment_pp"],
            hhad_league["sample"], hhad_league["hit_rate"],
            hhad_league["market_probability"], hhad_league["roi"],
            f"{league}竞彩让平历史条件频率相对市场的低权重先验",
            confidence="中", weights=rule_weights,
            risk=hhad_league["adjustment_pp"] < 0,
        ))

    target_difference = -handicap if handicap is not None else None
    result = {
        "version": HISTORICAL_MARKET_RULES_VERSION,
        "source_window": HISTORICAL_MARKET_RULES_WINDOW,
        "favorite": {
            "side": favorite_side,
            "odds": round(favorite_odds, 3) if favorite_odds is not None else None,
        },
        "ordinary_draw": _profile(ordinary, -2.5, 2.5),
        "handicap_draw": {
            **_profile(handicap_draw, -3.0, 5.0),
            "handicap": handicap,
            "target_goal_difference": target_difference,
            "definition": (
                f"主队恰好赢{target_difference}球"
                if target_difference is not None and target_difference > 0
                else f"客队恰好赢{abs(target_difference)}球"
                if target_difference is not None and target_difference < 0
                else "双方90分钟战平"
                if target_difference == 0 else "让球数不足，无法映射"
            ),
        },
        "favorite_risks": favorite_risks,
    }
    result["matched_rule_ids"] = list(dict.fromkeys(
        result["ordinary_draw"]["matched_rule_ids"]
        + result["handicap_draw"]["matched_rule_ids"]
        + [item["rule_id"] for item in favorite_risks]
    ))
    result["governance"] = {
        "historical_only": True,
        "fixed_replay_window": HISTORICAL_MARKET_RULES_WINDOW,
        "market_probability_method": "三项赔率倒数去水归一化",
        "adjustment_cap_pp": {"ordinary_draw": 2.5, "handicap_draw": 5.0},
        "instruction": (
            "历史条件频率仅用于有限概率修正和风险过滤；"
            "必须与本场五市场证据、数据质量及基本面共同判断，不得写成必出规律。"
        ),
    }
    return result
