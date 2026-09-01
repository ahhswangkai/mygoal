"""Deterministic total-goals and half/full-time market support.

The Sporttery calculator is the source of the exact-outcome prices.  This
module deliberately keeps network access outside the model so a saved FAE
snapshot is immutable and can be settled later without querying live odds.
"""

from __future__ import annotations

from math import exp, factorial
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


SPECIAL_MARKET_MODEL_VERSION = "special-markets-v1-market-calibrated"

TOTAL_GOAL_KEYS = {
    "s0": "0",
    "s1": "1",
    "s2": "2",
    "s3": "3",
    "s4": "4",
    "s5": "5",
    "s6": "6",
    "s7": "7+",
}

HALF_FULL_KEYS = {
    "hh": "胜胜",
    "hd": "胜平",
    "ha": "胜负",
    "dh": "平胜",
    "dd": "平平",
    "da": "平负",
    "ah": "负胜",
    "ad": "负平",
    "aa": "负负",
}


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _integer(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_match_number(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _updated_at(source: Dict[str, Any]) -> Optional[str]:
    date = str(source.get("updateDate") or "").strip()
    time = str(source.get("updateTime") or "").strip()
    if date and time:
        return f"{date} {time}"
    return date or time or None


def _parse_market(
    source: Any,
    key_map: Dict[str, str],
) -> Dict[str, Any]:
    raw = source if isinstance(source, dict) else {}
    odds: Dict[str, float] = {}
    flags: Dict[str, int] = {}
    for key, label in key_map.items():
        value = _number(raw.get(key))
        if value is None:
            continue
        odds[label] = round(value, 3)
        flags[label] = _integer(raw.get(f"{key}f")) or 0
    return {
        "available": len(odds) == len(key_map),
        "odds": odds,
        "flags": flags,
        "updated_at": _updated_at(raw),
    }


def parse_calculator_payload(payload: Any) -> Dict[str, Dict[str, Any]]:
    """Index official calculator TTG/HAFU snapshots by match number."""
    root = payload if isinstance(payload, dict) else {}
    if isinstance(root.get("data"), dict):
        root = root["data"]
    value = root.get("value") if isinstance(root.get("value"), dict) else root
    groups = value.get("matchInfoList") if isinstance(value, dict) else []
    result: Dict[str, Dict[str, Any]] = {}
    for group in groups or []:
        for match in (group or {}).get("subMatchList") or []:
            number = normalize_match_number(match.get("matchNumStr"))
            if not number:
                continue
            total_goals = _parse_market(match.get("ttg"), TOTAL_GOAL_KEYS)
            half_full = _parse_market(match.get("hafu"), HALF_FULL_KEYS)
            if not total_goals["odds"] and not half_full["odds"]:
                continue
            result[number] = {
                "source": "sporttery-calculator",
                "match_number": number,
                "calculator_match_id": str(match.get("matchId") or ""),
                "total_goals": total_goals,
                "half_full": half_full,
            }
    return result


def _no_vig(odds: Dict[str, Any]) -> Dict[str, float]:
    inverse = {
        label: 1 / value
        for label, raw in odds.items()
        for value in [_number(raw)]
        if value is not None
    }
    total = sum(inverse.values())
    if total <= 0:
        return {}
    return {
        label: value / total * 100
        for label, value in inverse.items()
    }


def _triplet_probabilities(values: Iterable[Any]) -> List[Optional[float]]:
    odds = list(values)
    probabilities = _no_vig({str(index): value for index, value in enumerate(odds)})
    return [probabilities.get(str(index)) for index in range(len(odds))]


def _poisson_distribution(expectation: float) -> Dict[str, float]:
    expectation = max(0.5, min(5.5, expectation))
    values = {
        str(goals): exp(-expectation) * expectation ** goals / factorial(goals)
        for goals in range(7)
    }
    values["7+"] = max(0.0, 1.0 - sum(values.values()))
    return {label: probability * 100 for label, probability in values.items()}


def _rank_options(
    market: Dict[str, Any],
    model_probabilities: Dict[str, float],
) -> List[Dict[str, Any]]:
    market_probabilities = _no_vig(market.get("odds") or {})
    rows = []
    for label, odds in (market.get("odds") or {}).items():
        market_probability = market_probabilities.get(label)
        model_probability = model_probabilities.get(label, market_probability or 0)
        expected_return = model_probability / 100 * float(odds)
        rows.append({
            "selection": label,
            "odds": round(float(odds), 3),
            "movement": int((market.get("flags") or {}).get(label) or 0),
            "market_probability": (
                round(market_probability, 2)
                if market_probability is not None else None
            ),
            "model_probability": round(model_probability, 2),
            "probability_edge_pp": (
                round(model_probability - market_probability, 2)
                if market_probability is not None else None
            ),
            "expected_return": round(expected_return, 3),
            "value_score": round(max(0, min(100, 50 + (expected_return - 1) * 100)), 1),
        })
    rows.sort(key=lambda item: (
        float(item.get("model_probability") or 0),
        float(item.get("expected_return") or 0),
    ), reverse=True)
    return rows


def _total_goal_analysis(
    market: Dict[str, Any],
    match_input: Dict[str, Any],
) -> Dict[str, Any]:
    probabilities = _no_vig(market.get("odds") or {})
    if len(probabilities) < 8:
        return {
            "available": False,
            "reason": "竞彩总进球赔率不完整",
            "options": [],
        }
    market_expectation = sum(
        (7.5 if label == "7+" else float(label)) * probability / 100
        for label, probability in probabilities.items()
    )
    total_values = ((match_input.get("total") or {}).get("current") or [])
    total_line = _number(total_values[1]) if len(total_values) > 1 else None
    ou_probs = _triplet_probabilities([
        total_values[0] if len(total_values) > 0 else None,
        total_values[2] if len(total_values) > 2 else None,
    ])
    expectation = market_expectation
    if total_line is not None:
        over = ou_probs[0] if len(ou_probs) > 0 else None
        under = ou_probs[1] if len(ou_probs) > 1 else None
        line_expectation = total_line
        if over is not None and under is not None:
            line_expectation += max(-0.45, min(0.45, (over - under) / 100 * 0.9))
        expectation = market_expectation * 0.75 + line_expectation * 0.25
    poisson = _poisson_distribution(expectation)
    model = {
        label: probabilities[label] * 0.82 + poisson.get(label, 0) * 0.18
        for label in probabilities
    }
    options = _rank_options(market, model)
    return {
        "available": True,
        "market": "总进球",
        "model_version": SPECIAL_MARKET_MODEL_VERSION,
        "primary": options[0],
        "secondary": options[1],
        "options": options,
        "expected_goals": round(expectation, 2),
        "confidence": "观察",
        "reason": (
            "以竞彩总进球8项去水概率为主，结合亚洲大小球盘口校正；"
            "首选与次选按校正概率排序，尚未经过长期样本校准。"
        ),
    }


def _half_full_context(match_input: Dict[str, Any]) -> Dict[str, float]:
    euro = ((match_input.get("euro") or {}).get("current") or [])
    probs = _triplet_probabilities(euro)
    if len(probs) != 3 or any(value is None for value in probs):
        return {}
    home, draw, away = (float(value) for value in probs)
    total_values = ((match_input.get("total") or {}).get("current") or [])
    total_line = _number(total_values[1]) if len(total_values) > 1 else None
    low_total = total_line is not None and total_line <= 2.5
    draw_hold = 0.64 if low_total else 0.54
    winner_hold = 0.60 if not low_total else 0.55
    context = {
        "胜胜": home * winner_hold,
        "平胜": home * (1 - winner_hold) * 0.82,
        "负胜": home * (1 - winner_hold) * 0.18,
        "平平": draw * draw_hold,
        "胜平": draw * (1 - draw_hold) / 2,
        "负平": draw * (1 - draw_hold) / 2,
        "负负": away * winner_hold,
        "平负": away * (1 - winner_hold) * 0.82,
        "胜负": away * (1 - winner_hold) * 0.18,
    }
    total = sum(context.values())
    return {
        label: value / total * 100 for label, value in context.items()
    } if total else {}


def _half_full_analysis(
    market: Dict[str, Any],
    match_input: Dict[str, Any],
) -> Dict[str, Any]:
    probabilities = _no_vig(market.get("odds") or {})
    if len(probabilities) < 9:
        return {
            "available": False,
            "reason": "竞彩半全场赔率不完整",
            "options": [],
        }
    context = _half_full_context(match_input)
    model = {
        label: probabilities[label] * 0.84 + context.get(label, probabilities[label]) * 0.16
        for label in probabilities
    }
    options = _rank_options(market, model)
    return {
        "available": True,
        "market": "半全场",
        "model_version": SPECIAL_MARKET_MODEL_VERSION,
        "primary": options[0],
        "secondary": options[1],
        "options": options,
        "confidence": "观察",
        "reason": (
            "以竞彩半全场9项去水概率为主，结合欧赔胜平负强弱和大小球节奏校正；"
            "首选与次选按校正概率排序，尚未经过长期样本校准。"
        ),
    }


def build_special_market_analysis(
    calculator_snapshot: Optional[Dict[str, Any]],
    match_input: Dict[str, Any],
) -> Dict[str, Any]:
    snapshot = calculator_snapshot if isinstance(calculator_snapshot, dict) else {}
    total_market = snapshot.get("total_goals") or {}
    half_full_market = snapshot.get("half_full") or {}
    return {
        "version": SPECIAL_MARKET_MODEL_VERSION,
        "source": snapshot.get("source") or "sporttery-calculator",
        "match_number": snapshot.get("match_number") or match_input.get("match_number"),
        "calculator_match_id": snapshot.get("calculator_match_id"),
        "total_goals": {
            "snapshot": total_market,
            **_total_goal_analysis(total_market, match_input),
        },
        "half_full": {
            "snapshot": half_full_market,
            **_half_full_analysis(half_full_market, match_input),
        },
    }


def _score_pair(match: Dict[str, Any], *, half: bool = False) -> Optional[Tuple[int, int]]:
    prefixes = ("home_half_score", "away_half_score") if half else ("home_score", "away_score")
    home = _integer(match.get(prefixes[0]))
    away = _integer(match.get(prefixes[1]))
    if home is not None and away is not None:
        return home, away
    field = "half_score" if half else "score"
    parsed = re.fullmatch(r"\s*(\d+)\s*[:-]\s*(\d+)\s*", str(match.get(field) or ""))
    return (int(parsed.group(1)), int(parsed.group(2))) if parsed else None


def _outcome(score: Tuple[int, int]) -> str:
    return "胜" if score[0] > score[1] else "负" if score[0] < score[1] else "平"


def settle_special_markets(
    source: Dict[str, Any],
    match: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Settle saved primary/secondary selections for both special markets."""
    analysis = (source.get("analysis") or {}).get("special_markets") or (
        (source.get("input_snapshot") or {}).get("special_markets") or {}
    )
    full_score = _score_pair(match)
    half_score = _score_pair(match, half=True)
    finished = match.get("status") in (2, "2") and full_score is not None
    rows: List[Dict[str, Any]] = []
    for key, title in (("total_goals", "总进球"), ("half_full", "半全场")):
        model = analysis.get(key) or {}
        if not model.get("available"):
            continue
        primary = model.get("primary") or {}
        secondary = model.get("secondary") or {}
        actual = None
        if finished and key == "total_goals":
            goals = sum(full_score or (0, 0))
            actual = str(goals) if goals <= 6 else "7+"
        elif finished and key == "half_full" and half_score is not None:
            actual = _outcome(half_score) + _outcome(full_score)
        result_pending = not finished or (key == "half_full" and half_score is None)
        selections = [
            dict(item) for item in (primary, secondary)
            if item.get("selection")
        ]
        for index, item in enumerate(selections):
            item["role"] = "primary" if index == 0 else "secondary"
            item["status"] = (
                "pending" if result_pending
                else "ungraded" if actual is None
                else "hit" if item.get("selection") == actual
                else "miss"
            )
        hit = next((item for item in selections if item.get("status") == "hit"), None)
        coverage_status = (
            "pending" if result_pending
            else "ungraded" if actual is None
            else "hit" if hit else "miss"
        )
        primary_status = selections[0].get("status") if selections else "ungraded"
        primary_odds = _number(primary.get("odds"))
        primary_return = (
            primary_odds if primary_status == "hit" and primary_odds is not None
            else 0.0 if primary_status == "miss"
            else None
        )
        coverage_stake = float(len(selections)) if coverage_status in {"hit", "miss"} else None
        coverage_return = (
            _number(hit.get("odds")) if coverage_status == "hit" and hit
            else 0.0 if coverage_status == "miss"
            else None
        )
        rows.append({
            "match_id": str(source.get("match_id") or ""),
            "match_number": source.get("match_number"),
            "home_team": source.get("home_team"),
            "away_team": source.get("away_team"),
            "league": source.get("league"),
            "result_type": "special_market",
            "market_key": key,
            "market": title,
            "primary_selection": primary.get("selection"),
            "secondary_selection": secondary.get("selection"),
            "selections": selections,
            "actual_selection": actual,
            "primary_status": primary_status,
            "primary_odds": primary_odds,
            "primary_return": round(primary_return, 3) if primary_return is not None else None,
            "primary_profit": round(primary_return - 1, 3) if primary_return is not None else None,
            "coverage_status": coverage_status,
            "status": coverage_status,
            "hit_selection": hit.get("selection") if hit else None,
            "hit_odds": hit.get("odds") if hit else None,
            "equal_stake_stake": coverage_stake,
            "equal_stake_return": (
                round(coverage_return, 3) if coverage_return is not None else None
            ),
            "equal_stake_profit": (
                round(coverage_return - coverage_stake, 3)
                if coverage_return is not None and coverage_stake is not None
                else None
            ),
            "result_score": (
                f"{full_score[0]}:{full_score[1]}" if full_score else None
            ),
            "half_score": (
                f"{half_score[0]}:{half_score[1]}" if half_score else None
            ),
            "model_version": model.get("model_version"),
        })
    return rows
