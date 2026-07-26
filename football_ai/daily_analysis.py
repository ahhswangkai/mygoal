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
from .version import ENGINE_VERSION


DAILY_PROMPT_VERSION = "five-market-daily-v19-draw-only-strict-gate"

OFFICIAL_PLAY_SELECTIONS = {"平局", "让平"}
OFFICIAL_MIN_BET_SCORE = 70.0
OFFICIAL_MIN_VALUE_SCORE = 60.0
OFFICIAL_MIN_MARKET_CONFIDENCE = 70.0
OFFICIAL_MIN_RATING = 4.0
ASIAN_HARD_DOWNGRADE_RISKS = {
    "deepen_high_water",
    "upper_water_rise",
    "water_drop_without_deepen",
    "handicap_retreat",
    "euro_asian_divergence",
    "overheated_shallow",
}

DRAW_SELECTION_POLICY_DEFAULT = "conservative"

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
                8,
                "竞彩让2球可观察强队赢两球的让平路径",
                {"sporttery_handicap": sporttery_handicap},
            )

    if hhad_draw_odds is not None:
        if 3.20 <= hhad_draw_odds <= 3.90:
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
    upset_warning_model = _build_upset_warning_model(
        match,
        sporttery_handicap,
        current_asian_risk,
        fundamentals,
        odds_band_model,
    )
    return {
        "match_id": str(match.get("match_id") or ""),
        "match_number": match.get("match_number") or match.get("round_id"),
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
            "version": "historical-market-rules-v1",
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
        "upset_warning_model": upset_warning_model,
        "current_asian_risk": current_asian_risk,
        "fundamentals": fundamentals,
        "data_warnings": list(dict.fromkeys(warnings)),
        "missing_fundamentals": missing_fundamentals,
    }


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
                "本轮仅重新研判未开赛比赛；已开赛的"
                + "、".join(labels)
                + "保留原赛前研判，不进入本轮新增推荐池和组合。"
            )
            summary["warnings"] = list(dict.fromkeys(warnings))[:20]
            result["daily_summary"] = summary
        return result

    def analyze(
        self,
        owner_date: str,
        match_inputs: Iterable[Dict[str, Any]],
        batch_size: int = 20,
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
        size = max(1, min(30, int(batch_size or 20)))
        outputs = []
        provider_batches = []
        for index in range(0, len(rows), size):
            batch = rows[index:index + size]
            batch_number = index // size + 1
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
                outputs.append(cached["output"])
                provider_batches.append({
                    **(cached.get("provider_meta") or {}),
                    "cache_hit": True,
                    "batch_hash": batch_hash,
                })
                continue
            text, metadata = self.client.generate(prompt)
            parsed = self._extract_json(text)
            if len(batch) == 1:
                generated_match = (
                    parsed.get("match")
                    if isinstance(parsed.get("match"), dict)
                    else parsed
                )
                if generated_match.get("match_id"):
                    parsed = {
                        "daily_summary": {},
                        "matches": [generated_match],
                    }
            outputs.append(parsed)
            batch_metadata = {
                **metadata,
                "cache_hit": False,
                "batch_hash": batch_hash,
            }
            provider_batches.append(batch_metadata)
            if batch_cache_save:
                batch_cache_save({
                    "batch_hash": batch_hash,
                    "owner_date": str(owner_date)[:10],
                    "kind": "detail",
                    "batch_number": batch_number,
                    "match_ids": [
                        str(item.get("match_id")) for item in batch
                    ],
                    "model": self.client.model,
                    "prompt_version": DAILY_PROMPT_VERSION,
                    "review_memory_hash": memory.get("memory_hash"),
                    "output": parsed,
                    "provider_meta": metadata,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                })

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
        stored_matches = self.normalize_match_memory_governance(
            stored_matches, memory
        )
        synthesis_meta = None
        global_summary = None
        if len(outputs) > 1:
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
            "batch_count": len(outputs),
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
        minimum_rating = 3.5
        avoid_ids = {
            str(item.get("match_id") or "")
            for item in pools.get("avoid") or []
        }
        radar = daily_summary.get("draw_radar") or {}
        radar_draw = [
            item for item in radar.get("ordinary_draw") or []
            if item.get("tier") == "core"
        ]
        radar_handicap_draw = [
            item for item in radar.get("handicap_draw") or []
            if item.get("tier") == "core"
        ]
        draw_source = radar_draw if radar else pools.get("draw") or []
        handicap_draw_source = (
            radar_handicap_draw
            if radar else pools.get("handicap_draw") or []
        )
        draw = [
            item for item in draw_source
            if (
                float(item.get("rating") or 0) >= minimum_rating
                and str(item.get("match_id") or "") not in avoid_ids
            )
        ]
        handicap_draw = [
            item for item in handicap_draw_source
            if (
                float(item.get("rating") or 0) >= minimum_rating
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
                "secondary_play": "防选；与主选不同，无法明确时填观望",
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
            "严格区分客队小胜与竞彩让负：away_small_win只放客队明确为胜负方向且预计净胜1球的比赛；竞彩让负必须放入handicap_lose，禁止放入away_small_win。",
            "正式推荐只服务用户主玩法：平局和让平。主胜、客胜、让胜、让负、大球、小球只能写方向观察或风险解释，禁止进入核心推荐和组合。",
            "正式推荐必须同时满足投注分>=70、价值指数>=60、盘口可信度>=70、星级>=4；不满足任一条件必须写不下注。",
            "亚盘不配合（退盘、升盘高水、上盘升水、降水不升盘、欧亚背离、热门浅盘）时，胜负方向必须硬降级为观察，不得只写风险提示后继续推荐。",
            "大小球跳动达到0.75或以上时优先标记数据异常，不得据此强推方向。",
            "不得伪造近期状态、伤停、首发、天气、战意和赛程；输入缺失必须明确说明。",
            "fundamentals来自500赛前页：recent、history、team_rankings、future可作基本面证据；lineups.status=predicted仅表示预计阵容，禁止称为官方首发；injuries.status=no_listed_players仅表示页面未列出球员，禁止称为确认无伤停。",
            "fundamentals.cache_status=stale时代表刷新失败后的过期缓存，只能低权重引用并必须提示时效风险。",
            "历史复盘记忆只用于提醒曾经出现的误判和风险，不是当前比赛事实，不得据此直接推荐。",
            "联赛历史画像来自当前比赛日期之前的完场数据并带时间衰减；只允许把eligible_for_adjustment=true且分段样本充足的内容作为低到中权重基线。",
            "联赛画像中的命中率、让平率、进球率是历史条件频率，不是真实胜率；不得单独据此推荐，必须与当天五项市场证据一致。",
            "league_tactical_model是人工沉淀的联赛模板指数，包含平局、让平、大小球和冷门指数；它只用于筛选和解释，不能覆盖赔率价值、盘口一致性和数据质量。",
            "odds_band_model是赔率区间扫描器：favorite_heat表示热门过热，underdog_upset表示下盘爆冷，handicap_draw_value表示让平价值；1.40-1.70热门危险区、1.80-2.20均势区、客场1.70-2.20陷阱区、平赔低位和盘口过深都只能作为降级热门或提高平/让平扫描权重的证据。",
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
            "每场必须区分主选和防选，且两者必须属于同一市场：普通胜平负只搭配普通胜平负，竞彩让球只搭配让胜/让平/让负；跨市场方向单独写入让球参考，不能放入防选。",
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
                "secondary_play": "防选；与主选不同，无法明确时填观望",
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
            "不得编造近期状态、伤停、首发、天气、战意或赛程。",
            "fundamentals来自500赛前页；预计阵容不能写成官方首发，伤停栏目未列球员不能写成确认无伤停。",
            "fundamentals.cache_status=stale时必须降低基本面权重并提示时效风险。",
            "历史复盘记忆只是低权重风险提醒，不是当前比赛事实；不得机械套用昨天结论。",
            "联赛历史画像只在eligible_for_adjustment=true时作为低到中权重基线；赔率分段样本不足时不得使用。",
            "历史联赛频率不是真实概率，必须让位于本场欧赔、亚盘、竞彩、大小球和市场一致性。",
            "league_tactical_model是联赛模板指数，只能作为低到中权重筛选层；指数高但赔率价值、盘口一致性或数据质量不足时仍必须降级或不下注。",
            "odds_band_model是赔率区间扫描器：favorite_heat、underdog_upset、handicap_draw_value分别对应热门过热、下盘爆冷、让平价值；指数高只能降低热门或增加防选，不得脱离盘口一致性直接反买。",
            "正式推荐只允许平局或让平；主胜、客胜、让胜、让负、大球、小球只保留方向观察。正式推荐必须投注分>=70、价值指数>=60、盘口可信度>=70、星级>=4。",
            "亚盘不配合时胜负方向必须硬降级为观察，不能只写风险提示后继续推荐。",
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
            "明确输出一个主选和一个同市场防选；普通胜平负不得把让胜/让平/让负写成防选，跨市场方向由系统单独计算；概率是未校准的FAE估算，不得表述成真实胜率。",
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
            "正式推荐池和组合只允许平局/让平；主胜、客胜、让胜、让负、大球、小球只能进入观察或避开说明。",
            "正式推荐必须投注分>=70、价值指数>=60、盘口可信度>=70、星级>=4；低于门槛不允许进入核心池。",
            "亚盘不配合时胜负方向必须硬降级为观察，不得在摘要里重新包装成可下注推荐。",
            "严格区分推荐池：客队小胜只放客胜方向且预计客队净胜1球的比赛；竞彩让负无论主客强弱都只能放入handicap_lose池。",
            "结合历史复盘记忆检查是否重复犯错，但记忆不能替代当天盘口，也不能把单日赛果当成稳定规律。",
            "validated_pattern_count为0时不得输出历史0%命中区间、严禁纳入、全部排除等绝对规则；单日小样本只能作为风险备注。",
            "横向校准星级：五星最多1场，四星到四星半最多3场；欧亚背离、极端水位或盘口跳档场次不得进入核心高星推荐。",
            "逐场主选与同市场防选已经给出；摘要池若采用防选方向，必须明确写为防范，不得与主选并列成两个高置信结论。",
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
        non_cover_guard = cls._favorite_non_cover_guard(
            source, effective_primary_play
        )
        if non_cover_guard.get("triggered"):
            effective_primary_play = str(
                non_cover_guard.get("effective_selection")
                or effective_primary_play
            )
        secondary_play = cls._secondary_play(
            source,
            effective_primary_play,
            (
                None
                if guard.get("triggered")
                or non_cover_guard.get("triggered")
                else generated.get("secondary_play")
            ),
        )
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
            for item in (guard, non_cover_guard)
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
                "handicap_play": cls._handicap_play(
                    source, effective_primary_play
                ),
                "model_primary_play": model_primary_play,
                "value_guard": value_guard,
                "consistency_guard": guard,
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
        allowed = {
            "平局", "让平", "让胜", "让负", "主胜", "客胜",
            "大球", "小球", "观望",
        }
        same_market = (
            {"主胜", "平局", "客胜"}
            if primary_play in {"主胜", "平局", "客胜"}
            else {"让胜", "让平", "让负"}
            if primary_play in {"让胜", "让平", "让负"}
            else {"大球", "小球"}
            if primary_play in {"大球", "小球"}
            else allowed
        )
        candidate = str(generated_secondary or "").strip()
        if (
            candidate in same_market
            and candidate not in {primary_play, "观望"}
        ):
            return candidate
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
                    return "平局"
        elif primary_play in {"让胜", "让平", "让负"}:
            hhad = probabilities.get("hhad") or {}
            groups = [
                ("让胜", _number(hhad.get("win")) or 0),
                ("让平", _number(hhad.get("draw")) or 0),
                ("让负", _number(hhad.get("lose")) or 0),
            ]
        elif primary_play in {"大球", "小球"}:
            totals = probabilities.get("over_under") or {}
            groups = [
                ("大球", _number(totals.get("over")) or 0),
                ("小球", _number(totals.get("under")) or 0),
            ]
        alternatives = [item for item in groups if item[0] != primary_play]
        return max(alternatives, key=lambda item: item[1])[0] if alternatives else "观望"

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
        candidates = [
            (
                label,
                _number(hhad.get(key)),
                _number(odds_values[index])
                if len(odds_values) > index else None,
            )
            for index, (label, key) in enumerate((
                ("让胜", "win"),
                ("让平", "draw"),
                ("让负", "lose"),
            ))
            if label in compatible
        ]
        valid = [item for item in candidates if item[1] is not None]
        return max(
            valid,
            key=lambda item: (
                item[1],
                item[1] * item[2] if item[2] is not None else 0,
            ),
        )[0] if valid else "观望"

    @classmethod
    def _historical_adjusted_profile(
        cls,
        source: Dict[str, Any],
        profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Conservatively calibrate draw plays with similar finished matches."""
        result = dict(profile or {})
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
            if str(reason) not in {"赔率价值不足", "综合投注分未达门槛"}
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
            "raw_probability": core_probability,
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
            + league_score_bonus
            + odds_band_score_bonus
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
        if league_note:
            reason_parts.append(league_note)
        if odds_band_note:
            reason_parts.append(odds_band_note)
        if matched_historical_rules:
            reason_parts.append(
                "历史赔率规则{}项，概率修正{:+g}个百分点".format(
                    len(matched_historical_rules),
                    historical_rule_adjustment,
                )
            )
        if tier == "core":
            reason_parts.append("达到独立核心门槛")
        elif tier == "watch":
            reason_parts.append("仅列观察，不进入组合")
        else:
            reason_parts.append("未达到展示与投注门槛")
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
            "historical_probability": round(historical_probability, 2)
            if historical_probability is not None else None,
            "market_probability": round(market_probability, 2)
            if market_probability is not None else None,
            "odds": round(odds, 3) if odds is not None else None,
            "odds_value": round(odds_value, 2)
            if odds_value is not None else None,
            "effective_sample": (
                metric.get("effective_sample")
                if metric.get("effective_sample") is not None
                else max(historical_rule_samples, default=None)
            ),
            "historical_odds_rule_adjustment_pp": round(
                historical_rule_adjustment, 2
            ),
            "historical_odds_rule_ids": matched_historical_rules,
            "historical_odds_rule_signals": (
                historical_rule_profile.get("signals") or []
            ),
            "confidence": (
                metric.get("confidence")
                if metric.get("eligible_for_adjustment")
                else historical_rule_confidence or "样本不足"
            ),
            "eligible_for_adjustment": history_eligible,
            "role_signals": role_signals,
            "risk_pattern_ids": relevant_risks,
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
            analysis["draw_radar"] = {
                "ordinary_draw": cls._draw_radar_candidate(row, "平局"),
                "handicap_draw": cls._draw_radar_candidate(row, "让平"),
            }
            row["analysis"] = analysis
            result.append(row)
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
            "version": "draw-radar-v1",
            "policy": (
                "核心候选可参与组合；观察候选只记录和复盘，"
                "负赔率价值不得升级为核心。"
            ),
            "ordinary_draw": [],
            "handicap_draw": [],
            "excluded_count": {
                "ordinary_draw": 0,
                "handicap_draw": 0,
            },
        }
        for item in matches:
            candidates = (
                (item.get("analysis") or {}).get("draw_radar") or {}
            )
            for key in ("ordinary_draw", "handicap_draw"):
                candidate = dict(candidates.get(key) or {})
                if candidate.get("tier") == "exclude":
                    radar["excluded_count"][key] += 1
                    continue
                if candidate.get("match_id"):
                    radar[key].append(candidate)
        for key in ("ordinary_draw", "handicap_draw"):
            radar[key] = sorted(
                radar[key],
                key=lambda item: (
                    item.get("tier") == "core",
                    float(item.get("score") or 0),
                    float(item.get("probability") or 0),
                ),
                reverse=True,
            )[:6]
        result["draw_radar"] = radar
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
    def _value_selection_guard(
        cls,
        source: Dict[str, Any],
        model_selection: str,
    ) -> tuple[str, Dict[str, Any]]:
        """Prefer a materially stronger bettable option over raw prediction."""
        policy = draw_selection_policy_profile((source or {}).get("draw_selection_policy"))
        allowed = {"主胜", "平局", "客胜", "让胜", "让平", "让负"}
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
                    and best_odds_value is not None
                    and best_odds_value >= float(
                        policy.get("min_value", {}).get(best_selection, 0)
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
            analysis["non_cover_guard"] = non_cover_guard
            if (
                guard.get("triggered")
                or value_guard.get("triggered")
                or non_cover_guard.get("triggered")
            ):
                analysis["secondary_play"] = None
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
            analysis.update({
                "model_rating": model_rating,
                "value_rating": value_rating,
                "rating": rating,
                "star_text": cls._stars(rating),
                "rating_adjustments": list(dict.fromkeys(adjustments)),
                "secondary_play": cls._secondary_play(
                    source,
                    effective_primary_play,
                    analysis.get("secondary_play"),
                ),
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
            if adjustments or historical_risk_notes:
                analysis["risks"] = list(dict.fromkeys(
                    list(analysis.get("risks") or [])
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
        pools = {}
        for key, items in (result.get("pools") or {}).items():
            if key in {"core", "away_small_win", "handicap_lose"}:
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
        ]
        calibrated_text = (
            "校准后核心：" + "；".join(core_parts) + "。"
            if core_parts else "校准后核心：今天没有达到4星正式门槛的平/让平核心场次。"
        )
        if downgraded:
            calibrated_text += (
                "风险降级：" + "、".join(
                    str(item.get("match_number") or item.get("match_id"))
                    for item in downgraded
                ) + "因赔率价值不足、市场背离或盘口异常退出高星核心。"
            )
        if not result.get("recommended_combinations"):
            calibrated_text += "平局与让平候选未同时达到门槛，不强行生成2/3关。"
        no_bet_labels = [
            str(item.get("match_number") or item.get("match_id"))
            for item in matches
            if (item.get("analysis") or {}).get("no_bet")
        ]
        if no_bet_labels:
            calibrated_text += (
                "不下注：" + "、".join(no_bet_labels)
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
        """Override only a severe, auditable handicap conclusion conflict."""
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
        triggered = (
            probability_gap >= 20
            and return_gap is not None
            and return_gap >= 0.20
        )
        if not triggered:
            return model_selection, {
                **base,
                "candidate_selection": top_selection,
                "probability_gap": round(probability_gap, 1),
                "expected_return_gap": (
                    round(return_gap, 3) if return_gap is not None else None
                ),
            }
        reason = (
            f"一致性护栏：模型原选{model_selection}，但赛前可验证概率"
            f"{model_probability:g}%显著低于{top_selection}{top_probability:g}%，"
            f"正式推荐改为{top_selection}"
        )
        return top_selection, {
            "triggered": True,
            "model_selection": model_selection,
            "effective_selection": top_selection,
            "model_probability": model_probability,
            "effective_probability": top_probability,
            "probability_gap": round(probability_gap, 1),
            "model_expected_return": round(model_return, 3),
            "effective_expected_return": round(top_return, 3),
            "expected_return_gap": round(return_gap, 3),
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
        avoid = list(pools.get("avoid") or [])
        avoid_ids = {str(item.get("match_id") or "") for item in avoid}
        for match_id, item in no_bet.items():
            if match_id in avoid_ids:
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
        ]
        warnings.append(
            "不下注场次：" + "、".join(labels)
            + "；方向分析保留，但不进入推荐榜和组合。"
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
