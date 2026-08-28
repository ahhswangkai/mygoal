"""Leakage-safe supervised draw and exact-goal-margin learning.

The learner only consumes immutable pre-match snapshots.  Final scores are
kept as labels and are never copied into the feature vector.  The first
release is intentionally dependency-free and explainable: regularised
logistic/softmax models are blended conservatively with no-vig market priors,
then evaluated with expanding-window backtests before they may affect FAE.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from itertools import combinations
import json
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .league_profile import _handicap_value


SUPERVISED_SCHEMA_VERSION = "1.4"
SUPERVISED_MODEL_VERSION = "draw-margin-supervised-v5"
MARGIN_CLASSES = (-3, -2, -1, 0, 1, 2, 3)
SINGLE_SELECTIONS = ("主胜", "平局", "客胜", "让胜", "让平", "让负")
SINGLE_MIN_ODDS = 1.50
SINGLE_MAX_ODDS = 2.20
SINGLE_DAILY_LIMIT = 2
SINGLE_POLICY_PROBABILITIES = (42.0, 46.0, 50.0, 54.0, 58.0)
SINGLE_POLICY_GAPS = (-6.0, -2.0, 0.0, 4.0, 8.0)

FEATURE_NAMES = (
    "euro_home_odds",
    "euro_draw_odds",
    "euro_away_odds",
    "euro_home_probability",
    "euro_draw_probability",
    "euro_away_probability",
    "euro_favorite_odds",
    "euro_favorite_probability",
    "euro_probability_spread",
    "favorite_odds_danger_140_170",
    "draw_odds_below_320",
    "draw_odds_320_360",
    "euro_home_change",
    "euro_draw_change",
    "euro_away_change",
    "asian_initial_line",
    "asian_current_line",
    "asian_line_change",
    "asian_home_water",
    "asian_away_water",
    "asian_home_water_change",
    "asian_away_water_change",
    "asian_favorite_depth",
    "asian_favorite_depth_change",
    "asian_favorite_water",
    "asian_favorite_water_change",
    "asian_deepen_high_water",
    "euro_asian_divergence",
    "hhad_handicap",
    "hhad_home_probability",
    "hhad_draw_probability",
    "hhad_away_probability",
    "hhad_home_change",
    "hhad_draw_change",
    "hhad_away_change",
    "hhad_draw_odds",
    "total_initial_line",
    "total_current_line",
    "total_line_change",
    "total_over_water",
    "total_under_water",
    "total_under_bias",
    "rank_gap",
    "weekend",
    "missing_euro",
    "missing_asian",
    "missing_sporttery_handicap",
    "missing_total",
)
SINGLE_META_FEATURE_NAMES = (
    *FEATURE_NAMES,
    "candidate_odds",
    "candidate_market_probability",
    "selection_home_win",
    "selection_draw",
    "selection_away_win",
    "selection_handicap_win",
    "selection_handicap_draw",
    "selection_handicap_lose",
)

# Fixed pre-match bins keep pattern descriptions auditable.  Results choose
# which bins and combinations survive; results never choose a threshold after
# seeing the evaluation window.
PATTERN_NUMERIC_BINS = {
    "euro_draw_odds": (3.0, 3.2, 3.4, 3.6, 4.0),
    "euro_favorite_odds": (1.3, 1.5, 1.7, 1.9, 2.2, 2.5),
    "euro_probability_spread": (0.08, 0.16, 0.24, 0.34, 0.46),
    "asian_favorite_depth": (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5),
    "asian_favorite_depth_change": (-0.25, -0.01, 0.01, 0.25),
    "asian_favorite_water": (0.75, 0.85, 0.95, 1.0, 1.05, 1.15),
    "asian_favorite_water_change": (-0.15, -0.05, 0.05, 0.15),
    "asian_favorite_initial_water": (0.75, 0.85, 0.95, 1.0, 1.05, 1.15),
    "asian_underdog_water": (0.75, 0.85, 0.95, 1.0, 1.05, 1.15),
    "asian_underdog_water_change": (-0.15, -0.05, 0.05, 0.15),
    "asian_underdog_initial_water": (0.75, 0.85, 0.95, 1.0, 1.05, 1.15),
    "asian_favorite_water_gap": (-0.2, -0.1, -0.03, 0.03, 0.1, 0.2),
    "asian_initial_favorite_water_gap": (
        -0.2, -0.1, -0.03, 0.03, 0.1, 0.2,
    ),
    "hhad_draw_odds": (2.8, 3.1, 3.3, 3.5, 3.8, 4.2),
    "hhad_draw_change": (-0.25, -0.08, 0.08, 0.25),
    "total_current_line": (2.0, 2.25, 2.5, 2.75, 3.0, 3.25),
    "total_line_change": (-0.5, -0.01, 0.01, 0.5),
    "total_initial_over_water": (0.75, 0.85, 0.95, 1.0, 1.05, 1.15),
    "total_over_water": (0.75, 0.85, 0.95, 1.0, 1.05, 1.15),
    "total_over_water_change": (-0.15, -0.05, 0.05, 0.15),
    "total_initial_under_water": (0.75, 0.85, 0.95, 1.0, 1.05, 1.15),
    "total_under_water": (0.75, 0.85, 0.95, 1.0, 1.05, 1.15),
    "total_under_water_change": (-0.15, -0.05, 0.05, 0.15),
    "total_initial_under_bias": (-0.2, -0.05, 0.05, 0.2),
    "total_under_bias": (-0.2, -0.05, 0.05, 0.2),
    "rank_gap": (-0.5, -0.2, 0.2, 0.5),
}
PATTERN_CATEGORICAL_FEATURES = ("hhad_handicap",)
PATTERN_TRUE_FEATURES = (
    "favorite_odds_danger_140_170",
    "asian_deepen_high_water",
    "euro_asian_divergence",
)
PATTERN_FEATURE_LABELS = {
    "euro_draw_odds": "欧赔平赔",
    "euro_favorite_odds": "热门胜赔",
    "euro_probability_spread": "欧赔胜负概率差",
    "favorite_odds_danger_140_170": "热门胜赔1.40-1.70",
    "asian_favorite_depth": "热门方亚盘深度",
    "asian_favorite_depth_change": "热门方升降盘",
    "asian_favorite_water": "热门方即时水位",
    "asian_favorite_water_change": "热门方水位变化",
    "asian_favorite_initial_water": "热门方初盘水位",
    "asian_underdog_water": "下盘方即时水位",
    "asian_underdog_water_change": "下盘方水位变化",
    "asian_underdog_initial_water": "下盘方初盘水位",
    "asian_favorite_water_gap": "即时热门-下盘水位差",
    "asian_initial_favorite_water_gap": "初盘热门-下盘水位差",
    "asian_deepen_high_water": "升盘高水",
    "euro_asian_divergence": "欧亚背离",
    "hhad_handicap": "竞彩让球数",
    "hhad_draw_odds": "竞彩让平赔率",
    "hhad_draw_change": "竞彩让平赔率变化",
    "total_current_line": "大小球盘口",
    "total_line_change": "大小球升降",
    "total_initial_over_water": "大球初盘水位",
    "total_over_water": "大球即时水位",
    "total_over_water_change": "大球水位变化",
    "total_initial_under_water": "小球初盘水位",
    "total_under_water": "小球即时水位",
    "total_under_water_change": "小球水位变化",
    "total_initial_under_bias": "初盘大球-小球水位差",
    "total_under_bias": "即时大球-小球水位差",
    "rank_gap": "排名差",
}


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _at(values: Any, index: int) -> Optional[float]:
    if not isinstance(values, (list, tuple)) or len(values) <= index:
        return None
    return _number(values[index])


def _safe(value: Optional[float], default: float = 0.0) -> float:
    return float(value) if value is not None and math.isfinite(value) else default


def _probabilities(odds: Sequence[Any]) -> List[Optional[float]]:
    values = [_number(value) for value in odds]
    if len(values) != 3 or any(value is None or value <= 1 for value in values):
        return [None, None, None]
    inverse = [1.0 / float(value) for value in values]
    total = sum(inverse)
    return [value / total for value in inverse]


def _sigmoid(value: float) -> float:
    value = max(-35.0, min(35.0, value))
    return 1.0 / (1.0 + math.exp(-value))


def _softmax(values: Sequence[float]) -> List[float]:
    maximum = max(values) if values else 0.0
    exponents = [math.exp(max(-35.0, min(35.0, value - maximum))) for value in values]
    total = sum(exponents) or 1.0
    return [value / total for value in exponents]


def _owner_weekend(owner_date: Any) -> float:
    try:
        return 1.0 if datetime.strptime(str(owner_date)[:10], "%Y-%m-%d").weekday() >= 5 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _rank(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    try:
        return float(text) if text else None
    except ValueError:
        return None


def _source(snapshot_match: Dict[str, Any]) -> Dict[str, Any]:
    return snapshot_match.get("input_snapshot") or snapshot_match


def extract_prematch_features(
    snapshot_match: Dict[str, Any],
    *,
    owner_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Convert one immutable FAE snapshot to a stable numeric feature row."""
    source = _source(snapshot_match)
    prematch_analysis = snapshot_match.get("analysis") or {}
    prematch_official = (
        prematch_analysis.get("official_bet_recommendation") or {}
    )
    euro = source.get("euro") or {}
    asian = source.get("asian") or {}
    hhad = source.get("sporttery_handicap") or {}
    total = source.get("total") or {}
    rank = source.get("rank") or {}

    euro_initial = euro.get("initial") or []
    euro_current = euro.get("current") or []
    hhad_initial = hhad.get("initial") or []
    hhad_current = hhad.get("current") or []
    asian_initial = asian.get("initial") or []
    asian_current = asian.get("current") or []
    total_initial = total.get("initial") or []
    total_current = total.get("current") or []

    euro_probs = _probabilities(euro_current)
    hhad_probs = _probabilities(hhad_current)
    initial_asian_line = _handicap_value(
        asian_initial[1] if len(asian_initial) > 1 else None
    )
    current_asian_line = _handicap_value(
        asian_current[1] if len(asian_current) > 1 else None
    )
    home_rank = _rank(rank.get("home"))
    away_rank = _rank(rank.get("away"))
    rank_gap = (
        max(-30.0, min(30.0, away_rank - home_rank)) / 30.0
        if home_rank is not None and away_rank is not None else 0.0
    )
    day = str(
        owner_date
        or snapshot_match.get("owner_date")
        or source.get("owner_date")
        or ""
    )[:10]

    euro_home_odds = _at(euro_current, 0)
    euro_draw_odds = _at(euro_current, 1)
    euro_away_odds = _at(euro_current, 2)
    if (
        euro_home_odds is not None
        and euro_away_odds is not None
        and abs(euro_home_odds - euro_away_odds) >= 0.01
    ):
        favorite_side = (
            "home" if euro_home_odds < euro_away_odds else "away"
        )
        favorite_odds = min(euro_home_odds, euro_away_odds)
        favorite_probability = (
            euro_probs[0] if favorite_side == "home" else euro_probs[2]
        )
    else:
        favorite_side = None
        favorite_odds = None
        favorite_probability = None

    initial_favorite_depth = None
    current_favorite_depth = None
    favorite_initial_water = None
    favorite_current_water = None
    underdog_initial_water = None
    underdog_current_water = None
    if favorite_side == "home":
        initial_favorite_depth = initial_asian_line
        current_favorite_depth = current_asian_line
        favorite_initial_water = _at(asian_initial, 0)
        favorite_current_water = _at(asian_current, 0)
        underdog_initial_water = _at(asian_initial, 2)
        underdog_current_water = _at(asian_current, 2)
    elif favorite_side == "away":
        initial_favorite_depth = (
            -initial_asian_line if initial_asian_line is not None else None
        )
        current_favorite_depth = (
            -current_asian_line if current_asian_line is not None else None
        )
        favorite_initial_water = _at(asian_initial, 2)
        favorite_current_water = _at(asian_current, 2)
        underdog_initial_water = _at(asian_initial, 0)
        underdog_current_water = _at(asian_current, 0)

    missing = []
    for market, values in (
        ("euro", euro_current),
        ("asian", asian_current),
        ("sporttery_handicap", hhad_current),
        ("total", total_current),
    ):
        if not values or sum(value is not None for value in values) < 2:
            missing.append(market)

    def change(current: Any, initial: Any) -> float:
        current_value = _number(current)
        initial_value = _number(initial)
        return (
            current_value - initial_value
            if current_value is not None and initial_value is not None else 0.0
        )

    feature_values = {
        "euro_home_odds": _safe(euro_home_odds, 3.0),
        "euro_draw_odds": _safe(euro_draw_odds, 3.3),
        "euro_away_odds": _safe(euro_away_odds, 3.0),
        "euro_home_probability": _safe(euro_probs[0], 1.0 / 3.0),
        "euro_draw_probability": _safe(euro_probs[1], 1.0 / 3.0),
        "euro_away_probability": _safe(euro_probs[2], 1.0 / 3.0),
        "euro_favorite_odds": _safe(favorite_odds, 2.2),
        "euro_favorite_probability": _safe(
            favorite_probability, 1.0 / 3.0
        ),
        "euro_probability_spread": (
            max(_safe(euro_probs[0]), _safe(euro_probs[2]))
            - min(_safe(euro_probs[0]), _safe(euro_probs[2]))
        ),
        "favorite_odds_danger_140_170": float(
            favorite_odds is not None and 1.40 <= favorite_odds <= 1.70
        ),
        "draw_odds_below_320": float(
            euro_draw_odds is not None and euro_draw_odds < 3.20
        ),
        "draw_odds_320_360": float(
            euro_draw_odds is not None and 3.20 <= euro_draw_odds <= 3.60
        ),
        "euro_home_change": change(_at(euro_current, 0), _at(euro_initial, 0)),
        "euro_draw_change": change(_at(euro_current, 1), _at(euro_initial, 1)),
        "euro_away_change": change(_at(euro_current, 2), _at(euro_initial, 2)),
        "asian_initial_line": _safe(initial_asian_line),
        "asian_current_line": _safe(current_asian_line),
        "asian_line_change": change(current_asian_line, initial_asian_line),
        "asian_home_water": _safe(_at(asian_current, 0), 1.0),
        "asian_away_water": _safe(_at(asian_current, 2), 1.0),
        "asian_home_water_change": change(_at(asian_current, 0), _at(asian_initial, 0)),
        "asian_away_water_change": change(_at(asian_current, 2), _at(asian_initial, 2)),
        "asian_favorite_depth": _safe(current_favorite_depth),
        "asian_favorite_depth_change": change(
            current_favorite_depth, initial_favorite_depth
        ),
        "asian_favorite_water": _safe(favorite_current_water, 1.0),
        "asian_favorite_water_change": change(
            favorite_current_water, favorite_initial_water
        ),
        # The linear learner already receives raw home/away water and change.
        # These side-normalised prices are kept for auditable combination
        # mining without duplicating highly collinear vector columns.
        "asian_favorite_initial_water": _safe(
            favorite_initial_water, 1.0
        ),
        "asian_underdog_water": _safe(underdog_current_water, 1.0),
        "asian_underdog_water_change": change(
            underdog_current_water, underdog_initial_water
        ),
        "asian_underdog_initial_water": _safe(
            underdog_initial_water, 1.0
        ),
        "asian_favorite_water_gap": change(
            favorite_current_water, underdog_current_water
        ),
        "asian_initial_favorite_water_gap": change(
            favorite_initial_water, underdog_initial_water
        ),
        "asian_deepen_high_water": float(
            current_favorite_depth is not None
            and initial_favorite_depth is not None
            and current_favorite_depth > initial_favorite_depth
            and favorite_current_water is not None
            and favorite_current_water >= 1.0
        ),
        "euro_asian_divergence": float(
            favorite_odds is not None
            and favorite_odds <= 2.20
            and current_favorite_depth is not None
            and current_favorite_depth <= 0
        ),
        "hhad_handicap": _safe(_number(hhad.get("value"))),
        "hhad_home_probability": _safe(hhad_probs[0], 1.0 / 3.0),
        "hhad_draw_probability": _safe(hhad_probs[1], 1.0 / 3.0),
        "hhad_away_probability": _safe(hhad_probs[2], 1.0 / 3.0),
        "hhad_home_change": change(_at(hhad_current, 0), _at(hhad_initial, 0)),
        "hhad_draw_change": change(_at(hhad_current, 1), _at(hhad_initial, 1)),
        "hhad_away_change": change(_at(hhad_current, 2), _at(hhad_initial, 2)),
        "hhad_draw_odds": _safe(_at(hhad_current, 1), 3.5),
        "total_initial_line": _safe(_at(total_initial, 1), 2.5),
        "total_current_line": _safe(_at(total_current, 1), 2.5),
        "total_line_change": change(_at(total_current, 1), _at(total_initial, 1)),
        "total_initial_over_water": _safe(_at(total_initial, 0), 1.0),
        "total_over_water": _safe(_at(total_current, 0), 1.0),
        "total_over_water_change": change(
            _at(total_current, 0), _at(total_initial, 0)
        ),
        "total_initial_under_water": _safe(_at(total_initial, 2), 1.0),
        "total_under_water": _safe(_at(total_current, 2), 1.0),
        "total_under_water_change": change(
            _at(total_current, 2), _at(total_initial, 2)
        ),
        "total_initial_under_bias": change(
            _at(total_initial, 0), _at(total_initial, 2)
        ),
        "total_under_bias": change(
            _at(total_current, 0), _at(total_current, 2)
        ),
        "rank_gap": rank_gap,
        "weekend": _owner_weekend(day),
        "missing_euro": float("euro" in missing),
        "missing_asian": float("asian" in missing),
        "missing_sporttery_handicap": float(
            "sporttery_handicap" in missing
        ),
        "missing_total": float("total" in missing),
    }
    return {
        "schema_version": SUPERVISED_SCHEMA_VERSION,
        "match_id": str(snapshot_match.get("match_id") or source.get("match_id") or ""),
        "match_number": snapshot_match.get("match_number") or source.get("match_number"),
        "owner_date": day,
        "match_time": snapshot_match.get("match_time") or source.get("match_time"),
        "league": str(snapshot_match.get("league") or source.get("league") or "未知"),
        "features": feature_values,
        "feature_vector": [feature_values[name] for name in FEATURE_NAMES],
        "market": {
            "ordinary_home_probability": euro_probs[0],
            "ordinary_draw_probability": euro_probs[1],
            "ordinary_away_probability": euro_probs[2],
            "ordinary_home_odds": _at(euro_current, 0),
            "ordinary_draw_odds": _at(euro_current, 1),
            "ordinary_away_odds": _at(euro_current, 2),
            "handicap_home_probability": hhad_probs[0],
            "handicap_draw_probability": hhad_probs[1],
            "handicap_away_probability": hhad_probs[2],
            "handicap_home_odds": _at(hhad_current, 0),
            "handicap_draw_odds": _at(hhad_current, 1),
            "handicap_away_odds": _at(hhad_current, 2),
            "handicap": _number(hhad.get("value")),
        },
        "quality": {
            "missing_markets": missing,
            "complete": not missing,
        },
        "prematch_ai": {
            "selection": str(
                prematch_analysis.get("single_play")
                or prematch_analysis.get("primary_play")
                or ""
            ),
            "analysis_source": str(
                snapshot_match.get("analysis_source") or ""
            ),
            "ai_verified": (
                str(snapshot_match.get("analysis_source") or "")
                == "volcengine-ark"
            ),
            "official_actionable": bool(
                prematch_official.get("actionable")
            ),
            "official_selection": str(
                prematch_official.get("selection") or ""
            ),
        },
    }


