"""Compact, date-isolated memory distilled from Ark post-match reviews."""

from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any, Dict, Iterable, List


REVIEW_MEMORY_VERSION = "review-memory-v2-sample-governance"


def _text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _list(value: Any, limit: int, item_limit: int) -> List[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        _text(item, item_limit) for item in value
        if _text(item, item_limit)
    ))[:limit]


def _number(value: Any, fallback: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _candidate_key(candidate: Dict[str, Any]) -> tuple:
    scope = _text(candidate.get("scope"), 40).lower()
    action = _text(candidate.get("action"), 20).lower()
    target = re.sub(
        r"[\s，。；、,:：;]+", "",
        _text(candidate.get("target"), 160).lower(),
    )
    return scope, action, target


def build_review_memory(
    reviews: Iterable[Dict[str, Any]],
    before_date: str,
    *,
    window_days: int = 7,
    observation_days: int = 3,
    minimum_pattern_days: int = 2,
    minimum_evidence: int = 10,
) -> Dict[str, Any]:
    """Build non-binding observations plus cross-day validated patterns.

    Only reviews with an owner date strictly earlier than ``before_date`` are
    eligible. This keeps backtests and reruns free from future-result leakage.
    """
    target_date = str(before_date or "")[:10]
    eligible = []
    for review in reviews:
        owner_date = str(review.get("owner_date") or "")[:10]
        deep = review.get("ai_deep_review") or {}
        if (
            owner_date
            and owner_date < target_date
            and deep.get("status") == "completed"
        ):
            eligible.append(review)
    eligible.sort(
        key=lambda item: str(item.get("owner_date") or ""),
        reverse=True,
    )
    rows = eligible[:max(1, int(window_days or 7))]

    observations = []
    for review in rows[:max(1, int(observation_days or 3))]:
        deep = review.get("ai_deep_review") or {}
        summary = deep.get("summary") or {}
        settlement = (
            ((review.get("summary") or {}).get("singles") or {})
        )
        lessons = deep.get("market_lessons") or {}
        observations.append({
            "date": str(review.get("owner_date") or "")[:10],
            "status": "unvalidated-observation",
            "binding": False,
            "sample": {
                "settled": int(settlement.get("settled") or 0),
                "hit_rate": _number(settlement.get("hit_rate")),
                "roi": _number(settlement.get("roi")),
            },
            "conclusion": _text(summary.get("conclusion"), 360),
            "what_failed": _list(
                summary.get("what_failed"), 4, 180
            ),
            "risk_patterns": _list(
                summary.get("risk_patterns"), 4, 180
            ),
            "next_actions": _list(
                summary.get("next_actions"), 4, 180
            ),
            "market_lessons": {
                key: _text(lessons.get(key), 220)
                for key in (
                    "euro", "asian", "sporttery", "total", "consistency"
                )
                if _text(lessons.get(key), 220)
            },
        })

    grouped: Dict[tuple, Dict[str, Any]] = {}
    for review in rows:
        owner_date = str(review.get("owner_date") or "")[:10]
        deep = review.get("ai_deep_review") or {}
        for candidate in deep.get("learning_candidates") or []:
            if not isinstance(candidate, dict):
                continue
            key = _candidate_key(candidate)
            if not all(key):
                continue
            item = grouped.setdefault(key, {
                "scope": key[0],
                "action": key[1],
                "target": _text(candidate.get("target"), 160),
                "dates": set(),
                "evidence": set(),
                "reasons": [],
                "deltas": [],
                "minimum_samples": 10,
            })
            item["dates"].add(owner_date)
            item["evidence"].update(
                f"{owner_date}:{value}"
                for value in candidate.get("evidence_match_ids") or []
                if value not in (None, "")
            )
            reason = _text(candidate.get("reason"), 240)
            if reason and reason not in item["reasons"]:
                item["reasons"].append(reason)
            item["deltas"].append(_number(candidate.get("delta")))
            try:
                required = int(candidate.get("minimum_samples") or 10)
            except (TypeError, ValueError):
                required = 10
            item["minimum_samples"] = max(
                item["minimum_samples"], required
            )

    validated_patterns = []
    for item in grouped.values():
        required_evidence = max(
            int(minimum_evidence or 10),
            int(item["minimum_samples"]),
        )
        if (
            len(item["dates"]) < max(2, int(minimum_pattern_days or 2))
            or len(item["evidence"]) < required_evidence
        ):
            continue
        deltas = item["deltas"] or [0]
        validated_patterns.append({
            "scope": item["scope"],
            "target": item["target"],
            "action": item["action"],
            "suggested_delta": round(sum(deltas) / len(deltas), 3),
            "observed_days": len(item["dates"]),
            "evidence_matches": len(item["evidence"]),
            "source_dates": sorted(item["dates"]),
            "reason": "；".join(item["reasons"][:3])[:500],
            "status": "historically-validated-memory",
        })
    validated_patterns.sort(
        key=lambda item: (
            item["observed_days"], item["evidence_matches"]
        ),
        reverse=True,
    )

    source_dates = sorted({
        str(item.get("owner_date") or "")[:10] for item in rows
    })
    payload = {
        "version": REVIEW_MEMORY_VERSION,
        "before_date": target_date,
        "source_dates": source_dates,
        "review_days": len(rows),
        "observation_count": len(observations),
        "validated_pattern_count": len(validated_patterns),
        "recent_observations": observations,
        "validated_patterns": validated_patterns[:10],
        "governance": {
            "future_data_excluded": True,
            "observations_are_non_binding": True,
            "absolute_exclusions_forbidden": True,
            "unvalidated_rates_must_not_be_generalized": True,
            "minimum_pattern_days": max(
                2, int(minimum_pattern_days or 2)
            ),
            "minimum_evidence_matches": max(
                10, int(minimum_evidence or 10)
            ),
            "instruction": (
                "近期观察只用于检查同类风险，不得单独决定赛果；"
                "单日0%或100%命中率均不得外推为禁选或必选规则；"
                "已验证模式也必须让位于当天真实盘口和数据质量。"
            ),
        },
    }
    payload["memory_hash"] = sha256(json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")).hexdigest()
    return payload
