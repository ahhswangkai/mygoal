"""Time-decayed league history profiles for pre-match FAE analysis."""

from __future__ import annotations

from datetime import datetime
import math
import re
from typing import Any, Dict, Iterable, List, Optional


LEAGUE_PROFILE_VERSION = "league-profile-v3-asian-risk-patterns"
GOAL_MARGIN_MODEL_VERSION = "goal-margin-similarity-v1"

HANDICAP_VALUES = {
    "平手": 0.0,
    "平/半": 0.25,
    "平手/半球": 0.25,
    "半球": 0.5,
    "半/一": 0.75,
    "半球/一球": 0.75,
    "一球": 1.0,
    "一/球半": 1.25,
    "一球/球半": 1.25,
    "球半": 1.5,
    "球半/两": 1.75,
    "球半/两球": 1.75,
    "两球": 2.0,
    "两/两球半": 2.25,
    "两球/两球半": 2.25,
    "两球半": 2.5,
    "两球半/三球": 2.75,
    "三球": 3.0,
}

ASIAN_RISK_PATTERNS = (
    ("handicap_retreat", "退盘削弱"),
    ("upper_water_rise", "上盘升水"),
    ("water_drop_without_deepen", "降水不升盘"),
    ("deepen_high_water", "升盘高水"),
    ("euro_asian_divergence", "欧亚背离"),
    ("overheated_shallow", "热门过热"),
    ("no_market_warning", "盘口无明显预警"),
)
ASIAN_RISK_LABELS = dict(ASIAN_RISK_PATTERNS)

LEAGUE_ALIAS_GROUPS = (
    ("瑞典超", "瑞超"),
    ("K1联赛", "韩职", "韩国K1联赛", "韩K联"),
    ("日职", "日职联", "J1联赛"),
    ("日职乙", "日乙", "J2联赛"),
    ("美职联", "美职业", "MLS"),
    ("欧罗巴", "欧联", "欧联杯"),
    ("欧协联", "欧会杯", "欧洲协会联赛"),
    ("沙特联", "沙特超"),
    ("西班牙杯", "国王杯"),
)


def league_aliases(league: Any) -> List[str]:
    """Return known equivalent source labels without merging competitions."""
    name = str(league or "").strip()
    if not name:
        return []
    for group in LEAGUE_ALIAS_GROUPS:
        if name in group:
            return list(group)
    return [name]


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _total_line(value: Any) -> Optional[float]:
    text = re.sub(r"[↑↓升降]", "", str(value or "")).strip()
    if not text:
        return None
    parts = [_number(item.strip()) for item in text.split("/")]
    if not parts or any(item is None for item in parts):
        return None
    return sum(parts) / len(parts)


def _favorite_band(odds: float) -> str:
    if odds <= 1.50:
        return "1.50及以下"
    if odds <= 1.80:
        return "1.51-1.80"
    if odds <= 2.20:
        return "1.81-2.20"
    return "2.20以上"


def _clean_handicap(value: Any) -> str:
    return re.sub(
        r"(?:[↑↓]|升|降)+$", "", re.sub(r"\s+", "", str(value or ""))
    )


def _handicap_value(value: Any) -> Optional[float]:
    text = _clean_handicap(value)
    if not text:
        return None
    number = _number(text)
    if number is not None and re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        return number
    receiving = text.startswith("受")
    key = text[1:] if receiving else text
    if key not in HANDICAP_VALUES:
        return None
    depth = HANDICAP_VALUES[key]
    return -depth if receiving else depth


