"""Time-decayed league history profiles for pre-match FAE analysis."""

from __future__ import annotations

from datetime import datetime
import math
import re
from typing import Any, Dict, Iterable, List, Optional


LEAGUE_PROFILE_VERSION = "league-profile-v1-time-decay"

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

        home_odds = _number(match.get("euro_current_win"))
        away_odds = _number(match.get("euro_current_lose"))
        favorite_side = None
        favorite_odds = None
        if home_odds and away_odds:
            favorite_side, favorite_odds = (
                ("home", home_odds)
                if home_odds <= away_odds else ("away", away_odds)
            )
        favorite_won = (
            outcome == favorite_side if favorite_side else None
        )
        favorite_won_by_one = (
            favorite_won and abs(difference) == 1
            if favorite_won is not None else None
        )

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
            "favorite_won": favorite_won,
            "favorite_won_by_one": favorite_won_by_one,
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
        favorite_bands[band] = {
            "sample": won["sample"],
            "effective_sample": won["effective_sample"],
            "favorite_win_rate": won["rate"],
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