def build_training_example(
    snapshot_match: Dict[str, Any],
    result_match: Dict[str, Any],
    *,
    owner_date: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Attach outcome labels to an immutable pre-match feature row."""
    if (result_match or {}).get("status") not in (2, "2"):
        return None
    home_score = _number((result_match or {}).get("home_score"))
    away_score = _number((result_match or {}).get("away_score"))
    if home_score is None or away_score is None:
        return None
    row = extract_prematch_features(snapshot_match, owner_date=owner_date)
    if not row.get("match_id"):
        return None
    margin = int(home_score - away_score)
    clipped_margin = max(MARGIN_CLASSES[0], min(MARGIN_CLASSES[-1], margin))
    handicap = row["market"].get("handicap")
    handicap_draw = bool(
        handicap is not None and abs((home_score + handicap) - away_score) < 1e-9
    )
    row["label"] = {
        "home_score": int(home_score),
        "away_score": int(away_score),
        "goal_margin": margin,
        "goal_margin_class": clipped_margin,
        "ordinary_draw": margin == 0,
        "handicap_draw": handicap_draw,
        "ordinary_selection": (
            "主胜" if margin > 0 else "客胜" if margin < 0 else "平局"
        ),
        "handicap_selection": (
            "让胜" if handicap is not None and margin + handicap > 0
            else "让负" if handicap is not None and margin + handicap < 0
            else "让平" if handicap is not None else None
        ),
    }
    return row


def build_training_days(
    snapshot_days: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build chronological, deduplicated examples from snapshot/result pairs."""
    result = []
    seen = set()
    for day in sorted(
        list(snapshot_days),
        key=lambda item: str((item.get("snapshot") or {}).get("owner_date") or ""),
    ):
        snapshot = day.get("snapshot") or {}
        owner_date = str(snapshot.get("owner_date") or "")[:10]
        results = {
            str(match_id): value
            for match_id, value in (day.get("results") or {}).items()
        }
        examples = []
        for match in snapshot.get("matches") or []:
            match_id = str(match.get("match_id") or "")
            key = (owner_date, match_id)
            if not match_id or key in seen:
                continue
            example = build_training_example(
                match, results.get(match_id) or {}, owner_date=owner_date
            )
            if not example:
                continue
            seen.add(key)
            examples.append(example)
        if examples:
            result.append({"owner_date": owner_date, "examples": examples})
    return result


def _pretty_number(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{float(value):g}"


def _pattern_feature_tokens(row: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """Return one mutually-exclusive, pre-match bin per eligible feature."""
    features = row.get("features") or {}
    missing = set((row.get("quality") or {}).get("missing_markets") or [])
    market_by_prefix = {
        "euro_": "euro",
        "favorite_": "euro",
        "asian_": "asian",
        "hhad_": "sporttery_handicap",
        "total_": "total",
    }
    output = []

    def market_missing(feature: str) -> bool:
        return any(
            feature.startswith(prefix) and market in missing
            for prefix, market in market_by_prefix.items()
        )

    for feature, thresholds in PATTERN_NUMERIC_BINS.items():
        if market_missing(feature):
            continue
        value = _number(features.get(feature))
        if value is None or not math.isfinite(value):
            continue
        bin_index = len(thresholds)
        for index, upper in enumerate(thresholds):
            if value <= upper:
                bin_index = index
                break
        lower = thresholds[bin_index - 1] if bin_index > 0 else None
        upper = thresholds[bin_index] if bin_index < len(thresholds) else None
        if lower is None:
            range_label = f"≤{_pretty_number(upper)}"
        elif upper is None:
            range_label = f">{_pretty_number(lower)}"
        else:
            range_label = (
                f"({_pretty_number(lower)},{_pretty_number(upper)}]"
            )
        token = f"{feature}#bin{bin_index}"
        output.append((token, {
            "token": token,
            "feature": feature,
            "feature_label": PATTERN_FEATURE_LABELS.get(feature, feature),
            "operator": "range",
            "lower_exclusive": lower,
            "upper_inclusive": upper,
            "label": range_label,
        }))

    for feature in PATTERN_CATEGORICAL_FEATURES:
        if market_missing(feature):
            continue
        value = _number(features.get(feature))
        if value is None:
            continue
        value = round(value * 4.0) / 4.0
        token = f"{feature}#eq{_pretty_number(value)}"
        output.append((token, {
            "token": token,
            "feature": feature,
            "feature_label": PATTERN_FEATURE_LABELS.get(feature, feature),
            "operator": "equal",
            "value": value,
            "label": f"={_pretty_number(value)}",
        }))

    for feature in PATTERN_TRUE_FEATURES:
        if market_missing(feature) or _safe(_number(features.get(feature))) < 0.5:
            continue
        token = f"{feature}#true"
        output.append((token, {
            "token": token,
            "feature": feature,
            "feature_label": PATTERN_FEATURE_LABELS.get(feature, feature),
            "operator": "true",
            "label": "是",
        }))
    return output


def _pattern_target(row: Dict[str, Any], selection: str) -> Optional[bool]:
    label = row.get("label") or {}
    if selection == "ordinary_draw":
        return bool(label.get("ordinary_draw"))
    if selection == "handicap_draw":
        if _number((row.get("market") or {}).get("handicap")) is None:
            return None
        return bool(label.get("handicap_draw"))
    return None


def _pattern_odds(row: Dict[str, Any], selection: str) -> Optional[float]:
    market = row.get("market") or {}
    return _number(market.get(
        "ordinary_draw_odds"
        if selection == "ordinary_draw" else "handicap_draw_odds"
    ))


def _aggregate_pattern_rows(
    rows: Sequence[Dict[str, Any]],
    selection: str,
    *,
    allowed_keys: Optional[set] = None,
) -> Tuple[Dict[Tuple[str, ...], Dict[str, float]], Dict[str, Dict[str, Any]]]:
    counts = defaultdict(lambda: {
        "support": 0.0, "hits": 0.0, "return": 0.0,
    })
    conditions = {}
    for row in rows:
        target = _pattern_target(row, selection)
        if target is None:
            continue
        token_rows = _pattern_feature_tokens(row)
        tokens = [item[0] for item in token_rows]
        conditions.update({item[0]: item[1] for item in token_rows})
        odds = _pattern_odds(row, selection)
        for size in (1, 2, 3):
            for key in combinations(tokens, size):
                key = tuple(sorted(key))
                if allowed_keys is not None and key not in allowed_keys:
                    continue
                stat = counts[key]
                stat["support"] += 1
                stat["hits"] += int(target)
                if target and odds is not None and odds > 1:
                    stat["return"] += odds
    return dict(counts), conditions


def _wilson_lower(hits: float, support: float, z: float = 1.28) -> float:
    if support <= 0:
        return 0.0
    probability = hits / support
    denominator = 1.0 + z * z / support
    centre = probability + z * z / (2.0 * support)
    spread = z * math.sqrt(
        probability * (1.0 - probability) / support
        + z * z / (4.0 * support * support)
    )
    return max(0.0, (centre - spread) / denominator)


def mine_feature_patterns(
    examples: Sequence[Dict[str, Any]],
    selection: str,
    *,
    limit: int = 24,
) -> Dict[str, Any]:
    """Mine 1-3 feature combinations with an internal chronological holdout."""
    rows = [
        row for row in examples
        if _pattern_target(row, selection) is not None
    ]
    dates = sorted({str(row.get("owner_date") or "")[:10] for row in rows})
    empty = {
        "selection": selection,
        "status": "insufficient_samples",
        "sample_count": len(rows),
        "patterns": [],
    }
    if len(rows) < 120 or len(dates) < 14:
        return empty
    split_index = max(1, min(len(dates) - 5, int(len(dates) * 0.70)))
    discovery_dates = set(dates[:split_index])
    validation_dates = set(dates[split_index:])
    discovery = [
        row for row in rows
        if str(row.get("owner_date") or "")[:10] in discovery_dates
    ]
    validation = [
        row for row in rows
        if str(row.get("owner_date") or "")[:10] in validation_dates
    ]
    if len(discovery) < 80 or len(validation) < 30:
        return empty

    discovery_base = sum(
        int(bool(_pattern_target(row, selection))) for row in discovery
    ) / len(discovery)
    validation_base = sum(
        int(bool(_pattern_target(row, selection))) for row in validation
    ) / len(validation)
    combined_base = sum(
        int(bool(_pattern_target(row, selection))) for row in rows
    ) / len(rows)
    discovery_counts, condition_lookup = _aggregate_pattern_rows(
        discovery, selection
    )
    candidates = []
    for key, stat in discovery_counts.items():
        support = int(stat["support"])
        minimum_support = max(12, int(math.ceil(len(discovery) * 0.04)))
        if support < minimum_support:
            continue
        hit_rate = stat["hits"] / support
        lift = hit_rate - discovery_base
        if lift < 0.04:
            continue
        candidates.append((
            lift * math.sqrt(support) * (1.0 + 0.12 * (len(key) - 1)),
            key,
        ))
    candidates.sort(reverse=True)
    candidate_keys = {key for _, key in candidates[:800]}
    if not candidate_keys:
        return {
            **empty,
            "status": "no_stable_patterns",
            "discovery_days": len(discovery_dates),
            "validation_days": len(validation_dates),
            "base_probability": round(combined_base * 100, 2),
        }
    validation_counts, validation_conditions = _aggregate_pattern_rows(
        validation, selection, allowed_keys=candidate_keys
    )
    condition_lookup.update(validation_conditions)
    combined_counts, _ = _aggregate_pattern_rows(
        rows, selection, allowed_keys=candidate_keys
    )

    patterns = []
    validation_minimum = max(5, int(math.ceil(len(validation) * 0.03)))
    prior_strength = 25.0
    for key in candidate_keys:
        discovery_stat = discovery_counts.get(key) or {}
        validation_stat = validation_counts.get(key) or {}
        combined_stat = combined_counts.get(key) or {}
        validation_support = int(validation_stat.get("support") or 0)
        if validation_support < validation_minimum:
            continue
        validation_hits = int(validation_stat.get("hits") or 0)
        validation_rate = validation_hits / validation_support
        validation_lift = validation_rate - validation_base
        if validation_lift < 0:
            continue
        support = int(combined_stat.get("support") or 0)
        hits = int(combined_stat.get("hits") or 0)
        if not support:
            continue
        hit_rate = hits / support
        lift = hit_rate - combined_base
        if lift < 0.025:
            continue
        shrunk_probability = (
            hits + prior_strength * combined_base
        ) / (support + prior_strength)
        roi = (
            (float(combined_stat.get("return") or 0) - support)
            / support * 100
        )
        score = (
            lift * 100 * 0.80
            + validation_lift * 100 * 1.20
            + math.log1p(support) * 1.5
            + (len(key) - 1) * 1.5
        )
        conditions = [condition_lookup[token] for token in key]
        description = " + ".join(
            f"{item['feature_label']}{item['label']}"
            for item in conditions
        )
        pattern_id = sha256(
            f"{selection}|{'|'.join(key)}".encode("utf-8")
        ).hexdigest()[:14]
        patterns.append({
            "pattern_id": pattern_id,
            "selection": selection,
            "tokens": list(key),
            "conditions": conditions,
            "description": description,
            "size": len(key),
            "support": support,
            "hits": hits,
            "hit_rate": round(hit_rate * 100, 2),
            "base_probability": round(combined_base * 100, 2),
            "lift_pp": round(lift * 100, 2),
            "shrunk_probability": round(shrunk_probability * 100, 2),
            "confidence_lower": round(_wilson_lower(hits, support) * 100, 2),
            "roi": round(roi, 1),
            "discovery_support": int(discovery_stat.get("support") or 0),
            "discovery_hit_rate": round(
                float(discovery_stat.get("hits") or 0)
                / float(discovery_stat.get("support") or 1) * 100,
                2,
            ),
            "validation_support": validation_support,
            "validation_hits": validation_hits,
            "validation_hit_rate": round(validation_rate * 100, 2),
            "validation_lift_pp": round(validation_lift * 100, 2),
            "score": round(score, 3),
            "time_direction_consistent": True,
        })
    patterns.sort(
        key=lambda item: (
            float(item.get("score") or 0),
            int(item.get("validation_support") or 0),
        ),
        reverse=True,
    )
    # Preserve representation diversity instead of returning 24 near-identical
    # triples from one odds bin.
    kept = []
    size_limits = {1: 6, 2: 10, 3: 10}
    size_counts = defaultdict(int)
    for pattern in patterns:
        size = int(pattern.get("size") or 1)
        if size_counts[size] >= size_limits.get(size, limit):
            continue
        kept.append(pattern)
        size_counts[size] += 1
        if len(kept) >= max(1, int(limit)):
            break
    return {
        "selection": selection,
        "status": "shadow_patterns_ready" if kept else "no_stable_patterns",
        "sample_count": len(rows),
        "training_days": len(dates),
        "discovery_days": len(discovery_dates),
        "validation_days": len(validation_dates),
        "base_probability": round(combined_base * 100, 2),
        "patterns": kept,
        "policy": (
            "固定赛前分箱；前70%比赛日发现，后30%比赛日验证；"
            "仅保留验证期方向一致的1至3项组合。"
        ),
    }


def _matched_pattern_signal(
    feature_row: Dict[str, Any], package: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    tokens = {
        token for token, _ in _pattern_feature_tokens(feature_row)
    }
    matches = [
        pattern for pattern in (package or {}).get("patterns") or []
        if set(pattern.get("tokens") or []).issubset(tokens)
    ]
    matches.sort(
        key=lambda item: (
            int(item.get("size") or 1),
            float(item.get("score") or 0),
            int(item.get("validation_support") or 0),
        ),
        reverse=True,
    )
    matches = matches[:3]
    weighted_probability = 0.0
    total_weight = 0.0
    total_validation_support = 0
    for pattern in matches:
        validation_support = int(pattern.get("validation_support") or 0)
        validation_lift = max(
            0.01, float(pattern.get("validation_lift_pp") or 0) / 100.0
        )
        specificity = 1.0 + 0.15 * (int(pattern.get("size") or 1) - 1)
        weight = math.sqrt(max(1, validation_support)) * validation_lift * specificity
        weighted_probability += (
            float(pattern.get("shrunk_probability") or 0) / 100.0
        ) * weight
        total_weight += weight
        total_validation_support += validation_support
    probability = (
        weighted_probability / total_weight if total_weight > 0 else None
    )
    # This layer is deliberately weaker than the market and logistic blend.
    # It can reorder shadow candidates, but cannot dominate on a small pattern.
    blend_weight = min(
        0.18,
        total_validation_support
        / float(total_validation_support + 120) * 0.30,
    ) if matches else 0.0
    return {
        "matched_count": len(matches),
        "probability": probability,
        "blend_weight": blend_weight,
        "validation_support": total_validation_support,
        "patterns": [{
            key: pattern.get(key)
            for key in (
                "pattern_id", "description", "size", "support", "hits",
                "hit_rate", "lift_pp", "shrunk_probability", "roi",
                "validation_support", "validation_hit_rate",
                "validation_lift_pp", "confidence_lower",
            )
        } for pattern in matches],
    }


def _apply_pattern_signal(
    probability: Optional[float], signal: Dict[str, Any]
) -> Tuple[Optional[float], float]:
    pattern_probability = _number(signal.get("probability"))
    weight = _safe(_number(signal.get("blend_weight")))
    if probability is None or pattern_probability is None or weight <= 0:
        return probability, 0.0
    adjusted = probability * (1.0 - weight) + pattern_probability * weight
    return adjusted, (adjusted - probability) * 100.0


def _standardizer_width(
    vectors: List[List[float]], width: int
) -> Tuple[List[float], List[float]]:
    if not vectors:
        return [0.0] * width, [1.0] * width
    means = [
        sum(row[index] for row in vectors) / len(vectors)
        for index in range(width)
    ]
    scales = []
    for index, mean in enumerate(means):
        variance = sum((row[index] - mean) ** 2 for row in vectors) / len(vectors)
        scales.append(max(0.01, math.sqrt(variance)))
    return means, scales


def _standardizer(vectors: List[List[float]]) -> Tuple[List[float], List[float]]:
    return _standardizer_width(vectors, len(FEATURE_NAMES))


def _scale(vector: Sequence[float], means: Sequence[float], scales: Sequence[float]) -> List[float]:
    return [
        1.0,
        *[
            max(-6.0, min(6.0, (float(value) - means[index]) / scales[index]))
            for index, value in enumerate(vector)
        ],
    ]


def _fit_binary(
    vectors: List[List[float]], labels: List[int], *, epochs: int = 320
) -> List[float]:
    width = len(vectors[0]) if vectors else len(FEATURE_NAMES) + 1
    if not vectors:
        return [0.0] * width
    matrix = np.asarray(vectors, dtype=float)
    target = np.asarray(labels, dtype=float)
    weights = np.zeros(width, dtype=float)
    learning_rate = 0.08
    regularization = 0.012
    for epoch in range(epochs):
        logits = np.clip(matrix @ weights, -35.0, 35.0)
        predictions = 1.0 / (1.0 + np.exp(-logits))
        gradients = matrix.T @ (predictions - target) / len(vectors)
        penalty = regularization * weights
        penalty[0] = 0.0
        rate = learning_rate / (1.0 + epoch / 220.0)
        weights -= rate * (gradients + penalty)
    return weights.tolist()


def _fit_softmax(
    vectors: List[List[float]], labels: List[int], *, epochs: int = 360
) -> List[List[float]]:
    width = len(vectors[0]) if vectors else len(FEATURE_NAMES) + 1
    if not vectors:
        return [[0.0] * width for _ in MARGIN_CLASSES]
    matrix = np.asarray(vectors, dtype=float)
    target = np.zeros((len(labels), len(MARGIN_CLASSES)), dtype=float)
    target[np.arange(len(labels)), np.asarray(labels, dtype=int)] = 1.0
    weights = np.zeros((len(MARGIN_CLASSES), width), dtype=float)
    learning_rate = 0.07
    regularization = 0.014
    for epoch in range(epochs):
        scores = matrix @ weights.T
        scores -= scores.max(axis=1, keepdims=True)
        exponents = np.exp(np.clip(scores, -35.0, 35.0))
        probabilities = exponents / exponents.sum(axis=1, keepdims=True)
        gradients = (probabilities - target).T @ matrix / len(vectors)
        penalty = regularization * weights
        penalty[:, 0] = 0.0
        rate = learning_rate / (1.0 + epoch / 240.0)
        weights -= rate * (gradients + penalty)
    return weights.tolist()


def _league_priors(examples: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    global_margin = defaultdict(int)
    global_draw = 0
    leagues = defaultdict(lambda: {"sample": 0, "draw": 0, "margins": defaultdict(int)})
    for example in examples:
        label = example["label"]
        league = str(example.get("league") or "未知")
        margin = int(label["goal_margin_class"])
        global_margin[margin] += 1
        global_draw += int(label["ordinary_draw"])
        row = leagues[league]
        row["sample"] += 1
        row["draw"] += int(label["ordinary_draw"])
        row["margins"][margin] += 1
    total = max(1, len(examples))
    global_draw_rate = global_draw / total
    global_margin_rates = {
        str(margin): global_margin[margin] / total for margin in MARGIN_CLASSES
    }


def _weight_explanation(weights: Sequence[float], limit: int = 6) -> Dict[str, Any]:
    return _weight_explanation_named(weights, FEATURE_NAMES, limit=limit)


def _weight_explanation_named(
    weights: Sequence[float],
    names: Sequence[str],
    limit: int = 6,
) -> Dict[str, Any]:
    rows = [
        {"feature": name, "coefficient": round(float(weight), 6)}
        for name, weight in zip(names, list(weights)[1:])
    ]
    return {
        "positive": sorted(
            (row for row in rows if row["coefficient"] > 0),
            key=lambda row: row["coefficient"],
            reverse=True,
        )[:limit],
        "negative": sorted(
            (row for row in rows if row["coefficient"] < 0),
            key=lambda row: row["coefficient"],
        )[:limit],
    }


def _result_probabilities_from_margins(
    margins: Dict[int, float],
    handicap: Optional[float],
) -> Dict[str, Optional[float]]:
    """Aggregate one coherent goal-margin distribution into six outcomes."""
    output: Dict[str, Optional[float]] = {
        "主胜": sum(value for margin, value in margins.items() if margin > 0),
        "平局": margins.get(0, 0.0),
        "客胜": sum(value for margin, value in margins.items() if margin < 0),
        "让胜": None,
        "让平": None,
        "让负": None,
    }
    if handicap is None:
        return output
    output["让胜"] = sum(
        value for margin, value in margins.items()
        if margin + handicap > 0
    )
    output["让平"] = sum(
        value for margin, value in margins.items()
        if abs(margin + handicap) < 1e-9
    )
    output["让负"] = sum(
        value for margin, value in margins.items()
        if margin + handicap < 0
    )
    return output


def _single_market_terms(
    market: Dict[str, Any], selection: str
) -> Tuple[Optional[float], Optional[float], str]:
    mapping = {
        "主胜": ("ordinary_home_probability", "ordinary_home_odds", "胜平负"),
        "平局": ("ordinary_draw_probability", "ordinary_draw_odds", "胜平负"),
        "客胜": ("ordinary_away_probability", "ordinary_away_odds", "胜平负"),
        "让胜": ("handicap_home_probability", "handicap_home_odds", "竞彩让球"),
        "让平": ("handicap_draw_probability", "handicap_draw_odds", "竞彩让球"),
        "让负": ("handicap_away_probability", "handicap_away_odds", "竞彩让球"),
    }
    probability_key, odds_key, market_name = mapping[selection]
    return (
        _number(market.get(probability_key)),
        _number(market.get(odds_key)),
        market_name,
    )


def _single_meta_vector(
    feature_row: Dict[str, Any], selection: str
) -> Optional[List[float]]:
    market_probability, odds, _ = _single_market_terms(
        feature_row.get("market") or {}, selection
    )
    if market_probability is None or odds is None or odds <= 1:
        return None
    one_hot = [float(selection == value) for value in SINGLE_SELECTIONS]
    return [
        *[float(value) for value in feature_row.get("feature_vector") or []],
        float(odds),
        float(market_probability),
        *one_hot,
    ]
    output = {}
    prior_strength = 30.0
    for league, row in leagues.items():
        sample = row["sample"]
        output[league] = {
            "sample": sample,
            "draw_probability": round(
                (row["draw"] + prior_strength * global_draw_rate)
                / (sample + prior_strength), 6
            ),
            "margin_probabilities": {
                str(margin): round(
                    (row["margins"][margin] + prior_strength * global_margin_rates[str(margin)])
                    / (sample + prior_strength),
                    6,
                )
                for margin in MARGIN_CLASSES
            },
        }
    return {
        "global_draw_probability": round(global_draw_rate, 6),
        "global_margin_probabilities": global_margin_rates,
        "leagues": output,
    }


class FAESupervisedTrainer:
    """Train and serialise the explainable shadow model."""

    def fit(
        self,
        examples: Iterable[Dict[str, Any]],
        *,
        fast: bool = False,
    ) -> Dict[str, Any]:
        rows = [deepcopy(row) for row in examples if row.get("label")]
        if len(rows) < 20:
            raise ValueError("监督模型至少需要20场已结算赛前快照")
        raw_vectors = [list(row["feature_vector"]) for row in rows]
        means, scales = _standardizer(raw_vectors)
        vectors = [_scale(vector, means, scales) for vector in raw_vectors]
        binary_labels = [int(row["label"]["ordinary_draw"]) for row in rows]
        margin_indexes = [
            MARGIN_CLASSES.index(int(row["label"]["goal_margin_class"])) for row in rows
        ]
        dates = sorted({str(row.get("owner_date") or "")[:10] for row in rows})
        draw_weights = _fit_binary(
            vectors, binary_labels, epochs=120 if fast else 320
        )
        margin_weights = _fit_softmax(
            vectors, margin_indexes, epochs=140 if fast else 360
        )
        single_meta_rows = [
            (vector, int(_single_event_hit(row, selection)))
            for row in rows
            for selection in SINGLE_SELECTIONS
            for vector in [_single_meta_vector(row, selection)]
            if vector is not None
        ]
        single_meta_raw_vectors = [
            vector for vector, _ in single_meta_rows
        ]
        single_meta_means, single_meta_scales = _standardizer_width(
            single_meta_raw_vectors, len(SINGLE_META_FEATURE_NAMES)
        )
        single_meta_vectors = [
            _scale(vector, single_meta_means, single_meta_scales)
            for vector in single_meta_raw_vectors
        ]
        single_meta_weights = _fit_binary(
            single_meta_vectors,
            [label for _, label in single_meta_rows],
            epochs=140 if fast else 360,
        )
        feature_patterns = {
            "ordinary_draw": mine_feature_patterns(
                rows, "ordinary_draw"
            ),
            "handicap_draw": mine_feature_patterns(
                rows, "handicap_draw"
            ),
        }
        artifact = {
            "schema_version": SUPERVISED_SCHEMA_VERSION,
            "model_version": SUPERVISED_MODEL_VERSION,
            "model_id": "",
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "feature_names": list(FEATURE_NAMES),
            "feature_means": means,
            "feature_scales": scales,
            "draw_weights": draw_weights,
            "margin_classes": list(MARGIN_CLASSES),
            "margin_weights": margin_weights,
            "single_meta_feature_names": list(SINGLE_META_FEATURE_NAMES),
            "single_meta_feature_means": single_meta_means,
            "single_meta_feature_scales": single_meta_scales,
            "single_meta_weights": single_meta_weights,
            "single_meta_sample_count": len(single_meta_rows),
            "sample_count": len(rows),
            "training_days": len(dates),
            "training_start_date": dates[0] if dates else None,
            "training_end_date": dates[-1] if dates else None,
            "league_priors": _league_priors(rows),
            "feature_patterns": feature_patterns,
            "feature_explanations": {
                "ordinary_draw": _weight_explanation(draw_weights),
                "goal_margin": {
                    str(margin): _weight_explanation(margin_weights[index])
                    for index, margin in enumerate(MARGIN_CLASSES)
                },
                "high_confidence_single": _weight_explanation_named(
                    single_meta_weights, SINGLE_META_FEATURE_NAMES
                ),
                "note": (
                    "系数来自标准化赛前特征；正负表示模型方向，"
                    "不代表单变量因果关系。"
                ),
            },
            "status": "shadow",
            "governance": {
                "immutable_prematch_only": True,
                "result_fields_used_as_features": False,
                "market_blend": True,
                "time_isolated_feature_pattern_mining": True,
                "pattern_max_features": 3,
                "pattern_max_blend_weight": 0.18,
                "weekend_pool_correction": True,
                "may_override_official_recommendations": False,
            },
        }
        identity = sha256(json.dumps(
            {key: artifact[key] for key in (
                "model_version", "feature_names", "draw_weights",
                "margin_weights", "single_meta_weights",
                "feature_patterns", "sample_count",
                "training_end_date",
            )}, sort_keys=True, default=str
        ).encode("utf-8")).hexdigest()[:16]
        artifact["model_id"] = f"{SUPERVISED_MODEL_VERSION}-{identity}"
        return artifact


class FAESupervisedPredictor:
    """Predict calibrated draw and exact handicap-draw probabilities."""

    def __init__(self, artifact: Dict[str, Any]):
        self.artifact = deepcopy(artifact or {})
        if not self.artifact.get("draw_weights"):
            raise ValueError("监督模型文件无效")

    def _scaled(self, feature_row: Dict[str, Any]) -> List[float]:
        return _scale(
            feature_row["feature_vector"],
            self.artifact.get("feature_means") or [0.0] * len(FEATURE_NAMES),
            self.artifact.get("feature_scales") or [1.0] * len(FEATURE_NAMES),
        )

    def predict(
        self,
        snapshot_match: Dict[str, Any],
        *,
        owner_date: Optional[str] = None,
        daily_match_count: int = 1,
    ) -> Dict[str, Any]:
        # Walk-forward evaluation passes an already frozen training example.
        # Re-extracting it as a raw snapshot would silently replace historical
        # markets with defaults and invalidate the out-of-sample test.
        if (
            isinstance(snapshot_match.get("feature_vector"), list)
            and isinstance(snapshot_match.get("features"), dict)
            and isinstance(snapshot_match.get("market"), dict)
        ):
            feature_row = deepcopy(snapshot_match)
        else:
            feature_row = extract_prematch_features(
                snapshot_match, owner_date=owner_date
            )
        vector = self._scaled(feature_row)
        draw_raw = _sigmoid(sum(
            weight * value
            for weight, value in zip(self.artifact["draw_weights"], vector)
        ))
        margin_scores = [
            sum(weight * value for weight, value in zip(row, vector))
            for row in self.artifact.get("margin_weights") or []
        ]
        margin_raw = _softmax(margin_scores)
        margin_probabilities = {
            int(margin): margin_raw[index]
            for index, margin in enumerate(self.artifact.get("margin_classes") or MARGIN_CLASSES)
        }

        market = feature_row["market"]
        sample_count = int(self.artifact.get("sample_count") or 0)
        reliability = min(0.65, sample_count / float(sample_count + 400))
        league = feature_row.get("league") or "未知"
        league_row = ((self.artifact.get("league_priors") or {}).get("leagues") or {}).get(league) or {}
        league_sample = int(league_row.get("sample") or 0)
        league_weight = min(0.18, league_sample / float(league_sample + 120) * 0.25)

        market_draw = _number(market.get("ordinary_draw_probability"))
        draw_base = market_draw if market_draw is not None else draw_raw
        draw_probability = draw_base + reliability * (draw_raw - draw_base)
        if league_row.get("draw_probability") is not None:
            draw_probability = (
                draw_probability * (1.0 - league_weight)
                + float(league_row["draw_probability"]) * league_weight
            )
        draw_probability_without_patterns = draw_probability
        draw_pattern_signal = _matched_pattern_signal(
            feature_row,
            (self.artifact.get("feature_patterns") or {}).get(
                "ordinary_draw"
            ),
        )
        draw_pattern_candidate, draw_pattern_adjustment = _apply_pattern_signal(
            draw_probability, draw_pattern_signal
        )
        pattern_activation = self.artifact.get(
            "feature_pattern_activation_guard"
        )
        draw_pattern_active = (
            True if pattern_activation is None else bool(
                (pattern_activation.get("ordinary_draw") or {}).get(
                    "active"
                )
            )
        )
        draw_probability = (
            draw_pattern_candidate
            if draw_pattern_active else draw_probability_without_patterns
        )

        handicap = _number(market.get("handicap"))
        target_margin = int(-handicap) if handicap is not None and float(-handicap).is_integer() else None
        exact_raw = margin_probabilities.get(target_margin) if target_margin is not None else None
        market_handicap_draw = _number(market.get("handicap_draw_probability"))
        if exact_raw is not None:
            exact_base = market_handicap_draw if market_handicap_draw is not None else exact_raw
            handicap_draw_probability = exact_base + reliability * (exact_raw - exact_base)
            league_margin = (league_row.get("margin_probabilities") or {}).get(str(target_margin))
            if league_margin is not None:
                handicap_draw_probability = (
                    handicap_draw_probability * (1.0 - league_weight)
                    + float(league_margin) * league_weight
                )
        else:
            handicap_draw_probability = market_handicap_draw
        handicap_probability_without_patterns = handicap_draw_probability
        handicap_pattern_signal = _matched_pattern_signal(
            feature_row,
            (self.artifact.get("feature_patterns") or {}).get(
                "handicap_draw"
            ),
        )
        (
            handicap_pattern_candidate,
            handicap_pattern_adjustment,
        ) = _apply_pattern_signal(
            handicap_draw_probability, handicap_pattern_signal
        )
        handicap_pattern_active = (
            True if pattern_activation is None else bool(
                (pattern_activation.get("handicap_draw") or {}).get(
                    "active"
                )
            )
        )
        handicap_draw_probability = (
            handicap_pattern_candidate
            if handicap_pattern_active
            else handicap_probability_without_patterns
        )

        if target_margin is None or target_margin == 0:
            favorite_win_probability = None
            conditional_exact = None
        elif target_margin > 0:
            favorite_win_probability = sum(
                probability for margin, probability in margin_probabilities.items() if margin > 0
            )
            conditional_exact = (
                exact_raw / favorite_win_probability
                if exact_raw is not None and favorite_win_probability > 0 else None
            )
        else:
            favorite_win_probability = sum(
                probability for margin, probability in margin_probabilities.items() if margin < 0
            )
            conditional_exact = (
                exact_raw / favorite_win_probability
                if exact_raw is not None and favorite_win_probability > 0 else None
            )

        pool_penalty_pp = max(0.0, math.log(max(1.0, daily_match_count / 8.0), 2.0)) * 0.75
        weekend_penalty_pp = 0.75 if feature_row["features"].get("weekend") and daily_match_count >= 12 else 0.0
        total_penalty_pp = pool_penalty_pp + weekend_penalty_pp

        model_outcomes = _result_probabilities_from_margins(
            margin_probabilities, handicap
        )
        league_margins = {
            int(margin): float(probability)
            for margin, probability in (
                league_row.get("margin_probabilities") or {}
            ).items()
        }
        league_outcomes = _result_probabilities_from_margins(
            league_margins, handicap
        ) if league_margins else {}
        meta_probabilities: Dict[str, float] = {}
        meta_weights = self.artifact.get("single_meta_weights") or []
        if meta_weights:
            for selection in SINGLE_SELECTIONS:
                raw_vector = _single_meta_vector(feature_row, selection)
                if raw_vector is None:
                    continue
                scaled_vector = _scale(
                    raw_vector,
                    self.artifact.get("single_meta_feature_means")
                    or [0.0] * len(SINGLE_META_FEATURE_NAMES),
                    self.artifact.get("single_meta_feature_scales")
                    or [1.0] * len(SINGLE_META_FEATURE_NAMES),
                )
                meta_probabilities[selection] = _sigmoid(sum(
                    weight * value
                    for weight, value in zip(meta_weights, scaled_vector)
                ))
            for market_selections in (
                ("主胜", "平局", "客胜"),
                ("让胜", "让平", "让负"),
            ):
                total = sum(
                    meta_probabilities.get(selection, 0.0)
                    for selection in market_selections
                )
                if total > 0:
                    for selection in market_selections:
                        if selection in meta_probabilities:
                            meta_probabilities[selection] /= total
        single_candidates = []
        for selection in SINGLE_SELECTIONS:
            margin_probability = _number(model_outcomes.get(selection))
            raw_probability = _number(meta_probabilities.get(selection))
            if raw_probability is None:
                raw_probability = margin_probability
            if raw_probability is None:
                continue
            league_probability = _number(league_outcomes.get(selection))
            if league_probability is not None and not meta_probabilities:
                raw_probability = (
                    raw_probability * (1.0 - league_weight)
                    + league_probability * league_weight
                )
            market_probability, odds, market_name = _single_market_terms(
                market, selection
            )
            probability = (
                market_probability
                + reliability * (raw_probability - market_probability)
                if market_probability is not None else raw_probability
            )
            probability = max(0.01, min(0.99, probability))
            ranking_probability = max(
                0.01, probability - total_penalty_pp / 100.0
            )
            single_candidates.append({
                "selection": selection,
                "market": market_name,
                "odds": round(odds, 3) if odds is not None else None,
                "probability": round(probability * 100, 2),
                "ranking_probability": round(
                    ranking_probability * 100, 2
                ),
                "model_probability": round(raw_probability * 100, 2),
                "goal_margin_probability": (
                    round(margin_probability * 100, 2)
                    if margin_probability is not None else None
                ),
                "market_probability": (
                    round(market_probability * 100, 2)
                    if market_probability is not None else None
                ),
                "market_edge_pp": (
                    round((probability - market_probability) * 100, 2)
                    if market_probability is not None else None
                ),
                "value_edge": (
                    round((probability * odds - 1.0) * 100, 2)
                    if odds is not None and odds > 1 else None
                ),
            })

        for candidate in single_candidates:
            market_rows = [
                row for row in single_candidates
                if row["market"] == candidate["market"]
            ]
            model_order = sorted(
                market_rows,
                key=lambda row: float(row.get("probability") or 0),
                reverse=True,
            )
            market_order = sorted(
                market_rows,
                key=lambda row: float(
                    row.get("market_probability") or 0
                ),
                reverse=True,
            )
            candidate["model_market_rank"] = (
                model_order.index(candidate) + 1
            )
            candidate["market_rank"] = market_order.index(candidate) + 1
            other_model_probabilities = [
                float(row.get("probability") or 0)
                for row in model_order if row is not candidate
            ]
            candidate["model_market_gap_pp"] = round(
                float(candidate.get("probability") or 0)
                - max(other_model_probabilities or [0.0]),
                2,
            )
            candidate["market_direction_agreement"] = bool(
                candidate["model_market_rank"] == 1
                and candidate["market_rank"] == 1
            )

        priced_candidates = [
            candidate for candidate in single_candidates
            if (
                _number(candidate.get("odds")) is not None
                and SINGLE_MIN_ODDS
                <= float(candidate["odds"])
                <= SINGLE_MAX_ODDS
                and candidate.get("market_direction_agreement")
            )
        ]
        priced_candidates.sort(
            key=lambda candidate: (
                float(candidate.get("ranking_probability") or 0),
                float(candidate.get("model_market_gap_pp") or 0),
                float(candidate.get("value_edge") or -999),
            ),
            reverse=True,
        )
        best_single = priced_candidates[0] if priced_candidates else None
        single_policy = dict(
            self.artifact.get("high_confidence_single_policy") or {}
        )
        minimum_probability = float(
            single_policy.get("minimum_probability")
            if single_policy.get("minimum_probability") is not None
            else 58.0
        )
        minimum_gap = float(
            single_policy.get("minimum_gap_pp")
            if single_policy.get("minimum_gap_pp") is not None else 6.0
        )
        single_reasons = []
        if not best_single:
            single_reasons.append("没有赔率与市场方向同时合格的单选")
        else:
            if float(best_single.get("probability") or 0) < minimum_probability:
                single_reasons.append(
                    f"模型概率低于{minimum_probability:g}%"
                )
            if float(best_single.get("model_market_gap_pp") or 0) < minimum_gap:
                single_reasons.append(
                    f"同市场领先优势低于{minimum_gap:g}个百分点"
                )
            if not feature_row.get("quality", {}).get("complete"):
                single_reasons.append("欧赔、亚盘、竞彩让球或大小球数据不完整")
        policy_active = bool(single_policy.get("active"))
        qualified = bool(best_single and not single_reasons)
        high_confidence_single = {
            "selection": (
                best_single.get("selection") if best_single else None
            ),
            "market": best_single.get("market") if best_single else None,
            "odds": best_single.get("odds") if best_single else None,
            "probability": (
                best_single.get("probability") if best_single else None
            ),
            "ranking_probability": (
                best_single.get("ranking_probability")
                if best_single else None
            ),
            "model_probability": (
                best_single.get("model_probability") if best_single else None
            ),
            "market_probability": (
                best_single.get("market_probability") if best_single else None
            ),
            "value_edge": (
                best_single.get("value_edge") if best_single else None
            ),
            "model_market_gap_pp": (
                best_single.get("model_market_gap_pp")
                if best_single else None
            ),
            "market_direction_agreement": bool(
                best_single
                and best_single.get("market_direction_agreement")
            ),
            "qualified_before_daily_limit": qualified,
            "actionable_before_daily_limit": qualified and policy_active,
            "policy_active": policy_active,
            "policy_status": single_policy.get("status") or "shadow_only",
            "minimum_probability": minimum_probability,
            "minimum_gap_pp": minimum_gap,
            "minimum_odds": float(
                single_policy.get("minimum_odds") or SINGLE_MIN_ODDS
            ),
            "maximum_odds": float(
                single_policy.get("maximum_odds") or SINGLE_MAX_ODDS
            ),
            "reason": (
                "通过监督概率、同市场领先、赔率区间和四市场完整性门槛"
                if qualified else "；".join(single_reasons)
            ),
            "candidates": single_candidates,
        }

        def output_probability(
            probability: Optional[float],
            market_probability: Optional[float],
            odds: Optional[float],
            *,
            probability_without_patterns: Optional[float],
            pattern_signal: Dict[str, Any],
            pattern_adjustment_pp: float,
            pattern_active: bool,
            pattern_candidate_probability: Optional[float],
        ) -> Dict[str, Any]:
            if probability is None:
                return {
                    "probability": None,
                    "ranking_probability": None,
                    "probability_without_patterns": None,
                    "ranking_probability_without_patterns": None,
                    "market_probability": round(market_probability * 100, 2) if market_probability is not None else None,
                    "value_edge": None,
                    "feature_pattern_count": 0,
                    "feature_pattern_active": False,
                    "matched_feature_patterns": [],
                }
            probability = max(0.01, min(0.99, probability))
            ranking = max(0.01, probability - total_penalty_pp / 100.0)
            baseline = (
                max(0.01, min(0.99, probability_without_patterns))
                if probability_without_patterns is not None else probability
            )
            baseline_ranking = max(
                0.01, baseline - total_penalty_pp / 100.0
            )
            return {
                "probability": round(probability * 100, 2),
                "ranking_probability": round(ranking * 100, 2),
                "probability_without_patterns": round(baseline * 100, 2),
                "ranking_probability_without_patterns": round(
                    baseline_ranking * 100, 2
                ),
                "market_probability": round(market_probability * 100, 2) if market_probability is not None else None,
                "value_edge": round((probability * odds - 1.0) * 100, 2) if odds and odds > 1 else None,
                "candidate_pool_penalty_pp": round(total_penalty_pp, 2),
                "feature_pattern_count": int(
                    pattern_signal.get("matched_count") or 0
                ),
                "feature_pattern_active": bool(pattern_active),
                "feature_pattern_probability": round(
                    float(pattern_signal["probability"]) * 100, 2
                ) if pattern_signal.get("probability") is not None else None,
                "feature_pattern_candidate_probability": round(
                    float(pattern_candidate_probability) * 100, 2
                ) if pattern_candidate_probability is not None else None,
                "feature_pattern_blend_weight": round(
                    float(pattern_signal.get("blend_weight") or 0), 4
                ),
                "feature_pattern_adjustment_pp": round(
                    pattern_adjustment_pp if pattern_active else 0.0, 2
                ),
                "feature_pattern_candidate_adjustment_pp": round(
                    pattern_adjustment_pp, 2
                ),
                "matched_feature_patterns": pattern_signal.get(
                    "patterns"
                ) or [],
            }

        return {
            "schema_version": SUPERVISED_SCHEMA_VERSION,
            "model_version": self.artifact.get("model_version"),
            "model_id": self.artifact.get("model_id"),
            "status": "shadow",
            "sample_count": sample_count,
            "training_end_date": self.artifact.get("training_end_date"),
            "ordinary_draw": output_probability(
                draw_probability,
                market_draw,
                _number(market.get("ordinary_draw_odds")),
                probability_without_patterns=(
                    draw_probability_without_patterns
                ),
                pattern_signal=draw_pattern_signal,
                pattern_adjustment_pp=draw_pattern_adjustment,
                pattern_active=draw_pattern_active,
                pattern_candidate_probability=draw_pattern_candidate,
            ),
            "handicap_draw": {
                **output_probability(
                    handicap_draw_probability,
                    market_handicap_draw,
                    _number(market.get("handicap_draw_odds")),
                    probability_without_patterns=(
                        handicap_probability_without_patterns
                    ),
                    pattern_signal=handicap_pattern_signal,
                    pattern_adjustment_pp=handicap_pattern_adjustment,
                    pattern_active=handicap_pattern_active,
                    pattern_candidate_probability=handicap_pattern_candidate,
                ),
                "target_goal_margin": target_margin,
                "favorite_win_probability": round(favorite_win_probability * 100, 2) if favorite_win_probability is not None else None,
                "conditional_exact_margin_probability": round(conditional_exact * 100, 2) if conditional_exact is not None else None,
            },
            "high_confidence_single": high_confidence_single,
            "goal_margin_distribution": {
                str(margin): round(probability * 100, 2)
                for margin, probability in margin_probabilities.items()
            },
            "quality": feature_row["quality"],
            "governance": "影子模型只用于样本外验证；发布门禁通过前不得覆盖正式推荐。",
        }


def _metric(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not events:
        return {
            "settled": 0, "hits": 0, "hit_rate": 0, "brier": 0,
            "market_comparison_samples": 0, "comparison_brier": 0,
            "market_brier": 0, "roi_settled": 0, "roi": 0, "ece": 0,
        }
    hits = sum(bool(row["label"]) for row in events)
    brier = sum((row["probability"] - row["label"]) ** 2 for row in events) / len(events)
    market_rows = [row for row in events if row.get("market_probability") is not None]
    market_brier = (
        sum((row["market_probability"] - row["label"]) ** 2 for row in market_rows) / len(market_rows)
        if market_rows else 0.0
    )
    comparison_brier = (
        sum((row["probability"] - row["label"]) ** 2 for row in market_rows)
        / len(market_rows)
        if market_rows else 0.0
    )
    financial = [
        row for row in events
        if _number(row.get("odds")) is not None
        and float(row["odds"]) > 1
    ]
    returns = sum(float(row["odds"]) for row in financial if row["label"])
    bins = defaultdict(list)
    for row in events:
        bins[min(9, int(row["probability"] * 10))].append(row)
    ece = sum(
        len(rows) / len(events) * abs(
            sum(row["probability"] for row in rows) / len(rows)
            - sum(row["label"] for row in rows) / len(rows)
        )
        for rows in bins.values()
    )
    return {
        "settled": len(events),
        "hits": hits,
        "hit_rate": round(hits / len(events) * 100, 1),
        "brier": round(brier, 4),
        "market_comparison_samples": len(market_rows),
        "comparison_brier": round(comparison_brier, 4),
        "market_brier": round(market_brier, 4),
        "brier_improvement": round(
            market_brier - comparison_brier, 4
        ),
        "roi_settled": len(financial),
        "roi": round(
            (returns - len(financial)) / len(financial) * 100, 1
        ) if financial else 0,
        "ece": round(ece * 100, 2),
    }


def _top_k_metric(events: List[Dict[str, Any]], k: int = 3) -> Dict[str, Any]:
    grouped = defaultdict(list)
    for row in events:
        grouped[str(row.get("owner_date") or "")].append(row)
    selected = []
    for rows in grouped.values():
        selected.extend(sorted(
            [
                row for row in rows
                if _number(row.get("odds")) is not None
                and float(row["odds"]) > 1
            ],
            key=lambda row: (
                float(row.get("ranking_probability") or 0),
                float(row.get("probability") or 0),
            ),
            reverse=True,
        )[:max(1, int(k))])
    result = _metric(selected)
    result["days"] = len(grouped)
    result["per_day"] = max(1, int(k))
    return result


def _baseline_pattern_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = []
    for row in events:
        item = dict(row)
        item["probability"] = float(
            row.get("probability_without_patterns")
            if row.get("probability_without_patterns") is not None
            else row.get("probability") or 0
        )
        item["ranking_probability"] = float(
            row.get("ranking_probability_without_patterns")
            if row.get("ranking_probability_without_patterns") is not None
            else row.get("ranking_probability") or 0
        )
        result.append(item)
    return result


def _feature_pattern_comparison(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    baseline_events = _baseline_pattern_events(events)
    baseline_all = _metric(baseline_events)
    candidate_all = _metric(events)
    baseline_top3 = _top_k_metric(baseline_events, 3)
    candidate_top3 = _top_k_metric(events, 3)
    matched = sum(
        int(row.get("feature_pattern_count") or 0) > 0 for row in events
    )
    return {
        "baseline_without_patterns": {
            "all": baseline_all,
            "top3": baseline_top3,
        },
        "candidate_with_patterns": {
            "all": candidate_all,
            "top3": candidate_top3,
        },
        "delta": {
            "brier_improvement": round(
                baseline_all.get("brier", 0)
                - candidate_all.get("brier", 0), 4
            ),
            "top3_hit_rate_pp": round(
                candidate_top3.get("hit_rate", 0)
                - baseline_top3.get("hit_rate", 0), 1
            ),
            "top3_roi_pp": round(
                candidate_top3.get("roi", 0)
                - baseline_top3.get("roi", 0), 1
            ),
        },
        "matched_events": matched,
        "coverage_rate": round(
            matched / len(events) * 100, 1
        ) if events else 0,
    }


def _feature_pattern_activation_guard(
    package: Dict[str, Any], comparison: Dict[str, Any]
) -> Dict[str, Any]:
    patterns = package.get("patterns") or []
    if not patterns:
        return {
            "active": False,
            "status": "no_stable_patterns",
            "reasons": ["没有通过时间验证的组合特征"],
        }
    delta = comparison.get("delta") or {}
    reasons = []
    if int(comparison.get("matched_events") or 0) < 30:
        reasons.append("样本外匹配少于30场")
    if float(delta.get("brier_improvement") or 0) <= 0:
        reasons.append("样本外概率误差未优于无组合基线")
    if float(delta.get("top3_hit_rate_pp") or 0) < 0:
        reasons.append("样本外Top3命中率低于无组合基线")
    if float(delta.get("top3_roi_pp") or 0) < 0:
        reasons.append("样本外Top3收益率低于无组合基线")
    return {
        "active": not reasons,
        "status": "shadow_active" if not reasons else "shadow_blocked",
        "matched_events": int(comparison.get("matched_events") or 0),
        "coverage_rate": comparison.get("coverage_rate"),
        "delta": delta,
        "reasons": reasons or ["通过无组合基线样本外对照"],
    }


def _combo_metric(
    draw_events: List[Dict[str, Any]],
    handicap_events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    draw_days = defaultdict(list)
    handicap_days = defaultdict(list)
    for row in draw_events:
        draw_days[str(row.get("owner_date") or "")].append(row)
    for row in handicap_events:
        handicap_days[str(row.get("owner_date") or "")].append(row)
    settled = 0
    returning = 0
    all_hit = 0
    total_stake = 0.0
    total_return = 0.0
    rows = []
    for owner_date in sorted(set(draw_days) & set(handicap_days)):
        draws = sorted(
            draw_days[owner_date],
            key=lambda row: float(row.get("ranking_probability") or 0),
            reverse=True,
        )
        lets = sorted(
            handicap_days[owner_date],
            key=lambda row: float(row.get("ranking_probability") or 0),
            reverse=True,
        )
        if len(draws) < 2:
            continue
        picks = draws[:2]
        used = {str(row.get("match_id") or "") for row in picks}
        let_pick = next(
            (row for row in lets if str(row.get("match_id") or "") not in used),
            None,
        )
        if not let_pick or any(not row.get("odds") for row in [*picks, let_pick]):
            continue
        picks = [*picks, let_pick]
        hit_odds = [float(row["odds"]) for row in picks if row["label"]]
        payout = 0.0
        for left in range(len(hit_odds)):
            for right in range(left + 1, len(hit_odds)):
                payout += hit_odds[left] * hit_odds[right]
        if len(hit_odds) == 3:
            payout += hit_odds[0] * hit_odds[1] * hit_odds[2]
        settled += 1
        total_stake += 4.0
        total_return += payout
        returning += int(len(hit_odds) >= 2)
        all_hit += int(len(hit_odds) == 3)
        rows.append({
            "owner_date": owner_date,
            "hit_count": len(hit_odds),
            "return_units": round(payout, 3),
            "picks": [
                {
                    "match_id": row.get("match_id"),
                    "selection": "平局" if index < 2 else "让平",
                    "odds": row.get("odds"),
                    "hit": bool(row.get("label")),
                }
                for index, row in enumerate(picks)
            ],
        })
    return {
        "structure": "2平+1让平",
        "play": "3场2、3关",
        "settled_days": settled,
        "returning_days": returning,
        "returning_rate": round(returning / settled * 100, 1) if settled else 0,
        "all_hit_days": all_hit,
        "all_hit_rate": round(all_hit / settled * 100, 1) if settled else 0,
        "stake_units": round(total_stake, 2),
        "return_units": round(total_return, 2),
        "roi": round((total_return / total_stake - 1) * 100, 1) if total_stake else 0,
        "days": rows,
    }


def _single_event_hit(
    example: Dict[str, Any], selection: Optional[str]
) -> bool:
    label = example.get("label") or {}
    if selection in {"主胜", "平局", "客胜"}:
        return selection == label.get("ordinary_selection")
    if selection in {"让胜", "让平", "让负"}:
        return selection == label.get("handicap_selection")
    return False


def _select_high_confidence_single_events(
    events: Iterable[Dict[str, Any]],
    *,
    minimum_probability: float,
    minimum_gap_pp: float,
    daily_limit: int = SINGLE_DAILY_LIMIT,
) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        odds = _number(event.get("odds"))
        if (
            odds is None
            or odds < SINGLE_MIN_ODDS
            or odds > SINGLE_MAX_ODDS
            or int(event.get("market_rank") or 99) != 1
            or not event.get("quality_complete")
            or not event.get("ai_verified")
            or not event.get("official_actionable")
            or str(event.get("official_selection") or "")
            != str(event.get("selection") or "")
            or float(event.get("probability") or 0) * 100
            < minimum_probability
            or float(event.get("model_market_gap_pp") or 0)
            < minimum_gap_pp
        ):
            continue
        grouped[str(event.get("owner_date") or "")].append(event)
    selected = []
    for rows in grouped.values():
        selected.extend(sorted(
            rows,
            key=lambda row: (
                float(row.get("ranking_probability") or 0),
                float(row.get("model_market_gap_pp") or 0),
                float(row.get("value_edge") or -999),
            ),
            reverse=True,
        )[:max(1, int(daily_limit))])
    return selected


def _wilson_lower(hits: int, sample: int, z: float = 1.96) -> float:
    if sample <= 0:
        return 0.0
    probability = hits / sample
    denominator = 1 + z * z / sample
    center = probability + z * z / (2 * sample)
    margin = z * math.sqrt(
        probability * (1 - probability) / sample
        + z * z / (4 * sample * sample)
    )
    return max(0.0, (center - margin) / denominator)


def _single_policy_report(
    events: List[Dict[str, Any]], tested_dates: Sequence[str]
) -> Dict[str, Any]:
    """Choose thresholds on early OOS days, validate on later days only."""
    dates = sorted({str(value)[:10] for value in tested_dates if value})
    split_index = max(1, min(len(dates) - 1, int(len(dates) * 0.70)))
    discovery_dates = set(dates[:split_index])
    validation_dates = set(dates[split_index:])
    discovery_events = [
        row for row in events
        if str(row.get("owner_date") or "")[:10] in discovery_dates
    ]
    validation_events = [
        row for row in events
        if str(row.get("owner_date") or "")[:10] in validation_dates
    ]
    candidates = []
    for minimum_probability in SINGLE_POLICY_PROBABILITIES:
        for minimum_gap in SINGLE_POLICY_GAPS:
            selected = _select_high_confidence_single_events(
                discovery_events,
                minimum_probability=minimum_probability,
                minimum_gap_pp=minimum_gap,
            )
            metric = _metric(selected)
            hits = int(metric.get("hits") or 0)
            sample = int(metric.get("settled") or 0)
            candidates.append({
                "minimum_probability": minimum_probability,
                "minimum_gap_pp": minimum_gap,
                "metric": metric,
                "confidence_lower": round(
                    _wilson_lower(hits, sample) * 100, 2
                ),
            })
    supported = [
        row for row in candidates
        if int((row.get("metric") or {}).get("settled") or 0) >= 30
    ]
    chosen = max(
        supported or candidates,
        key=lambda row: (
            float(row.get("confidence_lower") or 0),
            float((row.get("metric") or {}).get("roi") or -999),
            float((row.get("metric") or {}).get("hit_rate") or 0),
            int((row.get("metric") or {}).get("settled") or 0),
        ),
        default={
            "minimum_probability": 58.0,
            "minimum_gap_pp": 6.0,
            "metric": _metric([]),
            "confidence_lower": 0.0,
        },
    )
    validation_selected = _select_high_confidence_single_events(
        validation_events,
        minimum_probability=float(chosen["minimum_probability"]),
        minimum_gap_pp=float(chosen["minimum_gap_pp"]),
    )
    all_selected = _select_high_confidence_single_events(
        events,
        minimum_probability=float(chosen["minimum_probability"]),
        minimum_gap_pp=float(chosen["minimum_gap_pp"]),
    )
    validation_metric = _metric(validation_selected)
    all_metric = _metric(all_selected)
    validation_lower = _wilson_lower(
        int(validation_metric.get("hits") or 0),
        int(validation_metric.get("settled") or 0),
    ) * 100
    reasons = []
    if len(dates) < 30:
        reasons.append(f"滚动样本外仅{len(dates)}个比赛日，少于30日")
    if int((chosen.get("metric") or {}).get("settled") or 0) < 30:
        reasons.append("阈值发现集可投注样本少于30场")
    if float((chosen.get("metric") or {}).get("hit_rate") or 0) < 55:
        reasons.append("阈值发现集命中率低于55%")
    if float((chosen.get("metric") or {}).get("roi") or 0) < 0:
        reasons.append("阈值发现集ROI为负")
    if int(validation_metric.get("settled") or 0) < 20:
        reasons.append("独立验证集可投注样本少于20场")
    if float(validation_metric.get("hit_rate") or 0) < 60:
        reasons.append("独立验证集命中率低于60%")
    if float(validation_metric.get("roi") or 0) < 0:
        reasons.append("独立验证集ROI为负")
    if validation_lower < 45:
        reasons.append("独立验证集95%置信下限低于45%")
    if int(all_metric.get("settled") or 0) < 50:
        reasons.append("全部滚动样本外可投注样本少于50场")
    if float(all_metric.get("hit_rate") or 0) < 55:
        reasons.append("全部滚动样本外命中率低于55%")
    if float(all_metric.get("roi") or 0) < 0:
        reasons.append("全部滚动样本外ROI为负")
    active = not reasons
    policy = {
        "version": "high-confidence-single-v1",
        "status": "active" if active else "shadow_only",
        "active": active,
        "minimum_probability": chosen["minimum_probability"],
        "minimum_gap_pp": chosen["minimum_gap_pp"],
        "minimum_odds": SINGLE_MIN_ODDS,
        "maximum_odds": SINGLE_MAX_ODDS,
        "daily_limit": SINGLE_DAILY_LIMIT,
        "requires_market_favorite": True,
        "requires_ark_single_alignment": True,
        "requires_existing_official_gate": True,
        "requires_complete_four_markets": True,
        "selection_method": (
            "前70%滚动样本外日期选阈值，后30%日期独立验证"
        ),
        "reasons": reasons or [
            "通过跨日样本、60%命中率、非负ROI和置信下限门禁"
        ],
    }
    return {
        "policy": policy,
        "tested_days": len(dates),
        "discovery_dates": sorted(discovery_dates),
        "validation_dates": sorted(validation_dates),
        "discovery": chosen.get("metric") or {},
        "discovery_confidence_lower": chosen.get("confidence_lower"),
        "validation": validation_metric,
        "validation_confidence_lower": round(validation_lower, 2),
        "all_out_of_sample": all_metric,
        "candidate_policy_count": len(candidates),
        "evaluated_policies": sorted(
            candidates,
            key=lambda row: (
                float(row.get("confidence_lower") or 0),
                float((row.get("metric") or {}).get("roi") or -999),
            ),
            reverse=True,
        ),
    }


class FAESupervisedBacktestEngine:
    """Expanding-window out-of-sample evaluation and release governance."""

    def __init__(
        self,
        minimum_train_days: int = 7,
        minimum_train_samples: int = 40,
        retrain_interval_days: int = 7,
    ):
        self.minimum_train_days = minimum_train_days
        self.minimum_train_samples = minimum_train_samples
        self.retrain_interval_days = max(1, int(retrain_interval_days))
        self.trainer = FAESupervisedTrainer()

    @staticmethod
    def _release_guard(
        draw: Dict[str, Any],
        handicap_draw: Dict[str, Any],
        draw_top3: Dict[str, Any],
        handicap_top3: Dict[str, Any],
        days: int,
    ) -> Dict[str, Any]:
        reasons = []
        if days < 30:
            reasons.append(f"样本外仅覆盖{days}个比赛日，少于30日")
        for label, metric, ranking in (
            ("平局", draw, draw_top3),
            ("让平", handicap_draw, handicap_top3),
        ):
            if metric.get("settled", 0) < 100:
                reasons.append(f"{label}样本{metric.get('settled', 0)}场，少于100场")
            if metric.get("market_comparison_samples", 0) < 100:
                reasons.append(
                    f"{label}市场对照样本"
                    f"{metric.get('market_comparison_samples', 0)}场，少于100场"
                )
            if metric.get("comparison_brier", 1) >= metric.get("market_brier", 0):
                reasons.append(f"{label}概率误差尚未优于市场基线")
            if metric.get("ece", 100) > 5:
                reasons.append(f"{label}校准误差{metric.get('ece')}%高于5%")
            if ranking.get("roi_settled", 0) < 60:
                reasons.append(
                    f"{label}Top3可投注样本"
                    f"{ranking.get('roi_settled', 0)}场，少于60场"
                )
            if ranking.get("roi", -100) < 0:
                reasons.append(f"{label}Top3样本外ROI仍为负")
        return {
            "status": "eligible" if not reasons else "shadow_only",
            "can_promote": not reasons,
            "minimum_days": 30,
            "minimum_samples_per_market": 100,
            "reasons": reasons or ["平局与让平均通过样本、校准、市场基线和ROI门禁"],
            "automatic_promotion": False,
        }

    def build(self, training_days: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        days = sorted(list(training_days), key=lambda row: row.get("owner_date") or "")
        history = []
        draw_events = []
        handicap_events = []
        single_events = []
        tested_dates = []
        training_cutoffs = []
        predictor = None
        last_trained_day_index = None
        for day_index, day in enumerate(days):
            current = list(day.get("examples") or [])
            if (
                day_index < self.minimum_train_days
                or len(history) < self.minimum_train_samples
            ):
                history.extend(current)
                continue
            if (
                predictor is None
                or last_trained_day_index is None
                or day_index - last_trained_day_index >= self.retrain_interval_days
            ):
                artifact = self.trainer.fit(history, fast=True)
                predictor = FAESupervisedPredictor(artifact)
                last_trained_day_index = day_index
            training_cutoff = max(
                str(row.get("owner_date") or "")[:10]
                for row in history
            )
            test_date = str(day.get("owner_date") or "")[:10]
            if training_cutoff >= test_date:
                raise ValueError(
                    "滚动回测检测到未来数据泄漏: "
                    f"训练截止{training_cutoff}，测试日{test_date}"
                )
            training_cutoffs.append({
                "test_date": test_date,
                "training_end_date": training_cutoff,
                "training_samples": len(history),
            })
            daily_match_count = len(current)
            for example in current:
                prediction = predictor.predict(
                    example,
                    owner_date=day.get("owner_date"),
                    daily_match_count=daily_match_count,
                )
                draw = prediction["ordinary_draw"]
                draw_market_probability = draw.get("market_probability")
                draw_events.append({
                    "owner_date": day.get("owner_date"),
                    "match_id": example.get("match_id"),
                    "league": example.get("league"),
                    "probability": (draw.get("probability") or 0) / 100.0,
                    "probability_without_patterns": (
                        draw.get("probability_without_patterns") or 0
                    ) / 100.0,
                    "market_probability": (
                        float(draw_market_probability) / 100.0
                        if draw_market_probability is not None else None
                    ),
                    "ranking_probability": (draw.get("ranking_probability") or 0) / 100.0,
                    "ranking_probability_without_patterns": (
                        draw.get("ranking_probability_without_patterns") or 0
                    ) / 100.0,
                    "feature_pattern_count": int(
                        draw.get("feature_pattern_count") or 0
                    ),
                    "odds": example["market"].get("ordinary_draw_odds"),
                    "label": bool(example["label"]["ordinary_draw"]),
                    "weekend": bool(example["features"].get("weekend")),
                })
                handicap = prediction["handicap_draw"]
                if example["market"].get("handicap") is not None:
                    handicap_market_probability = handicap.get(
                        "market_probability"
                    )
                    handicap_events.append({
                        "owner_date": day.get("owner_date"),
                        "match_id": example.get("match_id"),
                        "league": example.get("league"),
                        "probability": (handicap.get("probability") or 0) / 100.0,
                        "probability_without_patterns": (
                            handicap.get("probability_without_patterns") or 0
                        ) / 100.0,
                        "market_probability": (
                            float(handicap_market_probability) / 100.0
                            if handicap_market_probability is not None
                            else None
                        ),
                        "ranking_probability": (handicap.get("ranking_probability") or 0) / 100.0,
                        "ranking_probability_without_patterns": (
                            handicap.get(
                                "ranking_probability_without_patterns"
                            ) or 0
                        ) / 100.0,
                        "feature_pattern_count": int(
                            handicap.get("feature_pattern_count") or 0
                        ),
                        "odds": example["market"].get("handicap_draw_odds"),
                        "label": bool(example["label"]["handicap_draw"]),
                        "weekend": bool(example["features"].get("weekend")),
                    })
                single_profile = (
                    prediction.get("high_confidence_single") or {}
                )
                official_selection = str(
                    (example.get("prematch_ai") or {}).get(
                        "official_selection"
                    ) or ""
                )
                single = next((
                    candidate
                    for candidate in single_profile.get("candidates") or []
                    if candidate.get("selection") == official_selection
                ), {})
                selection = single.get("selection")
                if selection in SINGLE_SELECTIONS:
                    single_events.append({
                        "owner_date": day.get("owner_date"),
                        "match_id": example.get("match_id"),
                        "league": example.get("league"),
                        "selection": selection,
                        "market": single.get("market"),
                        "probability": (
                            float(single.get("probability") or 0) / 100.0
                        ),
                        "ranking_probability": (
                            float(
                                single.get("ranking_probability") or 0
                            ) / 100.0
                        ),
                        "market_probability": (
                            float(single.get("market_probability")) / 100.0
                            if single.get("market_probability") is not None
                            else None
                        ),
                        "model_market_gap_pp": float(
                            single.get("model_market_gap_pp") or 0
                        ),
                        "market_direction_agreement": bool(
                            single.get("market_direction_agreement")
                        ),
                        "market_rank": int(
                            single.get("market_rank") or 99
                        ),
                        "quality_complete": bool(
                            prediction.get("quality", {}).get("complete")
                        ),
                        "ai_selection": str(
                            (example.get("prematch_ai") or {}).get(
                                "selection"
                            ) or ""
                        ),
                        "ai_verified": bool(
                            (example.get("prematch_ai") or {}).get(
                                "ai_verified"
                            )
                        ),
                        "official_actionable": bool(
                            (example.get("prematch_ai") or {}).get(
                                "official_actionable"
                            )
                        ),
                        "official_selection": official_selection,
                        "value_edge": single.get("value_edge"),
                        "odds": single.get("odds"),
                        "label": _single_event_hit(example, selection),
                        "weekend": bool(
                            example["features"].get("weekend")
                        ),
                    })
            tested_dates.append(day.get("owner_date"))
            history.extend(current)
        if len(history) < 20:
            raise ValueError("可训练的不可变赛前快照不足20场")
        final_artifact = self.trainer.fit(history)
        draw_metric = _metric(draw_events)
        handicap_metric = _metric(handicap_events)
        draw_top3 = _top_k_metric(draw_events, 3)
        handicap_top3 = _top_k_metric(handicap_events, 3)
        feature_pattern_comparison = {
            "ordinary_draw": _feature_pattern_comparison(draw_events),
            "handicap_draw": _feature_pattern_comparison(handicap_events),
        }
        feature_pattern_activation_guard = {
            key: _feature_pattern_activation_guard(
                (final_artifact.get("feature_patterns") or {}).get(key) or {},
                feature_pattern_comparison[key],
            )
            for key in ("ordinary_draw", "handicap_draw")
        }
        final_artifact["feature_pattern_activation_guard"] = (
            feature_pattern_activation_guard
        )
        high_confidence_single = _single_policy_report(
            single_events, tested_dates
        )
        final_artifact["high_confidence_single_policy"] = (
            high_confidence_single["policy"]
        )
        release_guard = self._release_guard(
            draw_metric,
            handicap_metric,
            draw_top3,
            handicap_top3,
            len(tested_dates),
        )
        pattern_reasons = []
        for key, label in (
            ("ordinary_draw", "平局"),
            ("handicap_draw", "让平"),
        ):
            package = (final_artifact.get("feature_patterns") or {}).get(
                key
            ) or {}
            if not (package.get("patterns") or []):
                continue
            comparison = feature_pattern_comparison[key]
            delta = comparison.get("delta") or {}
            if comparison.get("matched_events", 0) < 30:
                pattern_reasons.append(
                    f"{label}组合特征样本外仅匹配"
                    f"{comparison.get('matched_events', 0)}场，少于30场"
                )
            if float(delta.get("brier_improvement") or 0) < 0:
                pattern_reasons.append(
                    f"{label}组合特征使样本外概率误差变差"
                )
            if float(delta.get("top3_hit_rate_pp") or 0) < 0:
                pattern_reasons.append(
                    f"{label}组合特征使Top3命中率下降"
                )
        if pattern_reasons:
            existing_reasons = list(release_guard.get("reasons") or [])
            if release_guard.get("can_promote"):
                existing_reasons = []
            release_guard["reasons"] = list(dict.fromkeys(
                existing_reasons + pattern_reasons
            ))
            release_guard["status"] = "shadow_only"
            release_guard["can_promote"] = False
        report = {
            "schema_version": SUPERVISED_SCHEMA_VERSION,
            "model_version": SUPERVISED_MODEL_VERSION,
            "model_id": final_artifact["model_id"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_days": [day.get("owner_date") for day in days],
            "tested_dates": tested_dates,
            "training_cutoffs": training_cutoffs,
            "training_sample_count": len(history),
            "ordinary_draw": draw_metric,
            "handicap_draw": handicap_metric,
            "ranked_top3": {
                "ordinary_draw": draw_top3,
                "handicap_draw": handicap_top3,
            },
            "feature_pattern_shadow_comparison": (
                feature_pattern_comparison
            ),
            "feature_pattern_activation_guard": (
                feature_pattern_activation_guard
            ),
            "two_draw_one_handicap_combo": _combo_metric(
                draw_events, handicap_events
            ),
            "high_confidence_single": high_confidence_single,
            "weekend": {
                "ordinary_draw": _metric([row for row in draw_events if row["weekend"]]),
                "handicap_draw": _metric([row for row in handicap_events if row["weekend"]]),
                "ranked_top3": {
                    "ordinary_draw": _top_k_metric(
                        [row for row in draw_events if row["weekend"]], 3
                    ),
                    "handicap_draw": _top_k_metric(
                        [row for row in handicap_events if row["weekend"]], 3
                    ),
                },
            },
            "weekday": {
                "ordinary_draw": _metric([row for row in draw_events if not row["weekend"]]),
                "handicap_draw": _metric([row for row in handicap_events if not row["weekend"]]),
            },
            "release_guard": release_guard,
            "governance": {
                "split": "expanding-window-by-owner-date",
                "random_split": False,
                "immutable_prematch_only": True,
                "final_score_as_feature": False,
                "feature_pattern_discovery_split": "first-70-percent-days",
                "feature_pattern_validation_split": "last-30-percent-days",
            },
        }
        return {"model": final_artifact, "report": report}
