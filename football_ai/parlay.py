"""Draw and handicap-draw combination recommendations."""

from __future__ import annotations

from itertools import combinations
import re
from typing import Any, Dict, Iterable, List, Optional


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _match_number_key(item: Dict[str, Any]) -> tuple:
    text = str(item.get("match_number") or "")
    number = re.search(r"(\d+)$", text)
    return (int(number.group(1)) if number else 9999, text)


def _selection_text(item: Dict[str, Any]) -> str:
    label = str(item.get("recommendation") or "")
    handicap = _number(item.get("handicap"))
    if label != "让平" or handicap is None:
        return label
    value = int(handicap) if handicap.is_integer() else handicap
    prefix = "+" if handicap > 0 else ""
    return f"让平({prefix}{value})"


def _prepare_candidates(
    rankings: Dict[str, Any],
    strategy_weights: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    groups = rankings.get("groups") or {}
    weights = strategy_weights or {}
    for category in ("平局", "让平"):
        for raw in groups.get(category) or []:
            probability = _number(raw.get("probability")) or 0
            score = _number(raw.get("score")) or 0
            risk_score = _number((raw.get("risk") or {}).get("score")) or 0
            odds = _number(raw.get("odds"))
            implied_probability = 100 / odds if odds and odds > 1 else None
            strategy_weight = max(
                0.70, min(1.30, _number(weights.get(category)) or 1.0)
            )
            quality = (
                score * 0.60 + probability * 0.40 - risk_score * 0.08
            ) * strategy_weight
            item = dict(raw)
            item.update({
                "selection": category,
                "selection_text": _selection_text(raw),
                "odds_value": round(odds, 3) if odds else None,
                "implied_probability": (
                    round(implied_probability, 1)
                    if implied_probability is not None else None
                ),
                "model_edge": (
                    round(probability - implied_probability, 1)
                    if implied_probability is not None else None
                ),
                "rank_score": round(max(0, min(100, quality)), 1),
                "strategy_weight": round(strategy_weight, 3),
            })
            candidates.append(item)
    return candidates


def _build_combinations(
    candidates: Iterable[Dict[str, Any]],
    legs: int,
    limit: int,
) -> List[Dict[str, Any]]:
    rows = []
    for picks in combinations(list(candidates), legs):
        if len({str(item.get("match_id")) for item in picks}) != legs:
            continue
        odds_values = [item.get("odds_value") for item in picks]
        combined_odds = None
        if all(value and value > 1 for value in odds_values):
            combined_odds = 1.0
            for value in odds_values:
                combined_odds *= value
        model_probability = 1.0
        for item in picks:
            model_probability *= max(
                0, min(1, (_number(item.get("probability")) or 0) / 100)
            )
        model_probability *= 100
        implied_probability = (
            100 / combined_odds if combined_odds and combined_odds > 1 else None
        )
        combo_score = (
            sum(_number(item.get("rank_score")) or 0 for item in picks) / legs
            - (legs - 1) * 2
        )
        rows.append({
            "legs": legs,
            "play": f"{legs}串1",
            "picks": [dict(item) for item in picks],
            "combined_odds": round(combined_odds, 2) if combined_odds else None,
            "model_hit_probability": round(model_probability, 2),
            "implied_hit_probability": (
                round(implied_probability, 2)
                if implied_probability is not None else None
            ),
            "combo_score": round(max(0, min(100, combo_score))),
        })
    rows.sort(
        key=lambda item: (
            item["combo_score"],
            item["model_hit_probability"],
            item.get("combined_odds") or 0,
        ),
        reverse=True,
    )
    return rows[:limit]


def build_draw_parlays(
    rankings: Dict[str, Any],
    pool_size: int = 10,
    combination_limit: int = 5,
    strategy_weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Select one draw direction per match and build distinct 2/3-leg groups."""
    candidates = _prepare_candidates(rankings, strategy_weights)
    best_by_match: Dict[str, Dict[str, Any]] = {}
    for item in candidates:
        match_id = str(item.get("match_id") or "")
        if not match_id:
            continue
        current = best_by_match.get(match_id)
        if current is None or (
            item["rank_score"], item.get("probability", 0)
        ) > (
            current["rank_score"], current.get("probability", 0)
        ):
            best_by_match[match_id] = item

    match_recommendations = sorted(
        best_by_match.values(), key=_match_number_key
    )
    pool = sorted(
        match_recommendations,
        key=lambda item: (
            item.get("rank_score", 0),
            item.get("probability", 0),
        ),
        reverse=True,
    )[:max(3, pool_size)]

    return {
        "date": rankings.get("date"),
        "engine_version": rankings.get("engine_version"),
        "focus": ["平局", "让平"],
        "strategy_weights": strategy_weights or {"平局": 1.0, "让平": 1.0},
        "match_count": len(match_recommendations),
        "match_recommendations": match_recommendations,
        "two_leg": _build_combinations(pool, 2, combination_limit),
        "three_leg": _build_combinations(pool, 3, combination_limit),
        "method": {
            "same_match_deduplicated": True,
            "pool_size": min(len(pool), max(3, pool_size)),
            "combination_limit": combination_limit,
        },
        "disclaimer": (
            "组合命中概率按各场模型概率近似独立相乘，仅用于同日方案排序；"
            "串关会显著放大波动，不构成投注建议。"
        ),
    }
