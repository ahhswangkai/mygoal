"""Deterministic score, total-goals and half/full-time market support.

The Sporttery calculator is the source of the exact-outcome prices.  This
module deliberately keeps network access outside the model so a saved FAE
snapshot is immutable and can be settled later without querying live odds.
"""

from __future__ import annotations

from math import exp, factorial
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


SPECIAL_MARKET_MODEL_VERSION = "special-markets-v2-regime-direction"

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

CORRECT_SCORE_KEYS = {
    "s01s00": "1:0", "s02s00": "2:0", "s02s01": "2:1",
    "s03s00": "3:0", "s03s01": "3:1", "s03s02": "3:2",
    "s04s00": "4:0", "s04s01": "4:1", "s04s02": "4:2",
    "s05s00": "5:0", "s05s01": "5:1", "s05s02": "5:2",
    "s00s00": "0:0", "s01s01": "1:1", "s02s02": "2:2",
    "s03s03": "3:3",
    "s00s01": "0:1", "s00s02": "0:2", "s01s02": "1:2",
    "s00s03": "0:3", "s01s03": "1:3", "s02s03": "2:3",
    "s00s04": "0:4", "s01s04": "1:4", "s02s04": "2:4",
    "s00s05": "0:5", "s01s05": "1:5", "s02s05": "2:5",
    "s1sh": "胜其他", "s1sd": "平其他", "s1sa": "负其他",
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
    """Index official calculator CRS/TTG/HAFU snapshots by match number."""
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
            correct_score = _parse_market(
                match.get("crs"), CORRECT_SCORE_KEYS
            )
            total_goals = _parse_market(match.get("ttg"), TOTAL_GOAL_KEYS)
            half_full = _parse_market(match.get("hafu"), HALF_FULL_KEYS)
            if (
                not correct_score["odds"]
                and not total_goals["odds"]
                and not half_full["odds"]
            ):
                continue
            result[number] = {
                "source": "sporttery-calculator",
                "match_number": number,
                "calculator_match_id": str(match.get("matchId") or ""),
                "correct_score": correct_score,
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


def _option(
    options: List[Dict[str, Any]],
    *,
    selection: Optional[str] = None,
    exclude: Iterable[str] = (),
    predicate: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    excluded = set(exclude)
    for item in options:
        label = str(item.get("selection") or "")
        if not label or label in excluded:
            continue
        if selection is not None and label != selection:
            continue
        if predicate is not None and not predicate(label):
            continue
        return item
    return None


def _goal_number(label: str) -> float:
    return 7.5 if label == "7+" else float(label)


def _asian_home_line(value: Any) -> Optional[float]:
    raw = (
        str(value or "").replace(" ", "")
        .replace("升", "").replace("降", "").strip()
    )
    if not raw:
        return None
    received = raw.startswith("受")
    if received:
        raw = raw[1:]
    labels = {
        "平手": 0.0,
        "平手/半球": 0.25,
        "平半": 0.25,
        "半球": 0.5,
        "半球/一球": 0.75,
        "半一": 0.75,
        "一球": 1.0,
        "一球/球半": 1.25,
        "球半": 1.5,
        "球半/两球": 1.75,
        "两球": 2.0,
        "两球/两球半": 2.25,
        "两球半": 2.5,
    }
    number = labels.get(raw)
    if number is None:
        try:
            parts = [float(item) for item in raw.split("/")]
        except (TypeError, ValueError):
            return None
        if not parts:
            return None
        number = sum(parts) / len(parts)
    return -number if received else number


def _correct_score_analysis(
    market: Dict[str, Any],
    match_input: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the released low-total two-score cluster.

    The rule is intentionally narrow and auditable.  A March-to-September
    replay used data before August for score-template selection and August
    onward as holdout.  In the matching 0.5/0.75, total-2.5 and 1.51-1.80
    favourite band, the two-score cluster covered 8/30 holdout matches.  This
    remains an observation strategy rather than a profitability guarantee.
    """
    odds = market.get("odds") or {}
    if len(odds) < len(CORRECT_SCORE_KEYS):
        return {
            "available": False,
            "market": "比分",
            "actionable": False,
            "recommendation_status": "数据不足",
            "reason": "竞彩比分赔率不完整，不生成比分串候选。",
            "selections": [],
        }
    euro = ((match_input.get("euro") or {}).get("current") or [])
    if len(euro) < 3:
        return {
            "available": True,
            "market": "比分",
            "actionable": False,
            "recommendation_status": "条件不足",
            "reason": "缺少即时欧赔，无法确认比分串热门方向。",
            "selections": [],
        }
    home_odds, away_odds = _number(euro[0]), _number(euro[2])
    total = ((match_input.get("total") or {}).get("current") or [])
    total_line = _number(total[1]) if len(total) > 1 else None
    asian = match_input.get("asian") or {}
    initial = asian.get("initial") or []
    current = asian.get("current") or []
    current_line = _asian_home_line(
        current[1] if len(current) > 1 else None
    )
    initial_line = _asian_home_line(
        initial[1] if len(initial) > 1 else None
    )
    if home_odds is None or away_odds is None or current_line is None:
        return {
            "available": True,
            "market": "比分",
            "actionable": False,
            "recommendation_status": "条件不足",
            "reason": "缺少完整欧赔或亚洲盘口，无法生成比分串候选。",
            "selections": [],
        }
    favorite = "home" if home_odds < away_odds else "away"
    favorite_odds = home_odds if favorite == "home" else away_odds
    current_depth = current_line if favorite == "home" else -current_line
    initial_depth = (
        initial_line if favorite == "home" else -initial_line
    ) if initial_line is not None else None
    direction_aligned = current_depth > 0
    line_fit = 0.5 <= current_depth <= 0.75
    total_fit = total_line is not None and abs(total_line - 2.5) < 0.01
    odds_fit = 1.51 <= favorite_odds <= 1.80
    no_retreat = (
        initial_depth is None or current_depth >= initial_depth - 0.01
    )
    labels = ["1:0", "1:1"] if favorite == "home" else ["0:1", "1:1"]
    probabilities = _no_vig(odds)
    selections = [{
        "selection": label,
        "odds": round(float(odds[label]), 3),
        "movement": int((market.get("flags") or {}).get(label) or 0),
        "market_probability": round(probabilities.get(label, 0), 2),
    } for label in labels if _number(odds.get(label)) is not None]
    actionable = all((
        direction_aligned, line_fit, total_fit, odds_fit, no_retreat,
        len(selections) == 2,
    ))
    failed = []
    if not total_fit:
        failed.append("大小球不是2.5")
    if not line_fit or not direction_aligned:
        failed.append("亚盘未对齐半球/半一")
    if not odds_fit:
        failed.append("热门胜赔不在1.51-1.80")
    if not no_retreat:
        failed.append("亚洲盘口发生退盘")
    return {
        "available": True,
        "market": "比分",
        "model_version": "score-cluster-v1-low-total",
        "actionable": actionable,
        "recommendation_status": "比分串候选" if actionable else "观察",
        "favorite": favorite,
        "favorite_odds": round(favorite_odds, 3),
        "asian_depth": round(current_depth, 2),
        "initial_asian_depth": (
            round(initial_depth, 2) if initial_depth is not None else None
        ),
        "total_line": total_line,
        "selections": selections if actionable else [],
        "historical_validation": {
            "training_window": "2026-03-01/2026-07-31",
            "holdout_window": "2026-08-01/2026-09-13",
            "holdout_sample": 30,
            "holdout_hits": 8,
            "holdout_coverage": 26.7,
        },
        "reason": (
            "低总球比分簇：热门胜赔1.51-1.80、亚盘半球/半一且未退盘、"
            "大小球2.5，覆盖热门1球小胜与1:1。"
            if actionable else "；".join(failed) or "未达到比分串条件"
        ),
    }


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
    over_price = _number(total_values[0]) if len(total_values) > 0 else None
    under_price = _number(total_values[2]) if len(total_values) > 2 else None
    if total_line is None or over_price is None or under_price is None:
        return {
            "available": False,
            "market": "总进球",
            "model_version": SPECIAL_MARKET_MODEL_VERSION,
            "data_complete": False,
            "calculator_available": True,
            "options": [],
            "expected_goals": round(market_expectation, 2),
            "baseline_only": True,
            "actionable": False,
            "recommendation_status": "数据不足",
            "confidence": "不足",
            "reason": (
                "缺少亚洲大小球即时盘口或两侧水位；竞彩总进球赔率"
                "只能作为原始市场数据，不生成首选、次选或复盘样本。"
            ),
        }
    ou_probs = _triplet_probabilities([
        over_price,
        under_price,
    ])
    over = ou_probs[0] if len(ou_probs) > 0 else None
    under = ou_probs[1] if len(ou_probs) > 1 else None
    line_expectation = total_line
    if over is not None and under is not None:
        line_expectation += max(-0.45, min(0.45, (over - under) / 100 * 0.9))
    expectation = market_expectation * 0.75 + line_expectation * 0.25
    poisson = _poisson_distribution(expectation)
    water_edge = (
        float(over) - float(under)
        if over is not None and under is not None else 0.0
    )
    if expectation >= 3.05 or (total_line is not None and total_line >= 3.0):
        regime = "high"
        regime_label = "高进球结构"
    elif (
        expectation <= 2.55
        or (
            water_edge <= -8.0
            and (total_line is None or total_line <= 2.75)
        )
    ):
        regime = "low"
        regime_label = "低进球结构"
    elif water_edge >= 6.0:
        regime = "high"
        regime_label = "高进球结构"
    else:
        regime = "standard"
        regime_label = "常规市场基线"
    market_weight = 0.72 if regime != "standard" else 0.82
    model = {
        label: (
            probabilities[label] * market_weight
            + poisson.get(label, 0) * (1 - market_weight)
        )
        for label in probabilities
    }
    options = _rank_options(market, model)
    primary = options[0]
    primary_number = _goal_number(str(primary.get("selection")))
    secondary = None
    if regime == "low":
        secondary = _option(
            options,
            exclude=(str(primary.get("selection")),),
            predicate=lambda label: _goal_number(label) < primary_number,
        )
    elif regime == "high":
        secondary = _option(
            options,
            exclude=(str(primary.get("selection")),),
            predicate=lambda label: _goal_number(label) > primary_number,
        )
    secondary = secondary or options[1]
    baseline_only = (
        regime == "standard"
        and {
            str(primary.get("selection")),
            str(secondary.get("selection")),
        }.issubset({"2", "3"})
    )
    recommendation_status = (
        "市场基线" if baseline_only else "结构候选"
    )
    return {
        "available": True,
        "market": "总进球",
        "model_version": SPECIAL_MARKET_MODEL_VERSION,
        "primary": primary,
        "secondary": secondary,
        "options": options,
        "expected_goals": round(expectation, 2),
        "regime": regime,
        "regime_label": regime_label,
        "data_complete": True,
        "calculator_available": True,
        "baseline_only": baseline_only,
        "actionable": not baseline_only,
        "recommendation_status": recommendation_status,
        "confidence": "观察" if not baseline_only else "基线",
        "reason": (
            f"{regime_label}：以竞彩总进球8项去水概率为底座，结合亚洲大小球"
            "盘口和水位确定尾部方向；低进球结构的次选向下寻找，高进球结构"
            "的次选向上寻找。常规2/3球只标记市场基线，不升级为结构推荐。"
        ),
    }


def _half_full_direction(match_input: Dict[str, Any]) -> Dict[str, Any]:
    euro = ((match_input.get("euro") or {}).get("current") or [])
    probs = _triplet_probabilities(euro)
    if len(probs) != 3 or any(value is None for value in probs):
        return {}
    home, draw, away = (float(value) for value in probs)
    direction = "home" if home >= away else "away"
    direction_probability = home if direction == "home" else away
    gap = abs(home - away)
    asian_values = ((match_input.get("asian") or {}).get("current") or [])
    asian_line = _asian_home_line(
        asian_values[1] if len(asian_values) > 1 else None
    )
    home_water = _number(asian_values[0]) if len(asian_values) > 0 else None
    away_water = _number(asian_values[2]) if len(asian_values) > 2 else None
    asian_support = (
        asian_line is not None
        and (
            (direction == "home" and asian_line >= 0.25)
            or (direction == "away" and asian_line <= -0.25)
        )
    )
    asian_conflict = (
        asian_line is not None
        and (
            (direction == "home" and asian_line < 0)
            or (direction == "away" and asian_line > 0)
        )
    )
    water_conflict = False
    if home_water is not None and away_water is not None:
        water_conflict = (
            (direction == "home" and home_water - away_water >= 0.12)
            or (direction == "away" and away_water - home_water >= 0.12)
        )
    if (
        direction_probability >= 50
        and asian_support
        and not asian_conflict
        and not water_conflict
    ):
        tier = "strong"
        label = "强方向"
    elif (
        direction_probability >= 55
        and asian_support
        and not asian_conflict
    ):
        tier = "directional"
        label = "强方向但水位冲突"
    elif gap <= 10 or asian_conflict or water_conflict:
        tier = "balanced"
        label = "均势/冲突"
    else:
        tier = "directional"
        label = "方向观察"
    score = max(0.0, min(100.0, (
        direction_probability
        + (8 if asian_support else 0)
        - (12 if asian_conflict else 0)
        - (8 if water_conflict else 0)
        + min(12, gap * 0.6)
    )))
    return {
        "direction": direction,
        "direction_selection": "胜" if direction == "home" else "负",
        "direction_probability": round(direction_probability, 2),
        "draw_probability": round(draw, 2),
        "gap": round(gap, 2),
        "asian_line": asian_line,
        "asian_support": asian_support,
        "asian_conflict": asian_conflict,
        "water_conflict": water_conflict,
        "tier": tier,
        "label": label,
        "score": round(score, 1),
    }


def _half_full_context(
    match_input: Dict[str, Any],
    direction_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, float]:
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
    if (direction_profile or {}).get("tier") == "balanced":
        draw_boost = 1.28 if low_total else 1.16
        for label in ("平平", "胜平", "负平"):
            context[label] *= draw_boost
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
    direction_profile = _half_full_direction(match_input)
    context = _half_full_context(match_input, direction_profile)
    tier = direction_profile.get("tier") or "balanced"
    market_weight = {
        "strong": 0.78,
        "directional": 0.75,
        "balanced": 0.62,
    }.get(tier, 0.84)
    model = {
        label: (
            probabilities[label] * market_weight
            + context.get(label, probabilities[label]) * (1 - market_weight)
        )
        for label in probabilities
    }
    options = _rank_options(market, model)
    total_values = ((match_input.get("total") or {}).get("current") or [])
    total_line = _number(total_values[1]) if len(total_values) > 1 else None
    low_total = total_line is not None and total_line <= 2.5
    if tier in {"strong", "directional"}:
        final_selection = str(
            direction_profile.get("direction_selection") or ""
        )
        primary = _option(
            options,
            predicate=lambda label: label.endswith(final_selection),
        ) or options[0]
        secondary = _option(
            options,
            exclude=(str(primary.get("selection")),),
            predicate=lambda label: label.endswith(final_selection),
        ) or options[1]
    elif low_total:
        primary = _option(options, selection="平平") or options[0]
        secondary = _option(
            options,
            exclude=(str(primary.get("selection")),),
        ) or options[1]
    else:
        primary = options[0]
        secondary = _option(
            options,
            exclude=(str(primary.get("selection")),),
            predicate=lambda label: label.endswith("平"),
        ) or options[1]
    actionable = tier == "strong"
    recommendation_status = {
        "strong": "方向候选",
        "directional": "方向观察",
        "balanced": "均势观察",
    }.get(tier, "观察")
    return {
        "available": True,
        "market": "半全场",
        "model_version": SPECIAL_MARKET_MODEL_VERSION,
        "primary": primary,
        "secondary": secondary,
        "options": options,
        "direction_profile": direction_profile,
        "actionable": actionable,
        "recommendation_status": recommendation_status,
        "confidence": "观察",
        "reason": (
            f"{direction_profile.get('label') or '方向待定'}：先用欧赔、亚盘和水位"
            "确认全场方向，再在同一全场方向内选择半场路径；均势或冲突盘提高"
            "平平、胜平、负平权重，低总球均势盘优先检查平平。"
        ),
    }


def build_special_market_analysis(
    calculator_snapshot: Optional[Dict[str, Any]],
    match_input: Dict[str, Any],
) -> Dict[str, Any]:
    snapshot = calculator_snapshot if isinstance(calculator_snapshot, dict) else {}
    score_market = snapshot.get("correct_score") or {}
    total_market = snapshot.get("total_goals") or {}
    half_full_market = snapshot.get("half_full") or {}
    return {
        "version": SPECIAL_MARKET_MODEL_VERSION,
        "source": snapshot.get("source") or "sporttery-calculator",
        "match_number": snapshot.get("match_number") or match_input.get("match_number"),
        "calculator_match_id": snapshot.get("calculator_match_id"),
        "correct_score": {
            "snapshot": score_market,
            **_correct_score_analysis(score_market, match_input),
        },
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
    """Settle saved score, total-goals and half/full selections."""
    analysis = (source.get("analysis") or {}).get("special_markets") or (
        (source.get("input_snapshot") or {}).get("special_markets") or {}
    )
    full_score = _score_pair(match)
    half_score = _score_pair(match, half=True)
    finished = match.get("status") in (2, "2") and full_score is not None
    rows: List[Dict[str, Any]] = []
    for key, title in (
        ("correct_score", "比分"),
        ("total_goals", "总进球"),
        ("half_full", "半全场"),
    ):
        model = analysis.get(key) or {}
        if not model.get("available"):
            continue
        if key == "correct_score":
            score_selections = [
                dict(item) for item in model.get("selections") or []
                if item.get("selection")
            ][:2]
            if not model.get("actionable") or not score_selections:
                continue
            primary = score_selections[0]
            secondary = (
                score_selections[1] if len(score_selections) > 1 else {}
            )
        else:
            primary = model.get("primary") or {}
            secondary = model.get("secondary") or {}
        actual = None
        if finished and key == "correct_score":
            actual = f"{full_score[0]}:{full_score[1]}"
        elif finished and key == "total_goals":
            goals = sum(full_score or (0, 0))
            actual = str(goals) if goals <= 6 else "7+"
        elif finished and key == "half_full" and half_score is not None:
            actual = _outcome(half_score) + _outcome(full_score)
        result_pending = (
            not finished or (key == "half_full" and half_score is None)
        )
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
