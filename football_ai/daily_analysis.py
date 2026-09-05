"""Daily cross-match Ark analysis using FAE's five-market review framework."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Callable, Dict, Iterable, List, Optional

from json_repair import repair_json

from .league_profile import classify_asian_risk_patterns
from .provider import ArkNarrativeClient, FAEError, FAEOutputError
from .special_markets import build_special_market_analysis
from .version import ENGINE_VERSION


DAILY_PROMPT_VERSION = "five-market-daily-v36-special-market-regimes"

OFFICIAL_PLAY_SELECTIONS = {"平局", "让平"}
OFFICIAL_MIN_BET_SCORE = 70.0
OFFICIAL_MIN_VALUE_SCORE = 60.0
OFFICIAL_MIN_MARKET_CONFIDENCE = 70.0
OFFICIAL_MIN_RATING = 4.0

# The all-match single is a probability forecast, not a value-bet verdict.
# Very short prices add little parlay value and are deliberately excluded.
SINGLE_MIN_ODDS = 1.50
SINGLE_MODEL_WEIGHT = 0.35
SINGLE_MARKET_WEIGHT = 0.65
SINGLE_SHORT_FAVORITE_HANDICAP_MIN_PROBABILITY = 33.0

# Every match still receives a probability direction, but only a small,
# independently gated subset may enter the formal all-play betting pool.  This
# is deliberately separate from ``no_bet``: that legacy flag governs the
# specialised draw/handicap-draw value pool and therefore rejects ordinary
# win/lose directions by design.
OFFICIAL_SINGLE_DAILY_LIMIT = 5
OFFICIAL_SINGLE_MIN_PROBABILITY = 33.0
OFFICIAL_SINGLE_MIN_MARKET_CONFIDENCE = 55.0
OFFICIAL_SINGLE_MIN_MODEL_EXPECTED_RETURN = 0.85
OFFICIAL_SINGLE_MIN_MODEL_MARKET_EDGE = -3.0
OFFICIAL_SINGLE_MIN_VALUE_SCORE = 50.0
OFFICIAL_SINGLE_MIN_BET_SCORE = 50.0
OFFICIAL_SINGLE_MIN_MODEL_RATING = 2.5
HIGH_CONFIDENCE_SINGLE_DAILY_LIMIT = 2
TWO_OPTION_MIN_COVERAGE = 64.0
TWO_OPTION_MIN_MARKET_CONFIDENCE = 65.0
TWO_OPTION_MIN_SECOND_GAP = 2.0
TWO_OPTION_MIN_SECONDARY_COVERAGE = 20.0
TWO_OPTION_DAILY_LIMIT = 5
TWO_OPTION_LOW_PRICE_FAVORITE_LIMIT = 2
TWO_OPTION_LOW_PRICE_FAVORITE_ODDS = 1.45
TWO_OPTION_SECONDARY_VALUE_MAX_GAP = 5.0
TWO_OPTION_SECONDARY_VALUE_MIN_GAIN = 0.04
TWO_OPTION_SECONDARY_VALUE_MIN_RETURN = 0.90
TWO_OPTION_COMBO_LIMIT = 3
TWO_OPTION_COMBO_MIN_ANCHOR_PROBABILITY = 60.0
TWO_OPTION_COMBO_MIN_ANCHOR_EXPECTED_RETURN = 0.90
TWO_OPTION_COMBO_MIN_JOINT_COVERAGE = 40.0
TWO_OPTION_COMBO_MIN_PATH_ODDS = 2.40
TWO_OPTION_COMBO_TARGET_PATH_ODDS = 3.00
TWO_OPTION_PLAY_SELECTIONS = {
    "主胜", "平局", "客胜", "让胜", "让平", "让负",
}

# “正式推荐”仍只允许平/让平。雷达观察层只负责排序和复盘，不能
# 被后置汇总重新升级；正式池必须同时满足核心层、非负赔率价值与
# 赔率区间风险硬门槛。
RADAR_OFFICIAL_POOL_LIMITS = {"平局": 2, "让平": 2}
RADAR_DISPLAY_LIMITS = {"ordinary_draw": 3, "handicap_draw": 3}
RADAR_OFFICIAL_SMALL_MIN_SCORE = {"平局": 88.0, "让平": 80.0}
RADAR_OFFICIAL_SMALL_MIN_PROBABILITY = {"平局": 29.0, "让平": 27.0}
RADAR_OFFICIAL_SMALL_MIN_VALUE = {"平局": 0.0, "让平": 0.0}
RADAR_OFFICIAL_MIN_SAMPLE = {"平局": 60.0, "让平": 60.0}
RADAR_OFFICIAL_MIN_MARKET_CONFIDENCE = 55.0
RADAR_OFFICIAL_MAX_RISK_IDS = {"平局": 1, "让平": 1}
HANDICAP_SECONDARY_MODEL_WEIGHT = 0.65
HANDICAP_SECONDARY_MARKET_WEIGHT = 0.35
DAILY_AI_MAX_BATCH_SIZE = 10
DAILY_AI_RECOVERY_BATCH_SIZE = 3
ASIAN_HARD_DOWNGRADE_RISKS = {
    "deepen_high_water",
    "upper_water_rise",
    "water_drop_without_deepen",
    "handicap_retreat",
    "euro_asian_divergence",
    "overheated_shallow",
}

DRAW_SELECTION_POLICY_DEFAULT = "conservative"


DAILY_AI_COMPACT_ANALYSIS_FIELDS = (
    "primary_play",
    "secondary_play",
    "single_play",
    "single_secondary_play",
    "single_odds",
    "single_secondary_odds",
    "single_probability",
    "single_secondary_probability",
    "handicap_play",
    "predicted_result",
    "star_text",
    "rating",
    "no_bet",
    "model_primary_play",
    "consistency_guard",
    "verdict",
    "prediction_probability",
    "market_implied_probability",
    "value_score",
    "market_confidence",
    "bet_score",
    "decision",
    "non_cover_guard",
    "historical_calibration",
    "two_option_recommendation",
    "official_bet_recommendation",
    "high_confidence_single_recommendation",
    "draw_radar",
    "market_analysis",
    "evidence",
    "risks",
    "score_candidates",
)

DAILY_AI_COMPACT_SCORE_FIELDS = (
    "label",
    "odds",
    "bet_score",
    "no_bet",
)


def compact_daily_ai_run(source: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the recommendation-list view without prompt-only model inputs.

    A full match snapshot intentionally keeps fundamentals, league history and
    calibration evidence for audits and detail pages.  The recommendation list
    only needs final decisions, current odds and the small goal-margin card.
    Projecting that shape cuts large match days from megabytes to a few hundred
    kilobytes without changing the persisted analysis.
    """
    if not source:
        return source
    result = {
        key: value for key, value in dict(source).items()
        if key not in {"matches", "provider_meta", "input_hash"}
    }
    compact_matches = []
    for item in source.get("matches") or []:
        analysis = item.get("analysis") or {}
        snapshot = item.get("input_snapshot") or {}
        fae_core = snapshot.get("fae_core") or {}
        recommendation = fae_core.get("recommendation") or {}
        scores = []
        for score in recommendation.get("category_scores") or []:
            scores.append({
                key: score.get(key)
                for key in DAILY_AI_COMPACT_SCORE_FIELDS
                if key in score
            })
        compact_item = {
            key: item.get(key)
            for key in (
                "match_id",
                "match_number",
                "owner_date",
                "home_team",
                "away_team",
                "league",
                "match_time",
                "status_at_prediction",
                "current_status",
                "result_score",
                "retained_from_pregame",
                "retained_from_run_id",
                "generated_at",
                "analysis_source",
            )
            if key in item
        }
        compact_item.update({
            "analysis": {
                key: analysis.get(key)
                for key in DAILY_AI_COMPACT_ANALYSIS_FIELDS
                if key in analysis
            },
            "input_snapshot": {
                "euro": {"current": (snapshot.get("euro") or {}).get("current")},
                "asian": {"current": (snapshot.get("asian") or {}).get("current")},
                "sporttery_handicap": {
                    "value": (snapshot.get("sporttery_handicap") or {}).get("value"),
                    "current": (snapshot.get("sporttery_handicap") or {}).get("current"),
                },
                "total": {"current": (snapshot.get("total") or {}).get("current")},
                "upset_warning_model": snapshot.get(
                    "upset_warning_model"
                ) or {},
                "data_warnings": snapshot.get("data_warnings") or [],
                "historical_goal_margin_model": snapshot.get(
                    "historical_goal_margin_model"
                ) or {},
                "low_odds_asian_model": snapshot.get(
                    "low_odds_asian_model"
                ) or {},
                "supervised_shadow": snapshot.get(
                    "supervised_shadow"
                ) or {},
                "fae_core": {
                    "recommendation": {"category_scores": scores},
                    "risk": fae_core.get("risk") or {},
                },
            },
        })
        special = analysis.get("special_markets") or {}
        compact_special = {
            "version": special.get("version"),
            "source": special.get("source"),
        }
        for key in ("total_goals", "half_full"):
            market = special.get(key) or {}
            if not market:
                continue
            compact_special[key] = {
                field: market.get(field)
                for field in (
                    "available", "market", "model_version", "primary",
                    "secondary", "confidence", "reason", "expected_goals",
                    "regime", "regime_label", "baseline_only", "actionable",
                    "recommendation_status", "direction_profile",
                    "data_complete", "calculator_available",
                )
                if field in market
            }
            compact_special[key]["snapshot"] = {
                "updated_at": (market.get("snapshot") or {}).get("updated_at")
            }
        if len(compact_special) > 2:
            compact_item["analysis"]["special_markets"] = compact_special
        secondary_guard = analysis.get("secondary_selection_guard") or {}
        if secondary_guard:
            compact_item["analysis"]["secondary_selection_guard"] = {
                "selection": secondary_guard.get("selection"),
                "strategy": secondary_guard.get("strategy"),
                "cross_market": secondary_guard.get("cross_market", False),
                "source_market": secondary_guard.get("source_market"),
                "target_market": secondary_guard.get("target_market"),
                "secondary_gate": secondary_guard.get("secondary_gate"),
                "candidates": [{
                    "selection": candidate.get("selection"),
                    "coverage_score": candidate.get("coverage_score"),
                } for candidate in secondary_guard.get("candidates") or []],
            }
        compact_matches.append(compact_item)
    result["matches"] = compact_matches
    result["compact"] = True
    return result

# 通过可切换策略统一控制平/让平的门槛。便于AB测试、回测复盘和线上快速回退。
DRAW_SELECTION_POLICIES = {
    "conservative": {
        "min_probability": {"平局": 30.0, "让平": 30.0},
        "core_score": {"平局": 78.0, "让平": 80.0},
        "watch_score": {"平局": 64.0, "让平": 67.0},
        "min_value": {"平局": 2.0, "让平": 4.0},
        "min_sample": {"平局": 24.0, "让平": 28.0},
        "max_risk_ids": {"平局": 1, "让平": 2},
        "draw_upgrade_gap_from_draw": {"平局": 15.0, "让平": 17.0},
        "draw_upgrade_from_non_draw": {
            "best_score_min": 72.0,
            "best_value_min": 60.0,
            "score_gap_min": 18.0,
            "value_gap_min": 8.0,
        },
    },
    "balanced": {
        "min_probability": {"平局": 27.0, "让平": 28.0},
        "core_score": {"平局": 72.0, "让平": 74.0},
        "watch_score": {"平局": 62.0, "让平": 64.0},
        "min_value": {"平局": 1.0, "让平": 1.5},
        "min_sample": {"平局": 22.0, "让平": 24.0},
        "max_risk_ids": {"平局": 2, "让平": 3},
        "draw_upgrade_gap_from_draw": {"平局": 14.0, "让平": 16.0},
        "draw_upgrade_from_non_draw": {
            "best_score_min": 70.0,
            "best_value_min": 58.0,
            "score_gap_min": 16.0,
            "value_gap_min": 6.0,
        },
    },
    "aggressive": {
        "min_probability": {"平局": 25.0, "让平": 26.0},
        "core_score": {"平局": 68.0, "让平": 70.0},
        "watch_score": {"平局": 60.0, "让平": 62.0},
        "min_value": {"平局": 0.8, "让平": 1.2},
        "min_sample": {"平局": 18.0, "让平": 20.0},
        "max_risk_ids": {"平局": 3, "让平": 4},
        "draw_upgrade_gap_from_draw": {"平局": 12.0, "让平": 14.0},
        "draw_upgrade_from_non_draw": {
            "best_score_min": 66.0,
            "best_value_min": 56.0,
            "score_gap_min": 14.0,
            "value_gap_min": 4.0,
        },
    },
}

DRAW_SELECTION_MIN_PROBABILITY = DRAW_SELECTION_POLICIES[
    DRAW_SELECTION_POLICY_DEFAULT
]["min_probability"]
DRAW_SELECTION_CORE_SCORE = DRAW_SELECTION_POLICIES[
    DRAW_SELECTION_POLICY_DEFAULT
]["core_score"]
DRAW_SELECTION_WATCH_SCORE = DRAW_SELECTION_POLICIES[
    DRAW_SELECTION_POLICY_DEFAULT
]["watch_score"]
DRAW_SELECTION_MIN_VALUE = DRAW_SELECTION_POLICIES[
    DRAW_SELECTION_POLICY_DEFAULT
]["min_value"]
DRAW_SELECTION_MIN_SAMPLE = DRAW_SELECTION_POLICIES[
    DRAW_SELECTION_POLICY_DEFAULT
]["min_sample"]
DRAW_SELECTION_MAX_RISK_IDS = DRAW_SELECTION_POLICIES[
    DRAW_SELECTION_POLICY_DEFAULT
]["max_risk_ids"]


def normalize_draw_selection_policy(value: Any) -> str:
    policy = str(value or "").strip().lower().replace("-", "_")
    if policy in {"", "default", "conserv", "strict", "safe"}:
        return "conservative"
    if policy in {"normal", "moderate", "balanced", "middle"}:
        return "balanced"
    if policy in {"aggressive", "high", "wide"}:
        return "aggressive"
    if policy not in DRAW_SELECTION_POLICIES:
        return DRAW_SELECTION_POLICY_DEFAULT
    return policy


def draw_selection_policy_profile(policy: Any) -> Dict[str, Any]:
    return DRAW_SELECTION_POLICIES.get(
        normalize_draw_selection_policy(policy),
        DRAW_SELECTION_POLICIES[DRAW_SELECTION_POLICY_DEFAULT],
    )

HANDICAP_VALUES = {
    "平手": 0.0, "平/半": 0.25, "平手/半球": 0.25,
    "半球": 0.5, "半/一": 0.75, "半球/一球": 0.75,
    "一球": 1.0, "一/球半": 1.25, "一球/球半": 1.25,
    "球半": 1.5, "球半/两": 1.75, "球半/两球": 1.75,
    "两球": 2.0, "两/两球半": 2.25, "两球/两球半": 2.25,
    "两球半": 2.5, "两球半/三球": 2.75, "三球": 3.0,
    "三球/三球半": 3.25, "三球半": 3.5,
}


def _number(value: Any) -> Optional[float]:
    try:
        return float(
            re.sub(r"[^\d.+-]", "", str(value))
        ) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _total_line_number(value: Any) -> Optional[float]:
    text = re.sub(r"[↑↓升降]", "", str(value or "")).strip()
    if not text:
        return None
    parts = [_number(item.strip()) for item in text.split("/")]
    if not parts or any(item is None for item in parts):
        return None
    return round(sum(parts) / len(parts), 3)


def _clean_handicap(value: Any) -> str:
    return re.sub(r"(?:[↑↓]|升|降)+$", "", str(value or "").strip())


LEAGUE_TACTICAL_MODEL_VERSION = "league-tactical-model-v1"
UPSET_WARNING_MODEL_VERSION = "upset-warning-v1"
ODDS_BAND_MODEL_VERSION = "odds-band-model-v1"
LOW_ODDS_ASIAN_MODEL_VERSION = "low-odds-asian-hhad-v1"

# 历史回测：让平不能靠“升盘高水/欧亚背离”单独升级。
# 正向信号主要来自：联赛画像 + 竞彩让1球 + 热门胜赔区间 + 让平赔率区间。
HANDICAP_DRAW_BACKTEST_VERSION = "handicap-draw-backtest-v4-movement-soft-signal"
HANDICAP_DRAW_PATH_MODEL_VERSION = "handicap-draw-path-v2-price-confirmation"
SPORTTERY_DRAW_PRICE_SIGNAL_VERSION = "sporttery-draw-price-signal-v1"
ORDINARY_DRAW_BACKTEST_VERSION = "ordinary-draw-backtest-v1"
ORDINARY_DRAW_POSITIVE_LEAGUES = {
    "德甲",
    "沙特联",
    "葡超",
    "瑞典超",
    "K1联赛",
    "K联赛",
    "韩K联",
    "韩职",
    "法乙",
    "法甲",
}
ORDINARY_DRAW_RULE_MIN_SAMPLE = 25.0
HANDICAP_DRAW_RULE_MIN_SAMPLE = 25.0
HANDICAP_DRAW_POSITIVE_LEAGUES = {
    "法甲",
    "英冠",
    "芬超",
    "芬兰超",
    "非洲杯",
    "西甲",
    "日职",
    "J1联赛",
    "挪超",
    "K1联赛",
    "K联赛",
    "韩K联",
    "韩职",
    "沙特联",
}
HANDICAP_DRAW_NEGATIVE_LEAGUES = {
    "欧冠",
    "英超",
    "德甲",
    "葡超",
    "瑞典超",
    "荷甲",
    "荷乙",
    "澳超",
    "美职联",
    "MLS",
}

HANDICAP_DRAW_FORMAL_CORE_KINDS = {
    "backtested_hhad_plus1_low_odds_value",
    "backtested_hhad_small_rise_value",
}
HANDICAP_DRAW_FORMAL_SECONDARY_KINDS = {
    "backtested_hhad_minus1_draw_band",
}
HANDICAP_DRAW_FORMAL_KINDS = (
    HANDICAP_DRAW_FORMAL_CORE_KINDS
    | HANDICAP_DRAW_FORMAL_SECONDARY_KINDS
)

LEAGUE_TACTICAL_TEMPLATES = {
    "k_league": {
        "aliases": ("K1联赛", "K联赛", "韩K联", "韩职"),
        "label": "K联赛",
        "style": "节奏慢、防守强、平局多",
        "draw_base": 72,
        "handicap_draw_base": 74,
        "over_base": 38,
        "under_base": 72,
        "upset_base": 58,
        "total_direction": "小球",
        "score_templates": ["1:1", "1:0", "0:0"],
    },
    "finland_veikkausliiga": {
        "aliases": ("芬超", "芬兰超"),
        "label": "芬超",
        "style": "开放、进球多、强弱明显",
        "draw_base": 50,
        "handicap_draw_base": 62,
        "over_base": 64,
        "under_base": 42,
        "upset_base": 48,
        "total_direction": "大球",
        "score_templates": ["2:1", "2:2", "3:1"],
    },
    "sweden_allsvenskan": {
        "aliases": ("瑞典超",),
        "label": "瑞典超",
        "style": "攻防开放、主场强",
        "draw_base": 56,
        "handicap_draw_base": 64,
        "over_base": 68,
        "under_base": 40,
        "upset_base": 50,
        "total_direction": "大球",
        "score_templates": ["2:1", "1:1", "3:1"],
    },
    "norway_eliteserien": {
        "aliases": ("挪超",),
        "label": "挪超",
        "style": "高节奏、高进球",
        "draw_base": 44,
        "handicap_draw_base": 70,
        "over_base": 72,
        "under_base": 36,
        "upset_base": 52,
        "total_direction": "大球",
        "score_templates": ["2:1", "3:1", "2:2"],
    },
    "brazil_serie_a": {
        "aliases": ("巴甲",),
        "label": "巴甲",
        "style": "平局多、防守博弈",
        "draw_base": 74,
        "handicap_draw_base": 58,
        "over_base": 36,
        "under_base": 74,
        "upset_base": 60,
        "total_direction": "小球",
        "score_templates": ["1:1", "1:0", "0:0"],
    },
    "mls": {
        "aliases": ("美职联", "MLS"),
        "label": "MLS",
        "style": "开放、进球多、盘口容易深",
        "draw_base": 58,
        "handicap_draw_base": 68,
        "over_base": 76,
        "under_base": 34,
        "upset_base": 54,
        "total_direction": "大球",
        "score_templates": ["2:1", "2:2", "3:2"],
    },
}


def _league_tactical_template(league: Any) -> Optional[Dict[str, Any]]:
    text = str(league or "").strip()
    if not text:
        return None
    for key, template in LEAGUE_TACTICAL_TEMPLATES.items():
        if any(alias and alias in text for alias in template["aliases"]):
            return {"key": key, **template}
    return None


def _league_in_aliases(league: Any, aliases: Iterable[str]) -> bool:
    text = str(league or "").strip()
    if not text:
        return False
    for alias in aliases:
        alias_text = str(alias or "").strip()
        if not alias_text:
            continue
        # “日职”不能误命中“日职乙”，否则 J2 会被当成 J1 模型。
        if alias_text in {"日职", "J1联赛"} and text in {"日职乙", "日乙", "J2联赛"}:
            continue
        if alias_text in text:
            return True
    return False


def _clamp_index(value: Any) -> int:
    return int(round(max(0, min(99, float(value or 0)))))


def _market_no_vig(values: Iterable[Any]) -> List[Optional[float]]:
    numbers = [_number(value) for value in values]
    if any(value is None or value <= 1 for value in numbers):
        return [None for _ in numbers]
    inverse = [1 / float(value) for value in numbers]  # type: ignore[arg-type]
    total = sum(inverse)
    if not total:
        return [None for _ in numbers]
    return [round(value / total * 100, 2) for value in inverse]


def _rank_gap(rank: Dict[str, Any]) -> Optional[float]:
    home = _number(rank.get("home"))
    away = _number(rank.get("away"))
    if home is None or away is None:
        return None
    return abs(home - away)


def _handicap_value_from_text(value: Any) -> Optional[float]:
    text = re.sub(r"\s+", "", _clean_handicap(value))
    if not text:
        return None
    numeric = _number(text)
    if numeric is not None and re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        return numeric
    receiving = text.startswith("受")
    key = text[1:] if receiving else text
    if key not in HANDICAP_VALUES:
        return None
    return -HANDICAP_VALUES[key] if receiving else HANDICAP_VALUES[key]


def _build_league_tactical_model(
    match: Dict[str, Any],
    probabilities: Dict[str, Any],
    sporttery_handicap: Optional[float],
    current_total: Optional[float],
    current_asian_risk: Dict[str, Any],
    rank: Dict[str, Any],
) -> Dict[str, Any]:
    template = _league_tactical_template(match.get("league"))
    if not template:
        return {
            "version": LEAGUE_TACTICAL_MODEL_VERSION,
            "matched": False,
            "league": match.get("league"),
            "message": "当前联赛暂无固定模板，仅使用盘口和历史样本模型",
        }

    euro_probs = _market_no_vig([
        match.get("euro_current_win"),
        match.get("euro_current_draw"),
        match.get("euro_current_lose"),
    ])
    hhad = probabilities.get("hhad") or {}
    totals = probabilities.get("over_under") or {}
    draw_probability = _number(probabilities.get("draw")) or (
        euro_probs[1] if len(euro_probs) > 1 else None
    )
    handicap_draw_probability = _number(hhad.get("draw"))
    over_probability = _number(totals.get("over"))
    under_probability = _number(totals.get("under"))
    total_direction = str(template.get("total_direction") or "大球")
    total_probability = (
        over_probability if total_direction == "大球" else under_probability
    )

    risk_ids = {
        str(value)
        for value in current_asian_risk.get("pattern_ids") or []
    }
    unstable_risks = {
        "deepen_high_water",
        "upper_water_rise",
        "water_drop_without_deepen",
        "handicap_retreat",
        "euro_asian_divergence",
        "overheated_shallow",
    }
    favorite_side = current_asian_risk.get("favorite_side")
    ranking_gap = _rank_gap(rank)
    asian_line_value = _handicap_value_from_text(
        match.get("asian_current_handicap")
    )
    asian_depth = abs(asian_line_value) if asian_line_value is not None else None

    reasons: List[str] = [str(template["style"])]
    draw_index = float(template["draw_base"])
    if draw_probability is not None:
        draw_index += max(-10, min(10, (draw_probability - 27) * 1.2))
    if asian_depth is not None and asian_depth <= 0.25:
        draw_index += 8
        reasons.append("亚盘浅盘，平局指数加权")
    if current_total is not None and current_total <= 2.5:
        draw_index += 5
    if ranking_gap is not None and ranking_gap <= 4:
        draw_index += 5
        reasons.append("排名接近，平局指数加权")
    if risk_ids & unstable_risks:
        draw_index += 4

    handicap_draw_index = float(template["handicap_draw_base"])
    if handicap_draw_probability is not None:
        handicap_draw_index += max(
            -8, min(12, (handicap_draw_probability - 24) * 1.15)
        )
    if sporttery_handicap is not None and abs(sporttery_handicap) == 1:
        handicap_draw_index += 6
        reasons.append("竞彩让1球，精确一球差纳入让平模板")
    if asian_depth is not None and 0.5 <= asian_depth <= 1.0:
        handicap_draw_index += 6
    if risk_ids & unstable_risks:
        handicap_draw_index += 5
    if favorite_side and sporttery_handicap:
        if (
            (favorite_side == "away" and sporttery_handicap > 0)
            or (favorite_side == "home" and sporttery_handicap < 0)
        ):
            handicap_draw_index += 4

    over_index = float(template["over_base"])
    under_index = float(template["under_base"])
    if over_probability is not None:
        over_index += max(-10, min(10, (over_probability - 50) * 0.8))
    if under_probability is not None:
        under_index += max(-10, min(10, (under_probability - 50) * 0.8))
    if total_probability is not None:
        # Keep the selected league-side total direction sensitive to the
        # current market probability without letting it fully override market.
        total_index_hint = max(-8, min(8, (total_probability - 50) * 0.55))
        if total_direction == "大球":
            over_index += total_index_hint
        else:
            under_index += total_index_hint
    if current_total is not None:
        if current_total >= 2.75:
            over_index += 4
        if current_total <= 2.5:
            under_index += 4
    total_index = over_index if total_direction == "大球" else under_index
    counter_total_index = under_index if total_direction == "大球" else over_index

    upset_index = float(template["upset_base"])
    if risk_ids & unstable_risks:
        upset_index += 10
        reasons.append("热门方盘口/水位不稳，冷门指数加权")
    if (_number(current_asian_risk.get("upper_water_change")) or 0) >= 0.08:
        upset_index += 6
    if ranking_gap is not None and ranking_gap <= 5:
        upset_index += 4

    return {
        "version": LEAGUE_TACTICAL_MODEL_VERSION,
        "matched": True,
        "league": match.get("league"),
        "league_family": template["key"],
        "league_label": template["label"],
        "style": template["style"],
        "indexes": {
            "draw": _clamp_index(draw_index),
            "handicap_draw": _clamp_index(handicap_draw_index),
            "total": _clamp_index(total_index),
            "over": _clamp_index(over_index),
            "under": _clamp_index(under_index),
            "upset": _clamp_index(upset_index),
        },
        "total_direction": total_direction,
        "counter_total_index": _clamp_index(counter_total_index),
        "score_templates": template["score_templates"],
        "priority": {
            "first": (
                "平" if template["draw_base"] >= 70
                else total_direction if template["over_base"] >= 60
                else "观望"
            ),
            "second": "让平",
        },
        "matched_conditions": list(dict.fromkeys(reasons))[:6],
        "governance": (
            "联赛模板只作低到中权重先验；必须让位于当场欧赔、"
            "亚盘、竞彩让球、大小球、价值指数和数据质量。"
        ),
    }


def _index_level(value: Any) -> str:
    score = float(_number(value) or 0)
    if score >= 80:
        return "高危"
    if score >= 65:
        return "重点观察"
    if score >= 50:
        return "提示"
    return "正常"


def _favorite_team_name(match: Dict[str, Any], favorite_side: Any) -> Optional[str]:
    if favorite_side == "home":
        return match.get("home_team")
    if favorite_side == "away":
        return match.get("away_team")
    return None


def _build_odds_band_model(
    match: Dict[str, Any],
    sporttery_handicap: Optional[float],
    current_asian_risk: Dict[str, Any],
) -> Dict[str, Any]:
    euro_initial = [
        _number(match.get("euro_initial_win")),
        _number(match.get("euro_initial_draw")),
        _number(match.get("euro_initial_lose")),
    ]
    euro_current = [
        _number(match.get("euro_current_win")),
        _number(match.get("euro_current_draw")),
        _number(match.get("euro_current_lose")),
    ]
    if any(value is None or value <= 1 for value in euro_current):
        return {
            "version": ODDS_BAND_MODEL_VERSION,
            "available": False,
            "message": "欧赔数据不足，无法计算赔率区间指标",
            "indexes": {
                "favorite_heat": 0,
                "underdog_upset": 0,
                "handicap_draw_value": 0,
            },
            "signals": [],
        }

    favorite_index = min(range(3), key=lambda index: euro_current[index])
    favorite_side = ("home", "draw", "away")[favorite_index]
    draw_current = euro_current[1]
    draw_initial = euro_initial[1] if len(euro_initial) > 1 else None
    favorite_current = euro_current[favorite_index]
    favorite_initial = (
        euro_initial[favorite_index]
        if len(euro_initial) > favorite_index else None
    )
    risk_ids = {
        str(value)
        for value in current_asian_risk.get("pattern_ids") or []
    }
    current_line = _handicap_value_from_text(match.get("asian_current_handicap"))
    initial_line = _handicap_value_from_text(match.get("asian_initial_handicap"))
    current_depth = None
    initial_depth = None
    if favorite_side == "home":
        current_depth = current_line
        initial_depth = initial_line
    elif favorite_side == "away":
        current_depth = -current_line if current_line is not None else None
        initial_depth = -initial_line if initial_line is not None else None
    current_depth = abs(current_depth) if current_depth is not None else None
    initial_depth = abs(initial_depth) if initial_depth is not None else None
    hhad_current = [
        _number(match.get("hi_current_home_odds")),
        _number(match.get("hi_current_draw_odds")),
        _number(match.get("hi_current_away_odds")),
    ]
    hhad_draw_odds = hhad_current[1] if len(hhad_current) > 1 else None

    signals: List[Dict[str, Any]] = []
    favorite_heat = 25.0
    underdog_upset = 20.0
    handicap_draw_value = 20.0

    def add_signal(
        key: str,
        label: str,
        heat_delta: float,
        upset_delta: float,
        handicap_draw_delta: float,
        reason: str,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> None:
        nonlocal favorite_heat, underdog_upset, handicap_draw_value
        favorite_heat += heat_delta
        underdog_upset += upset_delta
        handicap_draw_value += handicap_draw_delta
        signals.append({
            "key": key,
            "label": label,
            "heat_delta": round(heat_delta, 2),
            "upset_delta": round(upset_delta, 2),
            "handicap_draw_delta": round(handicap_draw_delta, 2),
            "reason": reason,
            "evidence": evidence or {},
        })

    favorite_band = "unknown"
    favorite_band_label = "未知"
    if favorite_side == "draw":
        favorite_band = "draw_favorite"
        favorite_band_label = "平赔最低"
        add_signal(
            "draw_lowest_odds",
            "平赔最低",
            -5,
            8,
            4,
            "欧赔无明确胜负热门，优先按均势和普通平处理",
            {"current": euro_current},
        )
    elif favorite_current is not None:
        if favorite_current <= 1.30:
            favorite_band = "extreme_hot"
            favorite_band_label = "1.30以下极热低赔"
            add_signal(
                "favorite_extreme_hot_band",
                "极热低赔",
                30,
                8,
                5,
                "热门赔率极低，爆冷基础概率不高，但需防轮换/赛程导致的平局",
                {"favorite_odds": favorite_current},
            )
        elif favorite_current < 1.40:
            favorite_band = "hot_transition"
            favorite_band_label = "1.31-1.39强势过渡"
            add_signal(
                "favorite_hot_transition_band",
                "强势低赔",
                24,
                12,
                8,
                "热门明显占优，但赔率未到碾压区，需看盘口是否给足深度",
                {"favorite_odds": favorite_current},
            )
        elif favorite_current <= 1.70:
            favorite_band = "danger"
            favorite_band_label = "1.40-1.70热门危险区"
            add_signal(
                "favorite_danger_band",
                "热门危险区",
                22,
                25,
                14,
                "热门赔率看似稳定但未形成碾压，是下盘和让平重点观察区",
                {"favorite_odds": favorite_current},
            )
        elif favorite_current < 1.80:
            favorite_band = "danger_edge"
            favorite_band_label = "1.71-1.79危险边缘"
            add_signal(
                "favorite_danger_edge_band",
                "危险边缘",
                14,
                18,
                10,
                "热门优势有限，若盘口升水或平赔下降，热门不稳权重上升",
                {"favorite_odds": favorite_current},
            )
        elif favorite_current <= 2.20:
            favorite_band = "balanced"
            favorite_band_label = "1.80-2.20均势盘"
            add_signal(
                "favorite_balanced_band",
                "均势盘",
                4,
                10,
                5,
                "胜负差距不大，普通平局价值高于单纯追热门",
                {"favorite_odds": favorite_current},
            )
        elif favorite_current <= 2.50:
            favorite_band = "spread"
            favorite_band_label = "2.21-2.50分散均势"
            add_signal(
                "favorite_spread_band",
                "分散均势",
                0,
                6,
                2,
                "欧赔较分散，爆冷定义弱化，重点看平赔和盘口",
                {"favorite_odds": favorite_current},
            )
        else:
            favorite_band = "weak_favorite"
            favorite_band_label = "2.50以上弱热门"
            add_signal(
                "favorite_weak_band",
                "弱热门",
                -6,
                0,
                0,
                "无明确强热门，爆冷模型降权，按均势盘处理",
                {"favorite_odds": favorite_current},
            )

    if (
        favorite_side == "away"
        and favorite_current is not None
        and 1.70 <= favorite_current <= 2.20
    ):
        add_signal(
            "away_favorite_trap_band",
            "强队客场陷阱",
            8,
            14,
            5,
            "客胜处在1.70-2.20区间，客场热门不胜风险高于主场同赔率",
            {"favorite_odds": favorite_current},
        )

    draw_band = "unknown"
    if draw_current is not None:
        if draw_current <= 3.00:
            draw_band = "very_low"
            add_signal(
                "draw_odds_very_low_band",
                "平赔低于3.00",
                0,
                15,
                8,
                "平赔处在强防范区，爆冷优先考虑平局路径",
                {"draw_odds": draw_current},
            )
        elif draw_current <= 3.20:
            draw_band = "low"
            add_signal(
                "draw_odds_low_band",
                "平赔3.00-3.20",
                0,
                10,
                6,
                "平赔偏低，庄家对平局有防范",
                {"draw_odds": draw_current},
            )
        elif draw_current <= 3.50:
            draw_band = "normal"
            add_signal(
                "draw_odds_normal_band",
                "平赔3.20-3.50",
                0,
                4,
                2,
                "平赔正常区，需结合盘口和让球赔率判断",
                {"draw_odds": draw_current},
            )
        elif draw_current >= 4.00:
            draw_band = "high"
            add_signal(
                "draw_odds_high_band",
                "平赔4.00以上",
                4,
                -8,
                -4,
                "平赔偏高，普通平局基础权重下降",
                {"draw_odds": draw_current},
            )
        else:
            draw_band = "upper_normal"

    if (
        draw_initial is not None
        and draw_current is not None
        and draw_initial - draw_current >= 0.10
    ):
        add_signal(
            "draw_odds_drop_band",
            "平赔下降",
            0,
            15,
            8,
            "平赔从初盘到即时明显下降，热门不胜路径增强",
            {
                "initial": draw_initial,
                "current": draw_current,
                "change": round(draw_current - draw_initial, 3),
            },
        )

    if (
        favorite_initial is not None
        and favorite_current is not None
        and favorite_current - favorite_initial >= 0.06
    ):
        add_signal(
            "favorite_odds_rise_band",
            "热门胜赔上升",
            -2,
            20,
            8,
            "热门胜赔上升，市场对热门打出信心下降",
            {
                "initial": favorite_initial,
                "current": favorite_current,
                "change": round(favorite_current - favorite_initial, 3),
            },
        )
    elif (
        favorite_initial is not None
        and favorite_current is not None
        and favorite_initial - favorite_current >= 0.08
    ):
        add_signal(
            "favorite_odds_drop_no_deepen_watch",
            "热门降赔",
            10,
            0,
            3,
            "热门胜赔下降，若盘口不升深则按热门过热处理",
            {
                "initial": favorite_initial,
                "current": favorite_current,
                "change": round(favorite_current - favorite_initial, 3),
            },
        )

    unstable_risks = {
        "handicap_retreat",
        "upper_water_rise",
        "water_drop_without_deepen",
        "deepen_high_water",
        "euro_asian_divergence",
        "overheated_shallow",
    }
    if risk_ids & unstable_risks:
        add_signal(
            "asian_unstable_band",
            "盘口不配合",
            8,
            18,
            12,
            "盘口/水位存在热门不稳信号，赔率危险区需要额外降级热门",
            {"risk_ids": sorted(risk_ids & unstable_risks)},
        )

    depth_drop = (
        current_depth is not None
        and initial_depth is not None
        and current_depth < initial_depth - 0.20
    )
    shallow_danger = (
        favorite_current is not None
        and favorite_current <= 1.80
        and current_depth is not None
        and current_depth <= 0.50
    )
    if depth_drop or shallow_danger:
        add_signal(
            "favorite_shallow_support",
            "热门盘口偏浅",
            8,
            18,
            8,
            "热门赔率占优，但亚洲盘深度不给足支持",
            {
                "initial_depth": initial_depth,
                "current_depth": current_depth,
            },
        )

    deep_line = (
        (current_depth is not None and current_depth >= 1.25)
        or (sporttery_handicap is not None and abs(sporttery_handicap) >= 2)
    )
    if (
        deep_line
        and favorite_current is not None
        and favorite_current >= 1.35
    ):
        add_signal(
            "handicap_too_deep_band",
            "盘口过深",
            14,
            20,
            14,
            "盘口深度高于赔率碾压程度，优先防赢球输盘或下盘",
            {
                "favorite_odds": favorite_current,
                "sporttery_handicap": sporttery_handicap,
                "asian_depth": current_depth,
            },
        )

    if sporttery_handicap is not None:
        if abs(sporttery_handicap) == 1:
            add_signal(
                "sporttery_one_goal_line",
                "竞彩让1球",
                0,
                3,
                15,
                "竞彩让1球天然对应强队恰好赢1球的让平路径",
                {"sporttery_handicap": sporttery_handicap},
            )
        elif abs(sporttery_handicap) == 2:
            add_signal(
                "sporttery_two_goal_line",
                "竞彩让2球",
                2,
                2,
                -6,
                "历史回测中让2球让平区间偏弱，只作为盘口深度风险",
                {"sporttery_handicap": sporttery_handicap},
            )

    if hhad_draw_odds is not None:
        if 3.30 <= hhad_draw_odds <= 3.70:
            add_signal(
                "handicap_draw_odds_value_band",
                "让平赔率价值区",
                0,
                2,
                18,
                "竞彩让平赔率处在可博价值区，需结合盘口深度和历史样本",
                {"hhad_draw_odds": hhad_draw_odds},
            )
        elif 2.80 <= hhad_draw_odds < 3.20:
            add_signal(
                "handicap_draw_odds_low_band",
                "让平低赔防范区",
                0,
                4,
                8,
                "让平赔率偏低，市场有防范但赔付价值有限",
                {"hhad_draw_odds": hhad_draw_odds},
            )
        elif 3.90 < hhad_draw_odds <= 4.50:
            add_signal(
                "handicap_draw_odds_high_band",
                "让平高赔观察区",
                0,
                0,
                5,
                "让平赔率偏高，只作观察，需更强盘口证据支撑",
                {"hhad_draw_odds": hhad_draw_odds},
            )

    if favorite_side == "draw":
        suggested_focus = ["平局"]
    else:
        suggested_focus = []
        if underdog_upset >= 65:
            suggested_focus.append("平局")
        if handicap_draw_value >= 65:
            suggested_focus.append("让平")
        if not suggested_focus:
            suggested_focus.append("观察")

    return {
        "version": ODDS_BAND_MODEL_VERSION,
        "available": True,
        "favorite": {
            "side": favorite_side,
            "team": _favorite_team_name(match, favorite_side),
            "initial_odds": favorite_initial,
            "current_odds": favorite_current,
            "band": favorite_band,
            "band_label": favorite_band_label,
        },
        "draw_odds": {
            "initial": draw_initial,
            "current": draw_current,
            "band": draw_band,
        },
        "handicap_draw_odds": hhad_draw_odds,
        "asian_depth": {
            "initial": initial_depth,
            "current": current_depth,
        },
        "indexes": {
            "favorite_heat": _clamp_index(favorite_heat),
            "underdog_upset": _clamp_index(underdog_upset),
            "handicap_draw_value": _clamp_index(handicap_draw_value),
        },
        "levels": {
            "favorite_heat": _index_level(favorite_heat),
            "underdog_upset": _index_level(underdog_upset),
            "handicap_draw_value": _index_level(handicap_draw_value),
        },
        "signals": signals[:10],
        "suggested_focus": list(dict.fromkeys(suggested_focus))[:3],
        "governance": (
            "赔率区间指标只用于识别危险赔率带、热门过热和让平价值；"
            "不能单独覆盖五市场结论，必须结合亚盘、水位、竞彩让球和数据质量。"
        ),
    }


def _build_low_odds_asian_handicap_model(
    match: Dict[str, Any],
    sporttery_handicap: Optional[float],
    current_asian_risk: Dict[str, Any],
) -> Dict[str, Any]:
    """Calibrate low-priced favorites with the observed Asian line structure.

    The model is deliberately small and finite.  It translates six months of
    settled matches into percentage-point nudges for the three Sporttery
    handicap outcomes instead of treating a short 1X2 price as an automatic
    handicap winner.
    """
    euro_current = [
        _number(match.get("euro_current_win")),
        _number(match.get("euro_current_draw")),
        _number(match.get("euro_current_lose")),
    ]
    unavailable = {
        "version": LOW_ODDS_ASIAN_MODEL_VERSION,
        "available": False,
        "matched": False,
        "adjustment_pp": {"让胜": 0.0, "让平": 0.0, "让负": 0.0},
        "centric_adjustment_pp": {
            "cover": 0.0, "exact": 0.0, "fail": 0.0,
        },
        "signals": [],
    }
    if any(value is None or value <= 1 for value in euro_current):
        return {**unavailable, "message": "欧赔数据不足"}

    favorite_index = min((0, 2), key=lambda index: euro_current[index])
    favorite_side = "home" if favorite_index == 0 else "away"
    favorite_odds = euro_current[favorite_index]
    if favorite_odds is None or favorite_odds >= 1.50:
        return {
            **unavailable,
            "message": "最低胜赔不低于1.50，不进入低赔率热门样本层",
            "favorite": {
                "side": favorite_side,
                "team": _favorite_team_name(match, favorite_side),
                "odds": favorite_odds,
            },
        }
    if sporttery_handicap in (None, 0):
        return {**unavailable, "message": "竞彩让球数缺失或为0"}
    aligned = (
        (favorite_side == "home" and sporttery_handicap < 0)
        or (favorite_side == "away" and sporttery_handicap > 0)
    )
    if not aligned:
        return {
            **unavailable,
            "message": "欧赔热门方与竞彩让球方不一致",
            "favorite": {
                "side": favorite_side,
                "team": _favorite_team_name(match, favorite_side),
                "odds": favorite_odds,
            },
        }

    initial_line = _handicap_value_from_text(
        match.get("asian_initial_handicap")
    )
    current_line = _handicap_value_from_text(
        match.get("asian_current_handicap")
    )
    if current_line is None:
        return {**unavailable, "message": "亚洲盘口深度缺失"}
    current_depth = (
        current_line if favorite_side == "home" else -current_line
    )
    initial_depth = (
        initial_line if favorite_side == "home" else -initial_line
    ) if initial_line is not None else None
    if current_depth <= 0:
        return {**unavailable, "message": "亚洲盘口与欧赔热门方不一致"}

    favorite_water_field = (
        "asian_current_home_odds"
        if favorite_side == "home" else "asian_current_away_odds"
    )
    initial_favorite_water_field = (
        "asian_initial_home_odds"
        if favorite_side == "home" else "asian_initial_away_odds"
    )
    favorite_water = _number(match.get(favorite_water_field))
    initial_favorite_water = _number(match.get(initial_favorite_water_field))
    line_change = (
        round(current_depth - initial_depth, 3)
        if initial_depth is not None else None
    )
    water_change = (
        round(favorite_water - initial_favorite_water, 3)
        if favorite_water is not None and initial_favorite_water is not None
        else None
    )
    official_depth = abs(float(sporttery_handicap))
    depth_gap = round(current_depth - official_depth, 3)
    adjustments = {"cover": 0.0, "exact": 0.0, "fail": 0.0}
    signals: List[Dict[str, Any]] = []

    def add_signal(
        key: str,
        deltas: Dict[str, float],
        reason: str,
        sample: int,
        observed: Dict[str, float],
    ) -> None:
        for outcome, delta in deltas.items():
            adjustments[outcome] += float(delta)
        signals.append({
            "key": key,
            "adjustment_pp": {
                outcome: round(float(deltas.get(outcome, 0)), 2)
                for outcome in ("cover", "exact", "fail")
            },
            "sample": sample,
            "observed_rate": observed,
            "reason": reason,
        })

    if official_depth == 1:
        if current_depth <= 0.875:
            add_signal(
                "official1_asian075_exact",
                {"exact": 3.0, "cover": -1.0},
                "竞彩让1球而亚盘约半一，历史中恰好赢1球占比33.96%",
                53,
                {"cover": 35.85, "exact": 33.96, "fail": 30.19},
            )
        elif current_depth <= 1.125:
            add_signal(
                "official1_asian100_neutral",
                {"exact": 1.0},
                "竞彩与亚盘同为1球，历史恰好赢1球略高于全样本",
                103,
                {"cover": 34.95, "exact": 28.16, "fail": 36.89},
            )
        elif current_depth < 1.50:
            add_signal(
                "official1_asian125_exact",
                {"exact": 2.5, "cover": -1.0},
                "竞彩让1球而亚盘约球半前档，精确1球仍有历史支撑",
                104,
                {"cover": 34.62, "exact": 31.73, "fail": 33.65},
            )
        else:
            add_signal(
                "official1_asian150_cover",
                {"cover": 4.0, "exact": -4.0},
                "竞彩只让1球但亚盘至少球半，历史更偏穿盘而非让平",
                47,
                {"cover": 55.32, "exact": 17.02, "fail": 27.66},
            )
    elif official_depth >= 2:
        if current_depth <= 1.50:
            add_signal(
                "official2_asian150_fail",
                {"fail": 4.0, "cover": -3.0},
                "竞彩让2球但亚盘不深于球半，历史热门不穿比例51.61%",
                31,
                {"cover": 19.35, "exact": 29.03, "fail": 51.61},
            )
        elif current_depth >= 1.75:
            add_signal(
                "official2_asian175_cover",
                {"cover": 3.0, "exact": -3.0},
                "竞彩让2球且亚盘至少球半/两球，历史穿盘比例提高",
                74,
                {"cover": 47.30, "exact": 18.92, "fail": 33.78},
            )

    if depth_gap <= -0.50:
        add_signal(
            "asian_much_shallower_than_official",
            {"fail": 4.0, "cover": -3.0},
            "亚盘比竞彩让球浅至少半球，历史热门不穿比例46.15%",
            52,
            {"cover": 28.85, "exact": 25.00, "fail": 46.15},
        )
    elif depth_gap >= 0.50:
        add_signal(
            "asian_much_deeper_than_official",
            {"cover": 4.0, "exact": -3.0},
            "亚盘比竞彩让球深至少半球，历史穿盘比例50.77%",
            65,
            {"cover": 50.77, "exact": 18.46, "fail": 30.77},
        )

    if favorite_water is not None:
        if 0.76 <= favorite_water <= 0.85:
            add_signal(
                "favorite_low_water_not_strength",
                {"fail": 2.0, "cover": -2.0},
                "热门0.76-0.85低水并非强穿信号，历史不穿比例42.64%",
                129,
                {"cover": 29.46, "exact": 27.91, "fail": 42.64},
            )
        elif 0.86 <= favorite_water <= 0.95:
            add_signal(
                "favorite_normal_low_water_cover",
                {"cover": 2.0, "fail": -1.0},
                "热门0.86-0.95水位历史穿盘比例47.47%",
                158,
                {"cover": 47.47, "exact": 22.78, "fail": 29.75},
            )
        elif 0.96 <= favorite_water <= 1.05:
            add_signal(
                "favorite_middle_water_exact",
                {"exact": 1.0},
                "热门0.96-1.05中水下精确赢盘边界略有增加",
                160,
                {"cover": 36.88, "exact": 28.12, "fail": 35.00},
            )

    if line_change is not None:
        if line_change >= 0.24:
            add_signal(
                "asian_line_deepen",
                {"cover": 3.0, "exact": -2.0},
                "近期不可变赛前样本中升盘后穿盘比例56.41%",
                39,
                {"cover": 56.41, "exact": 17.95, "fail": 25.64},
            )
        elif line_change <= -0.24:
            add_signal(
                "asian_line_retreat",
                {"fail": 4.0, "cover": -3.0},
                "近期不可变赛前样本中退盘后不穿比例54.55%",
                11,
                {"cover": 18.18, "exact": 27.27, "fail": 54.55},
            )
        elif water_change is not None and water_change >= 0.05:
            add_signal(
                "asian_stable_water_rise",
                {"exact": 2.0},
                "亚盘稳定但热门升水，近期样本精确赢球差比例35.29%",
                17,
                {"cover": 41.18, "exact": 35.29, "fail": 23.53},
            )
        elif water_change is not None and water_change <= -0.05:
            add_signal(
                "asian_stable_water_drop",
                {"fail": 2.0, "cover": -1.0},
                "亚盘不升而热门降水，近期样本未形成稳定穿盘优势",
                34,
                {"cover": 35.29, "exact": 20.59, "fail": 44.12},
            )

    adjustments = {
        key: round(max(-5.0, min(5.0, value)), 2)
        for key, value in adjustments.items()
    }
    raw_adjustments = (
        {
            "让胜": adjustments["cover"],
            "让平": adjustments["exact"],
            "让负": adjustments["fail"],
        }
        if favorite_side == "home" else {
            "让胜": adjustments["fail"],
            "让平": adjustments["exact"],
            "让负": adjustments["cover"],
        }
    )
    strongest = max(
        ("cover", "exact", "fail"),
        key=lambda key: adjustments[key],
    )
    return {
        "version": LOW_ODDS_ASIAN_MODEL_VERSION,
        "available": True,
        "matched": bool(signals),
        "favorite": {
            "side": favorite_side,
            "team": _favorite_team_name(match, favorite_side),
            "odds": favorite_odds,
        },
        "official_handicap": sporttery_handicap,
        "asian": {
            "initial_depth": initial_depth,
            "current_depth": current_depth,
            "depth_gap_from_official": depth_gap,
            "line_change": line_change,
            "initial_favorite_water": initial_favorite_water,
            "current_favorite_water": favorite_water,
            "favorite_water_change": water_change,
        },
        "centric_adjustment_pp": adjustments,
        "adjustment_pp": raw_adjustments,
        "favored_outcome": strongest,
        "signals": signals[:8],
        "sample_basis": {
            "six_month_initial_market": 451,
            "recent_immutable_movement": 108,
        },
        "governance": (
            "只对最低胜赔低于1.50且欧赔热门与竞彩让球方一致的比赛，"
            "按亚盘深度、深度差、水位和走势作最多±5个百分点的有限校准；"
            "cover/exact/fail分别表示热门穿盘、恰好走到让球边界、热门不穿。"
        ),
    }


def _odds_band_match_from_input(source: Dict[str, Any]) -> Dict[str, Any]:
    euro = source.get("euro") or {}
    asian = source.get("asian") or {}
    hhad = source.get("sporttery_handicap") or {}
    euro_initial = euro.get("initial") or []
    euro_current = euro.get("current") or []
    asian_initial = asian.get("initial") or []
    asian_current = asian.get("current") or []
    hhad_initial = hhad.get("initial") or []
    hhad_current = hhad.get("current") or []

    def at(values: List[Any], index: int) -> Any:
        return values[index] if len(values) > index else None

    return {
        "league": source.get("league"),
        "home_team": source.get("home_team"),
        "away_team": source.get("away_team"),
        "euro_initial_win": at(euro_initial, 0),
        "euro_initial_draw": at(euro_initial, 1),
        "euro_initial_lose": at(euro_initial, 2),
        "euro_current_win": at(euro_current, 0),
        "euro_current_draw": at(euro_current, 1),
        "euro_current_lose": at(euro_current, 2),
        "asian_initial_home_odds": at(asian_initial, 0),
        "asian_initial_handicap": at(asian_initial, 1),
        "asian_initial_away_odds": at(asian_initial, 2),
        "asian_current_home_odds": at(asian_current, 0),
        "asian_current_handicap": at(asian_current, 1),
        "asian_current_away_odds": at(asian_current, 2),
        "hi_handicap_value": hhad.get("value"),
        "hi_initial_home_odds": at(hhad_initial, 0),
        "hi_initial_draw_odds": at(hhad_initial, 1),
        "hi_initial_away_odds": at(hhad_initial, 2),
        "hi_current_home_odds": at(hhad_current, 0),
        "hi_current_draw_odds": at(hhad_current, 1),
        "hi_current_away_odds": at(hhad_current, 2),
    }


def _score_pair(value: Any) -> Optional[tuple[int, int]]:
    parsed = re.search(r"(\d{1,2})\s*[:\-]\s*(\d{1,2})", str(value or ""))
    if not parsed:
        return None
    return int(parsed.group(1)), int(parsed.group(2))


def _team_recent_stats(rows: Iterable[Dict[str, Any]], team: Any) -> Dict[str, Any]:
    team_text = str(team or "").strip()
    total = 0
    scored = 0
    wins = 0
    narrow_wins = 0
    cover_losses = 0
    goals_for = 0
    for row in rows or []:
        score = _score_pair((row or {}).get("score"))
        if not score:
            continue
        home_name = str((row or {}).get("home_team") or "")
        away_name = str((row or {}).get("away_team") or "")
        if team_text and team_text in home_name:
            team_goals, opponent_goals = score
        elif team_text and team_text in away_name:
            opponent_goals, team_goals = score
        else:
            # 500 的近期战绩表通常已按当前球队分侧展示；若无法
            # 识别主客，保守使用左侧比分作为该队代理。
            team_goals, opponent_goals = score
        total += 1
        goals_for += team_goals
        if team_goals > 0:
            scored += 1
        if team_goals > opponent_goals:
            wins += 1
            if team_goals - opponent_goals <= 1:
                narrow_wins += 1
        handicap_result = str((row or {}).get("handicap_result") or "")
        if "输" in handicap_result:
            cover_losses += 1
    return {
        "sample": total,
        "scored": scored,
        "scored_rate": round(scored / total * 100, 1) if total else None,
        "wins": wins,
        "narrow_wins": narrow_wins,
        "cover_losses": cover_losses,
        "avg_goals_for": round(goals_for / total, 2) if total else None,
    }


def _build_upset_warning_model(
    match: Dict[str, Any],
    sporttery_handicap: Optional[float],
    current_asian_risk: Dict[str, Any],
    fundamentals: Dict[str, Any],
    odds_band_model: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    euro_initial = [
        _number(match.get("euro_initial_win")),
        _number(match.get("euro_initial_draw")),
        _number(match.get("euro_initial_lose")),
    ]
    euro_current = [
        _number(match.get("euro_current_win")),
        _number(match.get("euro_current_draw")),
        _number(match.get("euro_current_lose")),
    ]
    if any(value is None or value <= 1 for value in euro_current):
        return {
            "version": UPSET_WARNING_MODEL_VERSION,
            "score": 0,
            "level": "数据不足",
            "favorite_side": None,
            "factors": [],
            "suggested_defenses": [],
            "message": "欧赔数据不足，无法计算爆冷预警",
        }
    favorite_index = min(range(3), key=lambda index: euro_current[index])
    favorite_side = ("home", "draw", "away")[favorite_index]
    if favorite_side == "draw":
        return {
            "version": UPSET_WARNING_MODEL_VERSION,
            "score": 0,
            "level": "无明确热门",
            "favorite_side": favorite_side,
            "factors": [],
            "suggested_defenses": ["平局"],
            "message": "当前欧赔无明确胜负热门",
        }

    factors = []

    def add(
        key: str,
        label: str,
        points: int,
        reason: str,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> None:
        factors.append({
            "key": key,
            "label": label,
            "points": points,
            "reason": reason,
            "evidence": evidence or {},
        })

    risk_ids = {
        str(value)
        for value in current_asian_risk.get("pattern_ids") or []
    }
    current_line = _handicap_value_from_text(match.get("asian_current_handicap"))
    initial_line = _handicap_value_from_text(match.get("asian_initial_handicap"))
    current_depth = None
    initial_depth = None
    if current_line is not None:
        current_depth = current_line if favorite_side == "home" else -current_line
    if initial_line is not None:
        initial_depth = initial_line if favorite_side == "home" else -initial_line
    favorite_current_odds = euro_current[favorite_index]
    favorite_initial_odds = (
        euro_initial[favorite_index]
        if len(euro_initial) > favorite_index else None
    )
    shallow_strong_favorite = (
        favorite_current_odds is not None
        and favorite_current_odds <= 1.75
        and current_depth is not None
        and current_depth <= 0.50
    )
    handicap_downgrade = (
        "handicap_retreat" in risk_ids
        or "euro_asian_divergence" in risk_ids
        or "deepen_high_water" in risk_ids
        or "upper_water_rise" in risk_ids
        or "water_drop_without_deepen" in risk_ids
        or "overheated_shallow" in risk_ids
        or (
            current_depth is not None
            and initial_depth is not None
            and current_depth < initial_depth - 0.20
        )
        or shallow_strong_favorite
    )
    if handicap_downgrade:
        add(
            "handicap_downgrade",
            "盘口降级/偏浅",
            25,
            "热门方实力或赔率占优，但盘口深度不给足支持",
            {
                "initial_depth": initial_depth,
                "current_depth": current_depth,
                "risk_ids": sorted(risk_ids),
            },
        )

    if (
        favorite_initial_odds is not None
        and favorite_current_odds is not None
        and favorite_current_odds - favorite_initial_odds >= 0.06
    ):
        add(
            "favorite_odds_rise",
            "热门胜赔升",
            20,
            "热门方胜赔抬升，市场对热门打出信心下降",
            {
                "initial": favorite_initial_odds,
                "current": favorite_current_odds,
                "change": round(favorite_current_odds - favorite_initial_odds, 3),
            },
        )

    odds_indexes = (odds_band_model or {}).get("indexes") or {}
    odds_favorite = (odds_band_model or {}).get("favorite") or {}
    odds_signals = [
        item for item in (odds_band_model or {}).get("signals") or []
        if isinstance(item, dict)
    ]
    favorite_band = str(odds_favorite.get("band") or "")
    if favorite_band == "danger":
        add(
            "favorite_odds_danger_band",
            "赔率危险区",
            25,
            "热门胜赔处在1.40-1.70危险区，看似稳但容易走下盘或让平",
            {
                "favorite_odds": odds_favorite.get("current_odds"),
                "band": odds_favorite.get("band_label"),
            },
        )
    elif favorite_band == "danger_edge":
        add(
            "favorite_odds_danger_edge",
            "赔率危险边缘",
            16,
            "热门胜赔处在1.71-1.79边缘区，需要结合盘口升水和平赔判断",
            {
                "favorite_odds": odds_favorite.get("current_odds"),
                "band": odds_favorite.get("band_label"),
            },
        )
    elif favorite_band == "balanced":
        add(
            "favorite_odds_balanced_band",
            "均势赔率区",
            10,
            "胜赔处在1.80-2.20均势区，普通平局权重应高于追热门",
            {
                "favorite_odds": odds_favorite.get("current_odds"),
                "band": odds_favorite.get("band_label"),
            },
        )
    if any(item.get("key") == "away_favorite_trap_band" for item in odds_signals):
        add(
            "away_favorite_trap_band",
            "强队客场陷阱",
            14,
            "客场热门处在1.70-2.20赔率区间，强队客场不胜风险上升",
            {
                "favorite_odds": odds_favorite.get("current_odds"),
            },
        )
    if any(item.get("key") == "handicap_too_deep_band" for item in odds_signals):
        add(
            "handicap_too_deep",
            "盘口过深",
            20,
            "盘口深度高于赔率碾压程度，优先防赢球输盘或下盘",
            {
                "favorite_odds": odds_favorite.get("current_odds"),
                "underdog_upset_index": odds_indexes.get("underdog_upset"),
            },
        )

    initial_draw = euro_initial[1] if len(euro_initial) > 1 else None
    current_draw = euro_current[1] if len(euro_current) > 1 else None
    if (
        initial_draw is not None
        and current_draw is not None
        and initial_draw - current_draw >= 0.10
    ):
        add(
            "draw_odds_drop",
            "平赔下降",
            20,
            "平赔明显压低，爆冷更可能先走向平局",
            {
                "initial": initial_draw,
                "current": current_draw,
                "change": round(current_draw - initial_draw, 3),
            },
        )

    protected_selection = None
    cover_selection = None
    cover_index = None
    if sporttery_handicap is not None:
        if favorite_side == "home" and sporttery_handicap < 0:
            cover_selection, cover_index = "让胜", 0
            protected_selection = "让负"
        elif favorite_side == "away" and sporttery_handicap > 0:
            cover_selection, cover_index = "让负", 2
            protected_selection = "让胜"
    hhad_odds = (
        [
            _number(match.get("hi_current_home_odds")),
            _number(match.get("hi_current_draw_odds")),
            _number(match.get("hi_current_away_odds")),
        ]
    )
    if cover_selection and cover_index is not None:
        cover_odds = hhad_odds[cover_index]
        valid_hhad_odds = [value for value in hhad_odds if value is not None]
        lowest = min(valid_hhad_odds) if valid_hhad_odds else None
        if (
            cover_odds is not None
            and (
                cover_odds >= 2.30
                or (lowest is not None and cover_odds > lowest + 0.40)
            )
        ):
            add(
                "favorite_cover_odds_high",
                "热门穿盘赔率偏高",
                15,
                "竞彩让球不支持热门方轻松穿盘",
                {
                    "cover_selection": cover_selection,
                    "cover_odds": cover_odds,
                    "lowest_hhad_odds": lowest,
                    "protected_selection": protected_selection,
                },
            )

    recent = fundamentals.get("recent") or {}
    favorite_team = (
        match.get("home_team") if favorite_side == "home"
        else match.get("away_team")
    )
    underdog_team = (
        match.get("away_team") if favorite_side == "home"
        else match.get("home_team")
    )
    favorite_recent = recent.get("home" if favorite_side == "home" else "away") or []
    underdog_recent = recent.get("away" if favorite_side == "home" else "home") or []
    favorite_stats = _team_recent_stats(favorite_recent, favorite_team)
    underdog_stats = _team_recent_stats(underdog_recent, underdog_team)
    if (
        (favorite_stats.get("cover_losses") or 0) >= 3
        or (
            (favorite_stats.get("wins") or 0) >= 3
            and (favorite_stats.get("narrow_wins") or 0) >= 2
        )
    ):
        add(
            "favorite_cover_weak_recent",
            "强队赢盘率低",
            10,
            "热门近期存在赢球但难穿盘或盘口结果偏弱的迹象",
            favorite_stats,
        )
    if (
        (underdog_stats.get("sample") or 0) >= 4
        and (
            (underdog_stats.get("scored_rate") or 0) >= 70
            or (underdog_stats.get("avg_goals_for") or 0) >= 1.1
        )
    ):
        add(
            "underdog_scoring_recent",
            "弱队近期有球",
            10,
            "弱队近期进球能力不弱，热门穿盘难度上升",
            underdog_stats,
        )

    score = min(100, sum(int(item.get("points") or 0) for item in factors))
    if score >= 80:
        level = "重点防冷"
    elif score >= 60:
        level = "防冷观察"
    elif score >= 40:
        level = "轻微预警"
    else:
        level = "正常"
    suggested_defenses = []
    if protected_selection:
        suggested_defenses.append(protected_selection)
    if any(item["key"] == "draw_odds_drop" for item in factors):
        suggested_defenses.append("平局")
    if sporttery_handicap is not None and abs(sporttery_handicap) == 1:
        suggested_defenses.append("让平")
    if not suggested_defenses:
        suggested_defenses.append("平局")
    return {
        "version": UPSET_WARNING_MODEL_VERSION,
        "score": score,
        "level": level,
        "favorite_side": favorite_side,
        "favorite_team": favorite_team,
        "favorite_odds": favorite_current_odds,
        "factors": factors,
        "suggested_defenses": list(dict.fromkeys(suggested_defenses))[:3],
        "governance": (
            "爆冷预警只用于热门降级和防选提示；不得单独反买冷门，"
            "必须结合赔率价值、盘口一致性和数据质量。"
        ),
    }


def _finished_fixtures(value: Any, limit: int = 6) -> List[Dict[str, Any]]:
    rows = []
    for item in value if isinstance(value, list) else []:
        score = str((item or {}).get("score") or "")
        if not re.fullmatch(r"\d+\s*[:\-]\s*\d+", score):
            continue
        rows.append(dict(item))
        if len(rows) >= limit:
            break
    return rows


def _compact_lineup_side(value: Any) -> Dict[str, Any]:
    side = value if isinstance(value, dict) else {}

    def players(key: str) -> List[Dict[str, Any]]:
        result = []
        for item in side.get(key) or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            result.append({
                "number": item.get("number"),
                "name": item.get("name"),
                "position": item.get("position"),
            })
        return result

    return {
        "team": side.get("team"),
        "formation": side.get("formation"),
        "starters": players("starters")[:11],
        "substitutes": players("substitutes")[:12],
    }


def _fundamentals_snapshot(
    match: Dict[str, Any],
    source_analysis: Optional[Dict[str, Any]],
) -> tuple[Dict[str, Any], List[str]]:
    source = source_analysis or {}
    recent = source.get("recent") or {}
    home_recent = _finished_fixtures(recent.get("home"))
    away_recent = _finished_fixtures(recent.get("away"))
    history = _finished_fixtures(source.get("history"))
    future = source.get("future") or {}
    schedules = {
        side: [
            dict(item) for item in (future.get(side) or [])[:4]
            if isinstance(item, dict)
        ]
        for side in ("home", "away")
    }
    team_rankings = source.get("team_rankings") or {}
    standings = [
        dict(item) for item in (source.get("standings") or [])[:24]
        if isinstance(item, dict)
    ]
    lineups = source.get("lineups") or {}
    injuries = source.get("injuries") or {}
    compact_lineups = {}
    if isinstance(lineups, dict) and (
        lineups.get("home") or lineups.get("away")
    ):
        compact_lineups = {
            "status": lineups.get("status"),
            "label": lineups.get("label"),
            "home": _compact_lineup_side(lineups.get("home")),
            "away": _compact_lineup_side(lineups.get("away")),
        }

    has_rankings = bool(
        team_rankings
        or standings
        or match.get("home_rank")
        or match.get("away_rank")
    )
    availability = {
        "近期状态": bool(home_recent and away_recent),
        "历史交锋": bool(history),
        "积分排名": has_rankings,
        "未来赛程": bool(schedules["home"] or schedules["away"]),
        "伤停/停赛": bool(injuries),
        "预计阵容": bool(compact_lineups),
    }
    missing = [label for label, available in availability.items() if not available]
    return {
        "source": source.get("source"),
        "source_url": source.get("source_url"),
        "cache_status": source.get("cache_status") or "fresh",
        "teams": source.get("teams") or [],
        "recent": {"home": home_recent, "away": away_recent},
        "history": history,
        "team_rankings": team_rankings,
        "standings": standings,
        "future": schedules,
        "injuries": injuries,
        "lineups": compact_lineups,
        "availability": availability,
        "note": (
            "lineups为500预计阵容，不是官方确认首发"
            if compact_lineups.get("status") == "predicted" else ""
        ),
    }, missing


def build_daily_match_input(
    match: Dict[str, Any],
    fae_result: Optional[Dict[str, Any]] = None,
    league_profile: Optional[Dict[str, Any]] = None,
    goal_margin_model: Optional[Dict[str, Any]] = None,
    source_analysis: Optional[Dict[str, Any]] = None,
    draw_selection_policy: Optional[str] = None,
    special_market_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a compact, auditable input for the daily Ark request."""
    analysis = (fae_result or {}).get("analysis") or {}
    core = (fae_result or {}).get("core") or {}
    initial_total = _total_line_number(match.get("ou_initial_total"))
    current_total = _total_line_number(match.get("ou_current_total"))
    handicap_source = (
        match.get("hi_handicap_value")
        if match.get("hi_handicap_value") not in (None, "")
        else match.get("handicap")
    )
    sporttery_handicap = _number(handicap_source)
    initial_asian = _clean_handicap(match.get("asian_initial_handicap"))
    current_asian = _clean_handicap(match.get("asian_current_handicap"))
    fundamentals, missing_fundamentals = _fundamentals_snapshot(
        match, source_analysis
    )
    source_rankings = fundamentals.get("team_rankings") or {}

    def available_rank(side: str) -> Any:
        direct = match.get(f"{side}_rank")
        if direct not in (None, ""):
            return direct
        ranking = source_rankings.get(side) or {}
        for record in ranking.get("records") or []:
            if record.get("scope") == "总成绩" and record.get("rank"):
                return record.get("rank")
        return ranking.get("league_rank")

    warnings: List[str] = []
    if (
        initial_total is not None
        and current_total is not None
        and abs(current_total - initial_total) >= 0.75
    ):
        warnings.append(
            f"大小球盘口从{initial_total:g}跳至{current_total:g}，需核验采集或盘口切换"
        )
    if not all(
        match.get(field) not in (None, "")
        for field in (
            "euro_current_win", "euro_current_draw", "euro_current_lose"
        )
    ):
        warnings.append("欧赔数据不完整")
    if not current_asian:
        warnings.append("亚洲盘口数据缺失")
    if sporttery_handicap is None:
        warnings.append("竞彩让球数缺失")
    warnings.extend(
        str(item) for item in (
            ((core.get("data_quality") or {}).get("issues") or [])
        )[:8]
        if item
    )
    if missing_fundamentals:
        warnings.append(
            "缺少基本面：" + "、".join(missing_fundamentals)
        )
    if fundamentals.get("cache_status") == "stale":
        warnings.append("500基本面刷新失败，当前使用过期缓存并已降权")
    rank_data = {
        "home": available_rank("home"),
        "away": available_rank("away"),
    }
    probabilities = analysis.get("probabilities") or {}
    current_asian_risk = classify_asian_risk_patterns(match)
    league_tactical_model = _build_league_tactical_model(
        match,
        probabilities,
        sporttery_handicap,
        current_total,
        current_asian_risk,
        rank_data,
    )
    odds_band_model = _build_odds_band_model(
        match,
        sporttery_handicap,
        current_asian_risk,
    )
    low_odds_asian_model = _build_low_odds_asian_handicap_model(
        match,
        sporttery_handicap,
        current_asian_risk,
    )
    upset_warning_model = _build_upset_warning_model(
        match,
        sporttery_handicap,
        current_asian_risk,
        fundamentals,
        odds_band_model,
    )
    payload = {
        "match_id": str(match.get("match_id") or ""),
        "match_number": match.get("match_number") or match.get("round_id"),
        "owner_date": str(match.get("owner_date") or "")[:10] or None,
        "draw_selection_policy": normalize_draw_selection_policy(
            draw_selection_policy
        ),
        "league": match.get("league"),
        "match_time": match.get("match_time"),
        "home_team": match.get("home_team"),
        "away_team": match.get("away_team"),
        "rank": rank_data,
        "euro": {
            "initial": [
                _number(match.get("euro_initial_win")),
                _number(match.get("euro_initial_draw")),
                _number(match.get("euro_initial_lose")),
            ],
            "current": [
                _number(match.get("euro_current_win")),
                _number(match.get("euro_current_draw")),
                _number(match.get("euro_current_lose")),
            ],
        },
        "asian": {
            "initial": [
                _number(match.get("asian_initial_home_odds")),
                initial_asian or None,
                _number(match.get("asian_initial_away_odds")),
            ],
            "current": [
                _number(match.get("asian_current_home_odds")),
                current_asian or None,
                _number(match.get("asian_current_away_odds")),
            ],
        },
        "sporttery_handicap": {
            "value": sporttery_handicap,
            "initial": [
                _number(match.get("hi_initial_home_odds")),
                _number(match.get("hi_initial_draw_odds")),
                _number(match.get("hi_initial_away_odds")),
            ],
            "current": [
                _number(match.get("hi_current_home_odds")),
                _number(match.get("hi_current_draw_odds")),
                _number(match.get("hi_current_away_odds")),
            ],
        },
        "total": {
            "initial": [
                _number(match.get("ou_initial_over_odds")),
                initial_total,
                _number(match.get("ou_initial_under_odds")),
            ],
            "current": [
                _number(match.get("ou_current_over_odds")),
                current_total,
                _number(match.get("ou_current_under_odds")),
            ],
        },
        "fae_core": {
            "overall_score": analysis.get("overall_score"),
            "recommendation": analysis.get("recommendation"),
            "probabilities": analysis.get("probabilities"),
            "probability_basis": analysis.get("probability_basis"),
            "risk": analysis.get("risk"),
            "score_candidates": analysis.get("score_candidates"),
            "market_types": analysis.get("market_types"),
            "rule_signals": core.get("rule_signals"),
            "historical_odds_rules": core.get("historical_odds_rules"),
        },
        "historical_odds_rules": core.get("historical_odds_rules") or {
            "version": "historical-market-rules-v2",
            "matched_rule_ids": [],
            "ordinary_draw": {
                "eligible_for_adjustment": False,
                "adjustment_pp": 0,
                "signals": [],
            },
            "handicap_draw": {
                "eligible_for_adjustment": False,
                "adjustment_pp": 0,
                "signals": [],
            },
            "favorite_risks": [],
        },
        "league_history_profile": league_profile or {
            "league": match.get("league"),
            "sample_size": 0,
            "confidence": "样本不足",
            "eligible_for_adjustment": False,
            "hidden_signals": ["暂无可用联赛历史画像"],
        },
        "historical_goal_margin_model": goal_margin_model or {
            "version": "goal-margin-similarity-v1",
            "ordinary_draw": {
                "eligible_for_adjustment": False,
                "confidence": "样本不足",
                "signal": "暂无可用相似历史样本",
            },
            "handicap_draw": {
                "eligible_for_adjustment": False,
                "confidence": "样本不足",
                "signal": "暂无可用相似历史样本",
            },
        },
        "league_tactical_model": league_tactical_model,
        "odds_band_model": odds_band_model,
        "low_odds_asian_model": low_odds_asian_model,
        "upset_warning_model": upset_warning_model,
        "current_asian_risk": current_asian_risk,
        "fundamentals": fundamentals,
        "data_warnings": list(dict.fromkeys(warnings)),
        "missing_fundamentals": missing_fundamentals,
    }
    payload["special_markets"] = build_special_market_analysis(
        special_market_snapshot,
        payload,
    )
    total_goals_model = payload["special_markets"].get("total_goals") or {}
    if (
        total_goals_model.get("calculator_available")
        and not total_goals_model.get("data_complete")
    ):
        payload["data_warnings"].append(
            "亚洲大小球即时盘口或两侧水位缺失，总进球模型不输出推荐"
        )
    if not (
        total_goals_model.get("available")
        or (payload["special_markets"].get("half_full") or {}).get("available")
    ):
        payload["data_warnings"].append("竞彩总进球/半全场赔率快照缺失")
    return payload


class FAEDailyAIAnalyzer:
    """Analyze all daily fixtures together, then split the response per match."""

    def __init__(self, client: Optional[ArkNarrativeClient] = None):
        self.client = client or ArkNarrativeClient()

    @property
    def configured(self) -> bool:
        return self.client.configured

    def input_hash(
        self,
        owner_date: str,
        match_inputs: Iterable[Dict[str, Any]],
        review_memory: Optional[Dict[str, Any]] = None,
        draw_selection_policy: Optional[str] = None,
    ) -> str:
        """Stable cache key for one day's exact market snapshot."""
        rows = sorted(
            [dict(item) for item in match_inputs if item.get("match_id")],
            key=lambda item: str(item.get("match_id") or ""),
        )
        policy = normalize_draw_selection_policy(draw_selection_policy)
        return sha256(json.dumps(
            {
                "date": str(owner_date)[:10],
                "prompt_version": DAILY_PROMPT_VERSION,
                "model": self.client.model,
                "draw_selection_policy": policy,
                "matches": rows,
                "review_memory": review_memory or {},
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")).hexdigest()

    @classmethod
    def merge_retained_matches(
        cls,
        analysis_run: Dict[str, Any],
        retained_matches: Iterable[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Keep immutable pre-game judgements when a same-day rerun is partial.

        A rerun only sends fixtures that are still pre-game to Ark. Fixtures that
        have already started must remain visible for audit and later review, but
        they must not be added to the newly generated recommendation pools.
        """
        result = dict(analysis_run or {})
        retained_sources = {
            str(item.get("match_id") or ""): dict(item)
            for item in (retained_matches or [])
            if item.get("match_id")
        }
        fresh = [
            dict(item) for item in (result.get("matches") or [])
            if (
                item.get("match_id")
                and str(item.get("match_id")) not in retained_sources
            )
        ]
        fresh_ids = {str(item.get("match_id")) for item in fresh}
        retained = []
        for source in retained_sources.values():
            match_id = str(source.get("match_id") or "")
            if not match_id or match_id in fresh_ids:
                continue
            item = dict(source)
            item["match_id"] = match_id
            item["retained_from_pregame"] = True
            item.setdefault("retained_from_run_id", item.get("run_id"))
            retained.append(item)

        combined = fresh + retained
        combined.sort(key=lambda item: (
            str(item.get("match_time") or ""),
            str(item.get("match_number") or ""),
        ))
        # Re-rank the complete retained + fresh slate so a one-match T-30 run
        # cannot make every previously analysed match look actionable.
        combined = cls.apply_two_option_recommendations(combined)
        combined = cls.apply_official_bet_recommendations(combined)
        combined = cls.apply_high_confidence_single_recommendations(
            combined
        )
        result["matches"] = combined
        result["analyzed_match_count"] = len(fresh)
        result["retained_match_count"] = len(retained)
        result["match_count"] = len(combined)

        if retained:
            summary = dict(result.get("daily_summary") or {})
            warnings = list(summary.get("warnings") or [])
            labels = [
                str(item.get("match_number") or item.get("match_id"))
                for item in retained
            ]
            warnings.append(
                "本轮仅重新研判临近开赛比赛；其他场次"
                + "、".join(labels)
                + "保留原赛前研判。"
            )
            summary["warnings"] = list(dict.fromkeys(warnings))[:20]
            result["daily_summary"] = summary
        return result

    def rebuild_incremental_summary(
        self,
        analysis_run: Dict[str, Any],
        previous_summaries: Iterable[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Rebuild the visible daily pools after a partial pre-match run.

        Only due fixtures are sent to Ark, while already analysed fixtures are
        copied into the new immutable run.  Rebuilding here keeps all analysed
        matches visible in the rankings instead of replacing the page with the
        latest one-match batch.
        """
        result = dict(analysis_run or {})
        matches = [
            dict(item) for item in (result.get("matches") or [])
            if item.get("match_id")
        ]
        summaries = [
            dict(item) for item in (previous_summaries or [])
            if isinstance(item, dict)
        ]
        current_summary = result.get("daily_summary")
        if isinstance(current_summary, dict):
            summaries.append(current_summary)
        summary = self._merge_summaries(summaries, matches)
        summary = self._apply_summary_guard(summary, matches)
        summary = self._apply_no_bet_summary(summary, matches)
        summary = self.attach_draw_radar_summary(summary, matches)
        summary = self.attach_supervised_shadow_summary(summary, matches)
        summary = self.attach_league_model_rankings(summary, matches)
        summary = self.attach_upset_warning_summary(summary, matches)
        summary = self.attach_odds_band_summary(summary, matches)
        summary["recommended_combinations"] = self._ensure_mixed_combinations(
            summary
        )
        summary = self.normalize_summary_pool_semantics(summary, matches)
        summary = self.align_summary_ratings(summary, matches)
        summary = self.promote_draw_radar_recommendations(summary, matches)
        summary["recommended_combinations"] = self._ensure_mixed_combinations(
            summary
        )
        summary = self.attach_draw_parlay_tickets(summary)
        summary = self.normalize_summary_memory_governance(
            summary, result.get("review_memory") or {}
        )
        summary = self._humanize_summary_match_ids(summary, matches)
        result["daily_summary"] = summary
        return result

    def analyze(
        self,
        owner_date: str,
        match_inputs: Iterable[Dict[str, Any]],
        batch_size: int = DAILY_AI_MAX_BATCH_SIZE,
        batch_cache_get: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
        batch_cache_save: Optional[Callable[[Dict[str, Any]], Any]] = None,
        review_memory: Optional[Dict[str, Any]] = None,
        draw_selection_policy: Optional[str] = None,
    ) -> Dict[str, Any]:
        rows = [dict(item) for item in match_inputs if item.get("match_id")]
        policy = normalize_draw_selection_policy(draw_selection_policy)
        for item in rows:
            item["draw_selection_policy"] = policy
        if not rows:
            raise FAEOutputError("当天没有可分析的比赛")
        if not self.configured:
            raise FAEOutputError("火山方舟尚未配置，无法运行全日分析")
        memory = dict(review_memory or {})
        size = max(
            1,
            min(
                DAILY_AI_MAX_BATCH_SIZE,
                int(batch_size or DAILY_AI_MAX_BATCH_SIZE),
            ),
        )
        outputs = []
        provider_batches = []
        successful_match_ids = set()
        failed_match_ids = set()
        primary_batch_count = (len(rows) + size - 1) // size

        def normalize_provider_output(
            parsed: Dict[str, Any], batch: List[Dict[str, Any]]
        ) -> Dict[str, Any]:
            value = parsed
            if len(batch) == 1:
                generated_match = (
                    value.get("match")
                    if isinstance(value.get("match"), dict)
                    else value
                )
                if generated_match.get("match_id"):
                    value = {
                        "daily_summary": {},
                        "matches": [generated_match],
                    }
            return value

        def returned_match_ids(
            parsed: Dict[str, Any], expected: Iterable[str]
        ) -> set[str]:
            expected_ids = {str(item) for item in expected}
            return {
                str(item.get("match_id"))
                for item in parsed.get("matches") or []
                if isinstance(item, dict)
                and str(item.get("match_id") or "") in expected_ids
            }

        for index in range(0, len(rows), size):
            batch = rows[index:index + size]
            batch_number = index // size + 1
            batch_match_ids = [
                str(item.get("match_id")) for item in batch
            ]
            prompt = (
                self._build_single_prompt(
                    owner_date,
                    batch[0],
                    batch_number,
                    review_memory=memory,
                )
                if len(batch) == 1
                else self._build_prompt(
                    owner_date,
                    batch,
                    batch_number,
                    review_memory=memory,
                )
            )
            batch_hash = self._request_hash("detail", prompt)
            cached = batch_cache_get(batch_hash) if batch_cache_get else None
            if cached and isinstance(cached.get("output"), dict):
                cached_output = normalize_provider_output(
                    cached["output"], batch
                )
                outputs.append(cached_output)
                returned_ids = returned_match_ids(
                    cached_output, batch_match_ids
                )
                provider_batches.append({
                    **(cached.get("provider_meta") or {}),
                    "status": (
                        "completed" if len(returned_ids) == len(batch)
                        else "partial"
                    ),
                    "cache_hit": True,
                    "batch_hash": batch_hash,
                    "batch_number": batch_number,
                    "match_count": len(batch),
                    "match_ids": batch_match_ids,
                    "returned_match_count": len(returned_ids),
                    "missing_match_ids": sorted(
                        set(batch_match_ids) - returned_ids
                    ),
                })
                successful_match_ids.update(returned_ids)
                continue
            try:
                text, metadata = self.client.generate(prompt)
                parsed = self._extract_json(text)
            except FAEError as exc:
                # A transient provider timeout must not discard completed
                # checkpoints from other groups.  The failed group falls back
                # to deterministic FAE output and is retried on a forced run.
                message = str(exc)[:300]
                outputs.append({
                    "daily_summary": {
                        "warnings": [
                            f"第{batch_number}批大模型研判失败，"
                            "本批暂用FAE核心结论，可稍后重新研判。"
                        ],
                    },
                    "matches": [],
                })
                failed_match_ids.update(batch_match_ids)
                provider_batches.append({
                    "status": "failed",
                    "cache_hit": False,
                    "batch_hash": batch_hash,
                    "batch_number": batch_number,
                    "match_count": len(batch),
                    "match_ids": batch_match_ids,
                    "error": message,
                })
                continue
            parsed = normalize_provider_output(parsed, batch)
            returned_ids = returned_match_ids(parsed, batch_match_ids)
            outputs.append(parsed)
            batch_metadata = {
                **metadata,
                "status": (
                    "completed" if len(returned_ids) == len(batch)
                    else "partial"
                ),
                "cache_hit": False,
                "batch_hash": batch_hash,
                "batch_number": batch_number,
                "match_count": len(batch),
                "match_ids": batch_match_ids,
                "returned_match_count": len(returned_ids),
                "missing_match_ids": sorted(
                    set(batch_match_ids) - returned_ids
                ),
            }
            provider_batches.append(batch_metadata)
            successful_match_ids.update(returned_ids)
            if batch_cache_save:
                batch_cache_save({
                    "batch_hash": batch_hash,
                    "owner_date": str(owner_date)[:10],
                    "kind": "detail",
                    "batch_number": batch_number,
                    "match_ids": batch_match_ids,
                    "model": self.client.model,
                    "prompt_version": DAILY_PROMPT_VERSION,
                    "review_memory_hash": memory.get("memory_hash"),
                    "output": parsed,
                    "provider_meta": metadata,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                })

        # Models occasionally omit one or more rows even though the provider
        # request itself succeeds.  Count only returned IDs as AI analysed and
        # retry all omissions in compact recovery batches.  This prevents a
        # silent deterministic fallback from being reported as Ark coverage.
        all_match_ids = {str(item.get("match_id")) for item in rows}
        missing_rows = [
            item for item in rows
            if str(item.get("match_id")) not in successful_match_ids
        ]
        recovery_size = min(size, DAILY_AI_RECOVERY_BATCH_SIZE)
        for index in range(0, len(missing_rows), recovery_size):
            batch = missing_rows[index:index + recovery_size]
            recovery_number = index // recovery_size + 1
            batch_match_ids = [
                str(item.get("match_id")) for item in batch
            ]
            prompt = (
                self._build_single_prompt(
                    owner_date,
                    batch[0],
                    recovery_number,
                    review_memory=memory,
                )
                if len(batch) == 1
                else self._build_prompt(
                    owner_date,
                    batch,
                    recovery_number,
                    review_memory=memory,
                )
            )
            recovery_instruction = (
                "# 漏项补全批次\n"
                "上次返回遗漏了比赛。本次必须且只能返回以下match_id，"
                "每场恰好一次：" + "、".join(batch_match_ids) + "\n\n"
            )
            input_marker = (
                "# 比赛输入\n" if len(batch) == 1
                else "# 当日比赛输入\n"
            )
            prompt = prompt.replace(
                input_marker,
                recovery_instruction + input_marker,
                1,
            )
            batch_hash = self._request_hash("detail-recovery", prompt)
            cached = batch_cache_get(batch_hash) if batch_cache_get else None
            metadata = {}
            try:
                cached_output = (
                    normalize_provider_output(cached["output"], batch)
                    if cached and isinstance(cached.get("output"), dict)
                    else None
                )
                cached_ids = (
                    returned_match_ids(cached_output, batch_match_ids)
                    if cached_output else set()
                )
                if cached_output and len(cached_ids) == len(batch):
                    parsed = cached_output
                    metadata = cached.get("provider_meta") or {}
                    cache_hit = True
                else:
                    text, metadata = self.client.generate(prompt)
                    parsed = self._extract_json(text)
                    cache_hit = False
                parsed = normalize_provider_output(parsed, batch)
            except FAEError as exc:
                provider_batches.append({
                    "kind": "detail-recovery",
                    "status": "failed",
                    "cache_hit": False,
                    "batch_hash": batch_hash,
                    "batch_number": recovery_number,
                    "match_count": len(batch),
                    "match_ids": batch_match_ids,
                    "error": str(exc)[:300],
                })
                continue
            outputs.append(parsed)
            returned_ids = returned_match_ids(parsed, batch_match_ids)
            successful_match_ids.update(returned_ids)
            provider_batches.append({
                **metadata,
                "kind": "detail-recovery",
                "status": (
                    "completed" if len(returned_ids) == len(batch)
                    else "partial"
                ),
                "cache_hit": cache_hit,
                "batch_hash": batch_hash,
                "batch_number": recovery_number,
                "match_count": len(batch),
                "match_ids": batch_match_ids,
                "returned_match_count": len(returned_ids),
                "missing_match_ids": sorted(
                    set(batch_match_ids) - returned_ids
                ),
            })
            if (
                not cache_hit
                and len(returned_ids) == len(batch)
                and batch_cache_save
            ):
                batch_cache_save({
                    "batch_hash": batch_hash,
                    "owner_date": str(owner_date)[:10],
                    "kind": "detail-recovery",
                    "batch_number": recovery_number,
                    "match_ids": batch_match_ids,
                    "model": self.client.model,
                    "prompt_version": DAILY_PROMPT_VERSION,
                    "review_memory_hash": memory.get("memory_hash"),
                    "output": parsed,
                    "provider_meta": metadata,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                })

        failed_match_ids = all_match_ids - successful_match_ids

        if not successful_match_ids:
            errors = [
                str(item.get("error") or "")
                for item in provider_batches
                if item.get("status") == "failed"
            ]
            detail = next((item for item in errors if item), "未知错误")
            raise FAEOutputError(
                f"全部{primary_batch_count}批大模型研判失败: {detail}"
            )

        normalized_matches = []
        summaries = []
        for source in outputs:
            summaries.append(source.get("daily_summary") or {})
            normalized_matches.extend(source.get("matches") or [])
        by_id = {
            str(item.get("match_id")): item
            for item in normalized_matches
            if isinstance(item, dict) and item.get("match_id")
        }
        stored_matches = [
            self._normalize_match(
                source,
                by_id.get(str(source.get("match_id"))) or {},
            )
            for source in rows
        ]
        stored_matches = self.calibrate_daily_matches(stored_matches)
        stored_matches = self.apply_draw_radar(stored_matches)
        stored_matches = self.apply_draw_radar_recommendation_overrides(
            stored_matches
        )
        stored_matches = self.apply_two_option_recommendations(stored_matches)
        stored_matches = self.apply_official_bet_recommendations(
            stored_matches
        )
        stored_matches = self.apply_high_confidence_single_recommendations(
            stored_matches
        )
        stored_matches = self.normalize_match_memory_governance(
            stored_matches, memory
        )
        synthesis_meta = None
        global_summary = None
        if primary_batch_count > 1:
            try:
                synthesis_prompt = self._build_synthesis_prompt(
                    owner_date,
                    stored_matches,
                    review_memory=memory,
                )
                synthesis_hash = self._request_hash(
                    "synthesis", synthesis_prompt
                )
                cached = (
                    batch_cache_get(synthesis_hash)
                    if batch_cache_get else None
                )
                if cached and isinstance(cached.get("output"), dict):
                    synthesized = cached["output"]
                    synthesis_meta = {
                        **(cached.get("provider_meta") or {}),
                        "cache_hit": True,
                        "batch_hash": synthesis_hash,
                    }
                else:
                    text, metadata = self.client.generate(synthesis_prompt)
                    synthesized = self._extract_json(text)
                    synthesis_meta = {
                        **metadata,
                        "cache_hit": False,
                        "batch_hash": synthesis_hash,
                    }
                    if batch_cache_save:
                        batch_cache_save({
                            "batch_hash": synthesis_hash,
                            "owner_date": str(owner_date)[:10],
                            "kind": "synthesis",
                            "batch_number": 0,
                            "match_ids": [
                                str(item.get("match_id"))
                                for item in stored_matches
                            ],
                            "model": self.client.model,
                            "prompt_version": DAILY_PROMPT_VERSION,
                            "review_memory_hash": memory.get("memory_hash"),
                            "output": synthesized,
                            "provider_meta": metadata,
                            "generated_at": (
                                datetime.now(timezone.utc).isoformat()
                            ),
                        })
                global_summary = (
                    synthesized.get("daily_summary")
                    if isinstance(synthesized.get("daily_summary"), dict)
                    else synthesized
                )
            except FAEError:
                # Detailed per-match results remain valid; batch summaries are
                # still merged when the optional cross-batch synthesis fails.
                synthesis_meta = {"status": "failed"}
        daily_summary = self._merge_summaries(summaries, stored_matches)
        if global_summary:
            synthesized_summary = self._merge_summaries(
                [global_summary], stored_matches
            )
            daily_summary["core_conclusion"] = (
                synthesized_summary.get("core_conclusion")
                or daily_summary.get("core_conclusion")
            )
            daily_summary["warnings"] = list(dict.fromkeys(
                (synthesized_summary.get("warnings") or [])
                + (daily_summary.get("warnings") or [])
            ))[:20]
            for key, items in (
                synthesized_summary.get("pools") or {}
            ).items():
                if items:
                    daily_summary["pools"][key] = items
            if synthesized_summary.get("recommended_combinations"):
                daily_summary["recommended_combinations"] = (
                    synthesized_summary["recommended_combinations"]
                )
        daily_summary = self._apply_summary_guard(
            daily_summary, stored_matches
        )
        daily_summary = self._apply_no_bet_summary(
            daily_summary, stored_matches
        )
        daily_summary = self.attach_draw_radar_summary(
            daily_summary, stored_matches
        )
        daily_summary = self.attach_supervised_shadow_summary(
            daily_summary, stored_matches
        )
        daily_summary = self.attach_league_model_rankings(
            daily_summary, stored_matches
        )
        daily_summary = self.attach_upset_warning_summary(
            daily_summary, stored_matches
        )
        daily_summary = self.attach_odds_band_summary(
            daily_summary, stored_matches
        )
        daily_summary["recommended_combinations"] = (
            self._ensure_mixed_combinations(daily_summary)
        )
        daily_summary = self.normalize_summary_pool_semantics(
            daily_summary, stored_matches
        )
        daily_summary = self.align_summary_ratings(
            daily_summary, stored_matches
        )
        daily_summary = self.promote_draw_radar_recommendations(
            daily_summary, stored_matches
        )
        daily_summary["recommended_combinations"] = (
            self._ensure_mixed_combinations(daily_summary)
        )
        daily_summary = self.attach_draw_parlay_tickets(daily_summary)
        daily_summary = self.normalize_summary_memory_governance(
            daily_summary, memory
        )
        daily_summary = self._humanize_summary_match_ids(
            daily_summary, stored_matches
        )
        input_hash = self.input_hash(
            owner_date,
            rows,
            review_memory=memory,
            draw_selection_policy=policy,
        )
        generated_at = datetime.now(timezone.utc).isoformat()
        run_id = f"{str(owner_date)[:10]}-{input_hash[:16]}"
        for item in stored_matches:
            item.update({
                "run_id": run_id,
                "owner_date": str(owner_date)[:10],
                "model": self.client.model,
                "provider": "volcengine-ark",
                "prompt_version": DAILY_PROMPT_VERSION,
                "generated_at": generated_at,
            })
        return {
            "run_id": run_id,
            "owner_date": str(owner_date)[:10],
            "draw_selection_policy": policy,
            "engine_version": ENGINE_VERSION,
            "model": self.client.model,
            "provider": "volcengine-ark",
            "prompt_version": DAILY_PROMPT_VERSION,
            "input_hash": input_hash,
            "generated_at": generated_at,
            "match_count": len(stored_matches),
            "batch_count": primary_batch_count,
            "completed_batch_count": sum(
                1 for item in provider_batches
                if (
                    item.get("kind") != "detail-recovery"
                    and item.get("status") == "completed"
                )
            ),
            "failed_batch_count": sum(
                1 for item in provider_batches
                if (
                    item.get("kind") != "detail-recovery"
                    and item.get("status") == "failed"
                )
            ),
            "partial_batch_count": sum(
                1 for item in provider_batches
                if (
                    item.get("kind") != "detail-recovery"
                    and item.get("status") == "partial"
                )
            ),
            "recovery_batch_count": sum(
                1 for item in provider_batches
                if item.get("kind") == "detail-recovery"
            ),
            "ai_analyzed_match_count": len(successful_match_ids),
            "fallback_match_count": len(failed_match_ids),
            "partial_success": bool(failed_match_ids),
            "daily_summary": daily_summary,
            "review_memory": memory,
            "matches": stored_matches,
            "provider_meta": {
                "batches": provider_batches,
                "synthesis": synthesis_meta,
            },
        }

    def _request_hash(self, kind: str, prompt: str) -> str:
        return sha256(json.dumps({
            "kind": kind,
            "model": self.client.model,
            "prompt_version": DAILY_PROMPT_VERSION,
            "prompt": prompt,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    @classmethod
    def _ensure_mixed_combinations(
        cls, daily_summary: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Build combinations only from independently credible pool entries."""
        pools = daily_summary.get("pools") or {}
        minimum_rating = OFFICIAL_MIN_RATING
        avoid_ids = {
            str(item.get("match_id") or "")
            for item in pools.get("avoid") or []
        }

        def ready_for_combo(item: Dict[str, Any]) -> bool:
            level = item.get("radar_official_level")
            if level and level != "core":
                return False
            return float(item.get("rating") or 0) >= minimum_rating

        radar = daily_summary.get("draw_radar") or {}
        radar_draw = [
            item for item in radar.get("ordinary_draw") or []
            if item.get("tier") == "core"
        ]
        radar_handicap_draw = [
            item for item in radar.get("handicap_draw") or []
            if item.get("tier") == "core"
        ]
        draw_source = pools.get("draw") or radar_draw
        handicap_draw_source = (
            pools.get("handicap_draw") or radar_handicap_draw
        )
        draw = [
            item for item in draw_source
            if (
                ready_for_combo(item)
                and str(item.get("match_id") or "") not in avoid_ids
            )
        ]
        handicap_draw = [
            item for item in handicap_draw_source
            if (
                ready_for_combo(item)
                and str(item.get("match_id") or "") not in avoid_ids
            )
        ]
        eligible = {
            "平局": {str(item.get("match_id") or "") for item in draw},
            "让平": {
                str(item.get("match_id") or "")
                for item in handicap_draw
            },
        }
        generated = []
        if draw and handicap_draw:
            let_pick = handicap_draw[0]
            draw_pick = next(
                (
                    item for item in draw
                    if item.get("match_id") != let_pick.get("match_id")
                ),
                None,
            )
            if draw_pick:
                generated.append({
                    "play": "2串1",
                    "picks": [
                        {
                            "match_id": let_pick["match_id"],
                            "selection": "让平",
                        },
                        {
                            "match_id": draw_pick["match_id"],
                            "selection": "平局",
                        },
                    ],
                    "reason": "从全日让平榜与平局榜各取一场，避免组合被单一玩法占满。",
                })
                third = next(
                    (
                        item for item in handicap_draw[1:]
                        if item.get("match_id") not in {
                            let_pick["match_id"], draw_pick["match_id"]
                        }
                    ),
                    None,
                )
                if third:
                    generated.append({
                        "play": "3串1",
                        "picks": [
                            {
                                "match_id": let_pick["match_id"],
                                "selection": "让平",
                            },
                            {
                                "match_id": draw_pick["match_id"],
                                "selection": "平局",
                            },
                            {
                                "match_id": third["match_id"],
                                "selection": "让平",
                            },
                        ],
                        "reason": "两场让平搭配一场普通平局，兼顾玩法分散与全日评分。",
                    })
        existing = daily_summary.get("recommended_combinations") or []
        result = generated[:]
        seen = {
            tuple(
                (pick.get("match_id"), pick.get("selection"))
                for pick in item.get("picks") or []
            )
            for item in result
        }
        for item in existing:
            picks = item.get("picks") or []
            valid_existing = (
                len(picks) in (2, 3)
                and len({str(pick.get("match_id") or "") for pick in picks})
                == len(picks)
                and all(
                    str(pick.get("match_id") or "")
                    in eligible.get(str(pick.get("selection") or ""), set())
                    for pick in picks
                )
            )
            if not valid_existing:
                continue
            key = tuple(
                (pick.get("match_id"), pick.get("selection"))
                for pick in picks
            )
            if key and key not in seen:
                seen.add(key)
                result.append(item)
        return result[:10]

    def _build_prompt(
        self,
        owner_date: str,
        matches: List[Dict[str, Any]],
        batch_number: int,
        review_memory: Optional[Dict[str, Any]] = None,
    ) -> str:
        schema = {
            "daily_summary": {
                "core_conclusion": "80到200字的当日总览",
                "warnings": ["数据缺失、跳盘或市场冲突"],
                "pools": {
                    "handicap_draw": [
                        {"match_id": "必须来自输入", "rating": 1, "reason": "一句话"}
                    ],
                    "draw": [
                        {"match_id": "必须来自输入", "rating": 1, "reason": "一句话"}
                    ],
                    "away_small_win": [
                        {"match_id": "必须是客胜方向", "reason": "客队预计净胜1球的理由"}
                    ],
                    "handicap_lose": [
                        {"match_id": "必须来自输入", "reason": "竞彩让负理由"}
                    ],
                    "avoid": [
                        {"match_id": "必须来自输入", "reason": "一句话"}
                    ],
                },
                "recommended_combinations": [
                    {
                        "play": "2串1或3串1",
                        "picks": [
                            {"match_id": "必须来自输入", "selection": "平局或让平"}
                        ],
                        "reason": "组合理由",
                    }
                ],
            },
            "matches": [{
                "match_id": "必须与输入完全一致",
                "direction": "主胜/平局/客胜/主队不败/客队不败/观望",
                "primary_play": "平局/让平/让胜/让负/主胜/客胜/观望",
                "secondary_play": "次选方向；优先同市场防选，也可来自另一结果市场，无法明确时填观望",
                "rating": "1到5，可使用0.5",
                "verdict": "80到180字逐场结论",
                "market_analysis": {
                    "euro": "欧赔方向",
                    "asian": "是否真正升深，盘口与水位分开",
                    "sporttery": "竞彩让球数和胜平负赔率",
                    "total": "大小球及异常跳档",
                    "consistency": "各市场一致、背离或矛盾",
                },
                "evidence": ["2到6条，只引用输入数字"],
                "risks": ["0到5条"],
                "score_candidates": ["最多3个比分"],
            }],
        }
        rules = [
            "这是结论与依据生成任务，不输出隐藏思维链。",
            "固定按五项检查：欧赔方向、亚盘是否真正升深、竞彩让球盘、大小球、市场一致性。",
            "升降属于走势，不属于盘口名称；必须区分升盘与降水。",
            "竞彩让平必须结合具体让球数解释：主队-1时让平代表主队赢1球，主队+1时代表客队赢1球。",
            "primary_play与secondary_play只允许主胜、平局、客胜、让胜、让平、让负或观望；仅同市场两项可称双选覆盖，跨市场次选只是独立方向。大球、小球只能写入market_analysis.total作为辅助证据。",
            "严格区分客队小胜与竞彩让负：away_small_win只放客队明确为胜负方向且预计净胜1球的比赛；竞彩让负必须放入handicap_lose，禁止放入away_small_win。",
            "逐场决策分两层：单选核心仍只服务平局和让平；主选用于表达最可能结果。先寻找同市场有效防选；同市场第二项不足时，允许从另一结果市场选择一个达到门槛的独立次选方向，禁止为了凑双选强行补平局或让平。",
            "单选核心必须同时满足投注分>=70、价值指数>=60、盘口可信度>=70、星级>=4；未达到单选核心不代表整场没有方向，仍须输出按校准概率排序的主选；次选只有达到独立覆盖门槛才保留。",
            "主选排序的第一目标是命中概率：先比较同市场全部三个结果的历史校准概率，再比较市场去水概率；赔率价值只决定是否值得单选下注，不能把低概率高赔率项提到高概率方向之前。",
            "双选只有在同一市场第二方向也达到独立覆盖门槛时才成立；成立后应覆盖校准覆盖概率最高的两个结果。跨市场次选只表示独立方向，结果存在重叠，严禁概率相加、严禁计入双选覆盖或组合。",
            "若让平对应的historical_goal_margin_model同时满足expected_return<0.95且value_edge<-5%，让平不得作为主选；应改用同市场校准概率最高的方向项，让平最多保留为观察防选。",
            "联赛中亚盘不配合（退盘、升盘高水、上盘升水、降水不升盘、欧亚背离、热门浅盘）时，胜负方向必须硬降级为观察。杯赛/淘汰赛/两回合赛事若只有退盘或降水不升盘单一信号，且没有欧赔走弱、竞彩保护或阵容赛程第二项独立证据，只降低置信度，不得直接反转方向；赛事阶段缺失时必须说明未知。",
            "大小球跳动达到0.75或以上时优先标记数据异常，不得据此强推方向。",
            "special_markets包含体彩计算器的总进球与半全场固定快照及程序校正结果；它们是独立玩法，只能辅助解释比分路径，不得改写胜平负/让球主次选，程序会保留其首选和次选供单独复盘。",
            "不得伪造近期状态、伤停、首发、天气、战意和赛程；输入缺失必须明确说明。",
            "fundamentals来自500赛前页：recent、history、team_rankings、future可作基本面证据；lineups.status=predicted仅表示预计阵容，禁止称为官方首发；injuries.status=no_listed_players仅表示页面未列出球员，禁止称为确认无伤停。",
            "fundamentals.cache_status=stale时代表刷新失败后的过期缓存，只能低权重引用并必须提示时效风险。",
            "历史复盘记忆只用于提醒曾经出现的误判和风险，不是当前比赛事实，不得据此直接推荐。",
            "联赛历史画像来自当前比赛日期之前的完场数据并带时间衰减；只允许把eligible_for_adjustment=true且分段样本充足的内容作为低到中权重基线。",
            "联赛画像中的命中率、让平率、进球率是历史条件频率，不是真实胜率；不得单独据此推荐，必须与当天五项市场证据一致。",
            "league_tactical_model是人工沉淀的联赛模板指数，包含平局、让平、大小球和冷门指数；它只用于筛选和解释，不能覆盖赔率价值、盘口一致性和数据质量。",
            "odds_band_model是赔率区间扫描器：favorite_heat表示热门过热，underdog_upset表示下盘爆冷，handicap_draw_value表示让平价值；1.40-1.70热门危险区、1.80-2.20均势区、客场1.70-2.20陷阱区、平赔低位和盘口过深都只能作为降级热门或提高平/让平扫描权重的证据。",
            "low_odds_asian_model是低于1.50胜赔热门的竞彩让球校准层：cover/exact/fail分别表示热门穿盘、恰好走到竞彩让球边界、热门不穿；必须结合竞彩让球数、亚盘深度差、热门水位和升退盘解释，adjustment_pp只允许作有限概率修正，不得写成确定规律。",
            "最低胜赔低于1.50只表示该胜负选项投注回报不足，不等于热门会失手。过滤低赔热门后仍必须输出概率最高的可投注方向，不得改成观望；若替代项与热门穿盘方向不一致或融合概率未达到三项均分基线33%，必须标记低赔替代风险，但不能隐藏逐场结论。",
            "普通平局采用历史回测版规则：统一模型只允许正向联赛的均势平进入正式池，必须满足平赔2.75-3.20、亚盘退浅或平手保护、上/下盘水位区间正常；平赔2.85-3.14为核心区间，其余只能小试。另有联赛专属模型：葡超小球平、挪超退盘平、荷甲中低总球平、英超降水平、英冠半球不动平、澳超高平赔中低总球、意甲升盘高水平；巴甲只作为平局基线观察模型，不得因单日命中直接升级；日职中低总球目前只观察。强热门冷平若未命中联赛专属模型，只能观察，禁止进入正式推荐。",
            "让平升级采用历史回测版规则：通用模型只允许正向联赛、竞彩让1球、热门胜赔1.26-1.40、让平赔3.30-3.70，并要求亚盘上盘水位0.65-1.04、下盘水位不低于0.75；热门胜赔1.41-1.55只能小试。另有联赛专属让平口袋：意甲中赔让平、德甲中热门让平、法甲高让平赔、英超中高总球小球让平、西甲小球水位让平、沙特高赔大球让平、欧罗巴低水让平；挪超降水让平当前样本不足只观察。让平必须再通过净胜1球路径检查：若降水不升盘但竞彩受让保护项明显低赔，说明更像热门不穿或失手，不升级让平。≤1.25超热、让2球、低命中联赛、上盘≥1.08不得升级；升盘高水、欧亚背离和退盘只作为风险证据，不能单独推让平。",
            "小球只表示进球总数受限，不等于平局：若强方胜赔至少下降0.10、对手胜赔至少上升0.10，且亚盘真实升深或强方处于明确低水，必须把强方小胜放主选、平局放防选。",
            "让平是精确赢球差玩法，不能因用户偏好自动排第一：竞彩让1球时，若同市场方向项的校准概率领先让平至少3个百分点且也是最低赔率项，必须把方向项放主选、让平降为防选；若热门胜赔不高于1.50、亚盘至少真实升深0.25至一球且大小球不低于2.75，也应优先比较穿盘。",
            "主选和防选必须按当日可核验市场证据强弱排序，不得因为用户主玩平/让平就把精确结果放在更强的胜负或穿盘方向之前。",
            "竞彩让球防选不得默认填写让平：确定主选后，必须在剩余两个让球结果中重新比较模型概率与去水市场概率，选择覆盖概率更高的一项；让平只有真实排第二时才保留。",
            "upset_warning_model是爆冷预警扫描器：盘口降级、热门胜赔升、平赔下降、热门穿盘赔率偏高、强队近期穿盘代理偏弱、弱队近期有球会累加风险分；80分以上只能降低热门方向并提示防冷，禁止单独反买。",
            "historical_goal_margin_model按欧赔强弱、亚盘深度、大小球、竞彩让球数、联赛和时间衰减寻找相似完赛场次；ordinary_draw统计0球分差，handicap_draw统计当前让球数对应的精确净胜球差，两者严禁混用。",
            "只有historical_goal_margin_model中eligible_for_adjustment=true的结果才可参与校准；必须同时比较effective_sample、confidence、market_probability、blended_probability、odds和value_edge，样本不足时只允许写观察。",
            "历史相似模型以市场去水概率为先验并限制修正幅度，仍不是真实胜率或因果规律；若历史与市场明显冲突，降低星级或不下注，禁止用历史频率制造必出平局/让平的结论。",
            "historical_odds_rules是固定历史回放得到的赔率区间、竞彩让球数、联赛和初即时变化规则；只能使用matched_rule_ids中的已命中项，并按adjustment_pp做有限修正。",
            "历史赔率规则必须引用sample、hit_rate、market_probability和confidence；它是条件概率倾斜而非必买信号，禁止跳过当天五市场一致性与数据质量。",
            "主队+1且让平赔2.70-3.19时，让平严格表示客队恰好赢1球；最低欧赔1.70-1.89只提高普通平局权重，两条规则不得混用。",
            "联赛画像market_surprise表示临场欧赔存在明确热门时热门方未赢球的历史频率；favorite_fail_rate必须拆看favorite_draw_rate和underdog_win_rate，不能把两者混成同一投注结论。",
            "若当前热门赔率所在favorite_odds_bands样本不少于20，且联赛热门失手率或不穿盘率显著高于全库，只能降低热门方向置信度并增加平局、弱方不败或让球防选，不得脱离当天盘口直接反买。",
            "current_asian_risk表示当前比赛赛前触发的水位与盘口结构；league_history_profile.asian_risk_patterns表示该联赛同类结构的历史不穿率。只有当前模式一致、该模式样本不少于20且联赛画像可调权时，才可作为低到中权重风险证据。",
            "退盘削弱、上盘升水、降水不升盘、升盘高水、欧亚背离和热门过热都只是赛前市场预警，不能表述为赛果原因；盘口无明显预警时应明确需要比赛过程数据解释。",
            "若欧赔和亚盘同时推向客胜，但current_asian_risk出现升盘高水/上盘升水，且竞彩主队受让后的让胜为低赔最低项，必须把客胜降级为方向观察或不下注，不得作为主选胆。",
            "若联赛画像与当天盘口冲突，以当天盘口、数据质量和阵容事实为准，并在风险中说明冲突。",
            "单日观察项属于低权重提醒；只有validated_patterns中的跨日模式可以作为辅助校正，且必须让位于当天盘口。",
            "当validated_pattern_count为0时，代表没有经过跨日和足量样本验证的规则；禁止使用历史0%命中区间、严禁纳入、全部排除或类似绝对结论。",
            "单日某玩法0/N或N/N只说明当天小样本结果，不得外推到赔率区间或当天其他比赛；是否入选必须由当日五项市场证据独立决定。",
            "星级必须横向拉开：五星最多1场且要求多个市场一致、无明显数据异常；四星到四星半最多3场；有欧亚背离、极端水位或盘口跳档的场次最高3.5星。",
            "最终星级代表投注价值而非单纯胜率；必须同时比较FAE估算概率、市场去水概率、即时赔率、value_score、bet_score与盘口可信度。",
            "高胜率低赔率不等于高价值；no_bet为true或盘口可信度低于50的场次必须写明不下注，不得进入核心推荐和组合。",
            "每场必须区分主选与次选：优先同市场防选；同市场无有效第二项时可输出另一结果市场的最强独立方向。跨市场主次选不是互斥结果，不得称为双选覆盖，不得进入组合。",
            "输入概率属于欧赔去水后结合规则调整的FAE估算，未做长期校准；引用时必须写FAE估算或市场隐含概率，不得称为真实胜率。",
            "综合比较本批次全部比赛，可以输出平局、让平和混合2/3关，不得为了凑组合强行选择低质量比赛。",
            "所有自然语言结论、警告和理由必须使用match_number（如周五001）称呼比赛，禁止展示原始match_id；match_id只允许出现在JSON标识字段中。",
            "所有输入比赛必须在matches中恰好出现一次，只输出合法JSON。",
        ]
        return "\n\n".join([
            f"你是 Football AI Engine v{ENGINE_VERSION} 的全日研判层。",
            f"日期：{owner_date}；批次：{batch_number}；比赛数：{len(matches)}。",
            "# 分析约束\n" + "\n".join(f"- {rule}" for rule in rules),
            "# 输出JSON结构\n" + json.dumps(schema, ensure_ascii=False, indent=2),
            "# 历史复盘记忆\n" + json.dumps(
                review_memory or {
                    "review_days": 0,
                    "instruction": "暂无历史复盘记忆，只使用当天输入。",
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            "# 当日比赛输入\n" + json.dumps(
                matches, ensure_ascii=False, indent=2, default=str
            ),
        ])

    def _build_single_prompt(
        self,
        owner_date: str,
        match: Dict[str, Any],
        batch_number: int,
        review_memory: Optional[Dict[str, Any]] = None,
    ) -> str:
        schema = {
            "match": {
                "match_id": str(match.get("match_id") or ""),
                "direction": "主胜/平局/客胜/主队不败/客队不败/观望",
                "primary_play": "平局/让平/让胜/让负/主胜/客胜/观望",
                "secondary_play": "次选方向；优先同市场防选，也可来自另一结果市场，无法明确时填观望",
                "rating": "1到5，可使用0.5",
                "verdict": "80到180字结论",
                "market_analysis": {
                    "euro": "欧赔方向",
                    "asian": "是否真正升深，盘口与水位分开",
                    "sporttery": "竞彩让球数和胜平负赔率",
                    "total": "大小球及跳档异常",
                    "consistency": "各市场一致、背离或矛盾",
                },
                "evidence": ["2到6条，只引用输入数字"],
                "risks": ["0到5条"],
                "score_candidates": ["最多3个比分"],
            }
        }
        rules = [
            "只分析这一场，不输出当日排名。",
            "固定检查欧赔、亚盘真实升深、竞彩让球、大小球、市场一致性。",
            "升降是走势而非盘口名；严格区分升盘和水位变化。",
            "让平必须结合让球数解释；大小球跳动达到0.75优先标异常。",
            "special_markets中的总进球与半全场是程序按体彩计算器赔率生成的独立首选/次选；只可用于比分路径解释，不得把它们输出为胜平负或让球主次选。",
            "primary_play与secondary_play只允许主胜、平局、客胜、让胜、让平、让负或观望；仅同市场两项可称双选覆盖，跨市场次选只是独立方向。大球、小球只作为market_analysis.total辅助证据。",
            "不得编造近期状态、伤停、首发、天气、战意或赛程。",
            "fundamentals来自500赛前页；预计阵容不能写成官方首发，伤停栏目未列球员不能写成确认无伤停。",
            "fundamentals.cache_status=stale时必须降低基本面权重并提示时效风险。",
            "历史复盘记忆只是低权重风险提醒，不是当前比赛事实；不得机械套用昨天结论。",
            "联赛历史画像只在eligible_for_adjustment=true时作为低到中权重基线；赔率分段样本不足时不得使用。",
            "历史联赛频率不是真实概率，必须让位于本场欧赔、亚盘、竞彩、大小球和市场一致性。",
            "league_tactical_model是联赛模板指数，只能作为低到中权重筛选层；指数高但赔率价值、盘口一致性或数据质量不足时仍必须降级或不下注。",
            "odds_band_model是赔率区间扫描器：favorite_heat、underdog_upset、handicap_draw_value分别对应热门过热、下盘爆冷、让平价值；指数高只能降低热门或增加防选，不得脱离盘口一致性直接反买。",
            "low_odds_asian_model只校准最低胜赔低于1.50的竞彩让球三项：竞彩让1球配亚盘半一/一球/球半前档时比较让平，亚盘至少球半或明显深于竞彩时提高穿盘；亚盘明显浅于竞彩或退盘时提高不穿；热门0.76-0.85低水不能直接当作强穿。所有调整均受样本数和±5个百分点上限约束。",
            "最低胜赔低于1.50只表示该胜负选项投注回报不足，不等于热门会失手。过滤低赔热门后仍必须输出概率最高的可投注方向，不得改成观望；若替代项与热门穿盘方向不一致或融合概率未达到三项均分基线33%，必须标记低赔替代风险，但不能隐藏逐场结论。",
            "普通平局采用历史回测版规则：统一模型只允许正向联赛的均势平进入正式池，必须满足平赔2.75-3.20、亚盘退浅或平手保护、上/下盘水位区间正常；平赔2.85-3.14为核心区间，其余只能小试。另有联赛专属模型：葡超小球平、挪超退盘平、荷甲中低总球平、英超降水平、英冠半球不动平、澳超高平赔中低总球、意甲升盘高水平；巴甲只作为平局基线观察模型，不得因单日命中直接升级；日职中低总球目前只观察。强热门冷平若未命中联赛专属模型，只能观察，禁止进入正式推荐。",
            "让平升级采用历史回测版规则：通用模型只允许正向联赛、竞彩让1球、热门胜赔1.26-1.40、让平赔3.30-3.70，并要求亚盘上盘水位0.65-1.04、下盘水位不低于0.75；热门胜赔1.41-1.55只能小试。另有联赛专属让平口袋：意甲中赔让平、德甲中热门让平、法甲高让平赔、英超中高总球小球让平、西甲小球水位让平、沙特高赔大球让平、欧罗巴低水让平；挪超降水让平当前样本不足只观察。让平必须再通过净胜1球路径检查：若降水不升盘但竞彩受让保护项明显低赔，说明更像热门不穿或失手，不升级让平。≤1.25超热、让2球、低命中联赛、上盘≥1.08不得升级；升盘高水、欧亚背离和退盘只作为风险证据，不能单独推让平。",
            "小球只限制比分上限，不自动支持平局：强方胜赔下降、对手胜赔上升，并得到亚盘真实升深或明确低水支持时，优先强方小胜，平局只作防选。",
            "让平必须和穿盘方向比较：竞彩让1球、热门胜赔不高于1.50、亚盘真实升深至少0.25至一球且大小球不低于2.75时，正常低水应把让胜/让负放主选、让平放防选；不高于1.30的超强热门升至一球/球半后，不得机械把让平排第一。",
            "主次选按本场市场证据排序，不得因用户偏好平/让平而倒置。",
            "竞彩让球主选确定后，防选必须重新比较剩余两项的模型概率与去水市场概率，不得机械保留让平；让平只有真实排第二时才可作为防选。",
            "单选核心只允许平局或让平，且必须投注分>=70、价值指数>=60、盘口可信度>=70、星级>=4；逐场主选按校准概率排序。同市场第二项达到门槛时形成防选，否则可以比较另一结果市场的最强独立方向。",
            "未达到单选核心时仍须给出概率最高的主选；两个结果市场都没有达到独立门槛的次选时secondary_play填观望，不得机械补防选。不得输出大球/小球作为主次选。",
            "让平的历史进球差expected_return<0.95且value_edge<-5%时禁止排主选；同市场方向项概率领先至少3个百分点且为最低赔率项时，方向项必须排在让平之前。",
            "联赛中亚盘不配合时胜负方向必须硬降级为观察。杯赛/淘汰赛/两回合赛事若只有退盘或降水不升盘单一信号，且没有欧赔走弱、竞彩保护或阵容赛程第二项独立证据，只降低置信度，不得直接反转方向；赛事阶段缺失时必须说明未知。",
            "upset_warning_model达到重点防冷时，热门胜负方向必须降级为观察或不下注；防选优先写平局、受让保护项或让平，但不得把爆冷预警写成确定赛果。",
            "historical_goal_margin_model将普通平局定义为0球分差，将让平定义为当前竞彩让球数对应的精确净胜球差；两种玩法必须分开引用。仅eligible_for_adjustment=true且effective_sample达标时允许参与校准。",
            "historical_odds_rules是固定历史回放的有限修正规则；只能引用matched_rule_ids中已命中项及其sample、hit_rate、market_probability和adjustment_pp，不得写成必出规律。",
            "主队+1且让平赔2.70-3.19对应客队恰好赢1球；最低欧赔1.70-1.89只作用于普通平局，两者不得混用。",
            "引用相似历史模型时必须同时比较market_probability、blended_probability、odds与value_edge；它以市场为先验且不是因果规律，不得写成必出或真实胜率。",
            "market_surprise是明确热门方未赢球的历史频率，必须拆分热门打平和弱方爆冷；仅当当前赔率分段样本不少于20时用于降低热门置信度或增加防选，禁止单独据此反买。",
            "current_asian_risk只描述本场赛前水位结构；只有联赛画像中相同asian_risk_patterns模式样本不少于20时才能辅助降级或增加防选，禁止把市场预警写成赛果真实原因。",
            "欧赔和亚盘同时推向客胜但触发升盘高水/上盘升水，且竞彩主队受让后的让胜为低赔最低项时，客胜必须降级为观察或不下注。",
            "仅validated_patterns可作为跨日辅助校正，近期观察项不能单独改变推荐。",
            "单日0/N或N/N属于小样本，不得据此将当前比赛定义为严禁、必选、高危赔率区间或全部排除。",
            "存在欧亚背离、极端水位或大小球跳档时自动降级，最高3.5星；缺少多项基本面时不得给五星。",
            "推荐排序优先使用bet_score与value_score，而不是只按胜率；no_bet场次只保留方向观察，必须进入避开池。",
            "明确输出一个主选；同市场防选是可选项。同市场第二方向证据不足时可由系统从另一结果市场补充独立次选方向；跨市场结果不可相加为覆盖率。概率是未校准的FAE估算，不得表述成真实胜率。",
            "不输出隐藏思维链，只输出一个合法JSON对象。",
        ]
        return "\n\n".join([
            f"你是 Football AI Engine v{ENGINE_VERSION}。日期：{owner_date}；序号：{batch_number}。",
            "# 规则\n" + "\n".join(f"- {item}" for item in rules),
            "# 输出结构\n" + json.dumps(schema, ensure_ascii=False),
            "# 历史复盘记忆\n" + json.dumps(
                review_memory or {
                    "review_days": 0,
                    "instruction": "暂无历史复盘记忆，只使用当天输入。",
                },
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ),
            "# 比赛输入\n" + json.dumps(
                match, ensure_ascii=False, separators=(",", ":"), default=str
            ),
        ])

    def _build_synthesis_prompt(
        self,
        owner_date: str,
        matches: List[Dict[str, Any]],
        review_memory: Optional[Dict[str, Any]] = None,
    ) -> str:
        compact = [{
            "match_id": item.get("match_id"),
            "match_number": item.get("match_number"),
            "home_team": item.get("home_team"),
            "away_team": item.get("away_team"),
            "direction": (item.get("analysis") or {}).get("direction"),
            "primary_play": (item.get("analysis") or {}).get("primary_play"),
            "secondary_play": (item.get("analysis") or {}).get("secondary_play"),
            "rating": (item.get("analysis") or {}).get("rating"),
            "verdict": (item.get("analysis") or {}).get("verdict"),
            "risks": (item.get("analysis") or {}).get("risks"),
            "score_candidates": (
                (item.get("analysis") or {}).get("score_candidates")
            ),
        } for item in matches]
        schema = {
            "daily_summary": {
                "core_conclusion": "全日横向结论，80到200字",
                "warnings": ["全日共同风险"],
                "pools": {
                    "handicap_draw": [{
                        "match_id": "输入ID", "rating": 4, "reason": "理由"
                    }],
                    "draw": [{
                        "match_id": "输入ID", "rating": 4, "reason": "理由"
                    }],
                    "away_small_win": [{
                        "match_id": "客胜方向的输入ID", "rating": 4, "reason": "客队净胜1球理由"
                    }],
                    "handicap_lose": [{
                        "match_id": "输入ID", "rating": 4, "reason": "竞彩让负理由"
                    }],
                    "avoid": [{
                        "match_id": "输入ID", "rating": 4, "reason": "理由"
                    }],
                },
                "recommended_combinations": [{
                    "play": "2串1或3串1",
                    "picks": [
                        {"match_id": "输入ID", "selection": "平局或让平"}
                    ],
                    "reason": "为什么这样混合",
                }],
            }
        }
        return "\n\n".join([
            f"你是 FAE v{ENGINE_VERSION} 的全日总编。日期：{owner_date}。",
            "以下逐场结论已经完成。请横向比较全部比赛，只做当日排名和组合，不重写逐场分析。",
            "优先给出同时包含平局与让平的高质量2串1、3串1；不得为了混合而凑低质量选择。",
            "单选核心池和2/3关组合只允许平局/让平；逐场高覆盖双选可以使用主胜、平局、客胜、让胜、让平、让负，但不得把双选包装成单选核心。",
            "正式推荐必须投注分>=70、价值指数>=60、盘口可信度>=70、星级>=4；低于门槛不允许进入核心池。",
            "亚盘不配合时胜负方向必须硬降级为观察，不得在摘要里重新包装成可下注推荐。",
            "严格区分推荐池：客队小胜只放客胜方向且预计客队净胜1球的比赛；竞彩让负无论主客强弱都只能放入handicap_lose池。",
            "结合历史复盘记忆检查是否重复犯错，但记忆不能替代当天盘口，也不能把单日赛果当成稳定规律。",
            "validated_pattern_count为0时不得输出历史0%命中区间、严禁纳入、全部排除等绝对规则；单日小样本只能作为风险备注。",
            "横向校准星级：五星最多1场，四星到四星半最多3场；欧亚背离、极端水位或盘口跳档场次不得进入核心高星推荐。",
            "逐场主选已经给出，防选可能为观望；摘要池不得为凑双选自行补充第二方向，若采用有效防选必须明确写为防范，不得与主选并列成两个高置信结论。",
            "所有自然语言结论、警告和理由必须使用match_number（如周五001）称呼比赛，禁止展示原始match_id；match_id只允许出现在JSON标识字段中。",
            "不得输出隐藏思维链，不得添加输入中不存在的伤停、首发或状态事实，只输出合法JSON。",
            "# 输出结构\n" + json.dumps(schema, ensure_ascii=False, indent=2),
            "# 历史复盘记忆\n" + json.dumps(
                review_memory or {
                    "review_days": 0,
                    "instruction": "暂无历史复盘记忆，只使用当天输入。",
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            "# 全部逐场结论\n" + json.dumps(
                compact, ensure_ascii=False, indent=2, default=str
            ),
        ])

    @classmethod
    def _normalize_match(
        cls,
        source: Dict[str, Any],
        generated: Dict[str, Any],
    ) -> Dict[str, Any]:
        fallback = ((source.get("fae_core") or {}).get("recommendation") or {})
        rating = cls._rating(
            generated.get("rating", fallback.get("stars", 1))
        )
        market_analysis = generated.get("market_analysis")
        if not isinstance(market_analysis, dict):
            market_analysis = {}
        scores = [
            score for score in (generated.get("score_candidates") or [])
            if re.fullmatch(r"\d{1,2}:\d{1,2}", str(score or ""))
        ][:3]
        if not scores:
            scores = [
                str(score) for score in (
                    (source.get("fae_core") or {}).get("score_candidates") or []
                )[:3]
            ]
        has_model_output = bool(generated)
        model_primary_play = str(
            generated.get("primary_play")
            or fallback.get("primary")
            or "观望"
        )[:30]
        value_primary_play, value_guard = cls._value_selection_guard(
            source, model_primary_play
        )
        effective_primary_play, guard = cls._selection_consistency_guard(
            source, value_primary_play
        )
        pre_direction_primary_play = effective_primary_play
        effective_primary_play, direction_guard = (
            cls._directional_precision_guard(
                source, effective_primary_play
            )
        )
        non_cover_guard = cls._favorite_non_cover_guard(
            source, effective_primary_play
        )
        if non_cover_guard.get("triggered"):
            effective_primary_play = str(
                non_cover_guard.get("effective_selection")
                or effective_primary_play
            )
        secondary_decision = cls._secondary_play_decision(
            source,
            effective_primary_play,
            (
                pre_direction_primary_play
                if direction_guard.get("triggered")
                else guard.get("model_selection")
                if guard.get("guard_type")
                == "exact_margin_market_alignment"
                else
                None
                if guard.get("triggered")
                or non_cover_guard.get("triggered")
                else generated.get("secondary_play")
            ),
        )
        secondary_play = secondary_decision["selection"]
        verdict = cls._text(
            generated.get("verdict"),
            (
                "火山全日研判未返回本场完整内容，暂时保留FAE核心结果。"
                if not has_model_output else ""
            ),
            900,
        )
        guard_reasons = [
            str(item.get("reason") or "")
            for item in (guard, direction_guard, non_cover_guard)
            if item.get("triggered") and item.get("reason")
        ]
        if guard_reasons:
            verdict = cls._text(
                f"{'；'.join(guard_reasons)}。以下保留原始AI说明供审计：{verdict}",
                "",
                1100,
            )
        risks = list(dict.fromkeys(
            cls._list(generated.get("risks"), 5, 220)
            + guard_reasons
            + [str(item) for item in source.get("data_warnings") or []]
        ))[:8]
        return {
            "match_id": str(source.get("match_id") or ""),
            "match_number": source.get("match_number"),
            "owner_date": str(source.get("owner_date") or "")[:10] or None,
            "league": source.get("league"),
            "match_time": source.get("match_time"),
            "home_team": source.get("home_team"),
            "away_team": source.get("away_team"),
            "analysis_source": (
                "volcengine-ark" if has_model_output else "fae-core-fallback"
            ),
            "analysis": {
                "direction": str(
                    generated.get("direction")
                    or fallback.get("primary")
                    or "观望"
                )[:30],
                "primary_play": effective_primary_play,
                "secondary_play": secondary_play,
                "secondary_selection_guard": secondary_decision,
                "handicap_play": cls._handicap_play(
                    source, effective_primary_play
                ),
                "model_primary_play": model_primary_play,
                "value_guard": value_guard,
                "consistency_guard": guard,
                "directional_precision_guard": direction_guard,
                "non_cover_guard": non_cover_guard,
                "rating": rating,
                "model_rating": rating,
                "rating_adjustments": [],
                "star_text": cls._stars(rating),
                "verdict": verdict,
                "market_analysis": {
                    key: cls._text(
                        market_analysis.get(key),
                        "输入数据不足，暂不判断",
                        500,
                    )
                    for key in (
                        "euro", "asian", "sporttery", "total", "consistency"
                    )
                },
                "evidence": cls._list(generated.get("evidence"), 6, 220),
                "risks": risks,
                "score_candidates": scores,
                "special_markets": source.get("special_markets") or {},
            },
            "input_snapshot": source,
        }

    @classmethod
    def _secondary_play(
        cls,
        source: Dict[str, Any],
        primary_play: str,
        generated_secondary: Any = None,
    ) -> str:
        return str(cls._secondary_play_decision(
            source, primary_play, generated_secondary
        ).get("selection") or "观望")

    @classmethod
    def _cross_market_secondary_decision(
        cls,
        source: Dict[str, Any],
        primary_play: str,
        generated_secondary: Any = None,
    ) -> Dict[str, Any]:
        """Pick an optional direction from the other result market.

        This is not a same-market coverage pair: the two outcomes can overlap,
        so downstream combination logic must keep it out of the formal
        two-option pool.
        """
        if primary_play in {"主胜", "平局", "客胜"}:
            labels = ("让胜", "让平", "让负")
            probability_keys = {
                "让胜": "win", "让平": "draw", "让负": "lose",
            }
            probabilities = (
                (((source.get("fae_core") or {}).get("probabilities") or {})
                 .get("hhad") or {})
            )
            odds_values = (
                (source.get("sporttery_handicap") or {}).get("current")
                or (source.get("sporttery_handicap") or {}).get("initial")
                or []
            )
            target_market = "竞彩让球"
        elif primary_play in {"让胜", "让平", "让负"}:
            labels = ("主胜", "平局", "客胜")
            probability_keys = {
                "主胜": "home_win", "平局": "draw", "客胜": "away_win",
            }
            probabilities = (
                (source.get("fae_core") or {}).get("probabilities") or {}
            )
            odds_values = (
                (source.get("euro") or {}).get("current")
                or (source.get("euro") or {}).get("initial")
                or []
            )
            target_market = "胜平负"
        else:
            return {
                "selection": "观望",
                "strategy": "cross-market-secondary-unavailable",
                "generated_secondary": str(generated_secondary or "") or None,
                "changed": bool(generated_secondary),
                "cross_market": True,
                "candidates": [],
                "reason": "主选不属于可比较的结果市场，无法生成跨市场次选",
            }

        odds = {
            label: (
                _number(odds_values[index])
                if len(odds_values) > index else None
            )
            for index, label in enumerate(labels)
        }
        inverse = {
            label: 1 / value
            for label, value in odds.items()
            if value is not None and value > 1
        }
        inverse_total = sum(inverse.values())
        rows = []
        for label in labels:
            profile = cls._play_value_profile(source, label)
            model_probability = _number(profile.get("probability"))
            if model_probability is None:
                model_probability = _number(
                    probabilities.get(probability_keys[label])
                )
            market_probability = _number(
                profile.get("market_implied_probability")
            )
            if market_probability is None and inverse_total > 0:
                market_probability = (
                    inverse.get(label, 0) / inverse_total * 100
                )
            components = []
            if model_probability is not None:
                components.append((
                    model_probability, HANDICAP_SECONDARY_MODEL_WEIGHT
                ))
            if market_probability is not None:
                components.append((
                    market_probability, HANDICAP_SECONDARY_MARKET_WEIGHT
                ))
            component_weight = sum(value[1] for value in components)
            coverage_score = (
                sum(value * weight for value, weight in components)
                / component_weight
                if component_weight else None
            )
            current_odds = _number(profile.get("odds"))
            if current_odds is None:
                current_odds = odds.get(label)
            expected_return = (
                model_probability / 100 * current_odds
                if model_probability is not None and current_odds is not None
                else None
            )
            rows.append({
                "selection": label,
                "market": target_market,
                "model_probability": (
                    round(model_probability, 2)
                    if model_probability is not None else None
                ),
                "market_probability": (
                    round(market_probability, 2)
                    if market_probability is not None else None
                ),
                "coverage_score": (
                    round(coverage_score, 2)
                    if coverage_score is not None else None
                ),
                "odds": (
                    round(current_odds, 3)
                    if current_odds is not None else None
                ),
                "expected_return": (
                    round(expected_return, 3)
                    if expected_return is not None else None
                ),
                "odds_eligible": bool(
                    current_odds is not None
                    and current_odds >= SINGLE_MIN_ODDS
                ),
            })
        eligible = [
            row for row in rows
            if row.get("coverage_score") is not None
            and row.get("odds_eligible")
            and float(row.get("coverage_score") or 0)
            >= TWO_OPTION_MIN_SECONDARY_COVERAGE
        ]
        selected_row = max(
            eligible,
            key=lambda row: (
                float(row.get("coverage_score") or 0),
                float(row.get("expected_return") or 0),
            ),
        ) if eligible else {}
        selected = str(selected_row.get("selection") or "观望")
        proposed = max(
            [row for row in rows if row.get("odds_eligible")],
            key=lambda row: float(row.get("coverage_score") or 0),
            default={},
        )
        gate = {
            "passed": bool(selected_row),
            "proposed_selection": (
                selected
                if selected_row
                else str(proposed.get("selection") or "观望")
            ),
            "coverage_score": (
                selected_row.get("coverage_score")
                if selected_row else proposed.get("coverage_score")
            ),
            "minimum_coverage_score": TWO_OPTION_MIN_SECONDARY_COVERAGE,
            "minimum_odds": SINGLE_MIN_ODDS,
        }
        reason = (
            f"同市场无有效次选，改从{target_market}选择{selected}，"
            f"独立方向分{selected_row.get('coverage_score')}，赔率"
            f"{selected_row.get('odds')}；该方向不计入同市场双选覆盖"
            if selected_row else
            f"同市场无有效次选，{target_market}也没有同时达到"
            f"{TWO_OPTION_MIN_SECONDARY_COVERAGE:g}分和赔率"
            f"{SINGLE_MIN_ODDS:g}的独立方向"
        )
        candidate = str(generated_secondary or "").strip()
        return {
            "selection": selected,
            "strategy": (
                "cross-market-secondary-v1"
                if selected != "观望"
                else "optional-secondary-coverage-gate-v1"
            ),
            "generated_secondary": candidate or None,
            "changed": bool(candidate and candidate != selected),
            "cross_market": True,
            "source_market": (
                "胜平负" if primary_play in {"主胜", "平局", "客胜"}
                else "竞彩让球"
            ),
            "target_market": target_market,
            "model_weight": HANDICAP_SECONDARY_MODEL_WEIGHT,
            "market_weight": HANDICAP_SECONDARY_MARKET_WEIGHT,
            "secondary_gate": gate,
            "candidates": rows,
            "reason": reason,
        }

    @classmethod
    def _secondary_play_decision(
        cls,
        source: Dict[str, Any],
        primary_play: str,
        generated_secondary: Any = None,
    ) -> Dict[str, Any]:
        """Choose an optional same-market hedge and expose its ranking.

        Handicap coverage previously kept the model's original ``让平`` after
        a guard changed the primary direction.  That could omit a more likely
        directional outcome.  Handicap hedges now re-rank both remaining
        outcomes with calibrated model probability and de-vig market
        probability; expected return is used only as a tie-breaker.  A weak
        runner-up is no longer forced into the output and becomes ``观望``.
        """
        allowed = TWO_OPTION_PLAY_SELECTIONS | {"观望"}
        if primary_play not in TWO_OPTION_PLAY_SELECTIONS:
            return {
                "selection": "观望",
                "strategy": "result-market-only-hard-guard",
                "generated_secondary": str(generated_secondary or "") or None,
                "changed": bool(generated_secondary),
                "candidates": [],
                "reason": "主选不是胜平负或竞彩让球结果，禁止生成双选防选",
            }
        same_market = (
            {"主胜", "平局", "客胜"}
            if primary_play in {"主胜", "平局", "客胜"}
            else {"让胜", "让平", "让负"}
            if primary_play in {"让胜", "让平", "让负"}
            else allowed
        )
        candidate = str(generated_secondary or "").strip()
        if primary_play in {"让胜", "让平", "让负"}:
            labels = ("让胜", "让平", "让负")
            probability_keys = {
                "让胜": "win", "让平": "draw", "让负": "lose",
            }
            hhad = (
                (((source.get("fae_core") or {}).get("probabilities") or {})
                 .get("hhad") or {})
            )
            odds_values = (
                (source.get("sporttery_handicap") or {}).get("current")
                or (source.get("sporttery_handicap") or {}).get("initial")
                or []
            )
            odds = {
                label: (
                    _number(odds_values[index])
                    if len(odds_values) > index else None
                )
                for index, label in enumerate(labels)
            }
            inverse = {
                label: 1 / value
                for label, value in odds.items()
                if value is not None and value > 1
            }
            inverse_total = sum(inverse.values())
            rows = []
            for label in labels:
                profile = cls._play_value_profile(source, label)
                model_probability = _number(profile.get("probability"))
                if model_probability is None:
                    model_probability = _number(
                        hhad.get(probability_keys[label])
                    )
                market_probability = _number(
                    profile.get("market_implied_probability")
                )
                if market_probability is None and inverse_total > 0:
                    market_probability = (
                        inverse.get(label, 0) / inverse_total * 100
                    )
                components = []
                if model_probability is not None:
                    components.append((
                        model_probability, HANDICAP_SECONDARY_MODEL_WEIGHT
                    ))
                if market_probability is not None:
                    components.append((
                        market_probability, HANDICAP_SECONDARY_MARKET_WEIGHT
                    ))
                component_weight = sum(value[1] for value in components)
                coverage_score = (
                    sum(value * weight for value, weight in components)
                    / component_weight
                    if component_weight else None
                )
                current_odds = _number(profile.get("odds"))
                if current_odds is None:
                    current_odds = odds.get(label)
                expected_return = (
                    model_probability / 100 * current_odds
                    if (
                        model_probability is not None
                        and current_odds is not None
                    ) else None
                )
                rows.append({
                    "selection": label,
                    "model_probability": (
                        round(model_probability, 2)
                        if model_probability is not None else None
                    ),
                    "market_probability": (
                        round(market_probability, 2)
                        if market_probability is not None else None
                    ),
                    "coverage_score": (
                        round(coverage_score, 2)
                        if coverage_score is not None else None
                    ),
                    "odds": (
                        round(current_odds, 3)
                        if current_odds is not None else None
                    ),
                    "expected_return": (
                        round(expected_return, 3)
                        if expected_return is not None else None
                    ),
                })
            alternatives = [
                row for row in rows
                if row["selection"] != primary_play
            ]
            ranked = [
                row for row in alternatives
                if row.get("coverage_score") is not None
            ]
            ranked = sorted(
                ranked,
                key=lambda row: (
                    float(row.get("coverage_score") or 0),
                    float(row.get("expected_return") or 0),
                    float(row.get("model_probability") or 0),
                    float(row.get("market_probability") or 0),
                ),
                reverse=True,
            )
            selected_row = ranked[0] if ranked else {}
            value_protection = {
                "triggered": False,
                "coverage_selection": selected_row.get("selection"),
                "effective_selection": selected_row.get("selection"),
            }
            if len(ranked) >= 2:
                value_row = ranked[1]
                coverage_gap = (
                    float(selected_row.get("coverage_score") or 0)
                    - float(value_row.get("coverage_score") or 0)
                )
                selected_return = _number(
                    selected_row.get("expected_return")
                )
                value_return = _number(value_row.get("expected_return"))
                value_coverage = _number(value_row.get("coverage_score"))
                return_gain = (
                    value_return - selected_return
                    if selected_return is not None
                    and value_return is not None else None
                )
                value_veto_reasons = []
                shadow_gap = None
                # The higher-odds exact-margin option may only replace the
                # coverage runner-up when no stronger model layer rejects it.
                # Shadow probabilities remain a veto here: they never create
                # a recommendation or make a pair actionable by themselves.
                if value_row.get("selection") == "让平":
                    risk_ids = {
                        str(value) for value in (
                            (source.get("current_asian_risk") or {}).get(
                                "pattern_ids"
                            ) or []
                        )
                    }
                    draw_band_signal = cls._draw_odds_band_signal(
                        source, "让平", risk_ids
                    )
                    if draw_band_signal.get("block_official"):
                        value_veto_reasons.append(str(
                            draw_band_signal.get("note")
                            or "让平赔率区间已被历史门禁降级"
                        ))
                    shadow_candidates = (
                        (((source.get("supervised_shadow") or {})
                          .get("high_confidence_single") or {})
                         .get("candidates") or [])
                    )
                    shadow_probabilities = {
                        str(item.get("selection") or ""): _number(
                            item.get("probability")
                        )
                        for item in shadow_candidates
                        if str(item.get("market") or "") == "竞彩让球"
                    }
                    coverage_shadow = shadow_probabilities.get(str(
                        selected_row.get("selection") or ""
                    ))
                    value_shadow = shadow_probabilities.get("让平")
                    if (
                        coverage_shadow is not None
                        and value_shadow is not None
                    ):
                        shadow_gap = round(
                            coverage_shadow - value_shadow, 2
                        )
                        if shadow_gap >= 5.0:
                            value_veto_reasons.append(
                                "监督模型中{}概率{:.2f}%高于让平{:.2f}%"
                                "，影子层只用于阻止错误换挡".format(
                                    selected_row.get("selection"),
                                    coverage_shadow,
                                    value_shadow,
                                )
                            )
                value_protection_blocked = bool(value_veto_reasons)
                if (
                    coverage_gap <= TWO_OPTION_SECONDARY_VALUE_MAX_GAP
                    and return_gain is not None
                    and return_gain >= TWO_OPTION_SECONDARY_VALUE_MIN_GAIN
                    and value_return >= TWO_OPTION_SECONDARY_VALUE_MIN_RETURN
                    # Value protection may re-rank two already valid hedges,
                    # but it must never replace a passing coverage candidate
                    # with one that fails the independent direction floor.
                    and value_coverage is not None
                    and value_coverage >= TWO_OPTION_MIN_SECONDARY_COVERAGE
                    and not value_protection_blocked
                ):
                    value_protection = {
                        "triggered": True,
                        "blocked": False,
                        "coverage_selection": selected_row.get("selection"),
                        "effective_selection": value_row.get("selection"),
                        "coverage_gap": round(coverage_gap, 2),
                        "expected_return_gain": round(return_gain, 3),
                        "coverage_expected_return": selected_return,
                        "effective_expected_return": value_return,
                        "reason": (
                            "第二、第三方向覆盖分接近，改用赔率期望更高的防选"
                        ),
                    }
                    selected_row = value_row
                elif value_protection_blocked:
                    value_protection = {
                        "triggered": False,
                        "blocked": True,
                        "coverage_selection": selected_row.get("selection"),
                        "effective_selection": selected_row.get("selection"),
                        "rejected_value_selection": value_row.get(
                            "selection"
                        ),
                        "coverage_gap": round(coverage_gap, 2),
                        "expected_return_gain": (
                            round(return_gain, 3)
                            if return_gain is not None else None
                        ),
                        "shadow_probability_gap": shadow_gap,
                        "veto_reasons": value_veto_reasons,
                        "reason": (
                            "赔率期望换挡被精确进球差门禁阻止，保留覆盖概率"
                            "更高的防选"
                        ),
                    }
            proposed_row = dict(selected_row or {})
            proposed_selection = str(
                proposed_row.get("selection") or "观望"
            )
            proposed_coverage = _number(
                proposed_row.get("coverage_score")
            )
            secondary_gate = {
                "passed": bool(
                    proposed_selection != "观望"
                    and proposed_coverage is not None
                    and proposed_coverage
                    >= TWO_OPTION_MIN_SECONDARY_COVERAGE
                ),
                "proposed_selection": proposed_selection,
                "coverage_score": proposed_coverage,
                "minimum_coverage_score": (
                    TWO_OPTION_MIN_SECONDARY_COVERAGE
                ),
            }
            if not secondary_gate["passed"]:
                cross_market = cls._cross_market_secondary_decision(
                    source, primary_play, generated_secondary
                )
                cross_market["same_market_secondary_gate"] = secondary_gate
                cross_market["same_market_candidates"] = rows
                return cross_market
            selected = (
                proposed_selection
                if secondary_gate["passed"] else "观望"
            )
            selected_row = (
                proposed_row if selected != "观望" else {}
            )
            changed = bool(
                candidate in same_market
                and candidate not in {primary_play, "观望"}
                and candidate != selected
            )
            if not secondary_gate["passed"]:
                reason = (
                    f"让球次选可为空：主选{primary_play}后，候选"
                    f"{proposed_selection}覆盖分{proposed_coverage}低于"
                    f"{TWO_OPTION_MIN_SECONDARY_COVERAGE:g}，不强制补防选"
                )
            else:
                reason = (
                    f"让球双选动态次选：主选{primary_play}后，"
                    f"选择{selected}，覆盖分"
                    f"{selected_row.get('coverage_score')}"
                )
            if secondary_gate["passed"] and value_protection.get("triggered"):
                reason += (
                    f"；与{value_protection.get('coverage_selection')}仅差"
                    f"{value_protection.get('coverage_gap')}分，但赔率期望提高"
                    f"{float(value_protection.get('expected_return_gain') or 0) * 100:.1f}%"
                )
            elif secondary_gate["passed"] and value_protection.get("blocked"):
                reason += (
                    "；让平赔率期望换挡已被门禁阻止，"
                    + "、".join(
                        str(value) for value in
                        value_protection.get("veto_reasons") or []
                    )
                )
            elif secondary_gate["passed"]:
                reason += "，为剩余方向最高覆盖分"
            if changed:
                reason += f"，替换原防选{candidate}"
            return {
                "selection": selected,
                "strategy": (
                    "optional-secondary-coverage-gate-v1"
                    if not secondary_gate["passed"] else
                    "hhad-model-market-value-protection-v2"
                    if value_protection.get("triggered")
                    else "hhad-model-market-coverage-v1"
                ),
                "generated_secondary": candidate or None,
                "changed": changed,
                "model_weight": HANDICAP_SECONDARY_MODEL_WEIGHT,
                "market_weight": HANDICAP_SECONDARY_MARKET_WEIGHT,
                "value_protection": value_protection,
                "secondary_gate": secondary_gate,
                "candidates": rows,
                "reason": reason,
            }
        if primary_play in {"主胜", "平局", "客胜"}:
            labels = ("主胜", "平局", "客胜")
            probability_keys = {
                "主胜": "home_win", "平局": "draw", "客胜": "away_win",
            }
            probabilities = (
                (source.get("fae_core") or {}).get("probabilities") or {}
            )
            odds_values = (
                (source.get("euro") or {}).get("current")
                or (source.get("euro") or {}).get("initial")
                or []
            )
            odds = {
                label: (
                    _number(odds_values[index])
                    if len(odds_values) > index else None
                )
                for index, label in enumerate(labels)
            }
            inverse = {
                label: 1 / value
                for label, value in odds.items()
                if value is not None and value > 1
            }
            inverse_total = sum(inverse.values())
            rows = []
            for label in labels:
                profile = cls._play_value_profile(source, label)
                model_probability = _number(profile.get("probability"))
                if model_probability is None:
                    model_probability = _number(
                        probabilities.get(probability_keys[label])
                    )
                market_probability = _number(
                    profile.get("market_implied_probability")
                )
                if market_probability is None and inverse_total > 0:
                    market_probability = (
                        inverse.get(label, 0) / inverse_total * 100
                    )
                components = []
                if model_probability is not None:
                    components.append((
                        model_probability, HANDICAP_SECONDARY_MODEL_WEIGHT
                    ))
                if market_probability is not None:
                    components.append((
                        market_probability, HANDICAP_SECONDARY_MARKET_WEIGHT
                    ))
                component_weight = sum(value[1] for value in components)
                coverage_score = (
                    sum(value * weight for value, weight in components)
                    / component_weight
                    if component_weight else None
                )
                current_odds = _number(profile.get("odds"))
                if current_odds is None:
                    current_odds = odds.get(label)
                rows.append({
                    "selection": label,
                    "model_probability": (
                        round(model_probability, 2)
                        if model_probability is not None else None
                    ),
                    "market_probability": (
                        round(market_probability, 2)
                        if market_probability is not None else None
                    ),
                    "coverage_score": (
                        round(coverage_score, 2)
                        if coverage_score is not None else None
                    ),
                    "odds": (
                        round(current_odds, 3)
                        if current_odds is not None else None
                    ),
                })
            alternatives = [
                row for row in rows
                if row["selection"] != primary_play
                and row.get("coverage_score") is not None
            ]
            proposed_selection = (
                max(
                    alternatives,
                    key=lambda row: (
                        float(row.get("coverage_score") or 0),
                        float(row.get("model_probability") or 0),
                        float(row.get("market_probability") or 0),
                    ),
                )["selection"]
                if alternatives else "观望"
            )
            proposed_row = next(
                (
                    row for row in rows
                    if row["selection"] == proposed_selection
                ),
                {},
            )
            proposed_coverage = _number(
                proposed_row.get("coverage_score")
            )
            secondary_gate = {
                "passed": bool(
                    proposed_selection != "观望"
                    and proposed_coverage is not None
                    and proposed_coverage
                    >= TWO_OPTION_MIN_SECONDARY_COVERAGE
                ),
                "proposed_selection": proposed_selection,
                "coverage_score": proposed_coverage,
                "minimum_coverage_score": (
                    TWO_OPTION_MIN_SECONDARY_COVERAGE
                ),
            }
            if not secondary_gate["passed"]:
                cross_market = cls._cross_market_secondary_decision(
                    source, primary_play, generated_secondary
                )
                cross_market["same_market_secondary_gate"] = secondary_gate
                cross_market["same_market_candidates"] = rows
                return cross_market
            selected = (
                proposed_selection
                if secondary_gate["passed"] else "观望"
            )
            selected_row = proposed_row if selected != "观望" else {}
            changed = bool(
                candidate in same_market
                and candidate not in {primary_play, "观望"}
                and candidate != selected
            )
            reason = (
                f"胜平负双选动态次选：主选{primary_play}后，"
                f"{selected}覆盖分{selected_row.get('coverage_score')}最高"
                if secondary_gate["passed"] else
                f"胜平负次选可为空：主选{primary_play}后，候选"
                f"{proposed_selection}覆盖分{proposed_coverage}低于"
                f"{TWO_OPTION_MIN_SECONDARY_COVERAGE:g}，不强制补防选"
            )
            if changed:
                reason += f"，替换原防选{candidate}"
            return {
                "selection": selected,
                "strategy": (
                    "had-model-market-coverage-v1"
                    if secondary_gate["passed"]
                    else "optional-secondary-coverage-gate-v1"
                ),
                "generated_secondary": candidate or None,
                "changed": changed,
                "model_weight": HANDICAP_SECONDARY_MODEL_WEIGHT,
                "market_weight": HANDICAP_SECONDARY_MARKET_WEIGHT,
                "secondary_gate": secondary_gate,
                "candidates": rows,
                "reason": reason,
            }
        if (
            candidate in same_market
            and candidate not in {primary_play, "观望"}
        ):
            return {
                "selection": candidate,
                "strategy": "same-market-model-secondary",
                "generated_secondary": candidate,
                "changed": False,
                "candidates": [],
                "reason": "保留大模型同市场防选",
            }
        probabilities = (
            (source.get("fae_core") or {}).get("probabilities") or {}
        )
        groups = []
        if primary_play in {"主胜", "平局", "客胜"}:
            groups = [
                ("主胜", _number(probabilities.get("home_win")) or 0),
                ("平局", _number(probabilities.get("draw")) or 0),
                ("客胜", _number(probabilities.get("away_win")) or 0),
            ]
            if primary_play in {"主胜", "客胜"}:
                draw_probability = dict(groups).get("平局", 0)
                opposite = "客胜" if primary_play == "主胜" else "主胜"
                opposite_probability = dict(groups).get(opposite, 0)
                if draw_probability >= opposite_probability - 3:
                    return {
                        "selection": "平局",
                        "strategy": "ordinary-draw-defense",
                        "generated_secondary": candidate or None,
                        "changed": bool(candidate and candidate != "平局"),
                        "candidates": [
                            {
                                "selection": label,
                                "model_probability": probability,
                            }
                            for label, probability in groups
                        ],
                        "reason": "胜负主选优先使用平局作为邻近防选",
                    }
        elif primary_play in {"让胜", "让平", "让负"}:
            hhad = probabilities.get("hhad") or {}
            groups = [
                ("让胜", _number(hhad.get("win")) or 0),
                ("让平", _number(hhad.get("draw")) or 0),
                ("让负", _number(hhad.get("lose")) or 0),
            ]
        alternatives = [item for item in groups if item[0] != primary_play]
        selected = (
            max(alternatives, key=lambda item: item[1])[0]
            if alternatives else "观望"
        )
        return {
            "selection": selected,
            "strategy": "same-market-probability-fallback",
            "generated_secondary": candidate or None,
            "changed": bool(candidate and candidate != selected),
            "candidates": [
                {"selection": label, "model_probability": probability}
                for label, probability in groups
            ],
            "reason": "同市场按模型概率选择防选",
        }

    @classmethod
    def _two_option_profile(
        cls,
        source: Dict[str, Any],
        analysis: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Measure whether the final same-market pair is usable for coverage.

        A rejected single is not automatically a rejected match.  This layer
        evaluates the already-finalized primary and secondary as one coverage
        decision without pretending that the pair is a core single.  Big/small
        is intentionally excluded because the user only wants result markets.
        """
        primary = str(analysis.get("primary_play") or "")
        secondary = str(analysis.get("secondary_play") or "")
        if (
            primary not in TWO_OPTION_PLAY_SELECTIONS
            or secondary not in TWO_OPTION_PLAY_SELECTIONS
            or primary == secondary
        ):
            return {
                "actionable": False,
                "selections": [
                    value for value in (primary, secondary)
                    if value in TWO_OPTION_PLAY_SELECTIONS
                ],
                "reason": "主选和次选未形成有效的同市场双选",
            }
        ordinary = {"主胜", "平局", "客胜"}
        handicap = {"让胜", "让平", "让负"}
        market = (
            "胜平负" if {primary, secondary}.issubset(ordinary)
            else "竞彩让球" if {primary, secondary}.issubset(handicap)
            else ""
        )
        if not market:
            return {
                "actionable": False,
                "selections": [primary, secondary],
                "reason": "主选和次选不属于同一结果市场",
            }

        decision = analysis.get("secondary_selection_guard") or {}
        candidates = [
            dict(item) for item in decision.get("candidates") or []
            if str(item.get("selection") or "") in (
                ordinary if market == "胜平负" else handicap
            )
        ]
        if len(candidates) < 3:
            refreshed = cls._secondary_play_decision(
                source, primary, secondary
            )
            candidates = [
                dict(item) for item in refreshed.get("candidates") or []
            ]
        by_selection = {
            str(item.get("selection") or ""): item for item in candidates
        }
        selected_rows = [
            by_selection.get(primary) or {},
            by_selection.get(secondary) or {},
        ]
        if any(_number(item.get("coverage_score")) is None for item in selected_rows):
            return {
                "actionable": False,
                "market": market,
                "selections": [primary, secondary],
                "reason": "双选缺少可核验的模型与市场覆盖概率",
            }
        omitted_rows = [
            item for label, item in by_selection.items()
            if label not in {primary, secondary}
        ]
        if not omitted_rows:
            return {
                "actionable": False,
                "market": market,
                "selections": [primary, secondary],
                "reason": "双选缺少第三项对照概率",
            }
        coverage = sum(
            float(item.get("coverage_score") or 0)
            for item in selected_rows
        )
        model_coverage = sum(
            float(item.get("model_probability") or 0)
            for item in selected_rows
        )
        market_values = [
            _number(item.get("market_probability"))
            for item in selected_rows
        ]
        market_coverage = (
            sum(float(value) for value in market_values if value is not None)
            if all(value is not None for value in market_values) else None
        )
        second_score = min(
            float(item.get("coverage_score") or 0)
            for item in selected_rows
        )
        third_score = max(
            float(item.get("coverage_score") or 0)
            for item in omitted_rows
        )
        second_gap = second_score - third_score
        confidence = _number(
            (analysis.get("market_confidence") or {}).get("score")
        ) or 0
        risk = (source.get("fae_core") or {}).get("risk") or {}
        warnings = [str(value) for value in source.get("data_warnings") or []]
        severe_data_risk = bool(
            risk.get("dangerous")
            or any("跳至" in value or "跳档" in value for value in warnings)
            or (analysis.get("non_cover_guard") or {}).get("force_no_bet")
        )
        odds = {
            item.get("selection"): item.get("odds") for item in selected_rows
        }
        complete_odds = all(
            _number(odds.get(selection)) is not None
            for selection in (primary, secondary)
        )
        model_expected_return = None
        equal_stake_expected_roi = None
        pair_value_score = None
        dutch_return = None
        dutch_roi = None
        shortest_odds = None
        if complete_odds:
            priced_rows = [
                (
                    float(item.get("model_probability") or 0) / 100,
                    float(odds.get(str(item.get("selection") or "")) or 0),
                )
                for item in selected_rows
            ]
            model_expected_return = sum(
                probability * price for probability, price in priced_rows
            )
            equal_stake_expected_roi = (
                model_expected_return / len(priced_rows) - 1
            ) * 100
            pair_value_score = max(
                0.0, min(100.0, 100.0 + equal_stake_expected_roi)
            )
            inverse_sum = sum(
                1 / price for _, price in priced_rows if price > 0
            )
            if inverse_sum > 0:
                dutch_return = 1 / inverse_sum
                dutch_roi = (dutch_return - 1) * 100
            shortest_odds = min(price for _, price in priced_rows)
        coverage_edge = (
            coverage - market_coverage
            if market_coverage is not None else 0.0
        )
        low_price_favorite = bool(
            market == "胜平负"
            and shortest_odds is not None
            and shortest_odds < TWO_OPTION_LOW_PRICE_FAVORITE_ODDS
        )
        minimum_anchor_odds = (
            TWO_OPTION_COMBO_MIN_PATH_ODDS / shortest_odds
            if shortest_odds is not None and shortest_odds > 0 else None
        )
        target_anchor_odds = (
            TWO_OPTION_COMBO_TARGET_PATH_ODDS / shortest_odds
            if shortest_odds is not None and shortest_odds > 0 else None
        )
        parlay_fit = (
            "优" if minimum_anchor_odds is not None
            and minimum_anchor_odds <= 1.80
            else "中" if minimum_anchor_odds is not None
            and minimum_anchor_odds <= 2.10
            else "低"
        )
        eligible = bool(
            coverage >= TWO_OPTION_MIN_COVERAGE
            and confidence >= TWO_OPTION_MIN_MARKET_CONFIDENCE
            and second_gap >= TWO_OPTION_MIN_SECOND_GAP
            and complete_odds
            and not severe_data_risk
        )
        # Coverage is the admission gate, not the final ordering rule.  The
        # former formula almost entirely sorted by coverage and consequently
        # filled the slate with 1.20-1.40 favourites.  Rank the admitted pair
        # by its model-priced equal-stake value as well, while retaining a
        # modest confidence/gap contribution.  Ordinary low-price favourites
        # receive a transparent penalty and are capped at the daily selector.
        low_price_penalty = (
            max(0.0, TWO_OPTION_LOW_PRICE_FAVORITE_ODDS - shortest_odds) * 20
            if low_price_favorite and shortest_odds is not None else 0.0
        )
        rank_score = (
            coverage * 0.42
            + float(pair_value_score or 0) * 0.38
            + confidence * 0.08
            + min(12.0, max(0.0, second_gap)) * 0.45
            + max(-5.0, min(5.0, coverage_edge)) * 0.8
            - low_price_penalty
        )
        reasons = []
        if coverage < TWO_OPTION_MIN_COVERAGE:
            reasons.append(
                f"双选覆盖分{coverage:.1f}低于{TWO_OPTION_MIN_COVERAGE:g}"
            )
        if confidence < TWO_OPTION_MIN_MARKET_CONFIDENCE:
            reasons.append(
                f"盘口可信度{confidence:g}低于{TWO_OPTION_MIN_MARKET_CONFIDENCE:g}"
            )
        if second_gap < TWO_OPTION_MIN_SECOND_GAP:
            reasons.append(
                f"次选仅领先第三项{second_gap:.1f}个百分点"
            )
        if not complete_odds:
            reasons.append("双选赔率不完整")
        if severe_data_risk:
            reasons.append("存在危险盘口、异常跳档或热门不穿硬护栏")
        return {
            "actionable": eligible,
            "market": market,
            "selections": [primary, secondary],
            "selection_text": f"{primary} / {secondary}",
            "odds": odds,
            "coverage_score": round(coverage, 2),
            "model_coverage_probability": round(model_coverage, 2),
            "market_coverage_probability": (
                round(market_coverage, 2)
                if market_coverage is not None else None
            ),
            "second_over_third_gap": round(second_gap, 2),
            "market_confidence": round(confidence, 1),
            "coverage_value_edge": round(coverage_edge, 2),
            "pair_value_score": (
                round(pair_value_score, 1)
                if pair_value_score is not None else None
            ),
            "equal_stake_expected_roi": (
                round(equal_stake_expected_roi, 1)
                if equal_stake_expected_roi is not None else None
            ),
            "dutch_return": (
                round(dutch_return, 4) if dutch_return is not None else None
            ),
            "dutch_roi": (
                round(dutch_roi, 1) if dutch_roi is not None else None
            ),
            "shortest_odds": (
                round(shortest_odds, 3)
                if shortest_odds is not None else None
            ),
            "low_price_favorite": low_price_favorite,
            "low_price_penalty": round(low_price_penalty, 2),
            "minimum_anchor_odds": (
                round(minimum_anchor_odds, 2)
                if minimum_anchor_odds is not None else None
            ),
            "target_anchor_odds": (
                round(target_anchor_odds, 2)
                if target_anchor_odds is not None else None
            ),
            "parlay_fit": parlay_fit,
            "rank_score": round(rank_score, 2),
            "reason": (
                f"同市场前两项覆盖分{coverage:.1f}，次选领先第三项"
                f"{second_gap:.1f}个百分点"
                if eligible else "；".join(reasons)
            ),
        }

    @classmethod
    def apply_two_option_recommendations(
        cls,
        matches: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Keep only the strongest daily pairs as actionable suggestions."""
        rows = []
        eligible = []
        for index, item in enumerate(matches):
            row = dict(item or {})
            analysis = dict(row.get("analysis") or {})
            profile = cls._two_option_profile(
                row.get("input_snapshot") or {}, analysis
            )
            analysis["two_option_recommendation"] = profile
            row["analysis"] = analysis
            rows.append(row)
            analysis_source = str(row.get("analysis_source") or "")
            ai_verified = analysis_source in {"", "volcengine-ark"}
            profile["ai_verified"] = ai_verified
            profile["analysis_source"] = analysis_source or "legacy"
            if profile.get("actionable") and not ai_verified:
                profile["actionable"] = False
                profile["reason"] = (
                    str(profile.get("reason") or "")
                    + "；本场仅有FAE规则兜底，等待大模型研判后再进入双选核心"
                ).strip("；")
                analysis["two_option_recommendation"] = profile
                row["analysis"] = analysis
            if profile.get("actionable"):
                eligible.append((
                    float(profile.get("rank_score") or 0), index
                ))
        ordered_eligible = sorted(eligible, reverse=True)
        selected_order = []
        low_price_count = 0
        for _, candidate_index in ordered_eligible:
            profile = (
                (rows[candidate_index].get("analysis") or {})
                .get("two_option_recommendation") or {}
            )
            if profile.get("low_price_favorite"):
                if low_price_count >= TWO_OPTION_LOW_PRICE_FAVORITE_LIMIT:
                    continue
                low_price_count += 1
            selected_order.append(candidate_index)
            if len(selected_order) >= TWO_OPTION_DAILY_LIMIT:
                break
        selected = set(selected_order)
        daily_rank_by_index = {
            candidate_index: rank
            for rank, candidate_index in enumerate(selected_order, start=1)
        }
        for index, row in enumerate(rows):
            analysis = row["analysis"]
            profile = dict(analysis.get("two_option_recommendation") or {})
            shortlisted = index in selected
            profile["actionable"] = shortlisted
            profile["daily_rank"] = (
                daily_rank_by_index.get(index)
                if shortlisted else None
            )
            if not shortlisted and profile.get("rank_score") is not None:
                suffix = (
                    "；普通胜平负低于1.45的热门双选每日最多保留"
                    f"{TWO_OPTION_LOW_PRICE_FAVORITE_LIMIT}场"
                    if profile.get("low_price_favorite")
                    else f"；全日仅保留前{TWO_OPTION_DAILY_LIMIT}场双选"
                )
                profile["reason"] = (
                    str(profile.get("reason") or "") + suffix
                ).strip("；")
            analysis["two_option_recommendation"] = profile
            if shortlisted and analysis.get("no_bet"):
                analysis["decision"] = "双选可考虑"
            elif (
                not shortlisted
                and analysis.get("no_bet")
                and analysis.get("decision") == "双选可考虑"
            ):
                analysis["decision"] = "不下注"
            profile["recommendation_level"] = (
                "core" if shortlisted else "watch"
            )
            analysis["two_option_recommendation"] = profile
            row["analysis"] = analysis
        return [cls._align_single_with_two_option(row) for row in rows]

    @classmethod
    def _selection_margin_support(
        cls,
        source: Dict[str, Any],
        selection: str,
    ) -> set[int]:
        """Return representative goal margins covered by a result option."""
        margins = set(range(-20, 21))
        if selection == "主胜":
            return {value for value in margins if value > 0}
        if selection == "平局":
            return {0}
        if selection == "客胜":
            return {value for value in margins if value < 0}
        if selection not in {"让胜", "让平", "让负"}:
            return set()
        handicap = _number(
            (source.get("sporttery_handicap") or {}).get("value")
        )
        if handicap is None:
            return set()
        if selection == "让胜":
            return {
                value for value in margins if value + handicap > 0
            }
        if selection == "让平":
            return {
                value for value in margins if value + handicap == 0
            }
        return {value for value in margins if value + handicap < 0}

    @classmethod
    def _single_direction_matches_anchor(
        cls,
        source: Dict[str, Any],
        anchor: str,
        candidate: str,
    ) -> bool:
        """Whether two options express nested, rather than opposing, paths."""
        anchor_support = cls._selection_margin_support(source, anchor)
        candidate_support = cls._selection_margin_support(source, candidate)
        if not anchor_support or not candidate_support:
            return anchor == candidate
        return bool(
            anchor_support.issubset(candidate_support)
            or candidate_support.issubset(anchor_support)
        )

    @classmethod
    def _align_single_with_two_option(
        cls,
        item: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Cancel an independent single that opposes the pair's lead leg.

        The pair still maximises same-market coverage.  Only the single is
        constrained: a nested result in either market may remain, while an
        opposing result is removed instead of being replaced by a weaker pick.
        """
        row = dict(item or {})
        analysis = dict(row.get("analysis") or {})
        profile = dict(analysis.get("single_probability_profile") or {})
        pair = dict(analysis.get("two_option_recommendation") or {})
        selections = [
            str(value) for value in pair.get("selections") or []
            if str(value) in TWO_OPTION_PLAY_SELECTIONS
        ]
        if not pair.get("actionable") or not selections or not profile:
            row["analysis"] = analysis
            return row

        source = row.get("input_snapshot") or {}
        anchor = selections[0]
        current = str(
            profile.get("selection") or analysis.get("single_play") or "观望"
        )
        independent = str(
            profile.get("independent_selection") or current
        )
        candidates = [
            dict(candidate) for candidate in profile.get("candidates") or []
            if (
                str(candidate.get("selection") or "")
                in TWO_OPTION_PLAY_SELECTIONS
                and float(candidate.get("odds") or 0) >= SINGLE_MIN_ODDS
                and cls._single_direction_matches_anchor(
                    source,
                    anchor,
                    str(candidate.get("selection") or ""),
                )
            )
        ]
        candidates.sort(
            key=lambda candidate: (
                float(candidate.get("probability") or 0),
                float(candidate.get("market_probability") or 0),
                -float(candidate.get("odds") or 99),
            ),
            reverse=True,
        )
        conflict = bool(
            independent in TWO_OPTION_PLAY_SELECTIONS
            and not cls._single_direction_matches_anchor(
                source, anchor, independent
            )
        )
        effective = "观望" if conflict else current
        reason = (
            f"双选保持{' / '.join(selections)}；原单选{independent}与"
            f"双选主方向{anchor}冲突，取消该场单选且不强行替换"
            if conflict else
            f"双选保持{' / '.join(selections)}；单选{effective}与"
            f"双选主方向{anchor}一致，继续保留"
        )
        profile.setdefault("independent_selection", independent)
        profile["direction_alignment"] = {
            "applied": True,
            "changed": conflict,
            "cancelled": conflict,
            "policy": "conflict-veto",
            "pair_selections": selections,
            "anchor_selection": anchor,
            "independent_selection": independent,
            "effective_selection": effective,
            "compatible_selections": [
                candidate.get("selection") for candidate in candidates
            ],
            "reason": reason,
        }
        profile["reason"] = reason
        if conflict:
            profile.update({
                "selection": "观望",
                "secondary_selection": "观望",
                "market": None,
                "odds": None,
                "secondary_odds": None,
                "probability": None,
                "secondary_probability": None,
                "model_probability": None,
                "market_probability": None,
            })
            analysis.update({
                "single_play": "观望",
                "single_secondary_play": "观望",
                "single_odds": None,
                "single_secondary_odds": None,
                "single_probability": None,
                "single_secondary_probability": None,
            })
        analysis["single_probability_profile"] = profile
        row["analysis"] = analysis
        return row

    @classmethod
    def _official_bet_profile(
        cls,
        source: Dict[str, Any],
        analysis: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build the independent formal-pool gate for the all-match single.

        ``single_play`` answers which priced result is most likely after the
        1.50 odds floor.  It does not answer whether that result deserves a
        stake.  The formal pool adds value, market-consistency and data-risk
        gates without hiding the underlying direction when they fail.
        """
        single = analysis.get("single_probability_profile") or {}
        selection = str(
            single.get("selection")
            or analysis.get("single_play")
            or "观望"
        )
        candidates = [
            dict(item) for item in single.get("candidates") or []
            if str(item.get("selection") or "")
            in TWO_OPTION_PLAY_SELECTIONS
        ]
        candidate = next((
            item for item in candidates
            if str(item.get("selection") or "") == selection
        ), {})
        value = cls._play_value_profile(source, selection)
        odds = _number(candidate.get("odds"))
        if odds is None:
            odds = _number(value.get("odds"))
        probability = _number(candidate.get("probability"))
        model_probability = _number(candidate.get("model_probability"))
        if model_probability is None:
            model_probability = _number(value.get("probability"))
        market_probability = _number(candidate.get("market_probability"))
        if market_probability is None:
            market_probability = _number(
                value.get("market_implied_probability")
            )
        model_expected_return = (
            model_probability / 100 * odds
            if model_probability is not None and odds is not None else None
        )
        model_market_edge = (
            model_probability - market_probability
            if model_probability is not None
            and market_probability is not None else None
        )
        confidence = _number(
            (analysis.get("market_confidence") or {}).get("score")
        ) or 0.0
        value_score = _number(value.get("value_score")) or 0.0
        bet_score = _number(value.get("bet_score"))
        if bet_score is None:
            bet_score = _number(value.get("score")) or 0.0
        model_rating = _number(analysis.get("model_rating")) or 0.0
        warnings = [str(item) for item in source.get("data_warnings") or []]
        risk = (source.get("fae_core") or {}).get("risk") or {}
        current_asian = (source.get("asian") or {}).get("current") or []
        waters = [
            _number(current_asian[index])
            for index in (0, 2) if len(current_asian) > index
        ]
        extreme_water = any(
            item is not None and (item < 0.60 or item > 1.25)
            for item in waters
        )
        severe_data_risk = bool(
            risk.get("dangerous")
            or extreme_water
            or any("跳至" in item or "跳档" in item for item in warnings)
            or (analysis.get("non_cover_guard") or {}).get("force_no_bet")
        )
        short_favorite_guard = single.get("short_favorite_guard") or {}
        upset_warning = source.get("upset_warning_model") or {}
        upset_score = _number(upset_warning.get("score")) or 0.0
        favorite_side = str(upset_warning.get("favorite_side") or "")
        favorite_selection = {
            "home": "主胜",
            "away": "客胜",
        }.get(favorite_side)
        high_upset_favorite_conflict = bool(
            upset_score >= 75
            and favorite_selection
            and selection == favorite_selection
        )
        reasons = []
        if selection not in TWO_OPTION_PLAY_SELECTIONS:
            reasons.append("没有形成可结算的结果玩法方向")
        if odds is None or odds < SINGLE_MIN_ODDS:
            reasons.append(f"赔率低于{SINGLE_MIN_ODDS:.2f}正式池下限")
        if probability is None or probability < OFFICIAL_SINGLE_MIN_PROBABILITY:
            reasons.append(
                "融合概率低于"
                f"{OFFICIAL_SINGLE_MIN_PROBABILITY:g}%正式池门槛"
            )
        if confidence < OFFICIAL_SINGLE_MIN_MARKET_CONFIDENCE:
            reasons.append(
                f"盘口可信度低于{OFFICIAL_SINGLE_MIN_MARKET_CONFIDENCE:g}分"
            )
        if (
            model_expected_return is None
            or model_expected_return
            < OFFICIAL_SINGLE_MIN_MODEL_EXPECTED_RETURN
        ):
            reasons.append(
                "模型赔率期望低于"
                f"{OFFICIAL_SINGLE_MIN_MODEL_EXPECTED_RETURN:.2f}"
            )
        if (
            model_market_edge is None
            or model_market_edge < OFFICIAL_SINGLE_MIN_MODEL_MARKET_EDGE
        ):
            reasons.append(
                "模型概率相对市场低于"
                f"{OFFICIAL_SINGLE_MIN_MODEL_MARKET_EDGE:+g}个百分点"
            )
        if value_score < OFFICIAL_SINGLE_MIN_VALUE_SCORE:
            reasons.append(
                f"价值指数低于{OFFICIAL_SINGLE_MIN_VALUE_SCORE:g}分"
            )
        if bet_score < OFFICIAL_SINGLE_MIN_BET_SCORE:
            reasons.append(
                f"投注分低于{OFFICIAL_SINGLE_MIN_BET_SCORE:g}分"
            )
        if model_rating < OFFICIAL_SINGLE_MIN_MODEL_RATING:
            reasons.append(
                f"大模型原始评级低于{OFFICIAL_SINGLE_MIN_MODEL_RATING:g}星"
            )
        if short_favorite_guard.get("triggered"):
            reasons.append("低赔热门替代方向未通过独立穿盘确认")
        if high_upset_favorite_conflict:
            reasons.append("普通胜负热门方向与75分以上防冷预警冲突")
        if severe_data_risk:
            reasons.append("存在危险盘口、异常跳档或极端水位")

        eligible = not reasons
        rank_score = (
            float(probability or 0) * 0.42
            + confidence * 0.18
            + value_score * 0.14
            + float(bet_score or 0) * 0.10
            + max(-5.0, min(8.0, float(model_market_edge or 0))) * 1.0
            + max(
                -6.0,
                min(8.0, (float(model_expected_return or 0) - 1) * 100),
            ) * 0.8
        )
        return {
            "actionable": eligible,
            "qualified_before_daily_limit": eligible,
            "selection": selection,
            "market": candidate.get("market"),
            "odds": round(odds, 3) if odds is not None else None,
            "probability": (
                round(probability, 2) if probability is not None else None
            ),
            "model_probability": (
                round(model_probability, 2)
                if model_probability is not None else None
            ),
            "market_probability": (
                round(market_probability, 2)
                if market_probability is not None else None
            ),
            "model_market_edge": (
                round(model_market_edge, 2)
                if model_market_edge is not None else None
            ),
            "model_expected_return": (
                round(model_expected_return, 3)
                if model_expected_return is not None else None
            ),
            "value_score": round(value_score, 1),
            "bet_score": round(float(bet_score or 0), 1),
            "market_confidence": round(confidence, 1),
            "model_rating": round(model_rating, 1),
            "rank_score": round(rank_score, 2),
            "severe_data_risk": severe_data_risk,
            "upset_warning_score": round(upset_score, 1),
            "high_upset_favorite_conflict": high_upset_favorite_conflict,
            "reason": (
                "同时通过融合概率、赔率价值、盘口可信度和风险门槛"
                if eligible else "；".join(dict.fromkeys(reasons))
            ),
        }

    @classmethod
    def apply_official_bet_recommendations(
        cls,
        matches: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Select formal receiving-side parlays from two-option coverage."""
        two_option_profiles_available = any(
            bool((item.get("analysis") or {}).get(
                "two_option_recommendation"
            ))
            for item in matches or []
        )
        if two_option_profiles_available:
            return cls._apply_receiving_two_option_parlays(matches)
        profit_policy_active = any(
            bool(
                ((((item.get("input_snapshot") or {}).get(
                    "supervised_shadow"
                ) or {}).get("profit_single") or {}).get("policy_active"))
            )
            for item in matches or []
        )
        daily_limit = (
            1 if profit_policy_active else OFFICIAL_SINGLE_DAILY_LIMIT
        )
        rows = []
        eligible = []
        for index, item in enumerate(matches):
            row = dict(item or {})
            analysis = dict(row.get("analysis") or {})
            analysis_source = str(row.get("analysis_source") or "")
            ai_verified = analysis_source == "volcengine-ark"
            if profit_policy_active:
                shadow = (
                    (row.get("input_snapshot") or {}).get(
                        "supervised_shadow"
                    ) or {}
                )
                candidate = dict(shadow.get("profit_single") or {})
                candidate_active = bool(
                    candidate.get("actionable_before_daily_limit")
                )
                probability = _number(candidate.get("probability"))
                odds = _number(candidate.get("odds"))
                model_probability = _number(
                    candidate.get("model_probability")
                )
                market_probability = _number(
                    candidate.get("market_probability")
                )
                market_gap = _number(
                    candidate.get("model_market_gap_pp")
                ) or 0.0
                value_edge = _number(candidate.get("value_edge"))
                profile = {
                    "actionable": candidate_active,
                    "qualified_before_daily_limit": candidate_active,
                    "selection": candidate.get("selection"),
                    "market": candidate.get("market"),
                    "odds": round(odds, 3)
                    if odds is not None else None,
                    "probability": round(probability, 2)
                    if probability is not None else None,
                    "model_probability": round(model_probability, 2)
                    if model_probability is not None else None,
                    "market_probability": round(market_probability, 2)
                    if market_probability is not None else None,
                    "model_market_edge": candidate.get("market_edge_pp"),
                    "model_expected_return": (
                        round(probability / 100.0 * odds, 3)
                        if probability is not None and odds is not None
                        else None
                    ),
                    "value_score": round(value_edge, 1)
                    if value_edge is not None else None,
                    "bet_score": round(market_gap, 1),
                    "market_confidence": round(market_gap, 1),
                    "model_rating": None,
                    "rank_score": round(market_gap, 2),
                    "model_market_gap_pp": round(market_gap, 2),
                    "market_direction_agreement": bool(
                        candidate.get("market_direction_agreement")
                    ),
                    "strategy_version": candidate.get("policy_version"),
                    "strategy_source": "fae-supervised-profit-policy",
                    "ai_verified": ai_verified,
                    "analysis_source": (
                        analysis_source or "fae-supervised"
                    ),
                    "reason": (
                        "滚动样本外盈利策略：竞彩让球模型与市场"
                        "第一方向一致，全日按同市场领先分差取第一场"
                        if candidate_active else str(
                            candidate.get("reason")
                            or "未达到盈利单选候选条件"
                        )
                    ),
                }
            else:
                profile = cls._official_bet_profile(
                    row.get("input_snapshot") or {}, analysis
                )
                profile["ai_verified"] = ai_verified
                profile["analysis_source"] = (
                    analysis_source or "legacy"
                )
                if profile.get("actionable") and not ai_verified:
                    profile["actionable"] = False
                    profile["qualified_before_daily_limit"] = False
                    profile["reason"] = (
                        str(profile.get("reason") or "")
                        + "；本场不是火山大模型研判，不能进入正式投注池"
                    ).strip("；")
            analysis["official_bet_recommendation"] = profile
            row["analysis"] = analysis
            rows.append(row)
            if profile.get("actionable"):
                eligible.append((
                    float(profile.get("rank_score") or 0), index
                ))

        selected_order = [
            index for _, index in sorted(eligible, reverse=True)
        ][:daily_limit]
        selected = set(selected_order)
        rank_by_index = {
            index: rank
            for rank, index in enumerate(selected_order, start=1)
        }
        for index, row in enumerate(rows):
            analysis = row["analysis"]
            profile = dict(
                analysis.get("official_bet_recommendation") or {}
            )
            was_eligible = bool(profile.get("actionable"))
            shortlisted = index in selected
            profile["actionable"] = shortlisted
            profile["daily_rank"] = rank_by_index.get(index)
            profile["recommendation_level"] = (
                "official" if shortlisted else "direction"
            )
            if was_eligible and not shortlisted:
                profile["reason"] = (
                    str(profile.get("reason") or "")
                    + f"；全日正式投注池仅保留前{daily_limit}场"
                ).strip("；")
            analysis["official_bet_recommendation"] = profile
            row["analysis"] = analysis
        return rows

    @classmethod
    def _one_goal_margin_parlay_signal(
        cls,
        source: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Identify a narrow low-total path to an exact one-goal win.

        A low total alone is not a draw signal.  When the Euro favourite is
        still aligned with a Sporttery +/-1 line, the Asian handicap remains
        between a quarter and three-quarter ball, and the total is no higher
        than 2.5, the useful split is usually ``让平`` versus the protected
        side rather than ordinary draw versus favourite win.  The signal is
        deliberately narrow so a truly deepening/high-total favourite is not
        reclassified as an exact-margin pick.
        """
        handicap = _number(
            (source.get("sporttery_handicap") or {}).get("value")
        )
        if handicap is None or abs(handicap) != 1:
            return {"triggered": False}

        favorite = cls._favorite_market_profile(source)
        favorite_side = str(favorite.get("side") or "")
        favorite_odds = _number(favorite.get("odds"))
        aligned = (
            (favorite_side == "home" and handicap < 0)
            or (favorite_side == "away" and handicap > 0)
        )
        if not aligned or favorite_odds is None or favorite_odds > 2.30:
            return {"triggered": False}

        asian = cls._asian_favorite_depth_profile(source, favorite_side)
        current_depth = _number(asian.get("current_depth"))
        line_change = _number(asian.get("line_change"))
        favorite_water = _number(asian.get("current_favorite_water"))
        total = cls._total_market_profile(source)
        total_line = _number(total.get("line"))
        if (
            current_depth is None
            or not 0.25 <= current_depth <= 0.75
            or total_line is None
            or total_line > 2.50
            or (line_change is not None and line_change > 0.01)
            or (favorite_water is not None and favorite_water > 1.08)
        ):
            return {"triggered": False}

        return {
            "triggered": True,
            "selection": "让平",
            "favorite_side": favorite_side,
            "favorite_odds": round(favorite_odds, 3),
            "asian_depth": round(current_depth, 3),
            "asian_line_change": (
                round(line_change, 3) if line_change is not None else None
            ),
            "favorite_water": (
                round(favorite_water, 3)
                if favorite_water is not None else None
            ),
            "total_line": round(total_line, 3),
            "probability_adjustment_pp": 3.0,
            "score_bonus": 14.0,
            "reason": (
                f"一球差分流：竞彩让球{int(handicap):+d}、亚盘热门深度"
                f"{current_depth:g}球、大小球{total_line:g}；低总球只压低"
                "比分，热门仍与让球方向一致，优先评估刚好赢1球"
            ),
        }

    @classmethod
    def _deep_cover_parlay_signal(
        cls,
        source: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Recognise a genuinely deepening favourite that should cover."""
        handicap = _number(
            (source.get("sporttery_handicap") or {}).get("value")
        )
        if handicap is None or abs(handicap) < 1:
            return {"triggered": False}
        favorite = cls._favorite_market_profile(source)
        favorite_side = str(favorite.get("side") or "")
        favorite_odds = _number(favorite.get("odds"))
        aligned = (
            (favorite_side == "home" and handicap < 0)
            or (favorite_side == "away" and handicap > 0)
        )
        if not aligned or favorite_odds is None or favorite_odds > 1.45:
            return {"triggered": False}

        asian = cls._asian_favorite_depth_profile(source, favorite_side)
        current_depth = _number(asian.get("current_depth"))
        line_change = _number(asian.get("line_change"))
        total = cls._total_market_profile(source)
        total_line = _number(total.get("line"))
        if (
            current_depth is None
            or current_depth < abs(handicap) + 0.24
            or line_change is None
            or line_change < 0.24
            or total_line is None
            or total_line < 3.0
        ):
            return {"triggered": False}
        selection = "让胜" if favorite_side == "home" else "让负"
        return {
            "triggered": True,
            "selection": selection,
            "favorite_side": favorite_side,
            "favorite_odds": round(favorite_odds, 3),
            "asian_depth": round(current_depth, 3),
            "asian_line_change": round(line_change, 3),
            "total_line": round(total_line, 3),
            "reason": (
                f"穿盘分流：热门胜赔{favorite_odds:g}、亚盘真实升深至"
                f"{current_depth:g}球、大小球{total_line:g}，优先{selection}"
            ),
        }

    @classmethod
    def _two_option_parlay_selection(
        cls,
        row: Dict[str, Any],
        profile: Dict[str, Any],
        handicap: float,
    ) -> Dict[str, Any]:
        """Choose one leg from a two-option profile using market evidence."""
        analysis = row.get("analysis") or {}
        selections = {
            str(value) for value in profile.get("selections") or []
        }

        # A triggered guard is an explicit correction of the model pick and
        # must outrank every later heuristic.  This prevents a demoted
        # handicap draw from being reintroduced by the parlay builder.
        guard_selection = None
        guard_reason = None
        for key in (
            "consistency_guard",
            "directional_precision_guard",
            "non_cover_guard",
        ):
            guard = analysis.get(key) or {}
            if guard.get("triggered"):
                guard_selection = str(
                    guard.get("effective_selection") or ""
                )
                guard_reason = str(guard.get("reason") or "")
        if guard_selection:
            return {
                "eligible": guard_selection in selections,
                "selection": guard_selection,
                "basis": "guardrail",
                "reason": guard_reason or "使用护栏后的最终方向",
            }

        source = row.get("input_snapshot") or {}
        deep_cover = cls._deep_cover_parlay_signal(source)
        if deep_cover.get("triggered"):
            selection = str(deep_cover.get("selection") or "")
            if selection in selections:
                return {
                    "eligible": True,
                    "selection": selection,
                    "basis": "deep-cover",
                    "reason": deep_cover.get("reason"),
                    "deep_cover_signal": deep_cover,
                }

        one_goal = cls._one_goal_margin_parlay_signal(source)
        if one_goal.get("triggered") and "让平" in selections:
            return {
                "eligible": True,
                "selection": "让平",
                "basis": "one-goal-margin",
                "reason": one_goal.get("reason"),
                "one_goal_margin_signal": one_goal,
            }

        primary = str(analysis.get("primary_play") or "")
        if primary in selections:
            return {
                "eligible": True,
                "selection": primary,
                "basis": "analysis-primary",
                "reason": "使用逐场研判的最终主选",
            }

        receiving = "让负" if handicap < 0 else "让胜"
        return {
            "eligible": receiving in selections,
            "selection": receiving,
            "basis": "receiving-fallback",
            "reason": "缺少更强分差证据，回退到双选中的受让保护方向",
        }

    @classmethod
    def _apply_receiving_two_option_parlays(
        cls,
        matches: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Publish guard-aligned two-leg tickets from two-option coverage.

        The original policy always extracted the receiving-side outcome.  It
        preserved coverage but discarded the exact-margin information that
        made the second option useful.  Version 2 keeps the same model ranking
        and session split, while selecting each leg in this order: triggered
        guard, genuine deep-cover signal, narrow one-goal signal, final match
        primary, receiving-side fallback.  Odds still never reorder matches.
        """
        rows = [dict(item or {}) for item in matches or []]
        policy_version = "two-option-evidence-parlay-v2"
        strategy_source = "fae-two-option-receiving-parlay"
        candidates_by_session: Dict[str, List[Dict[str, Any]]] = {}

        def session_for(row: Dict[str, Any]) -> str:
            match_number = str(row.get("match_number") or "")
            owner_date = str(row.get("owner_date") or "")[:10]
            weekend = match_number.startswith(("周六", "周日"))
            if owner_date:
                try:
                    weekend = datetime.strptime(
                        owner_date, "%Y-%m-%d"
                    ).weekday() >= 5
                except ValueError:
                    pass
            if not weekend:
                return "全日"

            match_time = str(row.get("match_time") or "")
            dated = re.search(
                r"(?:^|\s)(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})",
                match_time,
            )
            if dated:
                month, day, hour, minute = map(int, dated.groups())
                if owner_date:
                    try:
                        base = datetime.strptime(owner_date, "%Y-%m-%d")
                        if (month, day) != (base.month, base.day):
                            return "晚场"
                    except ValueError:
                        pass
                return "早场" if hour * 60 + minute < 21 * 60 else "晚场"
            timed = re.search(r"(?:^|\s|T)(\d{1,2}):(\d{2})", match_time)
            if timed:
                hour, minute = map(int, timed.groups())
                return "早场" if hour * 60 + minute < 21 * 60 else "晚场"
            return "晚场"

        for index, row in enumerate(rows):
            analysis = dict(row.get("analysis") or {})
            row["analysis"] = analysis
            profile = dict(analysis.get("two_option_recommendation") or {})
            handicap = _number(
                ((row.get("input_snapshot") or {}).get(
                    "sporttery_handicap"
                ) or {}).get("value")
            )
            if profile.get("market") != "竞彩让球" or not handicap:
                continue
            selection_profile = cls._two_option_parlay_selection(
                row, profile, handicap
            )
            selection = str(selection_profile.get("selection") or "")
            if not selection_profile.get("eligible") or not selection:
                continue
            current = (
                ((row.get("input_snapshot") or {}).get(
                    "sporttery_handicap"
                ) or {}).get("current") or []
            )
            odds_index = {"让胜": 0, "让平": 1, "让负": 2}.get(selection)
            fallback_odds = (
                current[odds_index]
                if odds_index is not None and len(current) > odds_index
                else None
            )
            odds = _number((profile.get("odds") or {}).get(selection))
            if odds is None:
                odds = _number(fallback_odds)
            rank_score = _number(profile.get("rank_score"))
            if odds is None or odds <= 1 or rank_score is None:
                continue
            session = session_for(row)
            candidates_by_session.setdefault(session, []).append({
                "index": index,
                "selection": selection,
                "odds": odds,
                "rank_score": rank_score,
                "coverage_score": _number(profile.get("coverage_score")) or 0,
                "pair_value_score": _number(
                    profile.get("pair_value_score")
                ),
                "market_confidence": _number(
                    profile.get("market_confidence")
                ),
                "session": session,
                "selection_basis": selection_profile.get("basis"),
                "selection_reason": selection_profile.get("reason"),
                "one_goal_margin_signal": selection_profile.get(
                    "one_goal_margin_signal"
                ),
                "deep_cover_signal": selection_profile.get(
                    "deep_cover_signal"
                ),
            })

        selected: Dict[int, Dict[str, Any]] = {}
        global_rank = 0
        for session in ("早场", "晚场", "全日"):
            candidates = sorted(
                candidates_by_session.get(session) or [],
                key=lambda item: (
                    -float(item.get("rank_score") or 0),
                    -float(item.get("coverage_score") or 0),
                    str(rows[item["index"]].get("match_time") or ""),
                ),
            )[:2]
            if len(candidates) < 2:
                continue
            combined_odds = float(candidates[0]["odds"]) * float(
                candidates[1]["odds"]
            )
            ticket_id = (
                "formal-receiving-"
                + session
                + "-"
                + "-".join(sorted(
                    str(rows[item["index"]].get("match_id") or item["index"])
                    for item in candidates
                ))
            )
            for leg_rank, candidate in enumerate(candidates, 1):
                global_rank += 1
                selected[candidate["index"]] = {
                    **candidate,
                    "parlay_role": f"{session}第{leg_rank}腿",
                    "daily_rank": global_rank,
                    "ticket_id": ticket_id,
                    "combined_odds": combined_odds,
                }

        for index, row in enumerate(rows):
            analysis = row["analysis"]
            analysis_source = str(row.get("analysis_source") or "")
            if index not in selected:
                profile = {
                    "actionable": False,
                    "qualified_before_daily_limit": False,
                    "selection": None,
                    "market": None,
                    "odds": None,
                    "daily_rank": None,
                    "recommendation_level": "direction",
                    "strategy_version": policy_version,
                    "strategy_source": strategy_source,
                    "parlay_role": None,
                    "ticket_id": None,
                    "combined_odds": None,
                    "ai_verified": analysis_source == "volcengine-ark",
                    "analysis_source": (
                        analysis_source or "fae-two-option"
                    ),
                    "reason": "未进入所在时段受让方模型排名前二",
                }
            else:
                candidate = selected[index]
                odds = float(candidate["odds"])
                role = str(candidate["parlay_role"])
                combined_odds = float(candidate["combined_odds"])
                profile = {
                    "actionable": True,
                    "qualified_before_daily_limit": True,
                    "selection": candidate.get("selection"),
                    "market": "竞彩让球",
                    "odds": round(odds, 3),
                    "probability": None,
                    "model_probability": None,
                    "market_probability": None,
                    "model_market_edge": None,
                    "model_expected_return": None,
                    "value_score": candidate.get("pair_value_score"),
                    "bet_score": round(float(candidate["rank_score"]), 2),
                    "market_confidence": candidate.get("market_confidence"),
                    "model_rating": None,
                    "rank_score": round(float(candidate["rank_score"]), 2),
                    "coverage_score": round(
                        float(candidate["coverage_score"]), 2
                    ),
                    "strategy_version": policy_version,
                    "strategy_source": strategy_source,
                    "parlay_role": role,
                    "ticket_id": candidate["ticket_id"],
                    "combined_odds": round(combined_odds, 3),
                    "daily_rank": candidate["daily_rank"],
                    "recommendation_level": "official",
                    "ai_verified": analysis_source == "volcengine-ark",
                    "analysis_source": (
                        analysis_source or "fae-two-option"
                    ),
                    "reason": (
                        f"正式证据对齐二串一{role}：双选覆盖包含"
                        f"{candidate.get('selection')}，选择依据为"
                        f"{candidate.get('selection_reason') or '模型最终方向'}；"
                        f"按所在时段模型分排名前二，合计{combined_odds:.2f}倍"
                    ),
                    "selection_basis": candidate.get("selection_basis"),
                    "one_goal_margin_signal": candidate.get(
                        "one_goal_margin_signal"
                    ),
                    "deep_cover_signal": candidate.get(
                        "deep_cover_signal"
                    ),
                }
            analysis["official_bet_recommendation"] = profile
            row["analysis"] = analysis
        return rows

    @classmethod
    def apply_high_confidence_single_recommendations(
        cls,
        matches: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Publish only OOS-validated singles aligned with the Ark decision."""
        rows = []
        eligible = []
        for index, item in enumerate(matches):
            row = dict(item or {})
            analysis = dict(row.get("analysis") or {})
            shadow = (
                (row.get("input_snapshot") or {}).get(
                    "supervised_shadow"
                ) or {}
            )
            shadow_profile = dict(
                shadow.get("high_confidence_single") or {}
            )
            official = analysis.get("official_bet_recommendation") or {}
            selection = str(official.get("selection") or "")
            candidate = next((
                dict(value)
                for value in shadow_profile.get("candidates") or []
                if str(value.get("selection") or "") == selection
            ), {})
            source_profile = {
                **candidate,
                "policy_active": shadow_profile.get("policy_active"),
                "policy_status": shadow_profile.get("policy_status"),
                "minimum_probability": shadow_profile.get(
                    "minimum_probability"
                ),
                "minimum_gap_pp": shadow_profile.get("minimum_gap_pp"),
                "minimum_odds": shadow_profile.get("minimum_odds"),
                "maximum_odds": shadow_profile.get("maximum_odds"),
            }
            ai_selection = str(analysis.get("single_play") or "")
            analysis_source = str(row.get("analysis_source") or "")
            reasons = []
            if not source_profile.get("policy_active"):
                reasons.append("高命中单选仍在独立样本外验证")
            probability = _number(source_profile.get("probability"))
            minimum_probability = _number(
                source_profile.get("minimum_probability")
            ) or 58.0
            gap = _number(source_profile.get("model_market_gap_pp"))
            minimum_gap = _number(
                source_profile.get("minimum_gap_pp")
            )
            if minimum_gap is None:
                minimum_gap = 6.0
            odds = _number(source_profile.get("odds"))
            minimum_odds = _number(source_profile.get("minimum_odds")) or 1.5
            maximum_odds = _number(source_profile.get("maximum_odds")) or 2.2
            if probability is None or probability < minimum_probability:
                reasons.append(
                    f"候选命中概率低于{minimum_probability:g}%"
                )
            if gap is None or gap < minimum_gap:
                reasons.append(
                    f"候选领先优势低于{minimum_gap:g}个百分点"
                )
            if odds is None or not minimum_odds <= odds <= maximum_odds:
                reasons.append(
                    f"赔率不在{minimum_odds:g}-{maximum_odds:g}区间"
                )
            if int(source_profile.get("market_rank") or 99) != 1:
                reasons.append("不是同市场赔率第一方向")
            if not (shadow.get("quality") or {}).get("complete"):
                reasons.append("欧赔、亚盘、竞彩让球或大小球数据不完整")
            if analysis_source != "volcengine-ark":
                reasons.append("本场不是火山大模型研判")
            if selection not in TWO_OPTION_PLAY_SELECTIONS:
                reasons.append("正式池没有形成可结算单选")
            elif ai_selection != selection:
                reasons.append(
                    f"正式池{selection}与火山单选"
                    f"{ai_selection or '观望'}不一致"
                )
            if not official.get("actionable"):
                reasons.append("未通过现有正式池的价值与盘口可信度门槛")
            direction = (
                (analysis.get("single_probability_profile") or {}).get(
                    "direction_alignment"
                ) or {}
            )
            if direction.get("cancelled"):
                reasons.append("单选与双选主方向冲突，已执行取消策略")
            actionable = not reasons
            profile = {
                **source_profile,
                "actionable": actionable,
                "qualified_before_daily_limit": actionable,
                "ai_selection": ai_selection or "观望",
                "ai_verified": analysis_source == "volcengine-ark",
                "analysis_source": analysis_source or "legacy",
                "rank_score": round(
                    float(source_profile.get("ranking_probability") or 0)
                    + float(source_profile.get("model_market_gap_pp") or 0)
                    * 0.25
                    + max(
                        -10.0,
                        min(
                            10.0,
                            float(source_profile.get("value_edge") or 0),
                        ),
                    ) * 0.1,
                    2,
                ),
                "reason": (
                    "候选命中模型、市场方向与火山正式单选一致，且历史发布门禁已通过"
                    if actionable else "；".join(dict.fromkeys(reasons))
                ),
            }
            analysis["high_confidence_single_recommendation"] = profile
            row["analysis"] = analysis
            rows.append(row)
            if actionable:
                eligible.append((float(profile["rank_score"]), index))

        selected_order = [
            index for _, index in sorted(eligible, reverse=True)
        ][:HIGH_CONFIDENCE_SINGLE_DAILY_LIMIT]
        selected = set(selected_order)
        rank_by_index = {
            index: rank
            for rank, index in enumerate(selected_order, start=1)
        }
        for index, row in enumerate(rows):
            analysis = row["analysis"]
            profile = dict(
                analysis.get("high_confidence_single_recommendation") or {}
            )
            was_eligible = bool(profile.get("actionable"))
            profile["actionable"] = index in selected
            profile["daily_rank"] = rank_by_index.get(index)
            if was_eligible and index not in selected:
                profile["reason"] = (
                    str(profile.get("reason") or "")
                    + "；全日高命中单选只保留前2场"
                ).strip("；")
            analysis["high_confidence_single_recommendation"] = profile
            row["analysis"] = analysis
        return rows

    @classmethod
    def _compatible_handicap_selections(
        cls,
        source: Dict[str, Any],
        primary_play: str,
    ) -> set[str]:
        """Return handicap outcomes that can coexist with the 1X2 primary."""
        if primary_play in {"让胜", "让平", "让负"}:
            return {primary_play}
        goal_differences = {
            "主胜": range(1, 11),
            "平局": (0,),
            "客胜": range(-10, 0),
        }.get(primary_play)
        handicap = _number(
            (source.get("sporttery_handicap") or {}).get("value")
        )
        if goal_differences is None or handicap is None:
            return {"让胜", "让平", "让负"}
        compatible = set()
        for difference in goal_differences:
            adjusted = difference + handicap
            compatible.add(
                "让胜" if adjusted > 0
                else "让负" if adjusted < 0
                else "让平"
            )
        return compatible

    @classmethod
    def _handicap_play(
        cls,
        source: Dict[str, Any],
        primary_play: str = "",
    ) -> str:
        hhad = (
            (((source.get("fae_core") or {}).get("probabilities") or {})
             .get("hhad") or {})
        )
        compatible = cls._compatible_handicap_selections(
            source, primary_play
        )
        odds_values = (
            (source.get("sporttery_handicap") or {}).get("current")
            or (source.get("sporttery_handicap") or {}).get("initial")
            or []
        )
        candidates = []
        for index, (label, key) in enumerate((
            ("让胜", "win"),
            ("让平", "draw"),
            ("让负", "lose"),
        )):
            if label not in compatible:
                continue
            profile = cls._play_value_profile(source, label)
            probability = _number(profile.get("probability"))
            if probability is None:
                probability = _number(hhad.get(key))
            candidates.append((
                label,
                probability,
                _number(odds_values[index])
                if len(odds_values) > index else None,
            ))
        valid = [item for item in candidates if item[1] is not None]
        return max(
            valid,
            key=lambda item: (
                item[1],
                item[1] * item[2] if item[2] is not None else 0,
            ),
        )[0] if valid else "观望"

    @classmethod
    def _low_odds_asian_adjusted_profile(
        cls,
        source: Dict[str, Any],
        profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Apply the empirical short-favorite Asian/H-HAD calibration."""
        result = dict(profile or {})
        label = str(result.get("label") or "")
        if label not in {"让胜", "让平", "让负"}:
            return result
        if (result.get("low_odds_asian_calibration") or {}).get("applied"):
            return result
        model = source.get("low_odds_asian_model") or {}
        adjustments = model.get("adjustment_pp") or {}
        if not model.get("available") or not model.get("matched"):
            return result
        base_probability = _number(result.get("probability"))
        adjustment = _number(adjustments.get(label))
        if base_probability is None or adjustment is None:
            return result

        categories = (
            (((source.get("fae_core") or {}).get("recommendation") or {})
             .get("category_scores") or [])
        )
        base_probabilities = {
            str(item.get("label") or ""): _number(item.get("probability"))
            for item in categories
            if str(item.get("label") or "") in {"让胜", "让平", "让负"}
        }
        calibrated_probability = base_probability + adjustment
        if all(base_probabilities.get(key) is not None for key in (
            "让胜", "让平", "让负"
        )):
            base_total = sum(
                float(base_probabilities[key])
                for key in ("让胜", "让平", "让负")
            )
            shifted = {
                key: max(
                    0.1,
                    float(base_probabilities[key])
                    + float(_number(adjustments.get(key)) or 0),
                )
                for key in ("让胜", "让平", "让负")
            }
            shifted_total = sum(shifted.values())
            normalized = {
                key: value / shifted_total * base_total
                for key, value in shifted.items()
            }
            deltas = {
                key: normalized[key] - float(base_probabilities[key])
                for key in normalized
            }
            max_delta = max(abs(value) for value in deltas.values())
            scale = min(1.0, 4.0 / max_delta) if max_delta else 1.0
            calibrated_probability = (
                float(base_probabilities[label]) + deltas[label] * scale
            )
        calibrated_probability = round(
            max(
                0.1,
                min(
                    99.0,
                    base_probability + max(
                        -4.0,
                        min(4.0, calibrated_probability - base_probability),
                    ),
                ),
            ),
            2,
        )

        odds = _number(result.get("odds"))
        market_probability = _number(
            result.get("market_implied_probability")
        )
        expected_return = (
            calibrated_probability / 100 * odds
            if odds is not None and odds > 1 else None
        )
        value_edge = (
            calibrated_probability - market_probability
            if market_probability is not None else None
        )
        value_score = (
            round(55 + value_edge * 1.8 + (expected_return - 1) * 30)
            if value_edge is not None and expected_return is not None else 38
        )
        value_score = max(0, min(99, value_score))
        market_confidence = (
            (((source.get("fae_core") or {}).get("recommendation") or {})
             .get("market_confidence") or {})
        )
        confidence_score = float(market_confidence.get("score") or 50)
        prediction_score = float(result.get("prediction_score") or 50)
        bet_score = round(
            value_score * 0.55
            + confidence_score * 0.30
            + prediction_score * 0.15
        )
        bet_score = max(0, min(99, bet_score))
        generic_reasons = {
            "赔率价值不足",
            "综合投注分未达门槛",
            "低赔率亚盘校准后赔率价值不足",
            "低赔率亚盘校准后综合投注分未达门槛",
        }
        reasons = [
            str(reason) for reason in result.get("no_bet_reasons") or []
            if str(reason) not in generic_reasons
        ]
        if value_score < 52:
            reasons.append("低赔率亚盘校准后赔率价值不足")
        if bet_score < 55:
            reasons.append("低赔率亚盘校准后综合投注分未达门槛")
        result.update({
            "raw_probability": (
                result.get("raw_probability")
                if result.get("raw_probability") is not None
                else base_probability
            ),
            "probability": calibrated_probability,
            "value_probability": calibrated_probability,
            "value_edge": (
                round(value_edge, 2) if value_edge is not None else None
            ),
            "expected_return": (
                round(expected_return, 3)
                if expected_return is not None else None
            ),
            "value_score": value_score,
            "bet_score": bet_score,
            "score": bet_score,
            "stars": cls._rating(bet_score / 20),
            "no_bet_reasons": list(dict.fromkeys(reasons)),
            "no_bet": bool(reasons),
            "low_odds_asian_calibration": {
                "applied": True,
                "version": model.get("version"),
                "base_probability": base_probability,
                "calibrated_probability": calibrated_probability,
                "raw_adjustment_pp": adjustment,
                "effective_adjustment_pp": round(
                    calibrated_probability - base_probability, 2
                ),
                "favorite": model.get("favorite"),
                "asian": model.get("asian"),
                "signal_ids": [
                    item.get("key") for item in model.get("signals") or []
                    if item.get("key")
                ],
                "sample_basis": model.get("sample_basis"),
            },
        })
        return result

    @classmethod
    def _historical_adjusted_profile(
        cls,
        source: Dict[str, Any],
        profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Conservatively calibrate draw plays with similar finished matches."""
        result = cls._low_odds_asian_adjusted_profile(
            source, dict(profile or {})
        )
        policy = draw_selection_policy_profile(
            (source or {}).get("draw_selection_policy")
        )
        key = {
            "平局": "ordinary_draw",
            "让平": "handicap_draw",
        }.get(str(result.get("label") or ""))
        if not key:
            return result
        model = source.get("historical_goal_margin_model") or {}
        metric = model.get(key) or {}
        result["historical_goal_margin"] = metric
        if not metric.get("eligible_for_adjustment"):
            return result
        historical_probability = _number(
            metric.get("blended_probability")
        )
        core_probability = _number(result.get("probability"))
        if historical_probability is None or core_probability is None:
            return result

        credibility = min(
            0.45,
            max(0.15, 0.15 + float(metric.get("credibility_weight") or 0)),
        )
        calibrated_probability = (
            core_probability
            + (historical_probability - core_probability) * credibility
        )
        # Until the new calibration layer has enough settled, cross-day
        # evidence, history may strongly reduce an inflated estimate but may
        # raise it by at most two percentage points.
        calibrated_probability = round(
            min(calibrated_probability, core_probability + 2.0), 2
        )
        odds = _number(result.get("odds"))
        market_probability = _number(
            result.get("market_implied_probability")
        )
        expected_return = (
            calibrated_probability / 100 * odds
            if odds is not None and odds > 1 else None
        )
        value_edge = (
            calibrated_probability - market_probability
            if market_probability is not None else None
        )
        value_score = (
            round(
                55 + value_edge * 1.8
                + (expected_return - 1) * 30
            )
            if value_edge is not None and expected_return is not None
            else 38
        )
        value_score = max(0, min(99, value_score))
        market_confidence = (
            (((source.get("fae_core") or {}).get("recommendation") or {})
             .get("market_confidence") or {})
        )
        confidence_score = float(market_confidence.get("score") or 50)
        prediction_score = float(result.get("prediction_score") or 50)
        bet_score = round(
            value_score * 0.55
            + confidence_score * 0.30
            + prediction_score * 0.15
        )
        bet_score = max(0, min(99, bet_score))
        reasons = [
            str(reason) for reason in result.get("no_bet_reasons") or []
            if str(reason) not in {
                "赔率价值不足",
                "综合投注分未达门槛",
                "低赔率亚盘校准后赔率价值不足",
                "低赔率亚盘校准后综合投注分未达门槛",
            }
        ]
        metric_label = str(result.get("label") or "")
        effective_sample = _number(metric.get("effective_sample"))
        minimum_sample = (
            _number(policy.get("min_sample", {}).get(metric_label))
            or _number(DRAW_SELECTION_MIN_SAMPLE.get(metric_label))
        )
        if minimum_sample is not None:
            if effective_sample is None or effective_sample < minimum_sample:
                reasons.append("历史样本不足，不作为单场主推")
            elif (
                str(metric.get("confidence") or "") == "低"
                and effective_sample < minimum_sample * 1.4
            ):
                reasons.append("历史置信度不足，命中预估偏弱")
        if value_score < 52:
            reasons.append("历史校准后赔率价值不足")
        if bet_score < 55:
            reasons.append("历史校准后综合投注分未达门槛")
        result.update({
            "raw_probability": (
                result.get("raw_probability")
                if result.get("raw_probability") is not None
                else core_probability
            ),
            "probability": calibrated_probability,
            "value_probability": calibrated_probability,
            "value_edge": round(value_edge, 2)
            if value_edge is not None else None,
            "expected_return": round(expected_return, 3)
            if expected_return is not None else None,
            "value_score": value_score,
            "bet_score": bet_score,
            "score": bet_score,
            "stars": cls._rating(bet_score / 20),
            "no_bet_reasons": list(dict.fromkeys(reasons)),
            "no_bet": bool(reasons),
            "historical_calibration": {
                "applied": True,
                "weight": round(credibility, 3),
                "core_probability": core_probability,
                "similar_history_probability": historical_probability,
                "calibrated_probability": calibrated_probability,
                "effective_sample": metric.get("effective_sample"),
                "signal": metric.get("signal"),
            },
        })
        return result

    @classmethod
    def _play_value_profile(
        cls, source: Dict[str, Any], selection: str
    ) -> Dict[str, Any]:
        categories = (
            (((source.get("fae_core") or {}).get("recommendation") or {})
             .get("category_scores") or [])
        )
        profile = next(
            (
                dict(item) for item in categories
                if str(item.get("label") or "") == str(selection or "")
            ),
            {},
        )
        return cls._historical_adjusted_profile(source, profile)

    @staticmethod
    def _favorite_market_profile(source: Dict[str, Any]) -> Dict[str, Any]:
        """Return the current 1X2 favorite used by draw radar context rules."""
        current = ((source.get("euro") or {}).get("current") or [])
        if len(current) < 3:
            return {}
        odds = [_number(value) for value in current[:3]]
        if any(value is None or value <= 1 for value in odds):
            return {}
        index = min(range(3), key=lambda item: odds[item])
        return {
            "side": ("home", "draw", "away")[index],
            "odds": odds[index],
            "draw_odds": odds[1],
        }

    @staticmethod
    def _asian_favorite_depth_profile(
        source: Dict[str, Any],
        favorite_side: str,
    ) -> Dict[str, Any]:
        """Return Asian line and water from the 1X2 favorite's perspective."""
        risk = source.get("current_asian_risk") or {}
        asian = source.get("asian") or {}
        initial_values = asian.get("initial") or []
        current_values = asian.get("current") or []

        def side_water(values: List[Any], side: str) -> Optional[float]:
            index = 0 if side == "home" else 2
            return _number(values[index]) if len(values) > index else None

        current_favorite_water = (
            side_water(current_values, favorite_side)
            if favorite_side in {"home", "away"} else None
        )
        current_underdog_water = (
            side_water(
                current_values,
                "away" if favorite_side == "home" else "home",
            )
            if favorite_side in {"home", "away"} else None
        )
        initial_favorite_water = (
            side_water(initial_values, favorite_side)
            if favorite_side in {"home", "away"} else None
        )
        initial_underdog_water = (
            side_water(
                initial_values,
                "away" if favorite_side == "home" else "home",
            )
            if favorite_side in {"home", "away"} else None
        )
        favorite_water_change = (
            _number(risk.get("upper_water_change"))
            if risk.get("upper_water_change") is not None
            else (
                round(current_favorite_water - initial_favorite_water, 3)
                if (
                    current_favorite_water is not None
                    and initial_favorite_water is not None
                )
                else None
            )
        )
        initial_depth = _number(risk.get("initial_depth"))
        current_depth = _number(risk.get("current_depth"))
        line_change = _number(risk.get("line_change"))
        if current_depth is not None:
            return {
                "data_complete": bool(risk.get("data_complete", True)),
                "initial_depth": initial_depth,
                "current_depth": current_depth,
                "line_change": line_change,
                "initial_favorite_water": (
                    initial_favorite_water
                    if initial_favorite_water is not None
                    else _number(risk.get("initial_upper_water"))
                ),
                "current_favorite_water": (
                    current_favorite_water
                    if current_favorite_water is not None
                    else _number(risk.get("current_upper_water"))
                ),
                "favorite_water_change": favorite_water_change,
                "initial_underdog_water": initial_underdog_water,
                "current_underdog_water": current_underdog_water,
                "source": "current_asian_risk",
            }

        if len(current_values) < 2:
            return {"data_complete": False, "source": "asian"}
        current_line = _handicap_value_from_text(current_values[1])
        initial_line = (
            _handicap_value_from_text(initial_values[1])
            if len(initial_values) > 1 else None
        )
        if current_line is None:
            return {"data_complete": False, "source": "asian"}
        current_depth = (
            current_line if favorite_side == "home" else -current_line
        )
        initial_depth = (
            initial_line if favorite_side == "home" else -initial_line
        ) if initial_line is not None else None
        line_change = (
            round(current_depth - initial_depth, 3)
            if initial_depth is not None else None
        )
        return {
            "data_complete": True,
            "initial_depth": initial_depth,
            "current_depth": current_depth,
            "line_change": line_change,
            "initial_favorite_water": initial_favorite_water,
            "current_favorite_water": current_favorite_water,
            "favorite_water_change": favorite_water_change,
            "initial_underdog_water": initial_underdog_water,
            "current_underdog_water": current_underdog_water,
            "source": "asian",
        }

    @staticmethod
    def _total_market_profile(source: Dict[str, Any]) -> Dict[str, Any]:
        values = ((source.get("total") or {}).get("current") or [])
        initial_values = ((source.get("total") or {}).get("initial") or [])
        over_water = _number(values[0]) if len(values) > 0 else None
        line = _number(values[1]) if len(values) > 1 else None
        under_water = _number(values[2]) if len(values) > 2 else None
        initial_line = (
            _number(initial_values[1]) if len(initial_values) > 1 else None
        )
        if line is None:
            return {"available": False}
        if line <= 2.25:
            line_band = "<=2.25"
        elif line <= 2.75:
            line_band = "2.25-2.75"
        elif line <= 3.25:
            line_band = "2.75-3.25"
        else:
            line_band = ">=3.25"
        if (
            under_water is not None
            and over_water is not None
            and under_water + 0.08 < over_water
        ):
            bias = "under_low"
        elif (
            over_water is not None
            and under_water is not None
            and over_water + 0.08 < under_water
        ):
            bias = "over_low"
        else:
            bias = "even"
        return {
            "available": True,
            "line": line,
            "initial_line": initial_line,
            "line_change": (
                round(line - initial_line, 3)
                if initial_line is not None else None
            ),
            "line_band": line_band,
            "over_water": over_water,
            "under_water": under_water,
            "bias": bias,
        }

    @classmethod
    def _directional_precision_guard(
        cls,
        source: Dict[str, Any],
        model_selection: str,
    ) -> tuple[str, Dict[str, Any]]:
        """Reorder exact-margin picks when directional markets are stronger.

        A low total limits the score range but does not itself imply a draw.
        Likewise, a handicap draw is an exact winning-margin outcome and
        should not stay ahead of the cover outcome when a short-priced
        favorite truly deepens into a high-total market.  This guard only
        changes the ordering of the two same-market selections; the usual
        value, risk and no-bet gates still decide whether either can become an
        official recommendation.
        """
        base = {
            "triggered": False,
            "model_selection": model_selection,
            "effective_selection": model_selection,
            "secondary_selection": None,
        }
        if model_selection not in {"平局", "让平"}:
            return model_selection, base

        euro = source.get("euro") or {}
        initial_euro = euro.get("initial") or []
        current_euro = euro.get("current") or []
        if len(current_euro) < 3:
            return model_selection, base
        current_odds = [_number(value) for value in current_euro[:3]]
        if any(value is None or value <= 1 for value in current_odds):
            return model_selection, base
        win_indexes = (0, 2)
        favorite_index = min(win_indexes, key=lambda index: current_odds[index])
        favorite_side = "home" if favorite_index == 0 else "away"
        favorite_label = "主胜" if favorite_side == "home" else "客胜"
        opponent_index = 2 if favorite_index == 0 else 0
        favorite_odds = float(current_odds[favorite_index])
        favorite_initial = (
            _number(initial_euro[favorite_index])
            if len(initial_euro) > favorite_index else None
        )
        opponent_initial = (
            _number(initial_euro[opponent_index])
            if len(initial_euro) > opponent_index else None
        )
        favorite_drop = (
            round(favorite_initial - favorite_odds, 3)
            if favorite_initial is not None else None
        )
        opponent_rise = (
            round(current_odds[opponent_index] - opponent_initial, 3)
            if opponent_initial is not None else None
        )
        asian = cls._asian_favorite_depth_profile(source, favorite_side)
        current_depth = _number(asian.get("current_depth"))
        line_change = _number(asian.get("line_change"))
        favorite_water = _number(asian.get("current_favorite_water"))
        total = cls._total_market_profile(source)
        total_line = _number(total.get("line"))
        risk_ids = {
            str(value)
            for value in (
                (source.get("current_asian_risk") or {}).get("pattern_ids")
                or []
            )
        }

        if model_selection == "平局":
            directional_probability = _number(
                ((source.get("fae_core") or {}).get("probabilities") or {}).get(
                    "home_win" if favorite_side == "home" else "away_win"
                )
            )
            draw_probability = _number(
                ((source.get("fae_core") or {}).get("probabilities") or {}).get(
                    "draw"
                )
            )
            probability_supports_direction = (
                directional_probability is None
                or draw_probability is None
                or directional_probability >= draw_probability + 4
            )
            euro_support = (
                favorite_odds <= 2.70
                and favorite_drop is not None
                and opponent_rise is not None
                and favorite_drop >= 0.10
                and opponent_rise >= 0.10
            )
            true_deepen = (
                current_depth is not None
                and current_depth >= 0.25
                and line_change is not None
                and line_change >= 0.24
                and (favorite_water is None or favorite_water <= 1.02)
            )
            low_water_support = (
                current_depth is not None
                and current_depth >= 0
                and favorite_water is not None
                and favorite_water <= 0.86
            )
            unstable_direction = bool(risk_ids & {
                "handicap_retreat",
                "upper_water_rise",
                "deepen_high_water",
                "euro_asian_divergence",
                "overheated_shallow",
            })
            triggered = bool(
                euro_support
                and probability_supports_direction
                and (true_deepen or low_water_support)
                and not unstable_direction
            )
            if not triggered:
                return model_selection, {
                    **base,
                    "candidate_selection": favorite_label,
                    "favorite_odds": favorite_odds,
                    "favorite_odds_drop": favorite_drop,
                    "opponent_odds_rise": opponent_rise,
                    "asian_depth": current_depth,
                    "asian_line_change": line_change,
                    "favorite_water": favorite_water,
                    "total_line": total_line,
                }
            score_context = (
                "低总球只压低比分，不等于平局"
                if total_line is not None and total_line <= 2.50
                else "胜负方向的欧亚证据强于精确平局"
            )
            reason = (
                f"方向强度护栏：欧赔{favorite_label}下降"
                f"{favorite_drop:g}、对手胜赔上升{opponent_rise:g}，"
                f"亚盘{'真实升深' if true_deepen else '低水支持'}；"
                f"{score_context}，主选改为{favorite_label}、平局降为防选"
            )
            return favorite_label, {
                **base,
                "triggered": True,
                "effective_selection": favorite_label,
                "secondary_selection": "平局",
                "favorite_side": favorite_side,
                "favorite_odds": favorite_odds,
                "favorite_odds_drop": favorite_drop,
                "opponent_odds_rise": opponent_rise,
                "asian_depth": current_depth,
                "asian_line_change": line_change,
                "favorite_water": favorite_water,
                "total_line": total_line,
                "reason": reason,
            }

        handicap = _number(
            (source.get("sporttery_handicap") or {}).get("value")
        )
        if handicap is None or abs(handicap) != 1:
            return model_selection, base
        aligned_favorite = (
            (favorite_side == "home" and handicap < 0)
            or (favorite_side == "away" and handicap > 0)
        )
        if not aligned_favorite:
            return model_selection, base
        cover_selection = "让胜" if favorite_side == "home" else "让负"
        high_total = total_line is not None and total_line >= 2.75
        true_deepen = (
            current_depth is not None
            and current_depth >= 1.0
            and line_change is not None
            and line_change >= 0.24
        )
        normal_cover = (
            favorite_odds <= 1.50
            and true_deepen
            and high_total
            and favorite_water is not None
            and favorite_water <= 0.95
        )
        extreme_favorite_cover = (
            favorite_odds <= 1.30
            and current_depth is not None
            and current_depth >= 1.25
            and true_deepen
            and high_total
            and favorite_water is not None
            and favorite_water <= 1.08
        )
        if not (normal_cover or extreme_favorite_cover):
            return model_selection, {
                **base,
                "candidate_selection": cover_selection,
                "favorite_odds": favorite_odds,
                "asian_depth": current_depth,
                "asian_line_change": line_change,
                "favorite_water": favorite_water,
                "total_line": total_line,
            }
        reason = (
            f"赢球差护栏：热门胜赔{favorite_odds:g}，亚盘真实升深至"
            f"{current_depth:g}球，大小球{total_line:g}；高进球环境下"
            f"穿盘证据强于恰好赢1球，主选改为{cover_selection}、"
            "让平降为防选"
        )
        return cover_selection, {
            **base,
            "triggered": True,
            "effective_selection": cover_selection,
            "secondary_selection": "让平",
            "favorite_side": favorite_side,
            "favorite_odds": favorite_odds,
            "asian_depth": current_depth,
            "asian_line_change": line_change,
            "favorite_water": favorite_water,
            "total_line": total_line,
            "reason": reason,
        }

    @classmethod
    def _league_specific_draw_signal(
        cls,
        source: Dict[str, Any],
        favorite_odds: float,
        draw_odds: float,
        current_depth: Optional[float],
        line_change: Optional[float],
        favorite_water: Optional[float],
        underdog_water: Optional[float],
        favorite_water_change: Optional[float],
    ) -> Dict[str, Any]:
        """Backtested league-specific ordinary-draw pockets.

        These are deliberately narrow. They are not generic "league likes
        draws" statements; each rule is a market structure seen in historical
        replay with a positive train/test split.
        """
        league = source.get("league")
        total = cls._total_market_profile(source)
        total_band = str(total.get("line_band") or "")
        total_bias = str(total.get("bias") or "")
        line_move = (
            "retreat" if line_change is not None and line_change < -0.01
            else "deepen" if line_change is not None and line_change > 0.01
            else "same"
        )

        def matched(
            aliases: Iterable[str],
            *,
            name: str,
            sample: int,
            hit_rate: float,
            roi: float,
            score_bonus: float,
            official_score_min: float,
            core: bool,
            reason: str,
            condition: bool,
        ) -> Dict[str, Any]:
            if not condition or not _league_in_aliases(league, aliases):
                return {}
            return {
                "kind": (
                    "backtested_league_draw_value"
                    if core else "backtested_league_draw_secondary"
                ),
                "role": name,
                "score_bonus": score_bonus,
                "official_score_min": official_score_min,
                "backtest_version": ORDINARY_DRAW_BACKTEST_VERSION,
                "sample": sample,
                "hit_rate": hit_rate,
                "roi": roi,
                "note": (
                    f"{name}：历史回测样本{sample}场，命中率{hit_rate:g}%、"
                    f"ROI{roi:+g}%；{reason}"
                ),
            }

        checks = [
            matched(
                ("巴甲", "巴西甲"),
                name="巴甲平局基线观察模型",
                sample=26,
                hit_rate=42.3,
                roi=56.1,
                score_bonus=12.0,
                official_score_min=94.0,
                core=False,
                reason=(
                    "巴甲整体平局基线偏高，但细分到平赔/小球/浅盘后样本仍少，"
                    "只提高观察排序，不单独升级正式推荐"
                ),
                condition=(
                    draw_odds >= 2.75
                    and not (
                        favorite_odds <= 1.50
                        and draw_odds >= 4.00
                    )
                ),
            ),
            matched(
                ("葡超",),
                name="葡超小球平局模型",
                sample=72,
                hit_rate=41.7,
                roi=38.2,
                score_bonus=24.0,
                official_score_min=80.0,
                core=True,
                reason="大小球低水偏小，比赛被压到低节奏博弈",
                condition=total_bias == "under_low",
            ),
            matched(
                ("挪超",),
                name="挪超退盘平局模型",
                sample=40,
                hit_rate=37.5,
                roi=50.3,
                score_bonus=22.0,
                official_score_min=82.0,
                core=True,
                reason="热门方向退盘，开放联赛中更容易走到双方都有球后的平局",
                condition=line_move == "retreat",
            ),
            matched(
                ("荷甲",),
                name="荷甲中低总球平局模型",
                sample=51,
                hit_rate=39.2,
                roi=47.5,
                score_bonus=22.0,
                official_score_min=82.0,
                core=True,
                reason="大小球在2.25-2.75区间且下盘水位0.95-1.04，胜负分歧收敛",
                condition=(
                    total_band == "2.25-2.75"
                    and underdog_water is not None
                    and 0.95 <= underdog_water < 1.05
                ),
            ),
            matched(
                ("英超",),
                name="英超降水平局模型",
                sample=53,
                hit_rate=41.5,
                roi=47.4,
                score_bonus=22.0,
                official_score_min=82.0,
                core=True,
                reason="热门上盘0.75-0.84且较初盘降水，市场热度集中但未形成充分穿盘保护",
                condition=(
                    favorite_water is not None
                    and 0.75 <= favorite_water < 0.85
                    and favorite_water_change is not None
                    and favorite_water_change <= -0.05
                ),
            ),
            matched(
                ("日职", "J1联赛"),
                name="日职中低总球平局模型",
                sample=24,
                hit_rate=33.3,
                roi=11.3,
                score_bonus=8.0,
                official_score_min=90.0,
                core=False,
                reason="大小球2.25-2.75且上盘水位基本不动，但修正后样本不足，只作观察",
                condition=(
                    "日职乙" not in str(league or "")
                    and total_band == "2.25-2.75"
                    and (
                        favorite_water_change is None
                        or abs(favorite_water_change) < 0.05
                    )
                ),
            ),
            matched(
                ("英冠",),
                name="英冠半球不动平局模型",
                sample=32,
                hit_rate=40.6,
                roi=34.8,
                score_bonus=18.0,
                official_score_min=86.0,
                core=False,
                reason="热门半球盘维持不动，胜负倾向存在但没有继续加深",
                condition=(
                    current_depth is not None
                    and abs(current_depth - 0.5) < 0.01
                    and line_move == "same"
                ),
            ),
            matched(
                ("澳超",),
                name="澳超高平赔中低总球模型",
                sample=28,
                hit_rate=42.9,
                roi=45.0,
                score_bonus=18.0,
                official_score_min=86.0,
                core=False,
                reason="平赔3.25-3.49但大小球仅2.25-2.75，开放预期不足以支撑分胜负",
                condition=(
                    3.25 <= draw_odds <= 3.49
                    and total_band == "2.25-2.75"
                ),
            ),
            matched(
                ("意甲",),
                name="意甲升盘高水平局模型",
                sample=33,
                hit_rate=42.4,
                roi=63.6,
                score_bonus=18.0,
                official_score_min=86.0,
                core=False,
                reason="亚盘升深但热门上盘水位0.95-1.04，盘口增强但水位未同步压低",
                condition=(
                    line_move == "deepen"
                    and favorite_water is not None
                    and 0.95 <= favorite_water < 1.05
                ),
            ),
        ]
        return next((item for item in checks if item), {})

    @classmethod
    def _handicap_draw_path_signal(
        cls,
        source: Dict[str, Any],
        favorite_side: str,
        favorite_odds: float,
        current_depth: Optional[float],
        line_change: Optional[float],
        favorite_water: Optional[float],
        favorite_water_change: Optional[float],
        handicap: Optional[float],
        handicap_draw_odds: Optional[float],
        favorite_matches_one_goal: bool,
        risk_set: Iterable[str],
    ) -> Dict[str, Any]:
        """Classify whether the handicap market really supports exact 1-goal win.

        The league template can say "this league has let-draw style", but the
        actual bet needs a concrete path: the favorite wins, yet does not cover.
        This helper reads the three Sporttery handicap prices to separate
        exact-margin support from "favorite may fail outright" protection.
        """
        if (
            not favorite_matches_one_goal
            or handicap is None
            or handicap_draw_odds is None
        ):
            return {}

        hhad_current = (
            (source.get("sporttery_handicap") or {}).get("current")
            or (source.get("sporttery_handicap") or {}).get("initial")
            or []
        )
        hhad_initial = (
            (source.get("sporttery_handicap") or {}).get("initial") or []
        )
        if len(hhad_current) < 3:
            return {}
        odds = [_number(value) for value in hhad_current[:3]]
        if any(value is None or value <= 1 for value in odds):
            return {}

        if favorite_side == "home" and handicap < 0:
            cover_label, cover_index = "让胜", 0
            protected_label, protected_index = "让负", 2
        elif favorite_side == "away" and handicap > 0:
            cover_label, cover_index = "让负", 2
            protected_label, protected_index = "让胜", 0
        else:
            return {}

        cover_odds = odds[cover_index]
        draw_odds = odds[1]
        protected_odds = odds[protected_index]
        initial_draw_odds = (
            _number(hhad_initial[1]) if len(hhad_initial) > 1 else None
        )
        draw_odds_change = (
            round(draw_odds - initial_draw_odds, 3)
            if initial_draw_odds is not None else None
        )
        hhad = (
            (((source.get("fae_core") or {}).get("probabilities") or {})
             .get("hhad") or {})
        )
        probabilities = {
            "让胜": _number(hhad.get("win")),
            "让平": _number(hhad.get("draw")),
            "让负": _number(hhad.get("lose")),
        }
        draw_probability = probabilities.get("让平")
        cover_probability = probabilities.get(cover_label)
        protected_probability = probabilities.get(protected_label)
        valid_probabilities = [
            value for value in probabilities.values() if value is not None
        ]
        top_probability = max(valid_probabilities) if valid_probabilities else None
        risk_ids = {str(value) for value in risk_set or []}

        protected_low_price = (
            protected_odds is not None
            and cover_odds is not None
            and protected_odds <= 2.20
            and protected_odds + 0.15 < cover_odds
        )
        water_drop_trap = (
            "water_drop_without_deepen" in risk_ids
            and favorite_water_change is not None
            and favorite_water_change <= -0.05
            and favorite_odds <= 1.65
            and protected_low_price
        )
        if water_drop_trap:
            return {
                "kind": "handicap_draw_path_blocked_by_protected_side",
                "role": "让平路径被受让保护压制",
                "score_bonus": -18.0,
                "block_official": True,
                "backtest_version": HANDICAP_DRAW_PATH_MODEL_VERSION,
                "note": (
                    f"亚盘降水不升盘，但竞彩{protected_label}{protected_odds:g}"
                    f"明显低于{cover_label}{cover_odds:g}，更像热门不穿或直接失手，"
                    "不是清晰的刚好赢1球路径，不升级让平"
                ),
            }

        draw_near_market_top = (
            draw_probability is not None
            and top_probability is not None
            and draw_probability >= 28
            and top_probability - draw_probability <= 10
        )
        handicap_prices_balanced = (
            cover_odds is not None
            and protected_odds is not None
            and abs(cover_odds - protected_odds) <= 0.50
        )
        draw_price_supported = (
            3.30 <= draw_odds <= 3.70
            and (
                draw_odds_change is None
                or draw_odds_change <= 0.05
            )
        )
        deepen_high_water_path = (
            "deepen_high_water" in risk_ids
            and current_depth is not None
            and 0.75 <= current_depth <= 1.25
            and line_change is not None
            and line_change > 0.01
            and favorite_water is not None
            and 0.95 <= favorite_water < 1.08
        )
        if (
            draw_price_supported
            and draw_near_market_top
            and (
                handicap_prices_balanced
                or deepen_high_water_path
            )
        ):
            return {
                "kind": "handicap_draw_goal_margin_path_watch",
                "role": "净胜1球路径确认",
                "score_bonus": 8.0,
                "backtest_version": HANDICAP_DRAW_PATH_MODEL_VERSION,
                "note": (
                    f"竞彩让球三项中让平概率{draw_probability:g}%接近最高项，"
                    f"{cover_label}{cover_odds:g}/{protected_label}{protected_odds:g}"
                    "未形成单边压制，按净胜1球路径观察"
                ),
            }
        return {}

    @classmethod
    def _sporttery_draw_price_signal(
        cls,
        source: Dict[str, Any],
        selection: str,
    ) -> Dict[str, Any]:
        """Use the handicap three-way prices as a modest draw confirmation.

        For -1, 让胜高于让平 means the book prices an exact one-goal win
        ahead of a two-goal-or-more cover.  For +1, 让负高于让平 carries the
        same meaning for an away favorite.  The signal is deliberately small:
        it can improve ranking but cannot create historical eligibility or
        bypass the formal-rule allow-list by itself.
        """
        handicap = _number(
            (source.get("sporttery_handicap") or {}).get("value")
        )
        if handicap not in {-1.0, 1.0}:
            return {}
        prices = (
            (source.get("sporttery_handicap") or {}).get("current")
            or (source.get("sporttery_handicap") or {}).get("initial")
            or []
        )
        if len(prices) < 3:
            return {}
        draw_odds = _number(prices[1])
        compare_index = 0 if handicap < 0 else 2
        compare_odds = _number(prices[compare_index])
        if (
            draw_odds is None
            or compare_odds is None
            or draw_odds <= 1
            or compare_odds <= draw_odds
        ):
            return {}

        gap = round(compare_odds - draw_odds, 3)
        if gap >= 0.30:
            handicap_probability_pp, handicap_score = 1.0, 8.0
        elif gap >= 0.15:
            handicap_probability_pp, handicap_score = 0.75, 6.0
        else:
            handicap_probability_pp, handicap_score = 0.5, 4.0
        if selection == "让平":
            probability_pp = handicap_probability_pp
            score_bonus = handicap_score
        elif selection == "平局":
            probability_pp = handicap_probability_pp * 0.5
            score_bonus = handicap_score * 0.5
        else:
            return {}

        compare_label = "让胜" if handicap < 0 else "受让负"
        draw_label = "让平" if handicap < 0 else "受让平"
        return {
            "version": SPORTTERY_DRAW_PRICE_SIGNAL_VERSION,
            "role": f"{compare_label}高于{draw_label}",
            "selection": selection,
            "handicap": int(handicap),
            "comparison_label": compare_label,
            "comparison_odds": round(compare_odds, 3),
            "draw_label": draw_label,
            "draw_odds": round(draw_odds, 3),
            "odds_gap": gap,
            "probability_adjustment_pp": round(probability_pp, 2),
            "score_bonus": round(score_bonus, 2),
            "formal_rule": False,
            "note": (
                f"竞彩{compare_label}{compare_odds:g}高于{draw_label}"
                f"{draw_odds:g}，市场价格更防一球差；"
                + (
                    "作为让平的中权重确认信号"
                    if selection == "让平" else
                    "只作为热门不穿时普通平局的低权重辅助"
                )
            ),
        }

    @classmethod
    def _league_specific_handicap_draw_signal(
        cls,
        source: Dict[str, Any],
        favorite_odds: float,
        current_depth: Optional[float],
        line_change: Optional[float],
        favorite_water: Optional[float],
        underdog_water: Optional[float],
        favorite_water_change: Optional[float],
        handicap_draw_odds: Optional[float],
        favorite_matches_one_goal: bool,
    ) -> Dict[str, Any]:
        """Backtested league-specific handicap-draw pockets.

        让平不是统一模型：同样是竞彩让1球，不同联赛的有效区间不同。
        这里仅放历史回放里样本、命中率、ROI 同时能站住的窄规则。
        """
        league = source.get("league")
        if (
            not favorite_matches_one_goal
            or handicap_draw_odds is None
            or favorite_water is None
            or current_depth is None
        ):
            return {}

        total = cls._total_market_profile(source)
        total_band = str(total.get("line_band") or "")
        total_bias = str(total.get("bias") or "")
        line_move = (
            "retreat" if line_change is not None and line_change < -0.01
            else "deepen" if line_change is not None and line_change > 0.01
            else "same"
        )
        asian_context_ok = (
            0.25 <= current_depth <= 1.25
            and 0.65 <= favorite_water < 1.08
            and (underdog_water is None or underdog_water >= 0.70)
            and not (
                line_change is not None
                and line_change > 0.01
                and favorite_water >= 0.98
            )
        )
        if not asian_context_ok:
            return {}

        def matched(
            aliases: Iterable[str],
            *,
            name: str,
            sample: int,
            hit_rate: float,
            roi: float,
            score_bonus: float,
            official_score_min: float,
            core: bool,
            reason: str,
            condition: bool,
        ) -> Dict[str, Any]:
            if not condition or not _league_in_aliases(league, aliases):
                return {}
            return {
                "kind": (
                    "backtested_league_handicap_draw_value"
                    if core else "backtested_league_handicap_draw_secondary"
                ),
                "role": name,
                "score_bonus": score_bonus,
                "official_score_min": official_score_min,
                "backtest_version": HANDICAP_DRAW_BACKTEST_VERSION,
                "sample": sample,
                "hit_rate": hit_rate,
                "roi": roi,
                "note": (
                    f"{name}：历史回测样本{sample}场，命中率{hit_rate:g}%、"
                    f"ROI{roi:+g}%；{reason}"
                ),
            }

        checks = [
            matched(
                ("意甲",),
                name="意甲中赔让平模型",
                sample=68,
                hit_rate=41.2,
                roi=45.4,
                score_bonus=24.0,
                official_score_min=82.0,
                core=True,
                reason="热门胜赔1.90-2.19，胜负优势不碾压但竞彩让1球，适合找刚好赢1球",
                condition=(
                    1.90 <= favorite_odds < 2.20
                    and 3.30 <= handicap_draw_odds < 4.00
                ),
            ),
            matched(
                ("德甲",),
                name="德甲中热门让平模型",
                sample=32,
                hit_rate=46.9,
                roi=68.9,
                score_bonus=24.0,
                official_score_min=82.0,
                core=True,
                reason="热门胜赔1.55-1.69，进攻强但让球只压到一球，历史更容易赢球输让",
                condition=(
                    1.55 <= favorite_odds < 1.70
                    and 3.30 <= handicap_draw_odds < 4.00
                ),
            ),
            matched(
                ("法甲",),
                name="法甲高让平赔模型",
                sample=35,
                hit_rate=45.7,
                roi=63.5,
                score_bonus=22.0,
                official_score_min=82.0,
                core=True,
                reason="让平赔3.50-3.69，市场给一球差回报仍足，且亚盘未给深盘保护",
                condition=3.50 <= handicap_draw_odds < 3.70,
            ),
            matched(
                ("英超",),
                name="英超中高总球小球让平模型",
                sample=27,
                hit_rate=44.4,
                roi=70.4,
                score_bonus=20.0,
                official_score_min=84.0,
                core=True,
                reason="大小球2.75-3.25但小球低水，强队赢球路径更偏1球差",
                condition=(
                    3.30 <= handicap_draw_odds < 4.00
                    and total_band == "2.75-3.25"
                    and total_bias == "under_low"
                ),
            ),
            matched(
                ("西甲",),
                name="西甲小球水位让平模型",
                sample=45,
                hit_rate=42.2,
                roi=46.2,
                score_bonus=20.0,
                official_score_min=84.0,
                core=True,
                reason="上盘0.85-0.94且小球低水，热门有优势但大胜空间受限",
                condition=(
                    3.30 <= handicap_draw_odds < 4.00
                    and 0.85 <= favorite_water < 0.95
                    and total_bias == "under_low"
                ),
            ),
            matched(
                ("沙特联",),
                name="沙特高赔大球让平模型",
                sample=27,
                hit_rate=37.0,
                roi=41.8,
                score_bonus=14.0,
                official_score_min=88.0,
                core=False,
                reason="让平赔3.70-3.99且大球低水，进球数支持强队赢球但穿盘不稳",
                condition=(
                    3.70 <= handicap_draw_odds < 4.00
                    and total_bias == "over_low"
                ),
            ),
            matched(
                ("欧罗巴", "欧联"),
                name="欧罗巴低水让平模型",
                sample=25,
                hit_rate=44.0,
                roi=64.5,
                score_bonus=14.0,
                official_score_min=88.0,
                core=False,
                reason="上盘0.75-0.84低水但没有继续给深盘，杯赛更容易停在一球差",
                condition=(
                    3.30 <= handicap_draw_odds < 4.00
                    and 0.75 <= favorite_water < 0.85
                ),
            ),
            matched(
                ("挪超",),
                name="挪超降水让平观察模型",
                sample=22,
                hit_rate=45.5,
                roi=77.6,
                score_bonus=8.0,
                official_score_min=90.0,
                core=False,
                reason="上盘0.85-0.94且较初盘降水，开放联赛存在2:1路径；样本不足，只作观察",
                condition=(
                    3.30 <= handicap_draw_odds < 4.00
                    and 0.85 <= favorite_water < 0.95
                    and favorite_water_change is not None
                    and favorite_water_change <= -0.05
                    and line_move in {"same", "retreat"}
                ),
            ),
        ]
        return next((item for item in checks if item), {})

    @classmethod
    def _draw_odds_band_signal(
        cls,
        source: Dict[str, Any],
        selection: str,
        risk_ids: Iterable[str],
    ) -> Dict[str, Any]:
        """Classify ordinary draw candidates by odds interval.

        普通平局不能只看一个概率分。平赔 2.75-3.20 的均势平，
        和平赔 4.00-5.20 的强热门冷平，是两种完全不同的路径。
        """
        favorite = cls._favorite_market_profile(source)
        favorite_side = favorite.get("side")
        favorite_odds = _number(favorite.get("odds"))
        draw_odds = _number(favorite.get("draw_odds"))
        if (
            favorite_side not in {"home", "away"}
            or favorite_odds is None
            or draw_odds is None
        ):
            return {}

        risk_set = {str(value) for value in risk_ids or []}
        asian_depth = cls._asian_favorite_depth_profile(
            source, str(favorite_side)
        )
        current_depth = _number(asian_depth.get("current_depth"))
        line_change = _number(asian_depth.get("line_change"))
        favorite_water = _number(asian_depth.get("current_favorite_water"))
        underdog_water = _number(asian_depth.get("current_underdog_water"))
        favorite_water_change = _number(
            asian_depth.get("favorite_water_change")
        )

        def water_text(value: Optional[float]) -> str:
            return f"{value:g}" if value is not None else "--"

        unstable_risks = {
            "handicap_retreat",
            "upper_water_rise",
            "water_drop_without_deepen",
            "euro_asian_divergence",
            "overheated_shallow",
        }
        deepen_high_water = "deepen_high_water" in risk_set

        if selection == "让平":
            league = source.get("league")
            positive_league = _league_in_aliases(
                league, HANDICAP_DRAW_POSITIVE_LEAGUES
            )
            negative_league = _league_in_aliases(
                league, HANDICAP_DRAW_NEGATIVE_LEAGUES
            )
            hhad_values = (
                (source.get("sporttery_handicap") or {}).get("current")
                or (source.get("sporttery_handicap") or {}).get("initial")
                or []
            )
            initial_hhad_values = (
                (source.get("sporttery_handicap") or {}).get("initial")
                or []
            )
            handicap_draw_odds = (
                _number(hhad_values[1]) if len(hhad_values) > 1 else None
            )
            initial_handicap_draw_odds = (
                _number(initial_hhad_values[1])
                if len(initial_hhad_values) > 1 else None
            )
            handicap_draw_change = (
                handicap_draw_odds - initial_handicap_draw_odds
                if (
                    handicap_draw_odds is not None
                    and initial_handicap_draw_odds is not None
                ) else None
            )
            handicap = _number(
                (source.get("sporttery_handicap") or {}).get("value")
            )
            favorite_matches_one_goal = (
                handicap is not None
                and abs(handicap) == 1
                and (
                    (favorite_side == "home" and handicap < 0)
                    or (favorite_side == "away" and handicap > 0)
                )
            )
            cold_draw_competes = (
                4.00 <= draw_odds <= 5.20
                and favorite_odds <= 1.50
                and risk_set & unstable_risks
                and not deepen_high_water
            )
            if cold_draw_competes:
                return {
                    "kind": "cold_draw_competes",
                    "role": "冷平区间压制让平",
                    "score_bonus": -16.0,
                    "block_official": True,
                    "note": (
                        "平赔4.00-5.20且热门盘口不稳但未升深，优先按冷平处理，"
                        "不升级让平"
                    ),
                }
            if handicap is not None and abs(handicap) >= 2:
                return {
                    "kind": "handicap_draw_two_goal_block",
                    "role": "让2球回测降级",
                    "score_bonus": -12.0,
                    "block_official": True,
                    "note": (
                        "历史回测中竞彩让2球及以上的精确净胜球命中明显偏弱，"
                        "不升级为正式让平"
                    ),
                }
            if handicap_draw_odds is not None and handicap_draw_odds >= 4.00:
                return {
                    "kind": "handicap_draw_high_odds_block",
                    "role": "高让平赔降级",
                    "score_bonus": -12.0,
                    "block_official": True,
                    "note": (
                        "让平赔率4.00以上历史命中率和ROI均偏弱，"
                        "不因高回报升级为正式让平"
                    ),
                }
            if favorite_odds > 2.20:
                return {
                    "kind": "handicap_draw_weak_favorite_block",
                    "role": "弱热门让平降级",
                    "score_bonus": -10.0,
                    "block_official": True,
                    "note": (
                        "热门胜赔高于2.20时，历史精确一球差命中明显下降，"
                        "不升级为正式让平"
                    ),
                }
            if (
                handicap_draw_odds is not None
                and 3.30 <= handicap_draw_odds <= 3.70
                and favorite_matches_one_goal
                and favorite_odds <= 1.25
            ):
                return {
                    "kind": "handicap_draw_ultra_hot_block",
                    "role": "超低赔让平降级",
                    "score_bonus": -10.0,
                    "block_official": True,
                    "note": (
                        "热门胜赔≤1.25的超热区让平历史回测偏弱，"
                        "只保留观察不升级"
                    ),
                }
            if favorite_matches_one_goal and negative_league:
                return {
                    "kind": "handicap_draw_negative_league_block",
                    "role": "低命中联赛降级",
                    "score_bonus": -10.0,
                    "block_official": True,
                    "note": (
                        f"{league or '该联赛'}在让平赔率区间历史回测偏弱，"
                        "不升级为正式让平"
                    ),
                }
            path_signal = cls._handicap_draw_path_signal(
                source,
                str(favorite_side),
                favorite_odds,
                current_depth,
                line_change,
                favorite_water,
                favorite_water_change,
                handicap,
                handicap_draw_odds,
                favorite_matches_one_goal,
                risk_set,
            )
            if path_signal.get("block_official"):
                return path_signal
            path_supports_handicap_draw = bool(path_signal)
            path_note = (
                str(path_signal.get("note") or "").strip()
                if path_signal else ""
            )
            asian_water_supports_handicap_draw = (
                path_supports_handicap_draw
                or (
                    favorite_water is not None
                    and 0.65 <= favorite_water < 1.05
                    and (
                        underdog_water is None
                        or underdog_water >= 0.75
                    )
                    and not (
                        line_change is not None
                        and line_change > 0.01
                        and favorite_water >= 0.98
                    )
                )
            )
            if (
                handicap == 1
                and favorite_matches_one_goal
                and handicap_draw_odds is not None
                and 2.70 <= handicap_draw_odds < 3.20
            ):
                return {
                    "kind": "backtested_hhad_plus1_low_odds_value",
                    "role": "受让+1低赔让平模型",
                    "score_bonus": 28.0,
                    "official_score_min": 80.0,
                    "backtest_version": HANDICAP_DRAW_BACKTEST_VERSION,
                    "sample": 95,
                    "hit_rate": 40.0,
                    "market_probability": 29.0,
                    "roi": 23.0,
                    "note": (
                        "竞彩主队+1且客队为热门，让平赔2.70-3.19；"
                        "对应客队刚好赢1球，统一门禁后历史命中40.0%、"
                        "ROI+23.0%"
                        + (f"；{path_note}" if path_note else "")
                    ),
                }
            if (
                favorite_matches_one_goal
                and handicap == -1
                and handicap_draw_odds is not None
                and handicap_draw_change is not None
                and 3.20 <= handicap_draw_odds < 3.80
                and 0.03 <= handicap_draw_change < 0.10
            ):
                return {
                    "kind": "backtested_hhad_small_rise_value",
                    "role": "让平赔小升一球差模型",
                    "score_bonus": 24.0,
                    "official_score_min": 82.0,
                    "backtest_version": HANDICAP_DRAW_BACKTEST_VERSION,
                    "sample": 102,
                    "hit_rate": 38.2,
                    "market_probability": 25.2,
                    "roi": 33.5,
                    "note": (
                        f"竞彩让{int(handicap):+d}且让平赔小升"
                        f"{handicap_draw_change:.2f}、即时{handicap_draw_odds:g}；"
                        "统一门禁后历史命中38.2%、ROI+33.5%"
                        + (f"；{path_note}" if path_note else "")
                    ),
                }
            league_handicap_draw_signal = cls._league_specific_handicap_draw_signal(
                source,
                favorite_odds,
                current_depth,
                line_change,
                favorite_water,
                underdog_water,
                favorite_water_change,
                handicap_draw_odds,
                favorite_matches_one_goal,
            )
            if league_handicap_draw_signal:
                return league_handicap_draw_signal
            if (
                handicap_draw_odds is not None
                and 3.30 <= handicap_draw_odds <= 3.70
                and favorite_matches_one_goal
                and positive_league
                and not asian_water_supports_handicap_draw
            ):
                return {
                    "kind": "handicap_draw_blocked_by_asian_water",
                    "role": "让平缺少亚盘水位确认",
                    "score_bonus": -12.0,
                    "block_official": True,
                    "note": (
                        "让平赔率区间符合，但亚盘上盘水位不在0.65-1.04"
                        "支持区间，或下盘水位过低/升盘高水，不升级让平"
                    ),
                }
            if (
                handicap_draw_odds is not None
                and 3.30 <= handicap_draw_odds <= 3.70
                and favorite_matches_one_goal
                and positive_league
                and 1.26 <= favorite_odds <= 1.40
            ):
                return {
                    "kind": "backtested_league_one_goal_value",
                    "role": "回测正向让平区间",
                    "score_bonus": 30.0,
                    "official_score_min": 80.0,
                    "backtest_version": HANDICAP_DRAW_BACKTEST_VERSION,
                    "sample": 50,
                    "hit_rate": 48.0,
                    "roi": 69.5,
                    "note": (
                        f"{league or '该联赛'}让平历史回测为正向；"
                        "竞彩让1球、热门胜赔1.26-1.40、让平赔3.30-3.70，"
                        f"亚盘上盘水位{water_text(favorite_water)}处在支持区间，"
                        "按热门刚好赢一球路径评估"
                        + (f"；{path_note}" if path_note else "")
                    ),
                }
            if (
                handicap_draw_odds is not None
                and 3.30 <= handicap_draw_odds <= 3.70
                and favorite_matches_one_goal
                and positive_league
                and 1.40 < favorite_odds <= 1.55
            ):
                return {
                    "kind": "backtested_league_one_goal_secondary",
                    "role": "回测次级让平区间",
                    "score_bonus": 16.0,
                    "official_score_min": 86.0,
                    "backtest_version": HANDICAP_DRAW_BACKTEST_VERSION,
                    "sample": 52,
                    "hit_rate": 34.6,
                    "roi": 18.2,
                    "note": (
                        f"{league or '该联赛'}让平历史回测可观察；"
                        "竞彩让1球、让平赔3.30-3.70，但热门胜赔1.41-1.55"
                        f"区间命中率下降，亚盘上盘水位{water_text(favorite_water)}，"
                        "仅高分时小试"
                        + (f"；{path_note}" if path_note else "")
                    ),
                }
            if (
                favorite_matches_one_goal
                and handicap == -1
                and handicap_draw_odds is not None
                and 3.20 <= handicap_draw_odds < 3.80
            ):
                movement_text = (
                    f"{handicap_draw_change:+.2f}"
                    if handicap_draw_change is not None else "缺失"
                )
                return {
                    "kind": "backtested_hhad_minus1_draw_band",
                    "role": "主让1球一球差区间",
                    "score_bonus": 12.0,
                    "official_score_min": 88.0,
                    "backtest_version": HANDICAP_DRAW_BACKTEST_VERSION,
                    "sample": 1915,
                    "hit_rate": 26.7,
                    "market_probability": 24.9,
                    "roi": -5.5,
                    "note": (
                        f"竞彩主队-1且让平即时赔率{handicap_draw_odds:g}，"
                        f"初即时变化{movement_text}；赔率变化不再作为硬性"
                        "准入条件，持平或下降也可继续由模型概率、赔率价值、"
                        "亚盘水位与风险门槛综合判断。小升0.03-0.09仍作为"
                        "额外正向加分"
                        + (f"；{path_note}" if path_note else "")
                    ),
                }
            return {}

        if selection == "平局":
            league_draw_signal = cls._league_specific_draw_signal(
                source,
                favorite_odds,
                draw_odds,
                current_depth,
                line_change,
                favorite_water,
                underdog_water,
                favorite_water_change,
            )
            if league_draw_signal:
                return league_draw_signal

        if selection == "平局" and 2.75 <= draw_odds <= 3.20 and favorite_odds >= 2.15:
            league = source.get("league")
            positive_draw_league = _league_in_aliases(
                league, ORDINARY_DRAW_POSITIVE_LEAGUES
            )
            asian_water_confirms_draw = (
                favorite_water is not None
                and underdog_water is not None
                and 0.65 <= favorite_water < 1.15
                and underdog_water >= 0.75
                and favorite_water < 1.05
                if current_depth is not None and abs(current_depth) <= 0.25
                else (
                    favorite_water is not None
                    and 0.80 <= favorite_water < 1.15
                    and (
                        underdog_water is None
                        or underdog_water >= 0.75
                    )
                )
            )
            asian_confirms_draw = (
                current_depth is not None
                and asian_water_confirms_draw
                and (
                    abs(current_depth) <= 0.25
                    or (
                        current_depth <= 0.50
                        and line_change is not None
                        and line_change < -0.01
                    )
                )
            )
            draw_depth_guard = (
                current_depth is not None
                and (
                    current_depth <= 0
                    or (
                        line_change is not None
                        and line_change < -0.01
                    )
                )
            )
            if not asian_confirms_draw:
                return {
                    "kind": "balanced_draw_blocked_by_asian",
                    "role": "均势平缺少亚盘确认",
                    "score_bonus": -10.0,
                    "block_official": True,
                    "note": (
                        "平赔处在均势区，但亚盘未落到平手/平半浅盘或退浅，"
                        "或上/下盘水位区间不支持，"
                        "不升级普通平"
                    ),
                }
            if not positive_draw_league:
                return {
                    "kind": "balanced_draw_non_positive_league",
                    "role": "非正向联赛均势平",
                    "score_bonus": -6.0,
                    "block_official": True,
                    "note": (
                        f"{league or '该联赛'}未进入普通平局正向回测联赛，"
                        "均势平只保留观察"
                    ),
                }
            if not draw_depth_guard:
                return {
                    "kind": "balanced_draw_blocked_by_depth_guard",
                    "role": "均势平缺少退浅/平手保护",
                    "score_bonus": -6.0,
                    "block_official": True,
                    "note": (
                        "正向联赛和平赔区间符合，但亚盘仍是热门让平半且未退浅，"
                        "历史回测表现偏弱，不升级普通平"
                    ),
                }
            if 2.85 <= draw_odds <= 3.14:
                return {
                    "kind": "backtested_balanced_draw_value",
                    "role": "回测正向均势平",
                    "score_bonus": 26.0,
                    "official_score_min": 80.0,
                    "backtest_version": ORDINARY_DRAW_BACKTEST_VERSION,
                    "sample": 71,
                    "hit_rate": 47.9,
                    "roi": 43.4,
                    "note": (
                        f"{league or '该联赛'}普通平局历史回测为正向；"
                        "平赔2.85-3.14、亚盘退浅/平手保护，"
                        f"上盘水位{water_text(favorite_water)}、下盘水位"
                        f"{water_text(underdog_water)}，按均势平路径评估"
                    ),
                }
            return {
                "kind": "backtested_balanced_draw_secondary",
                "role": "回测次级均势平",
                "score_bonus": 18.0,
                "official_score_min": 84.0,
                "backtest_version": ORDINARY_DRAW_BACKTEST_VERSION,
                "sample": 94,
                "hit_rate": 46.8,
                "roi": 40.4,
                "note": (
                    f"{league or '该联赛'}普通平局历史回测可观察；"
                    "平赔2.75-3.20、亚盘退浅/平手保护，但平赔不在"
                    "2.85-3.14核心区间，仅高分时小试"
                ),
            }

        if selection == "平局" and (
            4.00 <= draw_odds <= 5.20
            and favorite_odds <= 1.50
            and risk_set & (unstable_risks | {"deepen_high_water"})
        ):
            asian_water_confirms_cold_draw = (
                favorite_water is not None
                and (
                    favorite_water >= 0.95
                    or (
                        favorite_water <= 0.75
                        and "overheated_shallow" in risk_set
                    )
                    or (
                        favorite_water_change is not None
                        and favorite_water_change >= 0.05
                    )
                )
            )
            asian_confirms_cold_draw = (
                current_depth is not None
                and asian_water_confirms_cold_draw
                and (
                    current_depth <= 0.75
                    or (
                        line_change is not None
                        and line_change <= 0.01
                        and risk_set & unstable_risks
                    )
                    or "overheated_shallow" in risk_set
                )
            )
            if not asian_confirms_cold_draw:
                return {
                    "kind": "cold_draw_blocked_by_asian_depth",
                    "role": "冷平缺少亚盘浅盘确认",
                    "score_bonus": -10.0,
                    "block_official": True,
                    "note": (
                        "平赔处在强热门冷平区，但亚盘深度或上盘水位区间"
                        "没有偏浅、退盘、不升深、高水/过热低水证据，"
                        "不升级普通平"
                    ),
                }
            if deepen_high_water and not (
                risk_set & {"handicap_retreat", "water_drop_without_deepen"}
            ):
                return {
                    "kind": "cold_draw_blocked_by_deepen",
                    "role": "冷平区间但升深高水",
                    "score_bonus": -8.0,
                    "block_official": True,
                    "note": (
                        "平赔4.00-5.20但亚盘升深高水，优先看热门小胜/让平，"
                        "不直接升级冷平"
                    ),
                }
            return {
                "kind": "cold_draw_watch_only",
                "role": "冷平观察",
                "score_bonus": 8.0,
                "block_official": True,
                "note": (
                    "平赔4.00-5.20、热门胜赔≤1.50且盘口不稳，"
                    f"亚盘上盘水位{water_text(favorite_water)}处在冷平确认区间，"
                    "但冷平历史回测整体为负，只观察不升级"
                ),
            }
        return {}

    @classmethod
    def _draw_radar_context_signal(
        cls,
        source: Dict[str, Any],
        selection: str,
        risk_ids: Iterable[str],
    ) -> Dict[str, Any]:
        """Add context for strong-favorite non-cover spots.

        Historical odds-band filters are useful, but they were too blunt for
        cases like 203/204: a low-priced favorite combined with shallow or
        unstable handicap movement should stay visible in the draw radar
        instead of being filtered only because the favorite is short-priced.
        """
        risk_set = {str(value) for value in risk_ids or []}
        unstable_risks = {
            "handicap_retreat",
            "upper_water_rise",
            "water_drop_without_deepen",
            "euro_asian_divergence",
            "overheated_shallow",
        }
        if not (risk_set & unstable_risks):
            return {}

        favorite = cls._favorite_market_profile(source)
        favorite_side = favorite.get("side")
        favorite_odds = _number(favorite.get("odds"))
        if favorite_side not in {"home", "away"} or favorite_odds is None:
            return {}

        handicap = _number(
            (source.get("sporttery_handicap") or {}).get("value")
        )
        if selection == "让平":
            favorite_matches_exact_margin = (
                (
                    favorite_side == "away"
                    and handicap is not None
                    and handicap > 0
                )
                or (
                    favorite_side == "home"
                    and handicap is not None
                    and handicap < 0
                )
            )
            if favorite_matches_exact_margin and favorite_odds <= 1.70:
                return {
                    "role": "强热门只赢一球风险",
                    "score_bonus": 8.0 if favorite_odds < 1.50 else 6.0,
                    "note": "强热门方向占优但盘口不稳，按只赢一球纳入让平雷达",
                }

        if selection == "平局" and favorite_odds < 1.50:
            return {
                "role": "热门不穿平局风险",
                "score_bonus": 12.0,
                "note": "低赔热门遇到不稳盘口，普通平不得被强热门过滤规则直接压掉",
            }
        return {}

    @classmethod
    def _favorite_non_cover_guard(
        cls,
        source: Dict[str, Any],
        model_selection: str,
    ) -> Dict[str, Any]:
        """Downgrade a favorite pick when handicap markets protect the dog.

        Example: 201 had euro/asian leaning to away win, but the Asian move was
        "deepen high water" and Sporttery +1 made home handicap-win the clear
        low-price outcome. In that structure the favorite win can remain a
        directional observation, but it should not be an official pick.
        """
        base = {
            "triggered": False,
            "model_selection": model_selection,
            "effective_selection": model_selection,
            "force_no_bet": False,
        }
        favorite = cls._favorite_market_profile(source)
        favorite_side = favorite.get("side")
        if (
            (model_selection == "客胜" and favorite_side != "away")
            or (model_selection == "主胜" and favorite_side != "home")
            or model_selection not in {"主胜", "客胜"}
        ):
            return base

        asian_risk = source.get("current_asian_risk") or {}
        risk_ids = {
            str(value)
            for value in asian_risk.get("pattern_ids") or []
        }
        unstable = {
            "deepen_high_water",
            "upper_water_rise",
            "water_drop_without_deepen",
            "handicap_retreat",
            "euro_asian_divergence",
            "overheated_shallow",
        }
        if str(asian_risk.get("favorite_side") or "") != favorite_side:
            return base
        if not (risk_ids & unstable):
            return base

        handicap = _number(
            (source.get("sporttery_handicap") or {}).get("value")
        )
        if handicap is None:
            return base
        protected_selection = None
        protected_index = None
        if favorite_side == "away" and handicap > 0:
            protected_selection = "让胜"
            protected_index = 0
        elif favorite_side == "home" and handicap < 0:
            protected_selection = "让负"
            protected_index = 2
        if not protected_selection:
            return base

        hhad = (
            (((source.get("fae_core") or {}).get("probabilities") or {})
             .get("hhad") or {})
        )
        probabilities = {
            "让胜": _number(hhad.get("win")),
            "让平": _number(hhad.get("draw")),
            "让负": _number(hhad.get("lose")),
        }
        protected_probability = probabilities.get(protected_selection)
        if protected_probability is None:
            return base
        strongest = max(
            (
                (label, value) for label, value in probabilities.items()
                if value is not None
            ),
            key=lambda item: item[1],
            default=(None, None),
        )
        if strongest[0] != protected_selection:
            return base

        odds_values = (
            (source.get("sporttery_handicap") or {}).get("current")
            or (source.get("sporttery_handicap") or {}).get("initial")
            or []
        )
        protected_odds = (
            _number(odds_values[protected_index])
            if len(odds_values) > protected_index else None
        )
        other_probabilities = [
            value for label, value in probabilities.items()
            if label != protected_selection and value is not None
        ]
        gap = (
            protected_probability - max(other_probabilities)
            if other_probabilities else 0
        )
        strong_protection = (
            protected_odds is not None
            and protected_odds <= 1.75
            and protected_probability >= 50
            and gap >= 12
        )
        if not strong_protection:
            upset_model = source.get("upset_warning_model") or {}
            upset_score = _number(upset_model.get("score")) or 0
            if upset_score >= 75:
                suggested = [
                    str(value) for value in (
                        upset_model.get("suggested_defenses") or []
                    )
                    if str(value) in {
                        "主胜", "平局", "客胜", "让胜", "让平", "让负"
                    }
                ]
                effective = suggested[0] if suggested else protected_selection
                reason = (
                    f"爆冷预警护栏：{model_selection}方向存在"
                    f"{upset_model.get('level') or '防冷'}信号，"
                    f"爆冷指数{upset_score:g}分，建议防"
                    f"{'、'.join(suggested) if suggested else protected_selection}，"
                    f"{model_selection}降级为观察并标记不下注"
                )
                return {
                    "triggered": True,
                    "model_selection": model_selection,
                    "effective_selection": effective,
                    "force_no_bet": True,
                    "protected_selection": protected_selection,
                    "protected_probability": round(protected_probability, 2),
                    "protected_odds": round(protected_odds, 3)
                    if protected_odds is not None else None,
                    "probability_gap": round(gap, 2),
                    "upset_warning_score": round(upset_score, 2),
                    "risk_pattern_ids": sorted(risk_ids & unstable),
                    "reason": reason,
                }
            return base

        risk_label = (
            asian_risk.get("primary_label")
            or "、".join(sorted(risk_ids & unstable))
            or "热门不穿"
        )
        reason = (
            f"热门不穿护栏：{model_selection}方向虽获欧赔/亚盘支持，"
            f"但盘口触发{risk_label}，竞彩受让保护项{protected_selection}"
            f"{protected_odds:g}为最低且FAE估算{protected_probability:g}%"
            f"领先{gap:g}个百分点，{model_selection}降级为观察并标记不下注"
        )
        return {
            "triggered": True,
            "model_selection": model_selection,
            "effective_selection": protected_selection,
            "force_no_bet": True,
            "protected_selection": protected_selection,
            "protected_probability": round(protected_probability, 2),
            "protected_odds": round(protected_odds, 3)
            if protected_odds is not None else None,
            "probability_gap": round(gap, 2),
            "risk_pattern_ids": sorted(risk_ids & unstable),
            "reason": reason,
        }

    @classmethod
    def _draw_candidate_guardrail_alignment(
        cls,
        analysis: Dict[str, Any],
        selection: str,
    ) -> Dict[str, Any]:
        """Keep draw tickets aligned with deterministic match guardrails."""
        conflicts = []
        for key in (
            "consistency_guard",
            "directional_precision_guard",
            "non_cover_guard",
        ):
            guard = analysis.get(key) or {}
            if not guard.get("triggered"):
                continue
            effective = str(guard.get("effective_selection") or "")
            if effective and effective != selection:
                conflicts.append({
                    "guard": key,
                    "effective_selection": effective,
                    "reason": guard.get("reason"),
                })
        return {
            "aligned": not bool(conflicts),
            "selection": selection,
            "conflicts": conflicts,
            "reason": (
                ""
                if not conflicts else
                "护栏最终方向为{}，{}不得重新进入组票".format(
                    "、".join(dict.fromkeys(
                        str(item.get("effective_selection") or "")
                        for item in conflicts
                    )),
                    selection,
                )
            ),
        }

    @classmethod
    def _draw_radar_candidate(
        cls,
        match: Dict[str, Any],
        selection: str,
    ) -> Dict[str, Any]:
        """Score draw outcomes independently from the match's final pick."""
        analysis = match.get("analysis") or {}
        source = match.get("input_snapshot") or {}
        model_key = (
            "ordinary_draw" if selection == "平局" else "handicap_draw"
        )
        metric = (
            (source.get("historical_goal_margin_model") or {})
            .get(model_key) or {}
        )
        profile = cls._play_value_profile(source, selection)
        probability = _number(profile.get("probability"))
        if probability is None:
            probability = (
                _number(metric.get("blended_probability"))
                if metric.get("eligible_for_adjustment")
                else None
            )
        market_probability = (
            _number(metric.get("market_probability"))
            if metric.get("market_probability") is not None
            else _number(profile.get("market_implied_probability"))
        )
        historical_probability = _number(
            metric.get("historical_probability")
        )
        odds = (
            _number(metric.get("odds"))
            if metric.get("odds") is not None
            else _number(profile.get("odds"))
        )
        sporttery_price_signal = cls._sporttery_draw_price_signal(
            source, selection
        )
        base_probability = probability
        price_probability_adjustment = float(
            sporttery_price_signal.get("probability_adjustment_pp") or 0
        )
        if probability is not None and price_probability_adjustment:
            probability = round(
                max(0.0, min(100.0, probability + price_probability_adjustment)),
                2,
            )
        one_goal_margin_signal = cls._one_goal_margin_parlay_signal(source)
        one_goal_probability_adjustment = 0.0
        one_goal_score_adjustment = 0.0
        one_goal_note = ""
        if one_goal_margin_signal.get("triggered"):
            if selection == "让平":
                one_goal_probability_adjustment = float(
                    one_goal_margin_signal.get(
                        "probability_adjustment_pp"
                    ) or 0
                )
                one_goal_score_adjustment = float(
                    one_goal_margin_signal.get("score_bonus") or 0
                )
                one_goal_note = str(
                    one_goal_margin_signal.get("reason") or ""
                )
            elif selection == "平局":
                # The match can still draw, but low total plus a clearly
                # aligned favourite is primarily an exact-margin path.
                one_goal_probability_adjustment = -2.0
                one_goal_score_adjustment = -12.0
                one_goal_note = (
                    "低总球一球差分流生效：热门方向仍成立，普通平局"
                    "降级，优先检查让平"
                )
            if probability is not None and one_goal_probability_adjustment:
                probability = round(max(
                    0.0,
                    min(100.0, probability + one_goal_probability_adjustment),
                ), 2)
        historical_rule_key = (
            "ordinary_draw" if selection == "平局" else "handicap_draw"
        )
        historical_rule_profile = (
            (source.get("historical_odds_rules") or {})
            .get(historical_rule_key) or {}
        )
        historical_rule_adjustment = float(
            historical_rule_profile.get("adjustment_pp") or 0
        )
        matched_historical_rules = [
            str(value)
            for value in historical_rule_profile.get("matched_rule_ids") or []
        ]
        expected_return = _number(profile.get("expected_return"))
        profile_odds_value = (
            round((expected_return - 1) * 100, 2)
            if expected_return is not None else None
        )
        metric_odds_value = _number(metric.get("value_edge"))
        odds_value = (
            profile_odds_value
            if matched_historical_rules and profile_odds_value is not None
            else metric_odds_value
            if metric_odds_value is not None
            else profile_odds_value
        )
        if (
            sporttery_price_signal
            and probability is not None
            and odds is not None
        ):
            incremental_value = price_probability_adjustment * odds
            odds_value = round(
                (odds_value if odds_value is not None else 0.0)
                + incremental_value,
                2,
            )
            sporttery_price_signal["odds_value_adjustment"] = round(
                incremental_value, 2
            )
        if (
            one_goal_probability_adjustment
            and odds is not None
        ):
            odds_value = round(
                (odds_value if odds_value is not None else 0.0)
                + one_goal_probability_adjustment * odds,
                2,
            )
        historical_rule_samples = [
            int(item.get("sample") or 0)
            for item in historical_rule_profile.get("signals") or []
            if item.get("sample")
        ]
        historical_rule_confidences = [
            str(item.get("confidence") or "")
            for item in historical_rule_profile.get("signals") or []
        ]
        historical_rule_confidence = (
            "高" if "高" in historical_rule_confidences
            else "中" if "中" in historical_rule_confidences
            else None
        )
        history_eligible = bool(
            metric.get("eligible_for_adjustment")
            or (
                historical_rule_profile.get("eligible_for_adjustment")
                and matched_historical_rules
            )
        )
        confidence = (
            (((source.get("fae_core") or {}).get("recommendation") or {})
             .get("market_confidence") or {})
        )
        confidence_score = float(confidence.get("score") or 45)
        role_signals = []
        if analysis.get("primary_play") == selection:
            role_signals.append("正式主选")
        if analysis.get("secondary_play") == selection:
            role_signals.append("同市场防选")
        if (
            selection == "让平"
            and analysis.get("handicap_play") == selection
        ):
            role_signals.append("竞彩让球参考")
        if (
            selection == "平局"
            and analysis.get("predicted_result") == selection
        ):
            role_signals.append("赛果倾向")
        if one_goal_margin_signal.get("triggered"):
            role_signals.append(
                "低总球一球差优先"
                if selection == "让平" else "低总球一球差分流"
            )

        risk_ids = [
            str(value) for value in (
                (source.get("current_asian_risk") or {}).get("pattern_ids")
                or []
            )
            if str(value) != "no_market_warning"
        ]
        draw_risk_ids = {
            "handicap_retreat",
            "upper_water_rise",
            "water_drop_without_deepen",
            "deepen_high_water",
            "euro_asian_divergence",
            "overheated_shallow",
        }
        relevant_risks = [
            value for value in risk_ids if value in draw_risk_ids
        ]
        context_signal = cls._draw_radar_context_signal(
            source, selection, relevant_risks
        )
        context_note = ""
        if context_signal:
            role = str(context_signal.get("role") or "").strip()
            if role:
                role_signals.append(role)
            context_note = str(context_signal.get("note") or "").strip()
        draw_band_signal = cls._draw_odds_band_signal(
            source, selection, relevant_risks
        )
        draw_band_note = ""
        if draw_band_signal:
            role = str(draw_band_signal.get("role") or "").strip()
            if role:
                role_signals.append(role)
            draw_band_note = str(draw_band_signal.get("note") or "").strip()
        league_model = source.get("league_tactical_model") or {}
        league_indexes = league_model.get("indexes") or {}
        league_score_bonus = 0.0
        league_note = ""
        if league_model.get("matched"):
            league_key = (
                "draw" if selection == "平局" else "handicap_draw"
            )
            league_index = _number(league_indexes.get(league_key))
            if league_index is not None and league_index >= 62:
                league_score_bonus = 8.0 if league_index >= 72 else 4.0
                role_signals.append(
                    f"{league_model.get('league_label') or '联赛'}"
                    f"{selection}模板"
                )
                league_note = (
                    f"联赛模板{selection}指数{league_index:g}分，"
                    "作为低到中权重先验"
                )
        odds_band_model = source.get("odds_band_model") or {}
        odds_band_indexes = odds_band_model.get("indexes") or {}
        odds_band_score_bonus = 0.0
        odds_band_note = ""
        if odds_band_model.get("available"):
            odds_key = (
                "underdog_upset" if selection == "平局"
                else "handicap_draw_value"
            )
            odds_index = _number(odds_band_indexes.get(odds_key))
            if odds_index is not None and odds_index >= 65:
                odds_band_score_bonus = 8.0 if odds_index >= 80 else 4.0
                role_signals.append(
                    "赔率区间防平"
                    if selection == "平局" else "赔率区间让平"
                )
                odds_band_note = (
                    f"赔率区间{selection}相关指数{odds_index:g}分，"
                    "作为爆冷/让平扫描先验"
                )
        role_bonus = min(12, len(role_signals) * 6)
        risk_bonus = min(5, len(relevant_risks) * 2)
        value_adjustment = max(
            -8.0, min(8.0, float(odds_value or 0) * 0.30)
        )
        historical_delta = (
            historical_probability - market_probability
            if (
                historical_probability is not None
                and market_probability is not None
            )
            else 0
        )
        history_adjustment = max(
            -6.0, min(6.0, historical_delta * 0.8)
        )
        profile_score = float(
            profile.get("bet_score") or profile.get("score") or 50
        )
        score = (
            float(probability or 0) * 1.35
            + confidence_score * 0.25
            + (profile_score - 50) * 0.18
            + role_bonus
            + risk_bonus
            + value_adjustment
            + history_adjustment
            + max(-4.0, min(6.0, historical_rule_adjustment * 1.2))
            + float(context_signal.get("score_bonus") or 0)
            + float(draw_band_signal.get("score_bonus") or 0)
            + league_score_bonus
            + odds_band_score_bonus
            + float(sporttery_price_signal.get("score_bonus") or 0)
            + one_goal_score_adjustment
        )
        if metric.get("confidence") == "高":
            score += 3
        elif not metric.get("eligible_for_adjustment"):
            score -= 8

        warnings = [str(value) for value in source.get("data_warnings") or []]
        severe_data_risk = any(
            "跳档" in value or "跳至" in value for value in warnings
        )
        current_asian = (source.get("asian") or {}).get("current") or []
        waters = [
            _number(current_asian[index])
            for index in (0, 2) if len(current_asian) > index
        ]
        severe_data_risk = severe_data_risk or any(
            value is not None and (value < 0.55 or value > 1.30)
            for value in waters
        )
        if severe_data_risk:
            score -= 8
        score = round(max(0, min(99, score)))

        policy = draw_selection_policy_profile(
            (source or {}).get("draw_selection_policy")
        )
        min_probability = (
            _number(policy.get("min_probability", {}).get(selection))
            or DRAW_SELECTION_MIN_PROBABILITY.get(selection, 23)
        )
        min_score = (
            _number(policy.get("core_score", {}).get(selection))
            or DRAW_SELECTION_CORE_SCORE.get(selection, 70)
        )
        min_watch_score = (
            _number(policy.get("watch_score", {}).get(selection))
            or DRAW_SELECTION_WATCH_SCORE.get(selection, 52)
        )
        min_value = (
            _number(policy.get("min_value", {}).get(selection))
            or DRAW_SELECTION_MIN_VALUE.get(selection, 0)
        )
        max_risk_count = (
            int(policy.get("max_risk_ids", {}).get(selection, 2))
            if isinstance(policy.get("max_risk_ids", {}).get(selection, 2), int)
            else DRAW_SELECTION_MAX_RISK_IDS.get(selection, 2)
        )
        core = bool(
            history_eligible
            and probability is not None
            and probability >= min_probability
            and odds_value is not None
            and odds_value >= min_value
            and score >= min_score
            and confidence_score >= 55
            and not profile.get("no_bet")
            and not severe_data_risk
            and len(relevant_risks) <= max_risk_count
        )
        watch = bool(
            not core
            and profile
            and (
                (
                    history_eligible
                    and score >= min_watch_score
                )
                or role_signals
            )
        )
        tier = "core" if core else "watch" if watch else "exclude"
        if tier == "core":
            rating = (
                5.0 if score >= 88
                else 4.5 if score >= 79
                else 4.0
            )
        elif tier == "watch":
            rating = 3.5 if score >= 61 else 3.0 if score >= 52 else 2.5
        else:
            rating = 2.5 if score >= 45 else 2.0
        if odds_value is not None and odds_value < 0:
            rating = min(rating, 3.5)

        handicap = _number(
            (source.get("sporttery_handicap") or {}).get("value")
        )
        target_difference = metric.get("target_goal_difference")
        if selection == "平局":
            definition = "双方90分钟战平（净胜球差0）"
        elif target_difference is not None:
            difference = int(float(target_difference))
            definition = (
                f"主队恰好赢{abs(difference)}球"
                if difference > 0 else
                f"客队恰好赢{abs(difference)}球"
                if difference < 0 else "双方90分钟战平"
            )
        elif handicap is not None:
            difference = int(-handicap)
            definition = (
                f"主队恰好赢{abs(difference)}球"
                if difference > 0 else
                f"客队恰好赢{abs(difference)}球"
                if difference < 0 else "双方90分钟战平"
            )
            target_difference = difference
        else:
            definition = "让球数缺失，无法映射精确进球差"

        reason_parts = []
        if role_signals:
            reason_parts.append("、".join(role_signals))
        if probability is not None and market_probability is not None:
            reason_parts.append(
                f"模型{probability:g}% / 市场{market_probability:g}%"
            )
        if odds_value is not None:
            reason_parts.append(
                f"赔率价值{odds_value:+g}%"
            )
        if relevant_risks:
            reason_parts.append("存在热门方不稳盘口信号")
        if context_note:
            reason_parts.append(context_note)
        if draw_band_note:
            reason_parts.append(draw_band_note)
        if league_note:
            reason_parts.append(league_note)
        if odds_band_note:
            reason_parts.append(odds_band_note)
        if sporttery_price_signal.get("note"):
            reason_parts.append(str(sporttery_price_signal["note"]))
        if one_goal_note:
            reason_parts.append(one_goal_note)
        if matched_historical_rules:
            reason_parts.append(
                "历史赔率规则{}项，概率修正{:+g}个百分点".format(
                    len(matched_historical_rules),
                    historical_rule_adjustment,
                )
            )
        draw_band_sample = _number(draw_band_signal.get("sample"))
        effective_sample_candidates = [
            value for value in (
                _number(metric.get("effective_sample")),
                max(historical_rule_samples, default=None),
                draw_band_sample,
            )
            if value is not None
        ]
        effective_sample = (
            max(effective_sample_candidates)
            if effective_sample_candidates else None
        )
        draw_band_confidence = (
            "高" if draw_band_sample is not None and draw_band_sample >= 40
            else "中" if draw_band_sample is not None else None
        )
        if tier == "core":
            reason_parts.append("达到独立核心门槛")
        elif tier == "watch":
            reason_parts.append("仅列观察，不进入组合")
        else:
            reason_parts.append("未达到展示与投注门槛")
        guardrail_alignment = cls._draw_candidate_guardrail_alignment(
            analysis, selection
        )
        return {
            "match_id": str(match.get("match_id") or ""),
            "match_number": match.get("match_number"),
            "selection": selection,
            "model_key": model_key,
            "tier": tier,
            "rating": cls._rating(rating),
            "score": score,
            "probability": round(probability, 2)
            if probability is not None else None,
            "base_probability": round(base_probability, 2)
            if base_probability is not None else None,
            "historical_probability": round(historical_probability, 2)
            if historical_probability is not None else None,
            "market_probability": round(market_probability, 2)
            if market_probability is not None else None,
            "odds": round(odds, 3) if odds is not None else None,
            "odds_value": round(odds_value, 2)
            if odds_value is not None else None,
            "effective_sample": effective_sample,
            "historical_odds_rule_adjustment_pp": round(
                historical_rule_adjustment, 2
            ),
            "historical_odds_rule_ids": matched_historical_rules,
            "historical_odds_rule_signals": (
                historical_rule_profile.get("signals") or []
            ),
            "sporttery_draw_price_signal": sporttery_price_signal,
            "one_goal_margin_signal": one_goal_margin_signal,
            "guardrail_alignment": guardrail_alignment,
            "guardrail_ticket_eligible": bool(
                guardrail_alignment.get("aligned")
            ),
            "confidence": (
                metric.get("confidence")
                if metric.get("eligible_for_adjustment")
                else historical_rule_confidence
                or draw_band_confidence
                or "样本不足"
            ),
            "eligible_for_adjustment": history_eligible,
            "role_signals": role_signals,
            "risk_pattern_ids": relevant_risks,
            "draw_odds_band_signal": draw_band_signal,
            "definition": definition,
            "target_goal_difference": target_difference,
            "reason": "；".join(reason_parts) + "。",
        }

    @classmethod
    def apply_draw_radar(
        cls, matches: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Attach auditable draw candidates without changing the final pick."""
        result = []
        for item in matches:
            row = dict(item or {})
            analysis = dict(row.get("analysis") or {})
            ordinary_draw = cls._apply_draw_radar_candidate_guard(
                cls._draw_radar_candidate(row, "平局")
            )
            handicap_draw = cls._apply_draw_radar_candidate_guard(
                cls._draw_radar_candidate(row, "让平")
            )
            handicap_draw = cls._route_handicap_draw_precision(
                row.get("input_snapshot") or {},
                ordinary_draw,
                handicap_draw,
            )
            analysis["draw_radar"] = {
                "ordinary_draw": ordinary_draw,
                "handicap_draw": handicap_draw,
            }
            row["analysis"] = analysis
            result.append(row)
        return result

    @classmethod
    def _route_handicap_draw_precision(
        cls,
        source: Dict[str, Any],
        ordinary_draw: Dict[str, Any],
        handicap_draw: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Keep exact-margin picks out of the ranking when another path wins.

        ``让平`` is not a generic favourite-risk outcome: the favourite must
        still win by the exact Sporttery handicap margin.  Two recurring
        structures need an explicit final route after both radar candidates
        have been scored:

        * deepening/high-water favourite plus a materially stronger ordinary
          draw value is primarily a favourite-not-winning path;
        * an Asian line a quarter-ball deeper than the Sporttery line, with
          low favourite water and a high total, is primarily a cover path.

        The candidate remains in the per-match audit payload, but it cannot
        enter the daily ranking or a formal ticket.  This keeps the decision
        explainable without allowing a generic odds-band bonus to overrule
        the more specific score-margin evidence.
        """
        result = dict(handicap_draw or {})
        if not result or result.get("tier") == "exclude":
            return result

        one_goal = cls._one_goal_margin_parlay_signal(source)
        if one_goal.get("triggered"):
            result["ranking_eligible"] = True
            return result

        handicap = _number(
            (source.get("sporttery_handicap") or {}).get("value")
        )
        favorite = cls._favorite_market_profile(source)
        favorite_side = str(favorite.get("side") or "")
        favorite_odds = _number(favorite.get("odds"))
        aligned = bool(
            handicap is not None
            and abs(handicap) == 1
            and (
                (favorite_side == "home" and handicap < 0)
                or (favorite_side == "away" and handicap > 0)
            )
        )
        if not aligned or favorite_odds is None:
            result["ranking_eligible"] = True
            return result

        asian = cls._asian_favorite_depth_profile(source, favorite_side)
        current_depth = _number(asian.get("current_depth"))
        favorite_water = _number(asian.get("current_favorite_water"))
        total_line = _number(cls._total_market_profile(source).get("line"))
        hhad_values = (
            (source.get("sporttery_handicap") or {}).get("current")
            or (source.get("sporttery_handicap") or {}).get("initial")
            or []
        )
        cover_index = 0 if favorite_side == "home" else 2
        cover_selection = "让胜" if favorite_side == "home" else "让负"
        cover_odds = (
            _number(hhad_values[cover_index])
            if len(hhad_values) > cover_index else None
        )
        draw_odds = (
            _number(hhad_values[1]) if len(hhad_values) > 1 else None
        )

        risk_ids = {
            str(value) for value in (
                (source.get("current_asian_risk") or {}).get("pattern_ids")
                or []
            )
        }
        regular_ordinary_value = _number(
            (ordinary_draw or {}).get("odds_value")
        )
        regular_ordinary_probability = _number(
            (ordinary_draw or {}).get("probability")
        )
        shadow_ordinary = (
            (source.get("supervised_shadow") or {}).get("ordinary_draw")
            or {}
        )
        shadow_ordinary_value = _number(shadow_ordinary.get("value_edge"))
        shadow_ordinary_probability = _number(
            shadow_ordinary.get("probability")
        )
        shadow_pattern_count = int(
            _number(shadow_ordinary.get("feature_pattern_count")) or 0
        )
        use_shadow_ordinary = bool(
            shadow_pattern_count > 0
            and shadow_ordinary_value is not None
            and shadow_ordinary_value >= 0
            and shadow_ordinary_probability is not None
            and shadow_ordinary_probability >= 20
            and (
                regular_ordinary_value is None
                or shadow_ordinary_value > regular_ordinary_value
            )
        )
        ordinary_value = (
            shadow_ordinary_value
            if use_shadow_ordinary else regular_ordinary_value
        )
        ordinary_probability = (
            shadow_ordinary_probability
            if use_shadow_ordinary else regular_ordinary_probability
        )
        ordinary_signal_source = (
            "监督影子普通平"
            if use_shadow_ordinary else "常规普通平"
        )
        handicap_value = _number(result.get("odds_value"))
        ordinary_route = bool(
            "deepen_high_water" in risk_ids
            and ordinary_value is not None
            and ordinary_value >= 0
            and ordinary_probability is not None
            and ordinary_probability >= 20
            and (
                handicap_value is None
                or ordinary_value >= handicap_value + 6
            )
        )
        cover_route = bool(
            favorite_odds <= 1.50
            and current_depth is not None
            and current_depth >= abs(handicap) + 0.24
            and favorite_water is not None
            and favorite_water <= 0.92
            and total_line is not None
            and total_line >= 2.75
            and cover_odds is not None
            and draw_odds is not None
            and cover_odds <= 2.20
            and draw_odds >= cover_odds + 0.65
        )
        if not ordinary_route and not cover_route:
            result["ranking_eligible"] = True
            return result

        if ordinary_route:
            kind = "ordinary_draw_over_exact_margin"
            preferred_selection = "平局"
            probability_adjustment = -4.0
            score_adjustment = -22.0
            route_reason = (
                f"升盘高水下{ordinary_signal_source}赔率价值"
                f"{ordinary_value:+g}%高于"
                f"让平{(handicap_value or 0):+g}%，风险更像热门不胜，"
                "不是刚好赢1球"
            )
        else:
            kind = "asian_cover_over_exact_margin"
            preferred_selection = cover_selection
            probability_adjustment = -4.0
            score_adjustment = -20.0
            route_reason = (
                f"亚盘热门深度{current_depth:g}球比竞彩让球"
                f"{abs(handicap):g}球深半档，热门水位{favorite_water:g}，"
                f"大小球{total_line:g}且{cover_selection}{cover_odds:g}"
                f"明显低于让平{draw_odds:g}，穿盘路径更强"
            )

        probability = _number(result.get("probability"))
        if probability is not None:
            result["probability"] = round(max(
                0.0, min(100.0, probability + probability_adjustment)
            ), 2)
        odds = _number(result.get("odds"))
        if handicap_value is not None and odds is not None:
            result["odds_value"] = round(
                handicap_value + probability_adjustment * odds,
                2,
            )
        result["score"] = round(max(
            0.0,
            float(result.get("score") or 0) + score_adjustment,
        ))
        result["rating"] = cls._rating(min(
            3.0,
            float(result.get("rating") or 3.0),
        ))
        result["ranking_eligible"] = False
        result["formal_eligible"] = False
        routing_guard = {
            "triggered": True,
            "kind": kind,
            "preferred_selection": preferred_selection,
            "probability_adjustment_pp": probability_adjustment,
            "score_adjustment": score_adjustment,
            "ordinary_signal_source": ordinary_signal_source,
            "reason": route_reason,
            "version": "handicap-draw-precision-routing-v1",
        }
        result["precision_routing_guard"] = routing_guard
        veto_reasons = [
            str(value) for value in result.get("official_veto_reasons") or []
            if str(value).strip()
        ]
        veto_reasons.append(f"精确进球差分流至{preferred_selection}")
        result["official_veto_reasons"] = list(dict.fromkeys(veto_reasons))
        reason = str(result.get("reason") or "").rstrip("。；")
        result["reason"] = (
            (reason + "；" if reason else "")
            + f"{route_reason}，让平退出当日排名。"
        )
        return result

    @classmethod
    def _draw_radar_hard_veto_reasons(
        cls, candidate: Dict[str, Any]
    ) -> List[str]:
        """Return deterministic reasons that forbid formal recommendation."""
        if not candidate:
            return ["缺少平/让平雷达候选"]
        reasons = [
            str(value) for value in candidate.get("official_veto_reasons") or []
            if str(value).strip()
        ]
        draw_band_signal = candidate.get("draw_odds_band_signal") or {}
        if draw_band_signal.get("block_official"):
            reasons.append("历史赔率区间规则明确禁止升级正式推荐")
        if candidate.get("guardrail_ticket_eligible") is False:
            alignment = candidate.get("guardrail_alignment") or {}
            reasons.append(
                str(alignment.get("reason") or "与护栏最终方向冲突")
            )
        odds_value = _number(candidate.get("odds_value"))
        if odds_value is None:
            reasons.append("缺少赔率价值数据，不得升级正式推荐")
        elif odds_value < 0:
            reasons.append("赔率价值为负，不得升级正式推荐")
        if candidate.get("tier") != "core":
            reasons.append("雷达仅为观察层，不能覆盖正式推荐门槛")
        return list(dict.fromkeys(reasons))

    @classmethod
    def _apply_draw_radar_candidate_guard(
        cls, candidate: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Downgrade candidates vetoed by immutable weekly risk guards."""
        result = dict(candidate or {})
        reasons = cls._draw_radar_hard_veto_reasons(result)
        # The tier-only reason describes an already-observational candidate;
        # other reasons can actively downgrade a previously computed core row.
        structural_reasons = [
            value for value in reasons
            if "仅为观察层" not in value
        ]
        if structural_reasons and result.get("tier") == "core":
            result["original_tier"] = "core"
            result["tier"] = "watch"
            result["rating"] = cls._rating(
                min(3.5, float(result.get("rating") or 3.5))
            )
            reason = str(result.get("reason") or "")
            reason = reason.replace("达到独立核心门槛。", "")
            guard_note = "、".join(structural_reasons)
            reason_prefix = reason.rstrip("。；")
            result["reason"] = (
                (reason_prefix + "；" if reason_prefix else "")
                + f"{guard_note}，仅列观察。"
            )
        result["official_veto_reasons"] = (
            cls._draw_radar_hard_veto_reasons(result)
        )
        result["formal_eligible"] = not bool(
            result["official_veto_reasons"]
        )
        return result

    @classmethod
    def attach_draw_radar_summary(
        cls,
        summary: Dict[str, Any],
        matches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Expose core/watch radar rows even when official pools exclude them."""
        result = dict(summary or {})
        radar = {
            "version": "draw-radar-v4-precision-routing",
            "policy": (
                "每天普通平局与竞彩让平分别最多展示前三；同一场只进入"
                "概率和证据更强的一榜。只有核心且非负赔率价值候选可参与"
                "组合，观察层和高风险赔率区间只复盘。"
            ),
            "ordinary_draw": [],
            "handicap_draw": [],
            "excluded_count": {
                "ordinary_draw": 0,
                "handicap_draw": 0,
            },
        }
        candidates_by_match: Dict[str, List[tuple[str, Dict[str, Any]]]] = {}
        for item in matches:
            candidates = (
                (item.get("analysis") or {}).get("draw_radar") or {}
            )
            for key in ("ordinary_draw", "handicap_draw"):
                candidate = dict(candidates.get(key) or {})
                if (
                    candidate.get("tier") == "exclude"
                    or candidate.get("ranking_eligible") is False
                ):
                    radar["excluded_count"][key] += 1
                    continue
                if candidate.get("match_id"):
                    match_id = str(candidate.get("match_id"))
                    candidates_by_match.setdefault(match_id, []).append((
                        key, candidate,
                    ))

        def candidate_strength(
            value: tuple[str, Dict[str, Any]],
        ) -> tuple[bool, bool, bool, float, float, float, float]:
            _, candidate = value
            one_goal = candidate.get("one_goal_margin_signal") or {}
            return (
                candidate.get("tier") == "core",
                candidate.get("guardrail_ticket_eligible") is not False,
                bool(
                    candidate.get("selection") == "让平"
                    and one_goal.get("triggered")
                ),
                float(candidate.get("probability") or 0),
                float(candidate.get("score") or 0),
                float(candidate.get("odds_value") or -999),
                float(candidate.get("effective_sample") or 0),
            )

        for rows in candidates_by_match.values():
            key, candidate = max(rows, key=candidate_strength)
            radar[key].append(candidate)
        for key in ("ordinary_draw", "handicap_draw"):
            radar[key] = sorted(
                radar[key],
                key=lambda item: (
                    item.get("tier") == "core",
                    item.get("guardrail_ticket_eligible") is not False,
                    bool(
                        item.get("selection") == "让平"
                        and (item.get("one_goal_margin_signal") or {}).get(
                            "triggered"
                        )
                    ),
                    float(item.get("score") or 0),
                    float(item.get("probability") or 0),
                ),
                reverse=True,
            )[:RADAR_DISPLAY_LIMITS.get(key, 3)]
        result["draw_radar"] = radar
        return result

    @classmethod
    def attach_draw_parlay_tickets(
        cls,
        summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Persist the exact draw/handicap-draw tickets shown to users.

        The UI used to assemble these tickets at render time, which meant the
        post-match reviewer could not reproduce the actual selections.  Keep
        both tickets in the immutable daily summary so settlement always uses
        the same picks and prices that were available before kick-off.
        """
        result = dict(summary or {})
        radar = result.get("draw_radar") or {}

        def candidates(key: str) -> List[Dict[str, Any]]:
            rows = []
            for raw in radar.get(key) or []:
                match_id = str(raw.get("match_id") or "")
                odds = _number(raw.get("odds"))
                if (
                    not match_id
                    or odds is None
                    or odds <= 1
                    or raw.get("guardrail_ticket_eligible") is False
                ):
                    continue
                item = dict(raw)
                item["match_id"] = match_id
                item["odds"] = round(odds, 3)
                rows.append(item)
            return rows

        ordinary = candidates("ordinary_draw")
        handicap = candidates("handicap_draw")

        def distinct_picks(
            ordinary_count: int,
            handicap_count: int,
        ) -> List[Dict[str, Any]]:
            picks: List[Dict[str, Any]] = []
            used = set()
            for market, rows, limit in (
                ("ordinary_draw", ordinary, ordinary_count),
                ("handicap_draw", handicap, handicap_count),
            ):
                added = 0
                for raw in rows:
                    match_id = str(raw.get("match_id") or "")
                    if not match_id or match_id in used:
                        continue
                    pick = dict(raw)
                    pick["market"] = market
                    pick["selection"] = (
                        "平局" if market == "ordinary_draw" else "让平"
                    )
                    picks.append(pick)
                    used.add(match_id)
                    added += 1
                    if added >= limit:
                        break
            return picks

        def best_available(limit: int) -> List[Dict[str, Any]]:
            """Fill a ticket without forcing a fixed draw/let-draw ratio."""
            ranked = []
            for market, rows in (
                ("ordinary_draw", ordinary),
                ("handicap_draw", handicap),
            ):
                for raw in rows:
                    item = dict(raw)
                    item["market"] = market
                    item["selection"] = (
                        "平局" if market == "ordinary_draw" else "让平"
                    )
                    ranked.append(item)
            ranked.sort(key=lambda item: (
                item.get("tier") == "core",
                float(item.get("score") or 0),
                float(item.get("probability") or 0),
                float(item.get("odds_value") or -999),
            ), reverse=True)
            picks = []
            used = set()
            for item in ranked:
                match_id = str(item.get("match_id") or "")
                if not match_id or match_id in used:
                    continue
                picks.append(item)
                used.add(match_id)
                if len(picks) >= limit:
                    break
            return picks

        def structure_label(picks: List[Dict[str, Any]]) -> str:
            ordinary_count = sum(
                pick.get("market") == "ordinary_draw" for pick in picks
            )
            handicap_count = len(picks) - ordinary_count
            parts = []
            if ordinary_count:
                parts.append(f"{ordinary_count}平")
            if handicap_count:
                parts.append(f"{handicap_count}让平")
            return "+".join(parts)

        def line(picks: List[Dict[str, Any]], indexes: List[int], key: str):
            selected = [picks[index] for index in indexes]
            combined_odds = 1.0
            for pick in selected:
                combined_odds *= float(pick["odds"])
            return {
                "key": key,
                "play": f"{len(indexes)}串1",
                "pick_refs": [{
                    "match_id": pick.get("match_id"),
                    "selection": pick.get("selection"),
                } for pick in selected],
                "combined_odds": round(combined_odds, 2),
            }

        two_three = None
        three_picks = distinct_picks(1, 2)
        if len(three_picks) < 3:
            three_picks = best_available(3)
        if len(three_picks) == 3:
            pair_indexes = ((0, 1), (0, 2), (1, 2))
            lines = [
                line(three_picks, list(indexes), f"pair-{index + 1}")
                for index, indexes in enumerate(pair_indexes)
            ]
            lines.append(line(three_picks, [0, 1, 2], "triple-1"))
            two_three = {
                "key": "draw-two-three",
                "title": "平/让平 3场2、3关",
                "play": "3场2、3关",
                "structure": structure_label(three_picks),
                "picks": three_picks,
                "lines": lines,
                "line_count": 4,
                "stake_units": 4,
            }

        two_leg = None
        two_picks = distinct_picks(1, 1)
        if len(two_picks) < 2:
            two_picks = best_available(2)
        if len(two_picks) == 2:
            two_leg = {
                "key": "draw-two-leg",
                "title": "平/让平二串一",
                "play": "2串1",
                "structure": structure_label(two_picks),
                "picks": two_picks,
                "lines": [line(two_picks, [0, 1], "pair-1")],
                "line_count": 1,
                "stake_units": 1,
            }

        result["draw_parlay_tickets"] = {
            "version": "draw-parlay-ticket-v1",
            "source": "draw_radar",
            "two_three": two_three,
            "two_leg": two_leg,
        }
        return result

    @classmethod
    def attach_supervised_shadow_summary(
        cls,
        summary: Dict[str, Any],
        matches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Expose supervised rankings without changing official selections."""
        result = dict(summary or {})
        groups = {"ordinary_draw": [], "handicap_draw": []}
        high_confidence_candidates = []
        model_meta = {}
        for match in matches or []:
            shadow = (match.get("input_snapshot") or {}).get(
                "supervised_shadow"
            ) or {}
            if not shadow.get("model_id"):
                continue
            model_meta = {
                "model_id": shadow.get("model_id"),
                "model_version": shadow.get("model_version"),
                "sample_count": shadow.get("sample_count"),
                "training_end_date": shadow.get("training_end_date"),
                "status": shadow.get("status") or "shadow",
            }
            for key, selection in (
                ("ordinary_draw", "平局"),
                ("handicap_draw", "让平"),
            ):
                profile = shadow.get(key) or {}
                probability = _number(profile.get("probability"))
                ranking_probability = _number(
                    profile.get("ranking_probability")
                )
                if probability is None or ranking_probability is None:
                    continue
                groups[key].append({
                    "match_id": str(match.get("match_id") or ""),
                    "match_number": match.get("match_number"),
                    "home_team": match.get("home_team"),
                    "away_team": match.get("away_team"),
                    "league": match.get("league"),
                    "selection": selection,
                    "probability": round(probability, 2),
                    "ranking_probability": round(
                        ranking_probability, 2
                    ),
                    "market_probability": profile.get(
                        "market_probability"
                    ),
                    "value_edge": profile.get("value_edge"),
                    "candidate_pool_penalty_pp": profile.get(
                        "candidate_pool_penalty_pp"
                    ),
                    "feature_pattern_count": profile.get(
                        "feature_pattern_count"
                    ),
                    "feature_pattern_probability": profile.get(
                        "feature_pattern_probability"
                    ),
                    "feature_pattern_active": profile.get(
                        "feature_pattern_active"
                    ),
                    "feature_pattern_candidate_probability": profile.get(
                        "feature_pattern_candidate_probability"
                    ),
                    "feature_pattern_adjustment_pp": profile.get(
                        "feature_pattern_adjustment_pp"
                    ),
                    "feature_pattern_candidate_adjustment_pp": profile.get(
                        "feature_pattern_candidate_adjustment_pp"
                    ),
                    "matched_feature_patterns": profile.get(
                        "matched_feature_patterns"
                    ) or [],
                    "target_goal_margin": profile.get(
                        "target_goal_margin"
                    ),
                    "favorite_win_probability": profile.get(
                        "favorite_win_probability"
                    ),
                    "conditional_exact_margin_probability": profile.get(
                        "conditional_exact_margin_probability"
                    ),
                    "quality": shadow.get("quality") or {},
                    "actionable": False,
                })
            single = (
                (match.get("analysis") or {}).get(
                    "high_confidence_single_recommendation"
                )
                or shadow.get("high_confidence_single")
                or {}
            )
            if single.get("selection") in TWO_OPTION_PLAY_SELECTIONS:
                high_confidence_candidates.append({
                    "match_id": str(match.get("match_id") or ""),
                    "match_number": match.get("match_number"),
                    "home_team": match.get("home_team"),
                    "away_team": match.get("away_team"),
                    "league": match.get("league"),
                    "selection": single.get("selection"),
                    "market": single.get("market"),
                    "odds": single.get("odds"),
                    "probability": single.get("probability"),
                    "ranking_probability": single.get(
                        "ranking_probability"
                    ),
                    "model_probability": single.get(
                        "model_probability"
                    ),
                    "market_probability": single.get(
                        "market_probability"
                    ),
                    "value_edge": single.get("value_edge"),
                    "model_market_gap_pp": single.get(
                        "model_market_gap_pp"
                    ),
                    "market_direction_agreement": single.get(
                        "market_direction_agreement"
                    ),
                    "qualified_before_daily_limit": bool(
                        single.get("qualified_before_daily_limit")
                    ),
                    "actionable_before_daily_limit": bool(
                        single.get("actionable")
                        or single.get("actionable_before_daily_limit")
                    ),
                    "policy_active": bool(single.get("policy_active")),
                    "policy_status": single.get("policy_status"),
                    "reason": single.get("reason"),
                    "quality": shadow.get("quality") or {},
                })
        for key in groups:
            groups[key] = sorted(
                groups[key],
                key=lambda item: (
                    float(item.get("ranking_probability") or 0),
                    float(item.get("value_edge") or -999),
                ),
                reverse=True,
            )[:3]

        high_confidence_candidates.sort(
            key=lambda item: (
                bool(item.get("actionable_before_daily_limit")),
                float(item.get("ranking_probability") or 0),
                float(item.get("model_market_gap_pp") or 0),
                float(item.get("value_edge") or -999),
            ),
            reverse=True,
        )
        high_confidence_single = []
        for item in high_confidence_candidates:
            if not item.get("actionable_before_daily_limit"):
                continue
            row = dict(item)
            row["actionable"] = True
            row["daily_rank"] = len(high_confidence_single) + 1
            high_confidence_single.append(row)
            if len(high_confidence_single) >= 2:
                break

        combinations = []
        draw = groups["ordinary_draw"]
        handicap_draw = groups["handicap_draw"]
        if len(draw) >= 2 and handicap_draw:
            picks = [dict(draw[0]), dict(draw[1])]
            used = {str(item.get("match_id") or "") for item in picks}
            let_pick = next(
                (
                    item for item in handicap_draw
                    if str(item.get("match_id") or "") not in used
                ),
                None,
            )
            if let_pick:
                picks.append(dict(let_pick))
                combinations.append({
                    "play": "3场2、3关",
                    "structure": "2平+1让平",
                    "picks": [{
                        "match_id": item.get("match_id"),
                        "match_number": item.get("match_number"),
                        "selection": item.get("selection"),
                        "probability": item.get("probability"),
                        "ranking_probability": item.get(
                            "ranking_probability"
                        ),
                    } for item in picks],
                    "actionable": False,
                    "status": "shadow",
                    "reason": (
                        "仅按校准概率生成影子组合；让平模型及组合发布"
                        "门禁通过前不得进入正式推荐。"
                    ),
                })
        result["supervised_shadow"] = {
            **model_meta,
            "ordinary_draw": groups["ordinary_draw"],
            "handicap_draw": groups["handicap_draw"],
            "high_confidence_single": high_confidence_single,
            "high_confidence_single_candidates": (
                high_confidence_candidates[:5]
            ),
            "high_confidence_single_policy_status": (
                high_confidence_candidates[0].get("policy_status")
                if high_confidence_candidates else "shadow_only"
            ),
            "combinations": combinations,
            "policy": (
                "按不可变赛前快照训练并进行候选池概率收缩；平/让平"
                "继续影子验证，高命中单选只有通过独立验证集60%命中率"
                "与非负ROI门禁后才展示。"
            ),
        }
        return result

    @classmethod
    def _radar_official_level(
        cls,
        candidate: Dict[str, Any],
        match: Dict[str, Any],
    ) -> Optional[str]:
        """Return core/small when a draw radar row can enter formal pools."""
        if not candidate or candidate.get("tier") == "exclude":
            return None
        selection = str(candidate.get("selection") or "")
        if selection not in OFFICIAL_PLAY_SELECTIONS:
            return None
        if not candidate.get("match_id"):
            return None
        if cls._draw_radar_hard_veto_reasons(candidate):
            return None

        analysis = match.get("analysis") or {}
        source = match.get("input_snapshot") or {}
        score = _number(candidate.get("score")) or 0.0
        probability = _number(candidate.get("probability")) or 0.0
        odds_value = _number(candidate.get("odds_value"))
        sample = _number(candidate.get("effective_sample")) or 0.0
        risk_count = len(candidate.get("risk_pattern_ids") or [])
        draw_band_signal = candidate.get("draw_odds_band_signal") or {}
        draw_band_kind = str(draw_band_signal.get("kind") or "")
        market_confidence = _number(
            ((analysis.get("market_confidence") or {}).get("score"))
        )
        if market_confidence is None:
            market_confidence = _number(
                ((((source.get("fae_core") or {}).get("recommendation") or {})
                  .get("market_confidence") or {}).get("score"))
            ) or 0.0

        severe_markers = (
            "风险模型判定危险",
            "盘口或水位异常尚未核验",
            "盘口跳档",
            "极端水位",
            "严禁",
        )
        joined_reasons = "；".join(
            str(value) for value in analysis.get("no_bet_reasons") or []
        )
        if any(marker in joined_reasons for marker in severe_markers):
            return None
        if odds_value is None:
            return None
        minimum_sample = RADAR_OFFICIAL_MIN_SAMPLE.get(selection, 60.0)
        if (
            selection == "平局"
            and draw_band_kind in {
                "backtested_balanced_draw_value",
                "backtested_balanced_draw_secondary",
                "backtested_league_draw_value",
                "backtested_league_draw_secondary",
            }
        ):
            minimum_sample = ORDINARY_DRAW_RULE_MIN_SAMPLE
        if selection == "让平" and draw_band_kind in HANDICAP_DRAW_FORMAL_KINDS:
            minimum_sample = HANDICAP_DRAW_RULE_MIN_SAMPLE
        if sample < minimum_sample:
            return None
        max_risk_count = (
            2 if draw_band_kind == "cold_draw"
            else RADAR_OFFICIAL_MAX_RISK_IDS.get(selection, 1)
        )
        if risk_count > max_risk_count:
            return None
        if market_confidence < RADAR_OFFICIAL_MIN_MARKET_CONFIDENCE:
            return None
        if selection == "平局":
            if draw_band_kind in {
                "backtested_balanced_draw_value",
                "backtested_balanced_draw_secondary",
                "backtested_league_draw_value",
                "backtested_league_draw_secondary",
            }:
                official_score_min = (
                    _number(draw_band_signal.get("official_score_min")) or 84.0
                )
                if score >= official_score_min:
                    if draw_band_kind in {
                        "backtested_balanced_draw_value",
                        "backtested_league_draw_value",
                    }:
                        return "core"
                    return "small"
            return None

        if selection == "让平":
            if draw_band_kind in HANDICAP_DRAW_FORMAL_KINDS:
                official_score_min = (
                    _number(draw_band_signal.get("official_score_min")) or 84.0
                )
                if score >= official_score_min:
                    if draw_band_kind in HANDICAP_DRAW_FORMAL_CORE_KINDS:
                        return "core"
                    return "small"
            return None

        if (
            candidate.get("tier") == "core"
            and odds_value >= 0
            and score >= DRAW_SELECTION_CORE_SCORE.get(selection, 72.0)
        ):
            return "core"

        if (
            score >= RADAR_OFFICIAL_SMALL_MIN_SCORE.get(selection, 90.0)
            and probability >= RADAR_OFFICIAL_SMALL_MIN_PROBABILITY.get(
                selection, 29.0
            )
            and odds_value >= RADAR_OFFICIAL_SMALL_MIN_VALUE.get(
                selection, -8.0
            )
        ):
            return "small"
        return None

    @classmethod
    def _radar_rank_score(cls, candidate: Dict[str, Any], level: str) -> float:
        score = _number(candidate.get("score")) or 0.0
        probability = _number(candidate.get("probability")) or 0.0
        odds_value = _number(candidate.get("odds_value")) or 0.0
        value_component = max(-5.0, min(8.0, odds_value)) * 2.0
        core_bonus = 10.0 if level == "core" else 0.0
        draw_band = candidate.get("draw_odds_band_signal") or {}
        band_bonus = {
            "backtested_balanced_draw_value": 10.0,
            "backtested_balanced_draw_secondary": 4.0,
            "backtested_league_draw_value": 10.0,
            "backtested_league_draw_secondary": 4.0,
            "backtested_league_one_goal_value": 10.0,
            "backtested_league_one_goal_secondary": 4.0,
            "backtested_league_handicap_draw_value": 10.0,
            "backtested_league_handicap_draw_secondary": 4.0,
            "backtested_hhad_plus1_low_odds_value": 10.0,
            "backtested_hhad_small_rise_value": 10.0,
            "backtested_hhad_minus1_draw_band": 4.0,
        }.get(str(draw_band.get("kind") or ""), 0.0)
        return round(
            score + probability * 0.5 + value_component + core_bonus
            + band_bonus,
            3,
        )

    @classmethod
    def _radar_pool_item(
        cls,
        match: Dict[str, Any],
        candidate: Dict[str, Any],
        level: str,
    ) -> Dict[str, Any]:
        selection = str(candidate.get("selection") or "")
        score = _number(candidate.get("score"))
        probability = _number(candidate.get("probability"))
        odds = candidate.get("odds")
        odds_value = _number(candidate.get("odds_value"))
        role = "核心" if level == "core" else "小试"
        rating = cls._rating(
            candidate.get("rating") if level == "core"
            else min(3.5, float(candidate.get("rating") or 3.5))
        )
        value_text = (
            f"，赔率价值{odds_value:+g}%"
            if odds_value is not None else ""
        )
        reason = (
            f"{match.get('match_number') or candidate.get('match_id')}"
            f"{selection}{role}：雷达{score:g}分，概率{probability:g}%"
            f"，赔率{odds if odds is not None else '--'}{value_text}；"
            f"{str(candidate.get('reason') or '').replace('仅列观察，不进入组合。', '').strip()}"
        )
        return {
            "match_id": str(candidate.get("match_id") or ""),
            "match_number": match.get("match_number") or candidate.get("match_number"),
            "selection": selection,
            "rating": rating,
            "reason": reason,
            "role": role,
            "radar_official_level": level,
            "radar_rank_score": cls._radar_rank_score(candidate, level),
        }

    @classmethod
    def apply_draw_radar_recommendation_overrides(
        cls,
        matches: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Promote independently qualified 平/让平 radar picks to match level."""
        gate_markers = (
            "正式门槛",
            "未达到投注门槛",
            "非平/让平玩法",
            "缺少主选对应的赔率价值数据",
            "全部玩法均未达到投注门槛",
        )
        prepared = []
        base_rows = []

        def candidate_priority(candidate: Dict[str, Any]) -> tuple:
            draw_band = candidate.get("draw_odds_band_signal") or {}
            return (
                draw_band.get("kind") == "backtested_balanced_draw_value",
                draw_band.get("kind") == "backtested_league_draw_value",
                draw_band.get("kind") == "backtested_balanced_draw_secondary",
                draw_band.get("kind") == "backtested_league_draw_secondary",
                draw_band.get("kind") == "backtested_league_one_goal_value",
                draw_band.get("kind") == "backtested_league_handicap_draw_value",
                draw_band.get("kind") == "backtested_hhad_plus1_low_odds_value",
                draw_band.get("kind") == "backtested_hhad_small_rise_value",
                draw_band.get("kind") == "backtested_hhad_minus1_draw_band",
                draw_band.get("kind") == "backtested_league_one_goal_secondary",
                draw_band.get("kind") == "backtested_league_handicap_draw_secondary",
                candidate.get("radar_official_level") == "core",
                float(candidate.get("radar_rank_score") or 0),
                float(candidate.get("score") or 0),
            )

        for index, item in enumerate(matches):
            row = dict(item or {})
            analysis = dict(row.get("analysis") or {})
            analysis.pop("radar_recommendation", None)
            row["analysis"] = analysis
            primary = str(analysis.get("primary_play") or "")
            radar_key = (
                "ordinary_draw" if primary == "平局"
                else "handicap_draw" if primary == "让平"
                else ""
            )
            primary_candidate = dict(
                ((analysis.get("draw_radar") or {}).get(radar_key) or {})
            )
            if radar_key and not cls._radar_official_level(
                primary_candidate, row
            ):
                veto_reasons = cls._draw_radar_hard_veto_reasons(
                    primary_candidate
                )
                if not veto_reasons:
                    veto_reasons = ["平/让平雷达未达到正式核心硬门槛"]
                existing_reasons = [
                    str(value)
                    for value in analysis.get("no_bet_reasons") or []
                ]
                analysis.update({
                    "decision": "观察",
                    "no_bet": True,
                    "no_bet_reasons": list(dict.fromkeys(
                        existing_reasons + veto_reasons
                    )),
                    "rating": cls._rating(min(
                        3.5, float(analysis.get("rating") or 3.5)
                    )),
                    "formal_veto": {
                        "selection": primary,
                        "reasons": veto_reasons,
                    },
                })
                analysis["star_text"] = cls._stars(analysis["rating"])
                row["analysis"] = analysis
            base_rows.append(row)
            radar = analysis.get("draw_radar") or {}
            candidates = []
            for key in ("ordinary_draw", "handicap_draw"):
                candidate = dict(radar.get(key) or {})
                level = cls._radar_official_level(candidate, row)
                if not level:
                    continue
                candidate["radar_official_level"] = level
                candidate["radar_rank_score"] = cls._radar_rank_score(
                    candidate, level
                )
                candidates.append(candidate)
            if not candidates:
                continue
            candidate = sorted(
                candidates,
                key=candidate_priority,
                reverse=True,
            )[0]
            prepared.append((index, row, candidate))

        selected_indexes = set()
        for selection in ("平局", "让平"):
            selection_rows = [
                value for value in prepared
                if str(value[2].get("selection") or "") == selection
            ]
            selection_rows = sorted(
                selection_rows,
                key=lambda value: candidate_priority(value[2]),
                reverse=True,
            )[:RADAR_OFFICIAL_POOL_LIMITS.get(selection, 2)]
            selected_indexes.update(value[0] for value in selection_rows)

        by_index = {
            index: (row, candidate)
            for index, row, candidate in prepared
            if index in selected_indexes
        }
        result = []
        for index, item in enumerate(base_rows):
            row = dict(item or {})
            analysis = dict(row.get("analysis") or {})
            analysis.pop("radar_recommendation", None)
            if index not in by_index:
                row["analysis"] = analysis
                result.append(row)
                continue
            row, candidate = by_index[index]
            analysis = dict(row.get("analysis") or {})
            level = str(candidate.get("radar_official_level") or "small")
            selection = str(candidate.get("selection") or "")
            rating = cls._rating(
                candidate.get("rating") if level == "core"
                else min(3.5, float(candidate.get("rating") or 3.5))
            )
            existing_reasons = [
                str(value) for value in analysis.get("no_bet_reasons") or []
                if not any(marker in str(value) for marker in gate_markers)
            ]
            adjustments = [
                str(value) for value in analysis.get("rating_adjustments") or []
                if "未达到投注门槛" not in str(value)
            ]
            adjustments.append(
                f"平/让平雷达{('核心' if level == 'core' else '小试')}升级，"
                "综合主选门槛不再一票否决"
            )
            analysis.update({
                "primary_play": selection,
                "decision": "可考虑",
                "no_bet": False,
                "no_bet_reasons": existing_reasons,
                "rating": rating,
                "star_text": cls._stars(rating),
                "rating_adjustments": list(dict.fromkeys(adjustments)),
                "prediction_probability": candidate.get("probability"),
                "odds": candidate.get("odds"),
                "market_implied_probability": candidate.get(
                    "market_probability"
                ),
                "value_edge": candidate.get("odds_value"),
                "radar_recommendation": cls._radar_pool_item(
                    row, candidate, level
                ),
            })
            if selection == "让平":
                analysis["handicap_play"] = "让平"
            secondary_decision = cls._secondary_play_decision(
                row.get("input_snapshot") or {},
                selection,
                analysis.get("secondary_play"),
            )
            analysis["secondary_play"] = secondary_decision["selection"]
            analysis["secondary_selection_guard"] = secondary_decision
            analysis["score_candidates"] = cls._compatible_scores(
                analysis, row.get("input_snapshot") or {}
            )
            if level == "core":
                analysis["bet_score"] = max(
                    round(_number(analysis.get("bet_score")) or 0),
                    int(OFFICIAL_MIN_BET_SCORE),
                )
                analysis["value_score"] = max(
                    round(_number(analysis.get("value_score")) or 0),
                    int(OFFICIAL_MIN_VALUE_SCORE),
                )
            analysis["verdict"] = cls._label_probability_language(
                cls._calibrated_verdict(row, analysis)
            )
            row["analysis"] = analysis
            result.append(row)
        return result

    @classmethod
    def promote_draw_radar_recommendations(
        cls,
        summary: Dict[str, Any],
        matches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Put qualified radar candidates back into official 平/让平 pools."""
        result = dict(summary or {})
        pools = {
            key: list(items or [])
            for key, items in (result.get("pools") or {}).items()
        }
        by_selection = {"平局": [], "让平": []}
        eligible_ids = {"平局": set(), "让平": set()}
        promoted_ids = set()
        for match in matches:
            analysis = match.get("analysis") or {}
            primary = str(analysis.get("primary_play") or "")
            match_id = str(match.get("match_id") or "")
            if (
                primary in eligible_ids
                and match_id
                and not analysis.get("no_bet")
            ):
                eligible_ids[primary].add(match_id)
            official = analysis.get("radar_recommendation") or {}
            selection = str(official.get("selection") or "")
            if selection not in by_selection:
                continue
            row = dict(official)
            row["match_id"] = str(match.get("match_id") or row.get("match_id") or "")
            if not row["match_id"]:
                continue
            by_selection[selection].append(row)

        for selection, key in (("平局", "draw"), ("让平", "handicap_draw")):
            existing = {
                str(item.get("match_id") or ""): dict(item)
                for item in pools.get(key) or []
                if str(item.get("match_id") or "")
                in eligible_ids[selection]
            }
            rows = sorted(
                by_selection[selection],
                key=lambda item: (
                    item.get("radar_official_level") == "core",
                    float(item.get("radar_rank_score") or 0),
                    float(item.get("rating") or 0),
                ),
                reverse=True,
            )[:RADAR_OFFICIAL_POOL_LIMITS.get(selection, 2)]
            for row in rows:
                existing[str(row.get("match_id") or "")] = row
                promoted_ids.add(str(row.get("match_id") or ""))
            pools[key] = sorted(
                existing.values(),
                key=lambda item: (
                    item.get("radar_official_level") == "core",
                    float(item.get("radar_rank_score") or 0),
                    float(item.get("rating") or 0),
                ),
                reverse=True,
            )[:RADAR_OFFICIAL_POOL_LIMITS.get(selection, 2)]

        if promoted_ids:
            pools["avoid"] = [
                item for item in pools.get("avoid") or []
                if str(item.get("match_id") or "") not in promoted_ids
            ]
        result["pools"] = pools
        result["two_option_combinations"] = (
            cls.build_two_option_combinations(matches)
        )

        promoted = sorted(
            [
                item for key in ("handicap_draw", "draw")
                for item in pools.get(key) or []
                if item.get("radar_official_level")
            ],
            key=lambda item: (
                item.get("radar_official_level") == "core",
                float(item.get("radar_rank_score") or 0),
            ),
            reverse=True,
        )
        if promoted:
            core_text = []
            small_text = []
            for item in promoted:
                label = (
                    item.get("match_number")
                    or item.get("match_id")
                    or "本场"
                )
                text = (
                    f"{label}{item.get('selection')}"
                    f"{float(item.get('rating') or 0):g}星"
                )
                if item.get("radar_official_level") == "core":
                    core_text.append(text)
                else:
                    small_text.append(text)
            conclusion = []
            if core_text:
                conclusion.append("雷达核心：" + "、".join(core_text))
            if small_text:
                conclusion.append("小试候选：" + "、".join(small_text[:4]))
            avoid_labels = [
                str(item.get("match_number") or item.get("match_id"))
                for item in pools.get("avoid") or []
            ]
            if avoid_labels:
                conclusion.append(
                    "其余" + str(len(avoid_labels)) + "场仅保留方向观察"
                )
            result["core_conclusion"] = "；".join(conclusion) + "。"
            warnings = list(result.get("warnings") or [])
            warnings.append(
                "平/让平采用雷达分层：核心可进重点，小试降星入池；"
                "严重盘口异常、低盘口可信度或负价值过深仍不推荐。"
            )
            result["warnings"] = list(dict.fromkeys(warnings))[:20]
        return result

    @classmethod
    def attach_league_model_rankings(
        cls,
        summary: Dict[str, Any],
        matches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Expose league-template indexes as a transparent screening layer."""
        result = dict(summary or {})
        rows = []
        for item in matches:
            source = item.get("input_snapshot") or {}
            model = source.get("league_tactical_model") or {}
            indexes = model.get("indexes") or {}
            if not model.get("matched"):
                continue
            match_id = str(item.get("match_id") or "")
            if not match_id:
                continue
            total_direction = str(model.get("total_direction") or "大小球")
            base = {
                "match_id": match_id,
                "match_number": item.get("match_number"),
                "league": item.get("league"),
                "home_team": item.get("home_team"),
                "away_team": item.get("away_team"),
                "league_label": model.get("league_label"),
                "style": model.get("style"),
                "score_templates": model.get("score_templates") or [],
                "conditions": model.get("matched_conditions") or [],
            }
            rows.append({
                **base,
                "bucket": "draw",
                "selection": "平局",
                "index": indexes.get("draw"),
                "reason": (
                    f"{model.get('league_label')}平局模板；"
                    f"常见比分{'、'.join(model.get('score_templates') or [])}"
                ),
            })
            rows.append({
                **base,
                "bucket": "handicap_draw",
                "selection": "让平",
                "index": indexes.get("handicap_draw"),
                "reason": (
                    f"{model.get('league_label')}让平模板；"
                    "结合竞彩让球数看精确一球差"
                ),
            })
            rows.append({
                **base,
                "bucket": "total",
                "selection": total_direction,
                "index": indexes.get("total"),
                "reason": (
                    f"{model.get('league_label')}大小球模板偏{total_direction}"
                ),
            })
            rows.append({
                **base,
                "bucket": "upset",
                "selection": "冷门/热门不穿",
                "index": indexes.get("upset"),
                "reason": (
                    f"{model.get('league_label')}冷门指数；"
                    "只用于降级热门或增加防选"
                ),
            })

        def top(bucket: str, limit: int) -> List[Dict[str, Any]]:
            return sorted(
                [
                    row for row in rows
                    if row["bucket"] == bucket and row.get("index") is not None
                ],
                key=lambda row: float(row.get("index") or 0),
                reverse=True,
            )[:limit]

        result["league_model_rankings"] = {
            "version": LEAGUE_TACTICAL_MODEL_VERSION,
            "policy": (
                "联赛模板指数只作筛选与解释层，不直接进入组合；"
                "正式推荐仍以五市场、价值指数、投注分和数据质量为准。"
            ),
            "draw": top("draw", 2),
            "handicap_draw": top("handicap_draw", 3),
            "total": top("total", 3),
            "upset": top("upset", 3),
        }
        return result

    @classmethod
    def attach_upset_warning_summary(
        cls,
        summary: Dict[str, Any],
        matches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Expose a dedicated upset-warning scanner for favorite downgrades."""
        result = dict(summary or {})
        rows = []
        for item in matches:
            source = item.get("input_snapshot") or {}
            model = source.get("upset_warning_model") or {}
            score = _number(model.get("score"))
            if score is None or score < 40:
                continue
            match_id = str(item.get("match_id") or "")
            if not match_id:
                continue
            factors = [
                factor for factor in model.get("factors") or []
                if isinstance(factor, dict)
            ]
            rows.append({
                "match_id": match_id,
                "match_number": item.get("match_number"),
                "league": item.get("league"),
                "home_team": item.get("home_team"),
                "away_team": item.get("away_team"),
                "score": round(score, 1),
                "level": model.get("level"),
                "favorite_team": model.get("favorite_team"),
                "favorite_side": model.get("favorite_side"),
                "favorite_odds": model.get("favorite_odds"),
                "suggested_defenses": (
                    model.get("suggested_defenses") or []
                ),
                "factor_labels": [
                    str(factor.get("label") or factor.get("key") or "")
                    for factor in factors[:4]
                    if factor.get("label") or factor.get("key")
                ],
                "factors": factors,
                "reason": "；".join(
                    str(factor.get("reason") or "")
                    for factor in factors[:3]
                    if factor.get("reason")
                ),
            })
        result["upset_warning"] = {
            "version": UPSET_WARNING_MODEL_VERSION,
            "policy": (
                "爆冷预警用于识别热门方不稳和下盘风险；"
                "80分以上重点防冷，60-79分观察，不能单独反买。"
            ),
            "items": sorted(
                rows,
                key=lambda row: float(row.get("score") or 0),
                reverse=True,
            )[:8],
        }
        return result

    @classmethod
    def attach_odds_band_summary(
        cls,
        summary: Dict[str, Any],
        matches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Expose odds-band indicators requested for upset/draw scanning."""
        result = dict(summary or {})
        rows = []
        labels = {
            "favorite_heat": "热门过热指数",
            "underdog_upset": "下盘爆冷指数",
            "handicap_draw_value": "让平价值指数",
        }
        selections = {
            "favorite_heat": "热门降级观察",
            "underdog_upset": "防平/防下盘",
            "handicap_draw_value": "让平扫描",
        }
        for item in matches:
            source = item.get("input_snapshot") or {}
            model = source.get("odds_band_model") or {}
            if not model.get("available") and source:
                raw_match = _odds_band_match_from_input(source)
                model = _build_odds_band_model(
                    raw_match,
                    _number(
                        (source.get("sporttery_handicap") or {}).get("value")
                    ),
                    source.get("current_asian_risk") or {},
                )
            if not model.get("available"):
                continue
            indexes = model.get("indexes") or {}
            levels = model.get("levels") or {}
            favorite = model.get("favorite") or {}
            signals = [
                signal for signal in model.get("signals") or []
                if isinstance(signal, dict)
            ]
            match_id = str(item.get("match_id") or "")
            if not match_id:
                continue
            base = {
                "match_id": match_id,
                "match_number": item.get("match_number"),
                "league": item.get("league"),
                "home_team": item.get("home_team"),
                "away_team": item.get("away_team"),
                "favorite_team": favorite.get("team"),
                "favorite_odds": favorite.get("current_odds"),
                "favorite_band": favorite.get("band_label"),
                "draw_odds": (model.get("draw_odds") or {}).get("current"),
                "handicap_draw_odds": model.get("handicap_draw_odds"),
                "suggested_focus": model.get("suggested_focus") or [],
                "signal_labels": [
                    str(signal.get("label") or signal.get("key") or "")
                    for signal in signals[:4]
                    if signal.get("label") or signal.get("key")
                ],
                "reason": "；".join(
                    str(signal.get("reason") or "")
                    for signal in signals[:3]
                    if signal.get("reason")
                ),
            }
            for key, title in labels.items():
                index = _number(indexes.get(key))
                if index is None or index < 45:
                    continue
                rows.append({
                    **base,
                    "bucket": key,
                    "title": title,
                    "selection": selections[key],
                    "index": round(index, 1),
                    "level": levels.get(key) or _index_level(index),
                })

        def top(bucket: str, limit: int) -> List[Dict[str, Any]]:
            return sorted(
                [row for row in rows if row.get("bucket") == bucket],
                key=lambda row: float(row.get("index") or 0),
                reverse=True,
            )[:limit]

        result["odds_band_indicators"] = {
            "version": ODDS_BAND_MODEL_VERSION,
            "policy": (
                "赔率区间指标用于识别危险赔率带：热门过热、下盘爆冷、"
                "让平价值分别排序；指标高只代表需要降级热门或纳入扫描，"
                "不等于直接投注。"
            ),
            "favorite_heat": top("favorite_heat", 5),
            "underdog_upset": top("underdog_upset", 5),
            "handicap_draw_value": top("handicap_draw_value", 5),
        }
        return result

    @staticmethod
    def _predicted_result(source: Dict[str, Any]) -> str:
        probabilities = (
            (source.get("fae_core") or {}).get("probabilities") or {}
        )
        candidates = {
            "主胜": _number(probabilities.get("home_win")),
            "平局": _number(probabilities.get("draw")),
            "客胜": _number(probabilities.get("away_win")),
        }
        valid = {
            key: value for key, value in candidates.items()
            if value is not None
        }
        return max(valid, key=valid.get) if valid else "观望"

    @classmethod
    def _score_matches_selection(
        cls,
        score: Any,
        selection: str,
        source: Dict[str, Any],
    ) -> bool:
        parsed = re.fullmatch(r"(\d{1,2}):(\d{1,2})", str(score or ""))
        if not parsed:
            return False
        home, away = int(parsed.group(1)), int(parsed.group(2))
        if selection == "主胜":
            return home > away
        if selection == "平局":
            return home == away
        if selection == "客胜":
            return home < away
        if selection in {"让胜", "让平", "让负"}:
            handicap = _number(
                (source.get("sporttery_handicap") or {}).get("value")
            )
            if handicap is None:
                return False
            adjusted = home + handicap - away
            actual = "让胜" if adjusted > 0 else "让负" if adjusted < 0 else "让平"
            return selection == actual
        if selection in {"大球", "小球"}:
            values = (
                (source.get("total") or {}).get("current")
                or (source.get("total") or {}).get("initial") or []
            )
            line = _number(values[1]) if len(values) > 1 else None
            if line is None or home + away == line:
                return False
            return (
                selection == "大球" and home + away > line
            ) or (
                selection == "小球" and home + away < line
            )
        return True

    @classmethod
    def _compatible_scores(
        cls,
        analysis: Dict[str, Any],
        source: Dict[str, Any],
    ) -> List[str]:
        selection = str(analysis.get("primary_play") or "观望")
        candidates = list(analysis.get("score_candidates") or [])
        candidates += list(
            (source.get("fae_core") or {}).get("score_candidates") or []
        )
        return list(dict.fromkeys(
            str(score) for score in candidates
            if cls._score_matches_selection(score, selection, source)
        ))[:3]

    @classmethod
    def _probability_single_profile(
        cls,
        source: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Pick the highest-probability result option above the odds floor.

        This forecast is intentionally independent from bet_score/value_score.
        It blends the model estimate with the de-vig market probability and is
        used by the all-match single review.  The value-bet primary remains in
        ``primary_play`` for the formal recommendation pipeline.
        """
        raw_categories = (
            (((source.get("fae_core") or {}).get("recommendation") or {})
             .get("category_scores") or [])
        )
        all_candidates = []
        excluded_low_odds = []
        for raw in raw_categories:
            label = str(raw.get("label") or "")
            if label not in TWO_OPTION_PLAY_SELECTIONS:
                continue
            odds = _number(raw.get("odds"))
            if odds is None:
                continue
            profile = cls._historical_adjusted_profile(
                source, dict(raw)
            )
            model_probability = _number(profile.get("probability"))
            market_probability = _number(
                profile.get("market_implied_probability")
            )
            if model_probability is None and market_probability is None:
                continue
            if model_probability is None:
                blended_probability = market_probability
            elif market_probability is None:
                blended_probability = model_probability
            else:
                blended_probability = (
                    model_probability * SINGLE_MODEL_WEIGHT
                    + market_probability * SINGLE_MARKET_WEIGHT
                )
            candidate = {
                "selection": label,
                "market": (
                    "竞彩让球" if label in {"让胜", "让平", "让负"}
                    else "胜平负"
                ),
                "odds": round(odds, 3),
                "model_probability": (
                    round(model_probability, 2)
                    if model_probability is not None else None
                ),
                "market_probability": (
                    round(market_probability, 2)
                    if market_probability is not None else None
                ),
                "probability": round(blended_probability, 2),
            }
            all_candidates.append(candidate)
            if odds < SINGLE_MIN_ODDS:
                excluded_low_odds.append({
                    "selection": label,
                    "odds": round(odds, 3),
                })

        candidates = [
            item for item in all_candidates
            if float(item.get("odds") or 0) >= SINGLE_MIN_ODDS
        ]
        if not candidates:
            return {
                "selection": "观望",
                "secondary_selection": "观望",
                "minimum_odds": SINGLE_MIN_ODDS,
                "excluded_low_odds": excluded_low_odds,
                "reason": "没有赔率不低于1.50且概率可核验的结果玩法",
            }

        ranked = sorted(
            candidates,
            key=lambda item: (
                float(item.get("probability") or 0),
                float(item.get("market_probability") or 0),
                -float(item.get("odds") or 99),
            ),
            reverse=True,
        )
        all_ranked = sorted(
            all_candidates,
            key=lambda item: (
                float(item.get("probability") or 0),
                float(item.get("market_probability") or 0),
                -float(item.get("odds") or 99),
            ),
            reverse=True,
        )
        primary = ranked[0]
        short_favorite_guard = {
            "triggered": False,
            "short_favorite": None,
            "allowed_handicap_selection": None,
            "minimum_handicap_probability": (
                SINGLE_SHORT_FAVORITE_HANDICAP_MIN_PROBABILITY
            ),
        }
        raw_leader = all_ranked[0] if all_ranked else {}
        raw_leader_selection = str(raw_leader.get("selection") or "")
        raw_leader_odds = _number(raw_leader.get("odds"))
        if (
            raw_leader_selection in {"主胜", "客胜"}
            and raw_leader_odds is not None
            and raw_leader_odds < SINGLE_MIN_ODDS
        ):
            handicap = _number(
                (source.get("sporttery_handicap") or {}).get("value")
            )
            allowed_handicap_selection = None
            if (
                raw_leader_selection == "主胜"
                and handicap is not None
                and handicap < 0
            ):
                allowed_handicap_selection = "让胜"
            elif (
                raw_leader_selection == "客胜"
                and handicap is not None
                and handicap > 0
            ):
                allowed_handicap_selection = "让负"
            selected_probability = _number(primary.get("probability"))
            independently_supported = bool(
                primary.get("selection") == allowed_handicap_selection
                and selected_probability is not None
                and selected_probability
                >= SINGLE_SHORT_FAVORITE_HANDICAP_MIN_PROBABILITY
            )
            short_favorite_guard = {
                "triggered": not independently_supported,
                "short_favorite": raw_leader_selection,
                "short_favorite_odds": round(raw_leader_odds, 3),
                "proposed_selection": primary.get("selection"),
                "proposed_probability": selected_probability,
                "allowed_handicap_selection": allowed_handicap_selection,
                "minimum_handicap_probability": (
                    SINGLE_SHORT_FAVORITE_HANDICAP_MIN_PROBABILITY
                ),
                "reason": (
                    "低于1.50的热门只代表投注价值不足，不能据此反推"
                    "平局、让平或热门不穿；当前仍保留概率最高的可投注"
                    "替代项，并标记其未独立达到热门穿盘确认条件"
                ),
            }
        same_market = [
            item for item in ranked[1:]
            if item.get("market") == primary.get("market")
        ]
        secondary = same_market[0] if same_market else {}
        return {
            "selection": primary.get("selection") or "观望",
            "secondary_selection": (
                secondary.get("selection") or "观望"
            ),
            "market": primary.get("market"),
            "odds": primary.get("odds"),
            "secondary_odds": secondary.get("odds"),
            "probability": primary.get("probability"),
            "secondary_probability": secondary.get("probability"),
            "model_probability": primary.get("model_probability"),
            "market_probability": primary.get("market_probability"),
            "minimum_odds": SINGLE_MIN_ODDS,
            "model_weight": SINGLE_MODEL_WEIGHT,
            "market_weight": SINGLE_MARKET_WEIGHT,
            "excluded_low_odds": excluded_low_odds,
            "short_favorite_guard": short_favorite_guard,
            "candidates": ranked,
            "reason": (
                short_favorite_guard.get("reason")
                if short_favorite_guard.get("triggered") else
                f"过滤赔率低于{SINGLE_MIN_ODDS:.2f}的选项后，"
                "按模型35%+市场去水概率65%选择单场最高概率项"
            ),
        }

    @classmethod
    def _value_selection_guard(
        cls,
        source: Dict[str, Any],
        model_selection: str,
    ) -> tuple[str, Dict[str, Any]]:
        """Prefer a materially stronger bettable option over raw prediction."""
        policy = draw_selection_policy_profile((source or {}).get("draw_selection_policy"))
        allowed = TWO_OPTION_PLAY_SELECTIONS
        categories = [
            cls._historical_adjusted_profile(source, dict(item))
            for item in (
                (((source.get("fae_core") or {}).get("recommendation") or {})
                 .get("category_scores") or [])
            )
            if str(item.get("label") or "") in allowed
        ]
        current = next(
            (
                item for item in categories
                if str(item.get("label") or "") == model_selection
            ),
            {},
        )
        bettable = [item for item in categories if not item.get("no_bet")]
        if model_selection not in allowed:
            if not categories:
                return "观望", {
                    "triggered": True,
                    "guard_type": "result_market_only",
                    "model_selection": model_selection,
                    "effective_selection": "观望",
                    "no_bet_only": True,
                    "reason": (
                        f"主选{model_selection or '为空'}不属于胜平负或竞彩让球，"
                        "且缺少可核验结果玩法，改为观望"
                    ),
                }
            pool = bettable or categories
            best_result = max(
                pool,
                key=lambda item: (
                    float(item.get("bet_score") or item.get("score") or 0),
                    float(item.get("value_score") or 0),
                    float(item.get("probability") or 0),
                ),
            )
            best_selection = str(best_result.get("label") or "观望")
            return best_selection, {
                "triggered": True,
                "guard_type": "result_market_only",
                "model_selection": model_selection,
                "effective_selection": best_selection,
                "effective_bet_score": float(
                    best_result.get("bet_score")
                    or best_result.get("score") or 0
                ),
                "no_bet_only": not bool(bettable),
                "reason": (
                    f"主选{model_selection}不属于胜平负或竞彩让球，"
                    f"按结果玩法评分改为{best_selection}"
                ),
            }
        if not bettable:
            if not categories:
                return model_selection, {
                    "triggered": False,
                    "model_selection": model_selection,
                    "effective_selection": model_selection,
                    "no_bet_only": True,
                    "reason": "没有可核验赔率价值的竞彩候选，保留方向观察并标记不下注",
                }
            best_observation = max(
                categories,
                key=lambda item: (
                    float(item.get("bet_score") or item.get("score") or 0),
                    float(item.get("prediction_score") or 0),
                ),
            )
            best_selection = str(
                best_observation.get("label") or model_selection
            )
            return best_selection, {
                "triggered": best_selection != model_selection,
                "model_selection": model_selection,
                "effective_selection": best_selection,
                "effective_bet_score": float(
                    best_observation.get("bet_score")
                    or best_observation.get("score") or 0
                ),
                "no_bet_only": True,
                "reason": (
                    f"全部玩法未达到投注门槛，保留{best_selection}作为"
                    "方向观察，但正式结论为不下注"
                ),
            }
        best = max(
            bettable,
            key=lambda item: (
                float(item.get("bet_score") or item.get("score") or 0),
                float(item.get("value_score") or 0),
            ),
        )
        best_selection = str(best.get("label") or model_selection)
        best_profile = cls._historical_adjusted_profile(source, dict(best))
        best_score = float(
            best_profile.get("bet_score")
            or best_profile.get("score")
            or best.get("bet_score")
            or best.get("score")
            or 0
        )
        best_odds_value = _number(best_profile.get("odds_value"))
        best_value_score = float(best_profile.get("value_score") or 0)
        current_score = float(
            current.get("bet_score") or current.get("score") or 0
        )
        current_profile = (
            cls._historical_adjusted_profile(source, dict(current))
            if current else {}
        )
        current_value_profile = float(
            current_profile.get("value_score")
            or current_profile.get("bet_score")
            or current_score
            or 0
        )
        base_triggered = (
            best_selection != model_selection
            and best_score >= 66
            and (best_odds_value is None or best_odds_value >= 0)
            and (
                not current
                or current.get("no_bet")
                or best_score - current_score >= 12
            )
        )
        if best_selection in {"平局", "让平"}:
            if model_selection in {"平局", "让平"}:
                draw_upgrade = False
                gap = float(
                    policy.get("draw_upgrade_gap_from_draw", {}).get(
                        best_selection, 15
                    )
                )
                if (
                    base_triggered
                    and best_score - current_score >= gap
                    and best_value_score >= float(
                        policy.get("draw_upgrade_from_non_draw", {}).get(
                            "best_value_min", 58
                        )
                    )
                    and best_odds_value is not None
                    and best_odds_value >= (
                        float(policy.get("min_value", {}).get(best_selection, 0))
                        if best_selection in {"平局", "让平"}
                        else 0
                    )
                ):
                    draw_upgrade = True
                triggered = draw_upgrade
            else:
                upgrade_policy = policy.get("draw_upgrade_from_non_draw", {})
                draw_upgrade = (
                    best_score >= float(upgrade_policy.get("best_score_min", 72))
                    and best_value_score >= float(upgrade_policy.get("best_value_min", 60))
                    and best_value_score - current_value_profile >= float(
                        upgrade_policy.get("value_gap_min", 8)
                    )
                    and best_score - current_score >= float(
                        upgrade_policy.get("score_gap_min", 18)
                    )
                    and (
                        best_odds_value is None
                        or best_odds_value >= float(
                            policy.get("min_value", {}).get(best_selection, 0)
                        )
                    )
                    and "数据缺失" not in str(best_profile.get("no_bet_reasons") or "")
                )
                triggered = bool(draw_upgrade)
        else:
            draw_upgrade = False
            triggered = base_triggered
        if (
            triggered
            and best_selection in {"平局", "让平"}
            and best_odds_value is not None
            and best_odds_value < float(
                policy.get("min_value", {}).get(best_selection, 0)
            )
        ):
            triggered = False
        if not triggered:
            return model_selection, {
                "triggered": False,
                "model_selection": model_selection,
                "effective_selection": model_selection,
                "candidate_selection": best_selection,
                "candidate_bet_score": round(best_score, 1),
            }
        reason = (
            f"价值护栏：模型原选{model_selection}投注分{current_score:g}，"
            f"{best_selection}投注分{best_score:g}且赔率价值更高，正式推荐改为"
            f"{best_selection}"
        )
        return best_selection, {
            "triggered": True,
            "model_selection": model_selection,
            "effective_selection": best_selection,
            "model_bet_score": round(current_score, 1),
            "effective_bet_score": round(best_score, 1),
            "reason": reason,
        }

    @classmethod
    def calibrate_daily_matches(
        cls, matches: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Apply auditable per-match caps and cross-match star scarcity."""
        calibrated = []
        for item in matches:
            row = dict(item or {})
            analysis = dict(row.get("analysis") or {})
            source = row.get("input_snapshot") or {}
            model_rating = cls._rating(
                analysis.get("model_rating", analysis.get("rating", 1))
            )
            model_primary_play = str(
                analysis.get("model_primary_play")
                or analysis.get("primary_play")
                or "观望"
            )
            value_primary_play, value_guard = cls._value_selection_guard(
                source, model_primary_play
            )
            effective_primary_play, guard = cls._selection_consistency_guard(
                source, value_primary_play
            )
            pre_direction_primary_play = effective_primary_play
            effective_primary_play, direction_guard = (
                cls._directional_precision_guard(
                    source, effective_primary_play
                )
            )
            non_cover_guard = cls._favorite_non_cover_guard(
                source, effective_primary_play
            )
            if non_cover_guard.get("triggered"):
                effective_primary_play = str(
                    non_cover_guard.get("effective_selection")
                    or effective_primary_play
                )
            analysis["model_primary_play"] = model_primary_play
            analysis["primary_play"] = effective_primary_play
            analysis["value_guard"] = value_guard
            analysis["consistency_guard"] = guard
            analysis["directional_precision_guard"] = direction_guard
            analysis["non_cover_guard"] = non_cover_guard
            secondary_hint = analysis.get("secondary_play")
            if direction_guard.get("triggered"):
                secondary_hint = pre_direction_primary_play
            elif (
                guard.get("guard_type")
                == "exact_margin_market_alignment"
            ):
                secondary_hint = guard.get("model_selection")
            elif (
                guard.get("triggered")
                or value_guard.get("triggered")
                or non_cover_guard.get("triggered")
            ):
                secondary_hint = None
            analysis["secondary_play"] = secondary_hint
            value_profile = cls._play_value_profile(
                source, effective_primary_play
            )
            market_confidence = (
                (((source.get("fae_core") or {}).get("recommendation") or {})
                 .get("market_confidence") or {})
            )
            cap = 5.0
            adjustments = []
            historical_risk_notes = []
            decision_guard_notes = [
                str(direction_guard.get("reason") or "")
            ] if direction_guard.get("triggered") else []
            warnings = [str(value) for value in source.get("data_warnings") or []]
            signals = (
                (source.get("fae_core") or {}).get("rule_signals") or []
            )
            signal_ids = {
                str(signal.get("rule_id") or "")
                for signal in signals if isinstance(signal, dict)
            }
            current_asian = (source.get("asian") or {}).get("current") or []
            waters = [
                _number(current_asian[index])
                for index in (0, 2) if len(current_asian) > index
            ]
            extreme_water = any(
                value is not None and (value < 0.60 or value > 1.25)
                for value in waters
            )
            severe_jump = any(
                "跳至" in warning or "跳档" in warning
                for warning in warnings
            )
            inferred_divergence = cls._has_euro_asian_divergence(source)
            if (
                severe_jump
                or extreme_water
                or "market-data-anomaly" in signal_ids
            ):
                cap = min(cap, 3.0)
                adjustments.append(
                    "盘口跳档或极端水位尚未核验，星级上限3星"
                )
            if (
                inferred_divergence
                or "euro-asian-divergence" in signal_ids
                or "handicap-drop" in signal_ids
            ):
                cap = min(cap, 3.5)
                adjustments.append(
                    "欧亚背离或热门方退盘，星级上限3.5星"
                )
            risk = (source.get("fae_core") or {}).get("risk") or {}
            if risk.get("dangerous"):
                cap = min(cap, 2.5)
                adjustments.append("风险模型判定危险，星级上限2.5星")
            elif str(risk.get("level") or "") == "高":
                cap = min(cap, 3.0)
                adjustments.append("综合风险较高，星级上限3星")
            elif str(risk.get("level") or "") == "中":
                cap = min(cap, 4.0)
            market_codes = {
                str(value.get("code") or "")
                for value in (source.get("fae_core") or {}).get("market_types") or []
                if isinstance(value, dict)
            }
            if market_codes.intersection({"D", "E"}):
                cap = min(cap, 3.5)
                adjustments.append("热门过热或深盘高水，星级上限3.5星")
            current_asian_risk = source.get("current_asian_risk") or {}
            league_profile = source.get("league_history_profile") or {}
            historical_patterns = (
                (league_profile.get("asian_risk_patterns") or {})
                .get("patterns") or {}
            )
            matched_pattern_evidence = []
            if league_profile.get("eligible_for_adjustment"):
                for pattern_id in (
                    current_asian_risk.get("pattern_ids") or []
                ):
                    if pattern_id == "no_market_warning":
                        continue
                    metric = historical_patterns.get(pattern_id) or {}
                    sample = int(metric.get("sample") or 0)
                    not_cover_rate = _number(metric.get("not_cover_rate"))
                    if sample < 20 or not_cover_rate is None:
                        continue
                    matched_pattern_evidence.append({
                        "pattern_id": pattern_id,
                        "label": metric.get("label") or pattern_id,
                        "sample": sample,
                        "not_cover_rate": round(not_cover_rate, 1),
                    })
            if matched_pattern_evidence:
                strongest_pattern = max(
                    matched_pattern_evidence,
                    key=lambda value: (
                        value["not_cover_rate"], value["sample"]
                    ),
                )
                historical_risk_notes.append(
                    "联赛{}模式历史不穿率{}%（{}场），仅作条件风险基线".format(
                        strongest_pattern["label"],
                        strongest_pattern["not_cover_rate"],
                        strongest_pattern["sample"],
                    )
                )
                favorite_side = current_asian_risk.get("favorite_side")
                favorite_cover_play = (
                    (favorite_side == "home"
                     and effective_primary_play == "让胜")
                    or
                    (favorite_side == "away"
                     and effective_primary_play == "让负")
                )
                if favorite_cover_play:
                    cap = min(cap, 3.5)
                    adjustments.append(
                        "当前水位模式与联赛历史高样本风险匹配，热门穿盘方向最高3.5星"
                    )
            historical_odds_rules = source.get("historical_odds_rules") or {}
            favorite_history_risks = [
                dict(item)
                for item in historical_odds_rules.get("favorite_risks") or []
                if isinstance(item, dict)
            ]
            away_favorite_risk = next(
                (
                    item for item in favorite_history_risks
                    if item.get("rule_id")
                    == "history-away-favorite-150-209-risk"
                ),
                None,
            )
            if away_favorite_risk and effective_primary_play == "客胜":
                cap = min(cap, 3.5)
                adjustments.append(
                    "客胜1.50-2.09历史表现低于市场预期，客胜方向最高3.5星"
                )
                historical_risk_notes.append(
                    str(away_favorite_risk.get("reason") or "")
                )
            if len(source.get("missing_fundamentals") or []) >= 3:
                cap = min(cap, 4.0)
                adjustments.append("基本面缺失较多，不允许评为五星")
            bet_score = float(
                value_profile.get("bet_score")
                or value_profile.get("score")
                or model_rating * 20
            )
            value_rating = cls._rating(
                value_profile.get("stars", bet_score / 20)
            )
            no_bet_reasons = list(value_profile.get("no_bet_reasons") or [])
            no_bet = bool(
                value_profile.get("no_bet")
                or value_guard.get("no_bet_only")
                or effective_primary_play == "观望"
                or not value_profile
            )
            value_score_number = _number(value_profile.get("value_score"))
            market_confidence_score = (
                _number(market_confidence.get("score")) or 0
            )
            asian_risk_ids = {
                str(value)
                for value in (current_asian_risk.get("pattern_ids") or [])
            }
            asian_hard_downgrade = bool(
                asian_risk_ids & ASIAN_HARD_DOWNGRADE_RISKS
            ) or inferred_divergence
            if value_guard.get("no_bet_only"):
                no_bet_reasons.append("全部玩法均未达到投注门槛")
            if not value_profile:
                no_bet_reasons.append("缺少主选对应的赔率价值数据")
            if effective_primary_play not in OFFICIAL_PLAY_SELECTIONS:
                no_bet = True
                no_bet_reasons.append(
                    "非平/让平玩法仅保留方向观察，不进入正式推荐"
                )
                cap = min(cap, 2.5)
                adjustments.append(
                    "主胜/客胜/让胜/让负不再作为正式推荐，只作方向观察"
                )
            if bet_score < OFFICIAL_MIN_BET_SCORE:
                no_bet = True
                no_bet_reasons.append(
                    f"投注分低于{OFFICIAL_MIN_BET_SCORE:g}分正式门槛"
                )
                cap = min(cap, 2.5)
            if (
                value_score_number is None
                or value_score_number < OFFICIAL_MIN_VALUE_SCORE
            ):
                no_bet = True
                no_bet_reasons.append(
                    f"价值指数低于{OFFICIAL_MIN_VALUE_SCORE:g}分正式门槛"
                )
                cap = min(cap, 2.5)
            if market_confidence_score < OFFICIAL_MIN_MARKET_CONFIDENCE:
                no_bet = True
                no_bet_reasons.append(
                    f"盘口可信度低于{OFFICIAL_MIN_MARKET_CONFIDENCE:g}分正式门槛"
                )
                cap = min(cap, 2.5)
            if risk.get("dangerous"):
                no_bet = True
                no_bet_reasons.append("风险模型判定危险")
            if severe_jump or extreme_water:
                no_bet = True
                no_bet_reasons.append("盘口或水位异常尚未核验")
            if inferred_divergence and bet_score < 70:
                no_bet = True
                no_bet_reasons.append("欧亚背离且投注分不足")
            if (
                asian_hard_downgrade
                and effective_primary_play not in OFFICIAL_PLAY_SELECTIONS
            ):
                no_bet = True
                no_bet_reasons.append(
                    "亚盘不配合触发硬降级，胜负方向不得进入正式推荐"
                )
                cap = min(cap, 2.5)
                adjustments.append("亚盘不配合，胜负方向硬降级为观察")
            if non_cover_guard.get("force_no_bet"):
                no_bet = True
                no_bet_reasons.append(
                    str(
                        non_cover_guard.get("reason")
                        or "热门不穿护栏触发"
                    )
                )
                cap = min(cap, 2.5)
                adjustments.append("热门方向被受让低赔保护项压制，正式结论为不下注")
            if no_bet:
                cap = min(cap, 2.5)
                adjustments.append("未达到投注门槛，正式结论为不下注")
            rating = cls._rating(min(value_rating, cap))
            secondary_decision = cls._secondary_play_decision(
                source,
                effective_primary_play,
                analysis.get("secondary_play"),
            )
            single_profile = cls._probability_single_profile(source)
            analysis.update({
                "model_rating": model_rating,
                "value_rating": value_rating,
                "rating": rating,
                "star_text": cls._stars(rating),
                "rating_adjustments": list(dict.fromkeys(adjustments)),
                "secondary_play": secondary_decision["selection"],
                "secondary_selection_guard": secondary_decision,
                "single_play": single_profile.get("selection", "观望"),
                "single_secondary_play": single_profile.get(
                    "secondary_selection", "观望"
                ),
                "single_odds": single_profile.get("odds"),
                "single_secondary_odds": single_profile.get(
                    "secondary_odds"
                ),
                "single_probability": single_profile.get("probability"),
                "single_secondary_probability": single_profile.get(
                    "secondary_probability"
                ),
                "single_probability_profile": single_profile,
                "handicap_play": cls._handicap_play(
                    source, effective_primary_play
                ),
                "predicted_result": cls._predicted_result(source),
                "prediction_probability": value_profile.get("probability"),
                "odds": value_profile.get("odds"),
                "market_implied_probability": value_profile.get(
                    "market_implied_probability"
                ),
                "value_probability": value_profile.get("value_probability"),
                "value_edge": value_profile.get("value_edge"),
                "expected_return": value_profile.get("expected_return"),
                "value_score": value_profile.get("value_score"),
                "bet_score": round(bet_score),
                "market_confidence": market_confidence,
                "historical_goal_margin": value_profile.get(
                    "historical_goal_margin"
                ),
                "historical_calibration": value_profile.get(
                    "historical_calibration"
                ),
                "historical_odds_rules": historical_odds_rules,
                "league_asian_risk_evidence": matched_pattern_evidence,
                "no_bet": no_bet,
                "no_bet_reasons": list(dict.fromkeys(no_bet_reasons)),
                "decision": "不下注" if no_bet else "可考虑",
            })
            analysis.setdefault("model_verdict", analysis.get("verdict"))
            analysis["score_candidates"] = cls._compatible_scores(
                analysis, source
            )
            analysis["verdict"] = cls._label_probability_language(
                cls._calibrated_verdict(row, analysis)
            )
            analysis["market_analysis"] = {
                key: cls._label_probability_language(value)
                for key, value in (analysis.get("market_analysis") or {}).items()
            }
            analysis["evidence"] = [
                cls._label_probability_language(value)
                for value in analysis.get("evidence") or []
            ]
            if adjustments or historical_risk_notes or decision_guard_notes:
                analysis["risks"] = list(dict.fromkeys(
                    list(analysis.get("risks") or [])
                    + decision_guard_notes
                    + historical_risk_notes
                    + no_bet_reasons
                    + adjustments
                ))[:10]
            row["analysis"] = analysis
            calibrated.append(row)

        def ranking_key(index: int) -> tuple[float, float]:
            analysis = calibrated[index].get("analysis") or {}
            snapshot = calibrated[index].get("input_snapshot") or {}
            core = snapshot.get("fae_core") or {}
            return (
                float(analysis.get("rating") or 0),
                float(core.get("overall_score") or 0),
            )

        ordered = sorted(
            range(len(calibrated)), key=ranking_key, reverse=True
        )
        five_star_seen = 0
        four_plus_seen = 0
        for index in ordered:
            analysis = calibrated[index]["analysis"]
            rating = float(analysis.get("rating") or 0)
            if rating >= 5:
                five_star_seen += 1
                if five_star_seen > 1:
                    rating = 4.5
                    analysis["rating_adjustments"].append(
                        "全日五星最多1场，跨场校准后降为4.5星"
                    )
            if rating >= 4:
                four_plus_seen += 1
                if four_plus_seen > 3:
                    rating = 3.5
                    analysis["rating_adjustments"].append(
                        "全日四星以上最多3场，跨场校准后降为3.5星"
                    )
            analysis["rating"] = cls._rating(rating)
            analysis["star_text"] = cls._stars(analysis["rating"])
            analysis["rating_adjustments"] = list(dict.fromkeys(
                analysis.get("rating_adjustments") or []
            ))
        return calibrated

    @classmethod
    def _calibrated_verdict(
        cls,
        match: Dict[str, Any],
        analysis: Dict[str, Any],
    ) -> str:
        """Render the final decision from normalized fields, not stale prose."""
        label = str(
            match.get("match_number")
            or (match.get("input_snapshot") or {}).get("match_number")
            or match.get("match_id")
            or "本场"
        )
        primary = str(analysis.get("primary_play") or "观望")
        predicted = str(analysis.get("predicted_result") or "观望")
        secondary = str(analysis.get("secondary_play") or "观望")
        handicap = str(analysis.get("handicap_play") or "观望")
        decision = str(analysis.get("decision") or "观望")
        direction = (
            f"方向观察{primary}，正式结论为不下注"
            if analysis.get("no_bet") else f"主选{primary}"
        )
        if secondary not in {"", "观望", primary}:
            direction += f"，同市场防选{secondary}"
        if handicap not in {"", "观望", primary}:
            direction += f"，竞彩让球参考{handicap}"
        metrics = []
        if analysis.get("prediction_probability") is not None:
            metrics.append(
                f"FAE估算概率{analysis['prediction_probability']}%（未校准）"
            )
        if analysis.get("odds") is not None:
            metrics.append(f"即时赔率{analysis['odds']}")
        if analysis.get("market_implied_probability") is not None:
            metrics.append(
                f"市场去水概率{analysis['market_implied_probability']}%"
            )
        if analysis.get("value_score") is not None:
            metrics.append(f"价值指数{analysis['value_score']}分")
        if analysis.get("bet_score") is not None:
            metrics.append(f"投注分{analysis['bet_score']}分")
        confidence = analysis.get("market_confidence") or {}
        if confidence.get("score") is not None:
            metrics.append(
                f"盘口可信度{confidence.get('score')}分"
                f"（{confidence.get('level') or '待定'}）"
            )
        reason = "；".join(
            str(value) for value in analysis.get("no_bet_reasons") or []
        )
        suffix = f"。不下注原因：{reason}" if reason else ""
        return (
            f"{label}最终校准：赛果预测{predicted}；{direction}，"
            f"投注结论{decision}。"
            + "，".join(metrics)
            + suffix
        )

    @classmethod
    def normalize_match_memory_governance(
        cls,
        matches: List[Dict[str, Any]],
        review_memory: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Keep unvalidated review observations out of match conclusions."""
        memory = review_memory or {}
        try:
            validated_count = int(
                memory.get("validated_pattern_count") or 0
            )
        except (TypeError, ValueError):
            validated_count = 0
        if validated_count > 0:
            return matches
        markers = (
            "昨日", "历史复盘", "复盘显示", "历史上频繁",
            "历史反噬", "同模式失误", "命中经验形成共振",
        )
        notice = (
            "近期复盘样本尚未通过跨日且足量验证，仅作风险提醒，"
            "不直接改变本场推荐。"
        )

        def clean_text(value: Any) -> str:
            sentences = re.findall(
                r"[^。！？\n]+[。！？]?", str(value or "")
            )
            return "".join(
                sentence for sentence in sentences
                if not any(marker in sentence for marker in markers)
            ).strip()

        rows = []
        for item in matches:
            row = dict(item or {})
            analysis = dict(row.get("analysis") or {})
            original_risks = list(analysis.get("risks") or [])
            removed_memory = any(
                any(marker in str(value) for marker in markers)
                for value in original_risks
            )
            analysis["verdict"] = clean_text(analysis.get("verdict"))
            analysis["evidence"] = [
                clean_text(value) for value in analysis.get("evidence") or []
                if clean_text(value)
            ]
            analysis["risks"] = list(dict.fromkeys(
                [
                    clean_text(value) for value in original_risks
                    if clean_text(value)
                ] + ([notice] if removed_memory else [])
            ))[:10]
            row["analysis"] = analysis
            rows.append(row)
        return rows

    @classmethod
    def _has_euro_asian_divergence(cls, source: Dict[str, Any]) -> bool:
        euro = source.get("euro") or {}
        current = euro.get("current") or []
        if len(current) < 3:
            return False
        home_odds, away_odds = _number(current[0]), _number(current[2])
        if home_odds is None or away_odds is None:
            return False
        favorite = "home" if home_odds < away_odds else "away"
        asian = source.get("asian") or {}
        initial_values = asian.get("initial") or []
        current_values = asian.get("current") or []
        if len(initial_values) < 2 or len(current_values) < 2:
            return False
        initial_line = cls._handicap_value(initial_values[1])
        current_line = cls._handicap_value(current_values[1])
        if initial_line is None or current_line is None:
            return False
        initial_depth = initial_line if favorite == "home" else -initial_line
        current_depth = current_line if favorite == "home" else -current_line
        return current_depth < initial_depth - 0.20

    @staticmethod
    def _handicap_value(value: Any) -> Optional[float]:
        text = re.sub(r"\s+", "", _clean_handicap(value))
        if not text:
            return None
        numeric = _number(text)
        if numeric is not None and re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
            return numeric
        receiving = text.startswith("受")
        key = text[1:] if receiving else text
        if key not in HANDICAP_VALUES:
            return None
        return -HANDICAP_VALUES[key] if receiving else HANDICAP_VALUES[key]

    @classmethod
    def build_two_option_combinations(
        cls,
        matches: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Pair one core double with a different high-confidence single.

        One unit is assigned to each double-selection path, so the combination
        costs two units.  ``minimum_path_odds`` is therefore the most useful
        guard: values above 2.0 make both winning paths profitable before any
        assumption about which side of the double lands.
        """
        core = [
            item for item in matches
            if (((item.get("analysis") or {})
                 .get("two_option_recommendation") or {})
                .get("actionable"))
            and str(item.get("analysis_source") or "")
            in {"", "volcengine-ark"}
        ]
        rows = []
        for double_match in core:
            double_analysis = double_match.get("analysis") or {}
            double_profile = (
                double_analysis.get("two_option_recommendation") or {}
            )
            double_selections = list(
                double_profile.get("selections") or []
            )[:2]
            double_odds = double_profile.get("odds") or {}
            candidate_rows = {
                str(item.get("selection") or ""): item
                for item in (
                    (double_analysis.get("secondary_selection_guard") or {})
                    .get("candidates") or []
                )
            }
            if len(double_selections) != 2 or any(
                _number(double_odds.get(selection)) is None
                or _number(
                    (candidate_rows.get(selection) or {})
                    .get("model_probability")
                ) is None
                for selection in double_selections
            ):
                continue
            for anchor_match in core:
                if str(anchor_match.get("match_id")) == str(
                    double_match.get("match_id")
                ):
                    continue
                anchor_analysis = anchor_match.get("analysis") or {}
                anchor_selection = str(
                    anchor_analysis.get("primary_play") or ""
                )
                anchor_candidates = (
                    (anchor_analysis.get("secondary_selection_guard") or {})
                    .get("candidates") or []
                )
                anchor_row = next((
                    item for item in anchor_candidates
                    if str(item.get("selection") or "") == anchor_selection
                ), None)
                if not anchor_row:
                    continue
                anchor_odds = _number(anchor_row.get("odds"))
                anchor_probability = _number(
                    anchor_row.get("model_probability")
                )
                anchor_confidence = _number(
                    (anchor_analysis.get("market_confidence") or {})
                    .get("score")
                ) or 0
                anchor_expected_return = (
                    anchor_probability / 100 * anchor_odds
                    if anchor_probability is not None
                    and anchor_odds is not None else None
                )
                if (
                    anchor_odds is None
                    or anchor_probability is None
                    or anchor_probability
                    < TWO_OPTION_COMBO_MIN_ANCHOR_PROBABILITY
                    or anchor_expected_return is None
                    or anchor_expected_return
                    < TWO_OPTION_COMBO_MIN_ANCHOR_EXPECTED_RETURN
                    or anchor_confidence
                    < TWO_OPTION_MIN_MARKET_CONFIDENCE
                ):
                    continue
                path_odds = {
                    selection: round(
                        anchor_odds
                        * float(double_odds.get(selection) or 0), 2
                    )
                    for selection in double_selections
                }
                minimum_path_odds = min(path_odds.values())
                maximum_path_odds = max(path_odds.values())
                joint_coverage = (
                    float(double_profile.get("coverage_score") or 0)
                    * anchor_probability / 100
                )
                if (
                    minimum_path_odds < TWO_OPTION_COMBO_MIN_PATH_ODDS
                    or joint_coverage < TWO_OPTION_COMBO_MIN_JOINT_COVERAGE
                ):
                    continue
                expected_return = sum(
                    float(
                        (candidate_rows.get(selection) or {})
                        .get("model_probability") or 0
                    ) / 100
                    * anchor_probability / 100
                    * float(double_odds.get(selection) or 0)
                    * anchor_odds
                    for selection in double_selections
                )
                expected_roi = (expected_return / 2 - 1) * 100
                path_fit_score = max(
                    0.0,
                    100.0
                    - abs(
                        minimum_path_odds
                        - TWO_OPTION_COMBO_TARGET_PATH_ODDS
                    ) * 30,
                )
                expected_value_score = max(
                    0.0, min(100.0, 100.0 + expected_roi)
                )
                rank_score = (
                    joint_coverage * 0.50
                    + path_fit_score * 0.20
                    + float(double_profile.get("pair_value_score") or 0)
                    * 0.15
                    + anchor_confidence * 0.10
                    + expected_value_score * 0.05
                )
                rows.append({
                    "play": "双选×单选 2串1",
                    "double_pick": {
                        "match_id": str(double_match.get("match_id") or ""),
                        "match_number": double_match.get("match_number"),
                        "selection_text": double_profile.get(
                            "selection_text"
                        ),
                        "selections": double_selections,
                        "odds": double_odds,
                    },
                    "anchor_pick": {
                        "match_id": str(anchor_match.get("match_id") or ""),
                        "match_number": anchor_match.get("match_number"),
                        "selection": anchor_selection,
                        "odds": round(anchor_odds, 3),
                        "model_probability": round(
                            anchor_probability, 2
                        ),
                        "expected_return": round(
                            anchor_expected_return, 3
                        ),
                    },
                    "path_odds": path_odds,
                    "minimum_path_odds": round(minimum_path_odds, 2),
                    "maximum_path_odds": round(maximum_path_odds, 2),
                    "joint_coverage_score": round(joint_coverage, 2),
                    "model_expected_roi": round(expected_roi, 1),
                    "rank_score": round(rank_score, 2),
                    "reason": (
                        f"{double_match.get('match_number')}双选搭配"
                        f"{anchor_match.get('match_number')}"
                        f"{anchor_selection}@{anchor_odds:g}；"
                        f"两条路径最低{minimum_path_odds:.2f}倍，"
                        f"联合覆盖分{joint_coverage:.1f}，"
                        f"锚点期望{anchor_expected_return:.2f}"
                    ),
                })
        rows.sort(
            key=lambda item: (
                float(item.get("rank_score") or 0),
                float(item.get("minimum_path_odds") or 0),
            ),
            reverse=True,
        )
        selected = []
        seen = set()
        for item in rows:
            key = (
                str((item.get("double_pick") or {}).get("match_id")),
                str((item.get("anchor_pick") or {}).get("match_id")),
            )
            if key in seen:
                continue
            seen.add(key)
            selected.append(item)
            if len(selected) >= TWO_OPTION_COMBO_LIMIT:
                break
        return selected

    @classmethod
    def align_summary_ratings(
        cls,
        summary: Dict[str, Any],
        matches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Keep summary pools aligned with the final per-match decision."""
        result = dict(summary or {})
        by_id = {
            str(item.get("match_id") or ""): item
            for item in matches if item.get("match_id")
        }
        pool_selections = {
            "handicap_draw": "让平",
            "draw": "平局",
        }
        source_pools = result.get("pools") or {}
        pools = {
            key: [] for key in ("away_small_win", "handicap_lose")
            if key in source_pools
        }
        for key, items in source_pools.items():
            if key in {
                "core", "official_single", "two_option_core", "away_small_win",
                "handicap_lose",
            }:
                continue
            rows = []
            for item in items or []:
                row = dict(item)
                match = by_id.get(str(row.get("match_id") or "")) or {}
                analysis = match.get("analysis") or {}
                if not match:
                    continue

                no_bet = bool(analysis.get("no_bet"))
                primary_play = str(analysis.get("primary_play") or "")
                secondary_play = str(analysis.get("secondary_play") or "")
                handicap_play = str(analysis.get("handicap_play") or "")
                predicted_result = str(
                    analysis.get("predicted_result") or ""
                )
                if key == "avoid" and not no_bet:
                    # Cross-match synthesis may identify a risk, but it cannot
                    # overturn the deterministic final betting decision.
                    continue
                if key != "avoid" and no_bet:
                    continue
                if key == "handicap_draw" and "让平" not in {
                    primary_play, handicap_play,
                }:
                    continue
                if key == "handicap_lose" and "让负" not in {
                    primary_play, handicap_play,
                }:
                    continue
                if key == "draw" and "平局" not in {
                    primary_play, secondary_play, predicted_result,
                }:
                    continue
                if key == "away_small_win" and "客胜" not in {
                    primary_play, predicted_result,
                }:
                    continue

                selection = pool_selections.get(key)
                if selection and key in {"handicap_draw", "handicap_lose"}:
                    category_scores = (
                        (((match.get("input_snapshot") or {}).get("fae_core") or {})
                         .get("recommendation") or {})
                        .get("category_scores") or []
                    )
                    candidate = next((
                        value for value in category_scores
                        if str(value.get("label") or "") == selection
                    ), None)
                    if candidate and candidate.get("no_bet"):
                        continue

                reason = str(row.get("reason") or "")
                stale_memory_reason = any(marker in reason for marker in (
                    "0%命中", "昨日复盘同模式", "与昨日", "历史失误率",
                    "落入高危区段", "严禁切入", "全部玩法降权",
                ))
                quality_adjustments = [
                    value for value in analysis.get("rating_adjustments") or []
                    if "基本面缺失" not in value
                ]
                if key == "avoid" and stale_memory_reason:
                    if not quality_adjustments:
                        continue
                    row["reason"] = "；".join(quality_adjustments)
                row["reason"] = cls._label_probability_language(
                    row.get("reason")
                )
                match_rating = cls._rating(analysis.get("rating", 1))
                if key != "avoid":
                    row["rating"] = min(
                        cls._rating(row.get("rating", match_rating)),
                        match_rating,
                    )
                row["role"] = (
                    "主选" if selection == analysis.get("primary_play")
                    else "防选" if selection == analysis.get("secondary_play")
                    else "让球参考" if (
                        key in {"handicap_draw", "handicap_lose"}
                        and selection == analysis.get("handicap_play")
                    )
                    else "观察"
                )
                rows.append(row)
            pools[key] = rows
        result["pools"] = pools
        original_core = str(result.get("core_conclusion") or "")
        result["model_core_conclusion"] = original_core
        candidates = sorted(
            (
                item for item in matches
                if str((item.get("analysis") or {}).get("primary_play") or "")
                in OFFICIAL_PLAY_SELECTIONS
                and float((item.get("analysis") or {}).get("rating") or 0)
                >= OFFICIAL_MIN_RATING
                and not (item.get("analysis") or {}).get("no_bet")
                and float((item.get("analysis") or {}).get("bet_score") or 0)
                >= OFFICIAL_MIN_BET_SCORE
                and float((item.get("analysis") or {}).get("value_score") or 0)
                >= OFFICIAL_MIN_VALUE_SCORE
                and float((((item.get("analysis") or {})
                           .get("market_confidence") or {})
                          .get("score") or 0))
                >= OFFICIAL_MIN_MARKET_CONFIDENCE
            ),
            key=lambda item: (
                float((item.get("analysis") or {}).get("rating") or 0),
                float((((item.get("input_snapshot") or {}).get("fae_core") or {})
                      .get("overall_score") or 0)),
            ),
            reverse=True,
        )[:3]
        pools["core"] = []
        for item in candidates:
            analysis = item.get("analysis") or {}
            primary_play = str(analysis.get("primary_play") or "观望")
            handicap_play = str(analysis.get("handicap_play") or "")
            reason_parts = [f"正式主选{primary_play}"]
            if handicap_play not in ("", "观望", primary_play):
                reason_parts.append(f"竞彩让球参考{handicap_play}")
            if analysis.get("bet_score") is not None:
                reason_parts.append(f"投注分{analysis.get('bet_score')}分")
            if analysis.get("value_score") is not None:
                reason_parts.append(f"价值指数{analysis.get('value_score')}分")
            pools["core"].append({
                "match_id": str(item.get("match_id") or ""),
                "rating": cls._rating(analysis.get("rating", 1)),
                "selection": primary_play,
                "handicap_play": handicap_play,
                "reason": "，".join(reason_parts),
                "role": "主选",
            })
        official_single_candidates = sorted(
            (
                item for item in matches
                if (((item.get("analysis") or {})
                    .get("official_bet_recommendation") or {})
                    .get("actionable"))
            ),
            key=lambda item: int(
                (((item.get("analysis") or {})
                  .get("official_bet_recommendation") or {})
                 .get("daily_rank") or 999)
            ),
        )[:OFFICIAL_SINGLE_DAILY_LIMIT]
        pools["official_single"] = []
        for item in official_single_candidates:
            profile = (
                (item.get("analysis") or {})
                .get("official_bet_recommendation") or {}
            )
            selection = str(profile.get("selection") or "")
            odds = _number(profile.get("odds"))
            probability = _number(profile.get("probability"))
            confidence = _number(profile.get("market_confidence"))
            is_profit_parlay = (
                profile.get("strategy_source")
                in {
                    "fae-supervised-profit-parlay",
                    "fae-ark-target-3-parlay",
                    "fae-two-option-receiving-parlay",
                }
            )
            role = str(profile.get("parlay_role") or "")
            reason_parts = [
                (
                    f"正式二串一{role}{selection}"
                    if is_profit_parlay else f"正式单选{selection}"
                ) + (f"@{odds:g}" if odds is not None else ""),
            ]
            if probability is not None:
                reason_parts.append(f"融合概率{probability:g}%")
            if confidence is not None:
                reason_parts.append(f"盘口可信度{confidence:g}分")
            pools["official_single"].append({
                "match_id": str(item.get("match_id") or ""),
                "selection": selection,
                "odds": odds,
                "probability": probability,
                "model_probability": profile.get("model_probability"),
                "market_probability": profile.get("market_probability"),
                "model_expected_return": profile.get(
                    "model_expected_return"
                ),
                "model_market_edge": profile.get("model_market_edge"),
                "value_score": profile.get("value_score"),
                "bet_score": profile.get("bet_score"),
                "market_confidence": confidence,
                "daily_rank": profile.get("daily_rank"),
                "rank_score": profile.get("rank_score"),
                "strategy_version": profile.get("strategy_version"),
                "strategy_source": profile.get("strategy_source"),
                "parlay_role": role or None,
                "ticket_id": profile.get("ticket_id"),
                "combined_odds": profile.get("combined_odds"),
                "reason": "，".join(reason_parts),
                "role": role or "正式投注",
            })
        pools["profit_parlay"] = [
            dict(item) for item in pools["official_single"]
            if item.get("strategy_source") in {
                "fae-supervised-profit-parlay",
                "fae-ark-target-3-parlay",
                "fae-two-option-receiving-parlay",
            }
        ]
        two_option_candidates = sorted(
            (
                item for item in matches
                if (((item.get("analysis") or {})
                    .get("two_option_recommendation") or {})
                    .get("actionable"))
            ),
            key=lambda item: (
                int(((((item.get("analysis") or {})
                       .get("two_option_recommendation") or {})
                      .get("daily_rank")) or 999)),
                -float(((((item.get("analysis") or {})
                          .get("two_option_recommendation") or {})
                         .get("rank_score")) or 0)),
            ),
        )[:TWO_OPTION_DAILY_LIMIT]
        pools["two_option_core"] = []
        for item in two_option_candidates:
            analysis = item.get("analysis") or {}
            profile = analysis.get("two_option_recommendation") or {}
            selections = list(profile.get("selections") or [])[:2]
            odds = profile.get("odds") or {}
            priced = " / ".join(
                "{}@{:g}".format(
                    selection, float(odds.get(selection))
                )
                if _number(odds.get(selection)) is not None else selection
                for selection in selections
            )
            coverage = float(profile.get("coverage_score") or 0)
            confidence = float(profile.get("market_confidence") or 0)
            pools["two_option_core"].append({
                "match_id": str(item.get("match_id") or ""),
                "market": profile.get("market"),
                "selections": selections,
                "selection_text": profile.get("selection_text"),
                "odds": odds,
                "coverage_score": round(coverage, 2),
                "market_confidence": round(confidence, 1),
                "pair_value_score": profile.get("pair_value_score"),
                "equal_stake_expected_roi": profile.get(
                    "equal_stake_expected_roi"
                ),
                "dutch_roi": profile.get("dutch_roi"),
                "coverage_value_edge": profile.get(
                    "coverage_value_edge"
                ),
                "minimum_anchor_odds": profile.get(
                    "minimum_anchor_odds"
                ),
                "target_anchor_odds": profile.get(
                    "target_anchor_odds"
                ),
                "parlay_fit": profile.get("parlay_fit"),
                "ai_verified": bool(profile.get("ai_verified")),
                "analysis_source": profile.get("analysis_source"),
                "daily_rank": profile.get("daily_rank"),
                "rank_score": profile.get("rank_score"),
                "reason": (
                    f"{priced}，覆盖分{coverage:g}，"
                    f"价值分{float(profile.get('pair_value_score') or 0):g}，"
                    f"盘口可信度{confidence:g}；"
                    f"{profile.get('reason') or '达到双选正式门槛'}"
                ),
                "role": "双选核心",
            })
        result["pools"] = pools
        core_parts = []
        for item in candidates:
            analysis = item.get("analysis") or {}
            match_label = item.get("match_number") or item.get("match_id")
            primary_play = analysis.get("primary_play")
            predicted_result = analysis.get("predicted_result")
            label = f"{match_label}{primary_play}"
            if (
                predicted_result not in (None, "", "观望")
                and primary_play != predicted_result
            ):
                label = (
                    f"{match_label}赛果{predicted_result}/投注{primary_play}"
                )
            handicap_play = analysis.get("handicap_play")
            if (
                handicap_play not in (None, "", "观望")
                and handicap_play != analysis.get("primary_play")
            ):
                label += f"（竞彩让球参考{handicap_play}）"
            label += f"{float(analysis.get('rating') or 0):g}星"
            core_parts.append(label)
        downgraded = [
            item for item in matches
            if any(
                "基本面缺失" not in value
                for value in (item.get("analysis") or {}).get("rating_adjustments") or []
            )
            and not (((item.get("analysis") or {})
                     .get("two_option_recommendation") or {})
                    .get("actionable"))
        ]
        calibrated_text = (
            "校准后核心：" + "；".join(core_parts) + "。"
            if core_parts else "校准后核心：今天没有达到4星正式门槛的平/让平单选核心。"
        )
        two_option_rows = sorted(
            (
                item for item in matches
                if (((item.get("analysis") or {})
                    .get("two_option_recommendation") or {})
                    .get("actionable"))
            ),
            key=lambda item: int(
                (((item.get("analysis") or {})
                  .get("two_option_recommendation") or {})
                 .get("daily_rank")) or 999
            ),
        )
        if two_option_rows:
            calibrated_text += (
                "高覆盖双选正式推荐：" + "、".join(
                    "{}{}".format(
                        item.get("match_number") or item.get("match_id"),
                        (((item.get("analysis") or {})
                          .get("two_option_recommendation") or {})
                         .get("selection_text") or ""),
                    )
                    for item in two_option_rows
                ) + "；双选按独立门槛入池，不继承单选不下注结论，"
                "仍需按组合成本控制投入。"
            )
        if result.get("two_option_combinations"):
            best_combo = result["two_option_combinations"][0]
            double_pick = best_combo.get("double_pick") or {}
            anchor_pick = best_combo.get("anchor_pick") or {}
            calibrated_text += (
                "组合收益筛选："
                f"{double_pick.get('match_number')}"
                f"{double_pick.get('selection_text')}搭配"
                f"{anchor_pick.get('match_number')}"
                f"{anchor_pick.get('selection')}，"
                f"最低路径{best_combo.get('minimum_path_odds')}倍；"
                "这是路径收益排序，不代表保证盈利。"
            )
        if downgraded:
            calibrated_text += (
                "风险降级：" + "、".join(
                    str(item.get("match_number") or item.get("match_id"))
                    for item in downgraded
                ) + "因赔率价值不足、市场背离或盘口异常退出高星核心。"
            )
        if not result.get("recommended_combinations"):
            calibrated_text += (
                "平局与让平单选候选未同时达到门槛，"
                "不强行生成单选2/3关。"
            )
        no_bet_labels = [
            str(item.get("match_number") or item.get("match_id"))
            for item in matches
            if (item.get("analysis") or {}).get("no_bet")
            and not (((item.get("analysis") or {})
                     .get("two_option_recommendation") or {})
                     .get("actionable"))
        ]
        if no_bet_labels:
            calibrated_text += (
                "其余单选不下注：" + "、".join(no_bet_labels)
                + "，仅保留方向观察。"
            )
        result["core_conclusion"] = calibrated_text
        result["warnings"] = [
            cls._label_probability_language(value)
            for value in result.get("warnings") or []
        ]
        return result

    @staticmethod
    def _label_probability_language(value: Any) -> str:
        text = str(value or "")
        text = re.sub(r"FAEFAE估算", "FAE估算", text)
        text = re.sub(
            r"(?<!FAE估算)(?:FAE)?"
            r"(主胜|平局|客胜|让胜|让平|让负)概率(约)?"
            r"(\d+(?:\.\d+)?)%",
            r"FAE估算\1概率\2\3%（未校准）",
            text,
        )
        return re.sub(r"FAEFAE估算", "FAE估算", text)

    @classmethod
    def _selection_consistency_guard(
        cls,
        source: Dict[str, Any],
        model_selection: str,
    ) -> tuple[str, Dict[str, Any]]:
        """Override an auditable handicap conclusion conflict.

        ``让平`` is an exact-margin outcome.  It must not stay ahead of a
        directional handicap outcome when both the deterministic probability
        ranking and the market's shortest price point to that same outcome.
        This narrower guard prevents a ``让平 / 让负`` pair from omitting an
        clearly leading ``让胜`` (and vice versa) without turning every small
        probability difference into an override.
        """
        labels = {"让胜": "win", "让平": "draw", "让负": "lose"}
        base = {
            "triggered": False,
            "model_selection": model_selection,
            "effective_selection": model_selection,
        }
        if model_selection not in labels:
            return model_selection, base
        probabilities = (
            (((source.get("fae_core") or {}).get("probabilities") or {})
             .get("hhad") or {})
        )
        normalized = {
            label: _number(probabilities.get(key))
            for label, key in labels.items()
        }
        if any(value is None for value in normalized.values()):
            return model_selection, base
        top_selection = max(normalized, key=normalized.get)
        if top_selection == model_selection:
            return model_selection, base
        odds_values = (
            (source.get("sporttery_handicap") or {}).get("current")
            or (source.get("sporttery_handicap") or {}).get("initial")
            or []
        )
        odds = {
            label: (
                _number(odds_values[index])
                if len(odds_values) > index else None
            )
            for index, label in enumerate(("让胜", "让平", "让负"))
        }
        model_probability = normalized[model_selection]
        top_probability = normalized[top_selection]
        probability_gap = top_probability - model_probability
        model_return = (
            model_probability / 100 * odds[model_selection]
            if odds[model_selection] is not None else None
        )
        top_return = (
            top_probability / 100 * odds[top_selection]
            if odds[top_selection] is not None else None
        )
        return_gap = (
            top_return - model_return
            if top_return is not None and model_return is not None else None
        )
        strong_conflict = (
            probability_gap >= 20
            and return_gap is not None
            and return_gap >= 0.20
        )
        priced = {
            label: value for label, value in odds.items()
            if value is not None and value > 1
        }
        shortest_selection = (
            min(priced, key=priced.get) if len(priced) == 3 else None
        )
        directional_guard_will_handle = False
        if model_selection == "让平":
            _, directional_preview = cls._directional_precision_guard(
                source, model_selection
            )
            directional_guard_will_handle = bool(
                directional_preview.get("triggered")
            )
        exact_margin_conflict = bool(
            model_selection == "让平"
            and top_selection in {"让胜", "让负"}
            # 让平是精确分差结果。命中率优先时，只要方向项已经
            # 明确领先且同时是最低赔率项，就不应继续把让平排在
            # 主选；3个百分点用于覆盖类似周六017的4pp冲突。
            and probability_gap >= 3
            and shortest_selection == top_selection
            and not directional_guard_will_handle
        )
        triggered = strong_conflict or exact_margin_conflict
        if not triggered:
            return model_selection, {
                **base,
                "candidate_selection": top_selection,
                "probability_gap": round(probability_gap, 1),
                "shortest_price_selection": shortest_selection,
                "expected_return_gap": (
                    round(return_gap, 3) if return_gap is not None else None
                ),
            }
        if exact_margin_conflict:
            reason = (
                f"精确分差护栏：{model_selection}只是精确净胜球结果，"
                f"赛前可验证概率{model_probability:g}%低于"
                f"{top_selection}{top_probability:g}%，且{top_selection}为"
                "竞彩让球最低赔率项；主选改为方向项，让平降为防选"
            )
        else:
            reason = (
                f"一致性护栏：模型原选{model_selection}，但赛前可验证概率"
                f"{model_probability:g}%显著低于{top_selection}{top_probability:g}%，"
                f"正式推荐改为{top_selection}"
            )
        return top_selection, {
            "triggered": True,
            "guard_type": (
                "exact_margin_market_alignment"
                if exact_margin_conflict else "severe_probability_conflict"
            ),
            "model_selection": model_selection,
            "effective_selection": top_selection,
            "model_probability": model_probability,
            "effective_probability": top_probability,
            "probability_gap": round(probability_gap, 1),
            "shortest_price_selection": shortest_selection,
            "model_expected_return": (
                round(model_return, 3) if model_return is not None else None
            ),
            "effective_expected_return": (
                round(top_return, 3) if top_return is not None else None
            ),
            "expected_return_gap": (
                round(return_gap, 3) if return_gap is not None else None
            ),
            "reason": reason,
        }

    @classmethod
    def _apply_summary_guard(
        cls,
        summary: Dict[str, Any],
        matches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        result = dict(summary or {})
        guarded = {}
        labels = {}
        for item in matches:
            analysis = item.get("analysis") or {}
            guard = analysis.get("consistency_guard") or {}
            match_id = str(item.get("match_id") or "")
            labels[match_id] = item.get("match_number") or match_id
            if guard.get("triggered"):
                guarded[match_id] = str(
                    guard.get("effective_selection")
                    or analysis.get("primary_play")
                    or ""
                )
        if not guarded:
            return result
        pools = {
            key: list(items or [])
            for key, items in (result.get("pools") or {}).items()
        }
        pools["handicap_draw"] = [
            item for item in pools.get("handicap_draw", [])
            if guarded.get(str(item.get("match_id"))) in (None, "让平")
        ]
        result["pools"] = pools
        combinations = []
        for combo in result.get("recommended_combinations") or []:
            conflict = any(
                str(pick.get("match_id")) in guarded
                and guarded[str(pick.get("match_id"))]
                != str(pick.get("selection") or "")
                for pick in combo.get("picks") or []
            )
            if not conflict:
                combinations.append(combo)
        result["recommended_combinations"] = combinations
        warnings = list(result.get("warnings") or [])
        for match_id, selection in guarded.items():
            warnings.append(
                f"{labels.get(match_id, match_id)}触发模型一致性护栏，"
                f"正式推荐按{selection}结算"
            )
        result["warnings"] = list(dict.fromkeys(warnings))[:20]
        return result

    @classmethod
    def _apply_no_bet_summary(
        cls,
        summary: Dict[str, Any],
        matches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Move matches below the betting threshold into the avoid pool."""
        result = dict(summary or {})
        no_bet = {
            str(item.get("match_id") or ""): item
            for item in matches
            if (item.get("analysis") or {}).get("no_bet")
        }
        if not no_bet:
            return result
        pools = {
            key: list(items or [])
            for key, items in (result.get("pools") or {}).items()
        }
        for key in (
            "core", "handicap_draw", "draw", "away_small_win",
            "handicap_lose"
        ):
            pools[key] = [
                item for item in pools.get(key) or []
                if str(item.get("match_id") or "") not in no_bet
            ]
        two_option_core_ids = {
            str(item.get("match_id") or "")
            for item in matches
            if (((item.get("analysis") or {})
                .get("two_option_recommendation") or {})
                .get("actionable"))
        }
        avoid = [
            item for item in pools.get("avoid") or []
            if str(item.get("match_id") or "") not in two_option_core_ids
        ]
        avoid_ids = {str(item.get("match_id") or "") for item in avoid}
        for match_id, item in no_bet.items():
            if match_id in avoid_ids or match_id in two_option_core_ids:
                continue
            analysis = item.get("analysis") or {}
            reasons = analysis.get("no_bet_reasons") or ["投注分未达门槛"]
            avoid.append({
                "match_id": match_id,
                "rating": analysis.get("rating") or 1,
                "reason": (
                    f"{item.get('match_number') or match_id}不下注："
                    + "；".join(str(value) for value in reasons[:3])
                ),
            })
        pools["avoid"] = avoid
        result["pools"] = pools
        result["recommended_combinations"] = [
            combo for combo in result.get("recommended_combinations") or []
            if all(
                str(pick.get("match_id") or "") not in no_bet
                for pick in combo.get("picks") or []
            )
        ]
        warnings = list(result.get("warnings") or [])
        labels = [
            str((item.get("match_number") or match_id))
            for match_id, item in no_bet.items()
            if match_id not in two_option_core_ids
        ]
        if labels:
            warnings.append(
                "单选不下注场次：" + "、".join(labels)
                + "；方向分析保留，但不进入单选推荐榜和组合。"
            )
        if two_option_core_ids:
            core_labels = [
                str(item.get("match_number") or item.get("match_id"))
                for item in matches
                if str(item.get("match_id") or "") in two_option_core_ids
            ]
            warnings.append(
                "双选独立入池：" + "、".join(core_labels)
                + "；不受单选不下注状态影响。"
            )
        result["warnings"] = list(dict.fromkeys(warnings))[:20]
        return result

    @classmethod
    def _merge_summaries(
        cls,
        summaries: List[Dict[str, Any]],
        matches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        valid_ids = {str(item.get("match_id")) for item in matches}
        pools = {
            "handicap_draw": [],
            "draw": [],
            "away_small_win": [],
            "handicap_lose": [],
            "avoid": [],
        }
        warnings = []
        conclusions = []
        combinations = []
        for summary in summaries:
            conclusion = cls._text(summary.get("core_conclusion"), "", 900)
            if conclusion:
                conclusions.append(conclusion)
            warnings.extend(cls._list(summary.get("warnings"), 12, 220))
            source_pools = summary.get("pools") or {}
            for key in pools:
                for item in source_pools.get(key) or []:
                    normalized = cls._pool_item(item, valid_ids)
                    if normalized:
                        pools[key].append(normalized)
            for item in summary.get("recommended_combinations") or []:
                normalized = cls._combination(item, valid_ids)
                if normalized:
                    combinations.append(normalized)
        return {
            "core_conclusion": "\n".join(conclusions)[:1800],
            "warnings": list(dict.fromkeys(warnings))[:20],
            "pools": {
                key: cls._dedupe_pool(value)[:12]
                for key, value in pools.items()
            },
            "recommended_combinations": combinations[:10],
        }

    @classmethod
    def normalize_summary_pool_semantics(
        cls,
        summary: Dict[str, Any],
        matches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Keep away-small-win and official handicap-loss pools distinct."""
        result = dict(summary or {})
        pools = {
            key: list(items or [])
            for key, items in (result.get("pools") or {}).items()
        }
        by_id = {
            str(item.get("match_id") or ""): item
            for item in matches
            if item.get("match_id")
        }
        handicap_lose = list(pools.get("handicap_lose") or [])
        away_small_win = []
        existing_let_lose_ids = {
            str(item.get("match_id") or "") for item in handicap_lose
        }
        for pool_item in pools.get("away_small_win") or []:
            match_id = str(pool_item.get("match_id") or "")
            match = by_id.get(match_id) or {}
            analysis = match.get("analysis") or {}
            snapshot = match.get("input_snapshot") or {}
            probabilities = (
                (snapshot.get("fae_core") or {}).get("probabilities") or {}
            )
            hhad = probabilities.get("hhad") or {}
            hhad_lose_is_top = (
                hhad.get("lose") is not None
                and float(hhad.get("lose") or 0) >= max(
                    float(hhad.get("win") or 0),
                    float(hhad.get("draw") or 0),
                )
            )
            reason = str(pool_item.get("reason") or "")
            is_handicap_lose = (
                analysis.get("primary_play") == "让负"
                or "让负" in reason
                or hhad_lose_is_top
            )
            direction = str(analysis.get("direction") or "")
            probabilities_away = float(
                probabilities.get("away_win") or 0
            )
            probabilities_home = float(
                probabilities.get("home_win") or 0
            )
            is_away_direction = (
                direction in {"客胜", "客队不败"}
                or probabilities_away > probabilities_home
            )
            if is_handicap_lose:
                if match_id not in existing_let_lose_ids:
                    handicap_lose.append(pool_item)
                    existing_let_lose_ids.add(match_id)
            elif is_away_direction:
                away_small_win.append(pool_item)
            # A row that is neither an away direction nor handicap loss is
            # discarded rather than mislabeled.
        pools["away_small_win"] = away_small_win
        pools["handicap_lose"] = handicap_lose
        result["pools"] = pools
        return result

    @classmethod
    def normalize_summary_memory_governance(
        cls,
        summary: Dict[str, Any],
        review_memory: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Prevent unvalidated review observations becoming hard bans."""
        result = dict(summary or {})
        memory = review_memory or {}
        try:
            validated_count = int(
                memory.get("validated_pattern_count") or 0
            )
        except (TypeError, ValueError):
            validated_count = 0
        history_markers = (
            "昨日", "前日", "历史", "复盘", "命中率", "0%", "100%",
        )
        absolute_markers = (
            "严禁", "全部排除", "一律排除", "禁止纳入",
            "不得纳入", "不建议纳入", "高危区间", "高危区段",
            "严禁切入", "全部玩法降权",
        )
        governance_notice = (
            (
                "历史复盘目前没有通过跨日且足量样本验证的规则；"
                "单日0%或100%结果仅作低权重风险提醒"
            )
            if validated_count == 0 else
            "历史复盘中的已验证模式仅用于辅助校正，不构成自动禁选规则"
        ) + (
            "，当前比赛仍按当天欧赔、亚盘真实升深、竞彩让球、"
            "大小球和市场一致性独立判断。"
        )
        observation_notice = (
            "近期复盘中存在表面相似的风险信号，但尚未通过跨日且足量"
            "样本验证，仅作核验提醒，不自动降权。"
        )

        def soften(value: Any) -> str:
            text = str(value or "").strip()
            if not text:
                return text
            sentences = re.findall(r"[^。！？\n]+[。！？]?", text)
            cleaned = []
            corrected = False
            for sentence in sentences:
                has_history = any(
                    marker in sentence for marker in history_markers
                )
                has_absolute = any(
                    marker in sentence for marker in absolute_markers
                )
                is_unvalidated_generalization = (
                    validated_count == 0
                    and has_history
                    and any(marker in sentence for marker in (
                        "历史失误率", "失败模式高度同构", "需降权",
                    ))
                )
                if is_unvalidated_generalization:
                    if not corrected:
                        cleaned.append(observation_notice)
                        corrected = True
                    continue
                if not (has_history and has_absolute):
                    cleaned.append(sentence)
                    continue
                cut_positions = [
                    sentence.find(marker)
                    for marker in (
                        "但受昨日", "但受历史", "与昨日", "落入",
                        "严禁", "全部排除", "一律排除", "禁止纳入",
                        "不得纳入", "高危区间", "高危区段",
                    )
                    if sentence.find(marker) >= 0
                ]
                if cut_positions:
                    begins_with_history = sentence.lstrip().startswith(
                        history_markers
                    )
                    prefix = "" if begins_with_history else sentence[
                        :min(cut_positions)
                    ].rstrip("，,；;。！？ ")
                    if len(prefix) >= 8:
                        cleaned.append(prefix + "。")
                if not corrected:
                    cleaned.append(governance_notice)
                    corrected = True
            return "".join(cleaned).strip()

        result["core_conclusion"] = soften(
            result.get("core_conclusion")
        )
        result["warnings"] = list(dict.fromkeys(
            soften(item) for item in result.get("warnings") or []
            if soften(item)
        ))[:20]
        result["pools"] = {
            key: [
                {**item, "reason": soften(item.get("reason"))}
                for item in items or []
            ]
            for key, items in (result.get("pools") or {}).items()
        }
        result["recommended_combinations"] = [
            {**item, "reason": soften(item.get("reason"))}
            for item in result.get("recommended_combinations") or []
        ]
        radar = dict(result.get("draw_radar") or {})
        for key in ("ordinary_draw", "handicap_draw"):
            radar[key] = [
                {**item, "reason": soften(item.get("reason"))}
                for item in radar.get(key) or []
            ]
        if radar:
            result["draw_radar"] = radar
        return result

    @classmethod
    def _humanize_summary_match_ids(
        cls,
        summary: Dict[str, Any],
        matches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Replace raw IDs in prose while preserving JSON identifier fields."""
        labels = {
            str(item.get("match_id") or ""): str(
                item.get("match_number") or item.get("match_id") or ""
            )
            for item in matches
            if item.get("match_id")
        }

        def humanize(value: Any) -> str:
            text = str(value or "")
            for match_id in sorted(labels, key=len, reverse=True):
                label = labels[match_id]
                if label and label != match_id:
                    text = text.replace(match_id, label)
            return text

        result = dict(summary or {})
        result["core_conclusion"] = humanize(
            result.get("core_conclusion")
        )
        result["warnings"] = [
            humanize(item) for item in result.get("warnings") or []
        ]
        result["pools"] = {
            key: [
                {
                    **item,
                    "reason": humanize(item.get("reason")),
                }
                for item in items or []
            ]
            for key, items in (result.get("pools") or {}).items()
        }
        result["recommended_combinations"] = [
            {
                **item,
                "reason": humanize(item.get("reason")),
            }
            for item in result.get("recommended_combinations") or []
        ]
        radar = dict(result.get("draw_radar") or {})
        for key in ("ordinary_draw", "handicap_draw"):
            radar[key] = [
                {
                    **item,
                    "reason": humanize(item.get("reason")),
                }
                for item in radar.get(key) or []
            ]
        if radar:
            result["draw_radar"] = radar
        return result

    @classmethod
    def _pool_item(
        cls, value: Any, valid_ids: set
    ) -> Optional[Dict[str, Any]]:
        item = value if isinstance(value, dict) else {}
        match_id = str(item.get("match_id") or "")
        if match_id not in valid_ids:
            return None
        return {
            "match_id": match_id,
            "rating": cls._rating(item.get("rating", 1)),
            "reason": cls._text(item.get("reason"), "", 260),
        }

    @classmethod
    def _combination(
        cls, value: Any, valid_ids: set
    ) -> Optional[Dict[str, Any]]:
        item = value if isinstance(value, dict) else {}
        picks = []
        seen = set()
        for pick in item.get("picks") or []:
            if not isinstance(pick, dict):
                continue
            match_id = str(pick.get("match_id") or "")
            selection = str(pick.get("selection") or "")
            if (
                match_id not in valid_ids
                or match_id in seen
                or selection not in {"平局", "让平"}
            ):
                continue
            seen.add(match_id)
            picks.append({"match_id": match_id, "selection": selection})
        if len(picks) not in (2, 3):
            return None
        return {
            "play": f"{len(picks)}串1",
            "picks": picks,
            "reason": cls._text(item.get("reason"), "", 300),
        }

    @staticmethod
    def _dedupe_pool(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        result = []
        seen = set()
        for item in rows:
            if item["match_id"] in seen:
                continue
            seen.add(item["match_id"])
            result.append(item)
        return result

    @staticmethod
    def _extract_json(raw_text: str) -> Dict[str, Any]:
        text = str(raw_text or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise FAEOutputError("全日分析输出不是JSON对象")
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            # Some compatible Coding models emit a trailing comma even in
            # JSON mode. Repair only this unambiguous syntax issue, then keep
            # the same strict object and field validation.
            repaired = re.sub(r",\s*([}\]])", r"\1", text[start:end + 1])
            try:
                data = json.loads(repaired)
            except json.JSONDecodeError:
                try:
                    data = repair_json(
                        text[start:end + 1],
                        return_objects=True,
                    )
                except Exception as repair_exc:
                    raise FAEOutputError(
                        f"全日分析JSON解析失败: {exc.msg}"
                    ) from repair_exc
        if not isinstance(data, dict):
            raise FAEOutputError("全日分析输出必须是JSON对象")
        return data

    @staticmethod
    def _rating(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = len(re.findall(r"★", str(value or "")))
        return round(max(1, min(5, number)) * 2) / 2

    @staticmethod
    def _stars(value: float) -> str:
        rating = max(0.0, min(5.0, float(value or 0)))
        full = max(0, min(5, int(rating)))
        text = "★" * full + "☆" * (5 - full)
        return f"{text} · {rating:g}星" if rating % 1 else text

    @staticmethod
    def _text(value: Any, fallback: str, limit: int) -> str:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        return (text or fallback)[:limit]

    @classmethod
    def _list(cls, value: Any, limit: int, item_limit: int) -> List[str]:
        if not isinstance(value, list):
            return []
        return list(dict.fromkeys(
            cls._text(item, "", item_limit) for item in value
            if cls._text(item, "", item_limit)
        ))[:limit]