def _favorite_from_odds(match: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    home_odds = _number(match.get("euro_current_win"))
    away_odds = _number(match.get("euro_current_lose"))
    if (
        home_odds is None
        or away_odds is None
        or home_odds <= 0
        or away_odds <= 0
        or abs(home_odds - away_odds) < 0.20
    ):
        return None
    side, odds = (
        ("home", home_odds)
        if home_odds < away_odds else ("away", away_odds)
    )
    return {
        "favorite_side": side,
        "favorite_odds": odds,
        "clear_favorite": odds <= 2.20,
    }


def classify_asian_risk_patterns(
    match: Dict[str, Any],
    favorite: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Describe pre-match Asian-market warning patterns without claiming cause."""
    favorite = favorite or _favorite_from_odds(match) or {}
    favorite_side = favorite.get("favorite_side")
    if not favorite_side or not favorite.get("clear_favorite"):
        return {
            "data_complete": False,
            "favorite_side": favorite_side,
            "pattern_ids": [],
            "patterns": [],
            "primary_id": None,
            "primary_label": "无明确热门方",
        }

    initial_line = _handicap_value(match.get("asian_initial_handicap"))
    current_line = _handicap_value(match.get("asian_current_handicap"))
    initial_water = _number(match.get(
        "asian_initial_home_odds"
        if favorite_side == "home" else "asian_initial_away_odds"
    ))
    current_water = _number(match.get(
        "asian_current_home_odds"
        if favorite_side == "home" else "asian_current_away_odds"
    ))
    initial_euro = _number(match.get(
        "euro_initial_win"
        if favorite_side == "home" else "euro_initial_lose"
    ))
    current_euro = _number(match.get(
        "euro_current_win"
        if favorite_side == "home" else "euro_current_lose"
    ))
    if (
        initial_line is None
        or current_line is None
        or initial_water is None
        or current_water is None
    ):
        return {
            "data_complete": False,
            "favorite_side": favorite_side,
            "pattern_ids": [],
            "patterns": [],
            "primary_id": None,
            "primary_label": "亚盘初即时数据不足",
        }

    initial_depth = (
        initial_line if favorite_side == "home" else -initial_line
    )
    current_depth = (
        current_line if favorite_side == "home" else -current_line
    )
    line_change = round(current_depth - initial_depth, 3)
    water_change = round(current_water - initial_water, 3)
    euro_change = (
        round(current_euro - initial_euro, 3)
        if current_euro is not None and initial_euro is not None
        else None
    )
    reasons: Dict[str, str] = {}

    if line_change < -0.01:
        reasons["handicap_retreat"] = (
            f"热门方盘口由{_clean_handicap(match.get('asian_initial_handicap'))}"
            f"退至{_clean_handicap(match.get('asian_current_handicap'))}"
        )
    if water_change >= 0.05 and line_change <= 0.01:
        reasons["upper_water_rise"] = (
            f"上盘水位由{initial_water:.2f}升至{current_water:.2f}"
        )
    if water_change <= -0.05 and abs(line_change) <= 0.01:
        reasons["water_drop_without_deepen"] = (
            f"上盘水位由{initial_water:.2f}降至{current_water:.2f}，盘口未升深"
        )
    if line_change > 0.01 and current_water >= 0.98:
        reasons["deepen_high_water"] = (
            f"盘口升深但上盘即时水位仍为{current_water:.2f}"
        )
    if (
        euro_change is not None
        and euro_change <= -0.03
        and (line_change < -0.01 or water_change >= 0.05)
    ):
        reasons["euro_asian_divergence"] = (
            f"热门欧赔由{initial_euro:.2f}降至{current_euro:.2f}，"
            "亚盘却退盘或上盘升水"
        )
    if (
        current_euro is not None
        and current_euro <= 1.65
        and current_depth <= 0.75
        and current_water <= 0.75
    ):
        reasons["overheated_shallow"] = (
            f"热门欧赔{current_euro:.2f}且上盘低水{current_water:.2f}，"
            f"盘口深度仅{abs(current_depth):g}"
        )
    if not reasons:
        reasons["no_market_warning"] = (
            "赛前初即时亚盘未触发已定义的明显风险模式"
        )

    pattern_ids = [
        pattern_id for pattern_id, _ in ASIAN_RISK_PATTERNS
        if pattern_id in reasons
    ]
    patterns = [{
        "id": pattern_id,
        "label": ASIAN_RISK_LABELS[pattern_id],
        "reason": reasons[pattern_id],
    } for pattern_id in pattern_ids]
    primary_id = pattern_ids[0] if pattern_ids else None
    return {
        "data_complete": True,
        "favorite_side": favorite_side,
        "initial_line": _clean_handicap(match.get("asian_initial_handicap")),
        "current_line": _clean_handicap(match.get("asian_current_handicap")),
        "initial_depth": initial_depth,
        "current_depth": current_depth,
        "line_change": line_change,
        "initial_upper_water": initial_water,
        "current_upper_water": current_water,
        "upper_water_change": water_change,
        "favorite_euro_change": euro_change,
        "pattern_ids": pattern_ids,
        "patterns": patterns,
        "primary_id": primary_id,
        "primary_label": (
            ASIAN_RISK_LABELS.get(primary_id)
            if primary_id else "盘口数据不足"
        ),
        "governance": (
            "仅表示赛前市场预警结构，不代表赛果原因；真实原因需结合比赛过程数据。"
        ),
    }


def classify_market_favorite(
    match: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Classify one finished match using the league-profile favorite rules."""
    home_score = _number(match.get("home_score"))
    away_score = _number(match.get("away_score"))
    home_odds = _number(match.get("euro_current_win"))
    away_odds = _number(match.get("euro_current_lose"))
    if (
        home_score is None
        or away_score is None
        or home_odds is None
        or away_odds is None
        or home_odds <= 0
        or away_odds <= 0
    ):
        return None

    odds_gap = abs(home_odds - away_odds)
    if odds_gap < 0.20:
        return None
    favorite_side, favorite_odds = (
        ("home", home_odds)
        if home_odds < away_odds else ("away", away_odds)
    )
    difference = home_score - away_score
    outcome = (
        "home" if difference > 0
        else "away" if difference < 0 else "draw"
    )
    favorite_won = outcome == favorite_side
    result_type = (
        "follow" if favorite_won
        else "draw" if outcome == "draw" else "upset"
    )

    handicap = _number(
        match.get("hi_handicap_value")
        if match.get("hi_handicap_value") not in (None, "")
        else match.get("handicap")
    )
    hhad_result = None
    favorite_covered = None
    if handicap is not None:
        adjusted = difference + handicap
        hhad_result = (
            "home" if adjusted > 0
            else "away" if adjusted < 0 else "draw"
        )
        favorite_covered = hhad_result == favorite_side

    return {
        "favorite_side": favorite_side,
        "favorite_odds": favorite_odds,
        "favorite_odds_gap": odds_gap,
        "favorite_band": _favorite_band(favorite_odds),
        "clear_favorite": favorite_odds <= 2.20,
        "favorite_won": favorite_won,
        "favorite_failed": not favorite_won,
        "favorite_drew": result_type == "draw",
        "underdog_won": result_type == "upset",
        "result_type": result_type,
        "hhad_result": hhad_result,
        "favorite_covered": favorite_covered,
        "favorite_not_cover": (
            not favorite_covered
            if favorite_covered is not None else None
        ),
    }


def _confidence(sample_size: int, effective_sample: float) -> str:
    if sample_size >= 100 and effective_sample >= 50:
        return "高"
    if sample_size >= 50 and effective_sample >= 25:
        return "中"
    if sample_size >= 20 and effective_sample >= 10:
        return "低"
    return "样本不足"


def _feature_rows(
    matches: Iterable[Dict[str, Any]],
    before_date: str,
    half_life_days: int,
) -> List[Dict[str, Any]]:
    target = _date(before_date)
    if not target:
        return []
    rows = []
    for match in matches:
        owner_date = _date(match.get("owner_date"))
        if not owner_date or owner_date >= target:
            continue
        home = _number(match.get("home_score"))
        away = _number(match.get("away_score"))
        if home is None or away is None:
            continue
        age_days = max(0, (target - owner_date).days)
        weight = math.pow(0.5, age_days / max(1, half_life_days))
        difference = home - away
        total = home + away
        outcome = "home" if difference > 0 else "away" if difference < 0 else "draw"

        handicap = _number(
            match.get("hi_handicap_value")
            if match.get("hi_handicap_value") not in (None, "")
            else match.get("handicap")
        )
        hhad_result = None
        if handicap is not None:
            adjusted = difference + handicap
            hhad_result = "home" if adjusted > 0 else "away" if adjusted < 0 else "draw"

        favorite = classify_market_favorite(match) or {}
        favorite_odds = favorite.get("favorite_odds")
        favorite_won = favorite.get("favorite_won")
        favorite_won_by_one = (
            favorite_won and abs(difference) == 1
            if favorite_won is not None else None
        )
        asian_risk = classify_asian_risk_patterns(match, favorite)

        line = _total_line(
            match.get("ou_current_total")
            if match.get("ou_current_total") not in (None, "")
            else match.get("ou_initial_total")
        )
        total_result = None
        if line is not None:
            total_result = (
                "over" if total > line else "under" if total < line else "push"
            )

        rows.append({
            "weight": weight,
            "owner_date": owner_date.strftime("%Y-%m-%d"),
            "outcome": outcome,
            "total_goals": total,
            "both_scored": home > 0 and away > 0,
            "one_goal_margin": abs(difference) == 1,
            "home_win_by_one": difference == 1,
            "away_win_by_one": difference == -1,
            "hhad_result": hhad_result,
            "favorite_band": (
                _favorite_band(favorite_odds)
                if favorite_odds is not None else None
            ),
            "favorite_odds": favorite_odds,
            "favorite_odds_gap": favorite.get("favorite_odds_gap"),
            "favorite_won": favorite_won,
            "favorite_won_by_one": favorite_won_by_one,
            "favorite_failed": favorite.get("favorite_failed"),
            "favorite_drew": favorite.get("favorite_drew"),
            "underdog_won": favorite.get("underdog_won"),
            "favorite_not_cover": favorite.get("favorite_not_cover"),
            "asian_risk_data_complete": asian_risk.get("data_complete"),
            "asian_risk_pattern_ids": asian_risk.get("pattern_ids") or [],
            "total_result": total_result,
        })
    return rows


def _weighted_rate(
    rows: Iterable[Dict[str, Any]],
    field: str,
    expected: Any = True,
) -> Dict[str, Any]:
    eligible = [row for row in rows if row.get(field) is not None]
    denominator = sum(row["weight"] for row in eligible)
    numerator = sum(
        row["weight"] for row in eligible
        if row.get(field) == expected
    )
    return {
        "sample": len(eligible),
        "effective_sample": round(denominator, 1),
        "rate": round(numerator / denominator * 100, 1)
        if denominator else None,
    }


def _weighted_average(
    rows: Iterable[Dict[str, Any]], field: str
) -> Optional[float]:
    eligible = [
        row for row in rows if _number(row.get(field)) is not None
    ]
    denominator = sum(row["weight"] for row in eligible)
    return round(
        sum(row["weight"] * float(row[field]) for row in eligible)
        / denominator,
        2,
    ) if denominator else None


def _no_vig_probabilities(values: Iterable[Any]) -> Optional[List[float]]:
    odds = [_number(value) for value in values]
    if len(odds) != 3 or any(value is None or value <= 1 for value in odds):
        return None
    inverse = [1 / float(value) for value in odds]
    total = sum(inverse)
    return [value / total for value in inverse] if total else None


def _current_euro_probabilities(match: Dict[str, Any]) -> Optional[List[float]]:
    return _no_vig_probabilities((
        match.get("euro_current_win"),
        match.get("euro_current_draw"),
        match.get("euro_current_lose"),
    ))


def _current_hhad_probabilities(match: Dict[str, Any]) -> Optional[List[float]]:
    return _no_vig_probabilities((
        match.get("hi_current_home_odds"),
        match.get("hi_current_draw_odds"),
        match.get("hi_current_away_odds"),
    ))


def _match_handicap(match: Dict[str, Any]) -> Optional[float]:
    return _number(
        match.get("hi_handicap_value")
        if match.get("hi_handicap_value") not in (None, "")
        else match.get("handicap")
    )


def _market_features(match: Dict[str, Any]) -> Dict[str, Any]:
    total_source = (
        match.get("ou_current_total")
        if match.get("ou_current_total") not in (None, "")
        else match.get("ou_initial_total")
    )
    asian_source = (
        match.get("asian_current_handicap")
        if match.get("asian_current_handicap") not in (None, "")
        else match.get("asian_initial_handicap")
    )
    return {
        "league": str(match.get("league") or "").strip(),
        "euro": _current_euro_probabilities(match),
        "hhad": _current_hhad_probabilities(match),
        "asian_line": _handicap_value(asian_source),
        "total_line": _total_line(total_source),
        "sporttery_handicap": _match_handicap(match),
    }


def _similarity_weight(
    current: Dict[str, Any],
    historical: Dict[str, Any],
) -> tuple[float, List[str]]:
    """Return a bounded pre-match market similarity weight.

    The result deliberately uses only fields available before kickoff.  Same
    league receives more weight, while global rows still provide shrinkage for
    competitions with limited history.
    """
    weight = 1.0
    matched = []
    current_league = str(current.get("league") or "")
    historical_league = str(historical.get("league") or "")
    if current_league and historical_league in league_aliases(current_league):
        matched.append("同联赛")
    else:
        weight *= 0.32

    current_euro = current.get("euro")
    historical_euro = historical.get("euro")
    if current_euro and historical_euro:
        distance = sum(
            abs(left - right)
            for left, right in zip(current_euro, historical_euro)
        ) / 3
        weight *= math.exp(-distance / 0.075)
        if distance <= 0.035:
            matched.append("欧赔强弱接近")
    else:
        weight *= 0.45

    current_hhad = current.get("hhad")
    historical_hhad = historical.get("hhad")
    if current_hhad and historical_hhad:
        distance = sum(
            abs(left - right)
            for left, right in zip(current_hhad, historical_hhad)
        ) / 3
        weight *= math.exp(-distance / 0.085)
        if distance <= 0.04:
            matched.append("竞彩概率接近")
    else:
        weight *= 0.65

    for key, scale, label in (
        ("asian_line", 0.45, "亚盘深度接近"),
        ("total_line", 0.85, "大小球接近"),
        ("sporttery_handicap", 0.80, "竞彩让球一致"),
    ):
        left = _number(current.get(key))
        right = _number(historical.get(key))
        if left is None or right is None:
            weight *= 0.70
            continue
        distance = abs(left - right)
        weight *= math.exp(-distance / scale)
        if distance <= (0.01 if key == "sporttery_handicap" else scale / 2):
            matched.append(label)
    return max(0.0001, min(1.0, weight)), matched


def _wilson_interval(probability: float, effective_sample: float) -> List[float]:
    if effective_sample <= 0:
        return [0.0, 100.0]
    z = 1.96
    denominator = 1 + z * z / effective_sample
    centre = (
        probability + z * z / (2 * effective_sample)
    ) / denominator
    margin = z * math.sqrt(
        probability * (1 - probability) / effective_sample
        + z * z / (4 * effective_sample * effective_sample)
    ) / denominator
    return [
        round(max(0.0, centre - margin) * 100, 1),
        round(min(1.0, centre + margin) * 100, 1),
    ]


def _weighted_event_metric(
    rows: List[Dict[str, Any]],
    predicate: Any,
    market_probability: Optional[float],
    odds: Optional[float],
    minimum_effective_sample: float,
) -> Dict[str, Any]:
    denominator = sum(row["weight"] for row in rows)
    squared = sum(row["weight"] ** 2 for row in rows)
    effective_sample = (
        denominator * denominator / squared if squared else 0.0
    )
    hits = sum(row["weight"] for row in rows if predicate(row))
    historical_probability = hits / denominator if denominator else None
    eligible = bool(
        historical_probability is not None
        and len(rows) >= minimum_effective_sample
        and effective_sample >= minimum_effective_sample
        and denominator >= minimum_effective_sample * 0.20
    )
    credibility = min(
        0.40,
        effective_sample / (effective_sample + 80) * 0.55,
    ) if eligible else 0.0
    blended = None
    if historical_probability is not None:
        if market_probability is None:
            blended = historical_probability
        else:
            # Market is the prior. History may move it, but never by more than
            # eight percentage points before out-of-sample calibration.
            historical_delta = max(
                -0.08,
                min(0.08, historical_probability - market_probability),
            )
            blended = market_probability + credibility * historical_delta
    expected_return = (
        blended * odds
        if blended is not None and odds is not None and odds > 1 else None
    )
    signal = "样本不足"
    if eligible and market_probability is not None:
        delta = historical_probability - market_probability
        signal = (
            "历史高于市场" if delta >= 0.025
            else "历史低于市场" if delta <= -0.025
            else "历史接近市场"
        )
    elif eligible:
        signal = "仅有历史频率"
    confidence = (
        "高" if effective_sample >= 100
        else "中" if effective_sample >= 50
        else "低" if effective_sample >= minimum_effective_sample
        else "样本不足"
    )
    return {
        "sample": len(rows),
        "effective_sample": round(effective_sample, 1),
        "similarity_weight_mass": round(denominator, 2),
        "historical_probability": (
            round(historical_probability * 100, 2)
            if historical_probability is not None else None
        ),
        "market_probability": (
            round(market_probability * 100, 2)
            if market_probability is not None else None
        ),
        "blended_probability": (
            round(blended * 100, 2) if blended is not None else None
        ),
        "confidence_interval_95": (
            _wilson_interval(historical_probability, effective_sample)
            if historical_probability is not None else None
        ),
        "odds": round(odds, 3) if odds is not None else None,
        "fair_odds": (
            round(1 / blended, 3) if blended and blended > 0 else None
        ),
        "expected_return": (
            round(expected_return, 3)
            if expected_return is not None else None
        ),
        "value_edge": (
            round((expected_return - 1) * 100, 2)
            if expected_return is not None else None
        ),
        "credibility_weight": round(credibility, 3),
        "confidence": confidence,
        "eligible_for_adjustment": eligible,
        "signal": signal,
    }


def build_match_goal_margin_models(
    current_matches: Iterable[Dict[str, Any]],
    historical_matches: Iterable[Dict[str, Any]],
    before_date: str,
    *,
    half_life_days: int = 180,
    minimum_effective_sample: float = 25,
    maximum_rows: int = 320,
) -> Dict[str, Dict[str, Any]]:
    """Build leakage-safe draw and handicap-draw estimates per current match."""
    target = _date(before_date)
    if not target:
        return {}
    history = []
    for match in historical_matches:
        owner_date = _date(match.get("owner_date"))
        home = _number(match.get("home_score"))
        away = _number(match.get("away_score"))
        if (
            not owner_date or owner_date >= target
            or home is None or away is None
        ):
            continue
        history.append({
            "owner_date": owner_date,
            "difference": int(home - away),
            "features": _market_features(match),
        })

    results = {}
    for match in current_matches:
        match_id = str(match.get("match_id") or "")
        if not match_id:
            continue
        features = _market_features(match)
        weighted = []
        for row in history:
            similarity, labels = _similarity_weight(
                features, row["features"]
            )
            age_days = max(0, (target - row["owner_date"]).days)
            time_weight = math.pow(
                0.5, age_days / max(1, half_life_days)
            )
            weighted.append({
                "weight": similarity * time_weight,
                "difference": row["difference"],
                "same_league": "同联赛" in labels,
                "labels": labels,
            })
        weighted.sort(key=lambda row: row["weight"], reverse=True)
        rows = weighted[:max(20, int(maximum_rows))]

        euro = _current_euro_probabilities(match)
        hhad = _current_hhad_probabilities(match)
        draw_odds = _number(match.get("euro_current_draw"))
        hhad_draw_odds = _number(match.get("hi_current_draw_odds"))
        handicap = _match_handicap(match)
        ordinary = _weighted_event_metric(
            rows,
            lambda row: row["difference"] == 0,
            euro[1] if euro else None,
            draw_odds,
            minimum_effective_sample,
        )
        if handicap is not None and abs(handicap - round(handicap)) <= 0.01:
            target_difference = int(-round(handicap))
            handicap_draw = _weighted_event_metric(
                rows,
                lambda row: row["difference"] == target_difference,
                hhad[1] if hhad else None,
                hhad_draw_odds,
                minimum_effective_sample,
            )
            handicap_draw["target_goal_difference"] = target_difference
            handicap_draw["definition"] = (
                f"主队净胜球差恰好为{target_difference:+d}"
            )
        else:
            handicap_draw = {
                "sample": len(rows),
                "effective_sample": 0,
                "historical_probability": None,
                "market_probability": hhad[1] * 100 if hhad else None,
                "blended_probability": None,
                "odds": hhad_draw_odds,
                "confidence": "样本不足",
                "eligible_for_adjustment": False,
                "signal": "竞彩让球数缺失或不是整数，让平无法映射到精确净胜球差",
                "target_goal_difference": None,
            }

        distribution = {}
        denominator = sum(row["weight"] for row in rows)
        for label, predicate in (
            ("客胜3球+", lambda value: value <= -3),
            ("客胜2球", lambda value: value == -2),
            ("客胜1球", lambda value: value == -1),
            ("平局", lambda value: value == 0),
            ("主胜1球", lambda value: value == 1),
            ("主胜2球", lambda value: value == 2),
            ("主胜3球+", lambda value: value >= 3),
        ):
            distribution[label] = round(
                sum(
                    row["weight"] for row in rows
                    if predicate(row["difference"])
                ) / denominator * 100,
                2,
            ) if denominator else None
        label_counts: Dict[str, int] = {}
        for row in rows[:80]:
            for label in row["labels"]:
                label_counts[label] = label_counts.get(label, 0) + 1
        results[match_id] = {
            "version": GOAL_MARGIN_MODEL_VERSION,
            "before_date": str(before_date or "")[:10],
            "league": features.get("league"),
            "ordinary_draw": ordinary,
            "handicap_draw": handicap_draw,
            "goal_margin_distribution": distribution,
            "similarity": {
                "candidate_rows": len(weighted),
                "used_rows": len(rows),
                "same_league_rows": sum(
                    1 for row in rows if row["same_league"]
                ),
                "top_match_dimensions": [
                    label for label, _ in sorted(
                        label_counts.items(),
                        key=lambda item: item[1],
                        reverse=True,
                    )[:5]
                ],
            },
            "governance": {
                "historical_only": True,
                "future_matches_excluded": True,
                "time_decay_half_life_days": half_life_days,
                "minimum_effective_sample": minimum_effective_sample,
                "instruction": (
                    "相似历史频率是条件估计，不是因果结论或真实胜率；"
                    "模型以市场去水概率为先验，只做有限幅度修正。"
                ),
            },
        }
    return results


def _compact_rate(metric: Dict[str, Any]) -> Optional[float]:
    return metric.get("rate") if isinstance(metric, dict) else None


def _build_profile(
    league: str,
    matches: Iterable[Dict[str, Any]],
    before_date: str,
    half_life_days: int,
    minimum_samples: int,
) -> Dict[str, Any]:
    rows = _feature_rows(matches, before_date, half_life_days)
    effective_sample = round(sum(row["weight"] for row in rows), 1)
    outcomes = {
        key: _weighted_rate(rows, "outcome", key)
        for key in ("home", "draw", "away")
    }
    hhad = {
        key: _weighted_rate(rows, "hhad_result", key)
        for key in ("home", "draw", "away")
    }
    totals = {
        key: _weighted_rate(rows, "total_result", key)
        for key in ("over", "push", "under")
    }
    clear_favorite_rows = [
        row for row in rows
        if (
            _number(row.get("favorite_odds")) is not None
            and float(row["favorite_odds"]) <= 2.20
            and _number(row.get("favorite_odds_gap")) is not None
            and float(row["favorite_odds_gap"]) >= 0.20
        )
    ]
    favorite_failed = _weighted_rate(
        clear_favorite_rows, "favorite_failed"
    )
    favorite_drew = _weighted_rate(
        clear_favorite_rows, "favorite_drew"
    )
    underdog_won = _weighted_rate(
        clear_favorite_rows, "underdog_won"
    )
    favorite_not_cover = _weighted_rate(
        clear_favorite_rows, "favorite_not_cover"
    )
    asian_risk_patterns = {}
    non_cover_rows = [
        row for row in clear_favorite_rows
        if row.get("favorite_not_cover") is True
    ]
    non_cover_weight = sum(row["weight"] for row in non_cover_rows)
    for pattern_id, pattern_label in ASIAN_RISK_PATTERNS:
        pattern_rows = [
            row for row in clear_favorite_rows
            if pattern_id in (row.get("asian_risk_pattern_ids") or [])
        ]
        not_cover = _weighted_rate(pattern_rows, "favorite_not_cover")
        covered_non_cover_weight = sum(
            row["weight"] for row in pattern_rows
            if row.get("favorite_not_cover") is True
        )
        asian_risk_patterns[pattern_id] = {
            "label": pattern_label,
            "sample": not_cover["sample"],
            "effective_sample": not_cover["effective_sample"],
            "not_cover_rate": not_cover["rate"],
            "not_cover_case_share": round(
                covered_non_cover_weight / non_cover_weight * 100, 1
            ) if non_cover_weight else None,
        }
    favorite_bands = {}
    for band in ("1.50及以下", "1.51-1.80", "1.81-2.20", "2.20以上"):
        band_rows = [row for row in rows if row.get("favorite_band") == band]
        if not band_rows:
            continue
        won = _weighted_rate(band_rows, "favorite_won")
        won_by_one = _weighted_rate(
            [row for row in band_rows if row.get("favorite_won")],
            "favorite_won_by_one",
        )
        failed = _weighted_rate(band_rows, "favorite_failed")
        drew = _weighted_rate(band_rows, "favorite_drew")
        underdog = _weighted_rate(band_rows, "underdog_won")
        not_cover = _weighted_rate(band_rows, "favorite_not_cover")
        favorite_bands[band] = {
            "sample": won["sample"],
            "effective_sample": won["effective_sample"],
            "favorite_win_rate": won["rate"],
            "favorite_fail_rate": failed["rate"],
            "favorite_draw_rate": drew["rate"],
            "underdog_win_rate": underdog["rate"],
            "favorite_not_cover_rate": not_cover["rate"],
            "handicap_sample": not_cover["sample"],
            "one_goal_given_favorite_win_rate": won_by_one["rate"],
            "favorite_win_sample": won_by_one["sample"],
        }

    source_dates = sorted({
        row["owner_date"] for row in rows if row.get("owner_date")
    })
    profile = {
        "version": LEAGUE_PROFILE_VERSION,
        "league": league,
        "before_date": str(before_date or "")[:10],
        "sample_size": len(rows),
        "effective_sample_size": effective_sample,
        "source_date_range": [
            source_dates[0] if source_dates else None,
            source_dates[-1] if source_dates else None,
        ],
        "confidence": _confidence(len(rows), effective_sample),
        "eligible_for_adjustment": (
            len(rows) >= minimum_samples
            and effective_sample >= minimum_samples * 0.5
        ),
        "baseline": {
            "home_win_rate": _compact_rate(outcomes["home"]),
            "draw_rate": _compact_rate(outcomes["draw"]),
            "away_win_rate": _compact_rate(outcomes["away"]),
            "avg_total_goals": _weighted_average(rows, "total_goals"),
            "both_teams_score_rate": _compact_rate(
                _weighted_rate(rows, "both_scored")
            ),
            "one_goal_margin_rate": _compact_rate(
                _weighted_rate(rows, "one_goal_margin")
            ),
            "home_win_by_one_rate": _compact_rate(
                _weighted_rate(rows, "home_win_by_one")
            ),
            "away_win_by_one_rate": _compact_rate(
                _weighted_rate(rows, "away_win_by_one")
            ),
        },
        "sporttery_handicap": {
            "sample": hhad["home"]["sample"],
            "effective_sample": hhad["home"]["effective_sample"],
            "let_win_rate": _compact_rate(hhad["home"]),
            "let_draw_rate": _compact_rate(hhad["draw"]),
            "let_lose_rate": _compact_rate(hhad["away"]),
        },
        "total_market": {
            "sample": totals["over"]["sample"],
            "effective_sample": totals["over"]["effective_sample"],
            "over_rate": _compact_rate(totals["over"]),
            "push_rate": _compact_rate(totals["push"]),
            "under_rate": _compact_rate(totals["under"]),
        },
        "market_surprise": {
            "definition": "临场欧赔热门方赔率不高于2.20且胜负赔率差至少0.20时，热门方最终未取胜",
            "sample": favorite_failed["sample"],
            "effective_sample": favorite_failed["effective_sample"],
            "favorite_fail_rate": _compact_rate(favorite_failed),
            "favorite_draw_rate": _compact_rate(favorite_drew),
            "underdog_win_rate": _compact_rate(underdog_won),
            "favorite_not_cover_rate": _compact_rate(
                favorite_not_cover
            ),
            "handicap_sample": favorite_not_cover["sample"],
        },
        "asian_risk_patterns": {
            "definition": (
                "先识别欧赔明确热门方，再按该方的亚盘深度和水位初即时变化分类；"
                "not_cover_rate是同类赛前结构的历史不穿率，不是当前比赛真实概率。"
            ),
            "minimum_recommended_sample": 20,
            "patterns": asian_risk_patterns,
        },
        "favorite_odds_bands": favorite_bands,
        "hidden_signals": [],
        "governance": {
            "historical_only": True,
            "future_matches_excluded": True,
            "time_decay_half_life_days": half_life_days,
            "minimum_samples_for_adjustment": minimum_samples,
            "instruction": (
                "联赛画像只提供历史基线和相对倾向，不是当前比赛事实；"
                "小样本分段不得调权，任何联赛倾向都必须让位于当天盘口、阵容和数据质量。"
            ),
        },
    }
    return profile


def _delta(value: Any, baseline: Any) -> Optional[float]:
    left, right = _number(value), _number(baseline)
    return round(left - right, 1) if left is not None and right is not None else None


def _add_hidden_signals(
    profile: Dict[str, Any], global_profile: Dict[str, Any]
) -> None:
    baseline = profile.get("baseline") or {}
    global_baseline = global_profile.get("baseline") or {}
    handicap = profile.get("sporttery_handicap") or {}
    global_handicap = global_profile.get("sporttery_handicap") or {}
    surprise = profile.get("market_surprise") or {}
    global_surprise = global_profile.get("market_surprise") or {}
    risk_patterns = (
        (profile.get("asian_risk_patterns") or {}).get("patterns") or {}
    )
    global_risk_patterns = (
        (global_profile.get("asian_risk_patterns") or {}).get("patterns") or {}
    )
    risk_pattern_deltas = {
        pattern_id: _delta(
            (risk_patterns.get(pattern_id) or {}).get("not_cover_rate"),
            (global_risk_patterns.get(pattern_id) or {}).get(
                "not_cover_rate"
            ),
        )
        for pattern_id, _ in ASIAN_RISK_PATTERNS
    }
    deltas = {
        "draw_rate": _delta(
            baseline.get("draw_rate"), global_baseline.get("draw_rate")
        ),
        "avg_total_goals": _delta(
            baseline.get("avg_total_goals"),
            global_baseline.get("avg_total_goals"),
        ),
        "one_goal_margin_rate": _delta(
            baseline.get("one_goal_margin_rate"),
            global_baseline.get("one_goal_margin_rate"),
        ),
        "let_draw_rate": _delta(
            handicap.get("let_draw_rate"),
            global_handicap.get("let_draw_rate"),
        ),
        "favorite_fail_rate": _delta(
            surprise.get("favorite_fail_rate"),
            global_surprise.get("favorite_fail_rate"),
        ),
        "favorite_not_cover_rate": _delta(
            surprise.get("favorite_not_cover_rate"),
            global_surprise.get("favorite_not_cover_rate"),
        ),
        "asian_risk_patterns": risk_pattern_deltas,
    }
    profile["delta_vs_global"] = deltas
    if not profile.get("eligible_for_adjustment"):
        profile["hidden_signals"] = ["样本不足，仅展示基线，不参与调权"]
        return

    signals = []
    if deltas["draw_rate"] is not None and abs(deltas["draw_rate"]) >= 3:
        signals.append(
            "平局率较全库{}{}个百分点".format(
                "高" if deltas["draw_rate"] > 0 else "低",
                abs(deltas["draw_rate"]),
            )
        )
    if (
        deltas["avg_total_goals"] is not None
        and abs(deltas["avg_total_goals"]) >= 0.2
    ):
        signals.append(
            "场均进球较全库{}{}球".format(
                "高" if deltas["avg_total_goals"] > 0 else "低",
                abs(deltas["avg_total_goals"]),
            )
        )
    if (
        deltas["one_goal_margin_rate"] is not None
        and abs(deltas["one_goal_margin_rate"]) >= 4
    ):
        signals.append(
            "一球分差率较全库{}{}个百分点".format(
                "高" if deltas["one_goal_margin_rate"] > 0 else "低",
                abs(deltas["one_goal_margin_rate"]),
            )
        )
    if (
        handicap.get("sample", 0) >= 20
        and deltas["let_draw_rate"] is not None
        and abs(deltas["let_draw_rate"]) >= 4
    ):
        signals.append(
            "竞彩让平率较全库{}{}个百分点".format(
                "高" if deltas["let_draw_rate"] > 0 else "低",
                abs(deltas["let_draw_rate"]),
            )
        )
    if (
        surprise.get("sample", 0) >= 30
        and deltas["favorite_fail_rate"] is not None
        and abs(deltas["favorite_fail_rate"]) >= 5
    ):
        signals.append(
            "明确热门失手率较全库{}{}个百分点".format(
                "高" if deltas["favorite_fail_rate"] > 0 else "低",
                abs(deltas["favorite_fail_rate"]),
            )
        )
    if (
        surprise.get("handicap_sample", 0) >= 30
        and deltas["favorite_not_cover_rate"] is not None
        and abs(deltas["favorite_not_cover_rate"]) >= 5
    ):
        signals.append(
            "热门方不穿竞彩盘率较全库{}{}个百分点".format(
                "高" if deltas["favorite_not_cover_rate"] > 0 else "低",
                abs(deltas["favorite_not_cover_rate"]),
            )
        )
    significant_patterns = []
    for pattern_id, label in ASIAN_RISK_PATTERNS:
        metric = risk_patterns.get(pattern_id) or {}
        pattern_delta = risk_pattern_deltas.get(pattern_id)
        if (
            metric.get("sample", 0) >= 20
            and pattern_delta is not None
            and abs(pattern_delta) >= 8
        ):
            significant_patterns.append((
                abs(pattern_delta),
                "{}模式不穿率较全库{}{}个百分点".format(
                    label,
                    "高" if pattern_delta > 0 else "低",
                    abs(pattern_delta),
                ),
            ))
    signals.extend(
        signal for _, signal in sorted(
            significant_patterns, reverse=True
        )[:2]
    )
    profile["hidden_signals"] = signals[:6] or ["与全库基线接近，未发现显著联赛偏移"]


def build_league_profiles(
    matches_by_league: Dict[str, Iterable[Dict[str, Any]]],
    before_date: str,
    *,
    global_matches: Optional[Iterable[Dict[str, Any]]] = None,
    half_life_days: int = 180,
    minimum_samples: int = 30,
) -> Dict[str, Dict[str, Any]]:
    """Build leakage-safe profiles for all requested leagues."""
    all_rows = (
        list(global_matches)
        if global_matches is not None
        else [
            match
            for matches in matches_by_league.values()
            for match in matches
        ]
    )
    global_profile = _build_profile(
        "全库",
        all_rows,
        before_date,
        half_life_days,
        minimum_samples,
    )
    profiles = {}
    for league, matches in matches_by_league.items():
        profile = _build_profile(
            str(league or ""),
            matches,
            before_date,
            half_life_days,
            minimum_samples,
        )
        _add_hidden_signals(profile, global_profile)
        profiles[str(league or "")] = profile
    return profiles
