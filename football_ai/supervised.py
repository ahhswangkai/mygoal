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
import json
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .league_profile import _handicap_value


SUPERVISED_SCHEMA_VERSION = "1.0"
SUPERVISED_MODEL_VERSION = "draw-margin-supervised-v1"
MARGIN_CLASSES = (-3, -2, -1, 0, 1, 2, 3)

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
    if favorite_side == "home":
        initial_favorite_depth = initial_asian_line
        current_favorite_depth = current_asian_line
        favorite_initial_water = _at(asian_initial, 0)
        favorite_current_water = _at(asian_current, 0)
    elif favorite_side == "away":
        initial_favorite_depth = (
            -initial_asian_line if initial_asian_line is not None else None
        )
        current_favorite_depth = (
            -current_asian_line if current_asian_line is not None else None
        )
        favorite_initial_water = _at(asian_initial, 2)
        favorite_current_water = _at(asian_current, 2)

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
        "total_over_water": _safe(_at(total_current, 0), 1.0),
        "total_under_water": _safe(_at(total_current, 2), 1.0),
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
            "ordinary_draw_probability": euro_probs[1],
            "ordinary_draw_odds": _at(euro_current, 1),
            "handicap_draw_probability": hhad_probs[1],
            "handicap_draw_odds": _at(hhad_current, 1),
            "handicap": _number(hhad.get("value")),
        },
        "quality": {
            "missing_markets": missing,
            "complete": not missing,
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


def _standardizer(vectors: List[List[float]]) -> Tuple[List[float], List[float]]:
    if not vectors:
        return [0.0] * len(FEATURE_NAMES), [1.0] * len(FEATURE_NAMES)
    means = [sum(row[index] for row in vectors) / len(vectors) for index in range(len(FEATURE_NAMES))]
    scales = []
    for index, mean in enumerate(means):
        variance = sum((row[index] - mean) ** 2 for row in vectors) / len(vectors)
        scales.append(max(0.01, math.sqrt(variance)))
    return means, scales


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
    rows = [
        {"feature": name, "coefficient": round(float(weight), 6)}
        for name, weight in zip(FEATURE_NAMES, list(weights)[1:])
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
            "sample_count": len(rows),
            "training_days": len(dates),
            "training_start_date": dates[0] if dates else None,
            "training_end_date": dates[-1] if dates else None,
            "league_priors": _league_priors(rows),
            "feature_explanations": {
                "ordinary_draw": _weight_explanation(draw_weights),
                "goal_margin": {
                    str(margin): _weight_explanation(margin_weights[index])
                    for index, margin in enumerate(MARGIN_CLASSES)
                },
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
                "weekend_pool_correction": True,
                "may_override_official_recommendations": False,
            },
        }
        identity = sha256(json.dumps(
            {key: artifact[key] for key in (
                "model_version", "feature_names", "draw_weights",
                "margin_weights", "sample_count", "training_end_date",
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

        def output_probability(probability: Optional[float], market_probability: Optional[float], odds: Optional[float]) -> Dict[str, Any]:
            if probability is None:
                return {
                    "probability": None,
                    "ranking_probability": None,
                    "market_probability": round(market_probability * 100, 2) if market_probability is not None else None,
                    "value_edge": None,
                }
            probability = max(0.01, min(0.99, probability))
            ranking = max(0.01, probability - total_penalty_pp / 100.0)
            return {
                "probability": round(probability * 100, 2),
                "ranking_probability": round(ranking * 100, 2),
                "market_probability": round(market_probability * 100, 2) if market_probability is not None else None,
                "value_edge": round((probability * odds - 1.0) * 100, 2) if odds and odds > 1 else None,
                "candidate_pool_penalty_pp": round(total_penalty_pp, 2),
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
            ),
            "handicap_draw": {
                **output_probability(
                    handicap_draw_probability,
                    market_handicap_draw,
                    _number(market.get("handicap_draw_odds")),
                ),
                "target_goal_margin": target_margin,
                "favorite_win_probability": round(favorite_win_probability * 100, 2) if favorite_win_probability is not None else None,
                "conditional_exact_margin_probability": round(conditional_exact * 100, 2) if conditional_exact is not None else None,
            },
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
                    "market_probability": (
                        float(draw_market_probability) / 100.0
                        if draw_market_probability is not None else None
                    ),
                    "ranking_probability": (draw.get("ranking_probability") or 0) / 100.0,
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
                        "market_probability": (
                            float(handicap_market_probability) / 100.0
                            if handicap_market_probability is not None
                            else None
                        ),
                        "ranking_probability": (handicap.get("ranking_probability") or 0) / 100.0,
                        "odds": example["market"].get("handicap_draw_odds"),
                        "label": bool(example["label"]["handicap_draw"]),
                        "weekend": bool(example["features"].get("weekend")),
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
            "two_draw_one_handicap_combo": _combo_metric(
                draw_events, handicap_events
            ),
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
            "release_guard": self._release_guard(
                draw_metric,
                handicap_metric,
                draw_top3,
                handicap_top3,
                len(tested_dates),
            ),
            "governance": {
                "split": "expanding-window-by-owner-date",
                "random_split": False,
                "immutable_prematch_only": True,
                "final_score_as_feature": False,
            },
        }
        return {"model": final_artifact, "report": report}
