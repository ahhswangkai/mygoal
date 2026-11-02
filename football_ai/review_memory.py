"""Compact, date-isolated memory distilled from Ark post-match reviews."""

from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
import re
from typing import Any, Dict, Iterable, List


REVIEW_MEMORY_VERSION = "review-memory-v4-long-term"


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


def _historical_performance(rows, target_date, half_life_days):
    """Describe all historical outcomes without treating them as predictions."""
    paths = {
        "singles": ("singles",), "handicap": ("handicap",),
        "two_option": ("two_option", "overall"),
        "ordinary_draw_radar": ("draw_radar", "ordinary_draw"),
        "handicap_draw_radar": ("draw_radar", "handicap_draw"),
        "total_goals_primary": ("special_markets", "total_goals", "primary"),
        "half_full_primary": ("special_markets", "half_full", "primary"),
    }
    result = {}
    for name, path in paths.items():
        settled = hits = decided = stake = returns = 0.0
        weighted_decided = weighted_hits = weighted_stake = weighted_returns = 0.0
        for review in rows:
            metric = review.get("summary") or {}
            for key in path:
                metric = metric.get(key) or {}
            count = _number(metric.get("settled"))
            push = _number(metric.get("pushes"))
            wins = _number(metric.get("hits"))
            age = (date.fromisoformat(target_date) - date.fromisoformat(review["owner_date"][:10])).days
            weight = 0.5 ** (age / half_life_days)
            settled += count
            decided += max(0, count - push)
            hits += wins
            weighted_decided += max(0, count - push) * weight
            weighted_hits += wins * weight
            cost = _number(metric.get("equal_stake") if name == "two_option" else metric.get("stake"))
            payout = _number(metric.get("equal_stake_return") if name == "two_option" else metric.get("return"))
            stake += cost
            returns += payout
            weighted_stake += cost * weight
            weighted_returns += payout * weight
        if settled:
            result[name] = {
                "settled": int(settled), "hits": int(hits),
                "hit_rate": round(hits / decided * 100, 1) if decided else None,
                "roi": round((returns / stake - 1) * 100, 1) if stake else None,
                "effective_samples": round(weighted_decided, 1),
                "weighted_hit_rate": round(weighted_hits / weighted_decided * 100, 1) if weighted_decided else None,
                "weighted_roi": round((weighted_returns / weighted_stake - 1) * 100, 1) if weighted_stake else None,
            }
    return result


def _long_term_patterns(rows, target_date, half_life_days,
                        minimum_pattern_days, minimum_evidence, recent_dates):
    """Accumulate historical evidence; keep contradictory/stale memories visible."""
    grouped: Dict[tuple, Dict[str, Any]] = {}
    if not rows:
        return [], [], []
    cutoff = date.fromisoformat(target_date)
    for review in rows:
        owner_date = str(review.get("owner_date") or "")[:10]
        age = max(0, (cutoff - date.fromisoformat(owner_date)).days)
        weight = 0.5 ** (age / half_life_days)
        known_ids = {
            str(row.get("match_id")) for row in review.get("match_results") or []
            if row.get("match_id")
        }
        for candidate in (review.get("ai_deep_review") or {}).get("learning_candidates") or []:
            if not isinstance(candidate, dict):
                continue
            key = _candidate_key(candidate)
            if not all(key) or key[1] not in {"increase", "decrease"}:
                continue
            evidence = {
                str(value) for value in candidate.get("evidence_match_ids") or []
                if value not in (None, "")
                and ("match_results" not in review or str(value) in known_ids)
            }
            if not evidence:
                continue
            item = grouped.setdefault(key, {
                "scope": key[0], "action": key[1],
                "target": _text(candidate.get("target"), 160),
                "dates": set(), "evidence": {}, "reasons": [],
                "daily_deltas": {}, "minimum_samples": 10,
            })
            item["dates"].add(owner_date)
            # Rows arrive newest-first: replaying a match cannot inflate support.
            for match_id in evidence:
                item["evidence"].setdefault(match_id, (owner_date, weight))
            reason = _text(candidate.get("reason"), 240)
            if reason and reason not in item["reasons"]:
                item["reasons"].append(reason)
            item["daily_deltas"].setdefault(
                owner_date, (_number(candidate.get("delta")), weight)
            )
            try:
                required = int(candidate.get("minimum_samples") or 10)
            except (TypeError, ValueError):
                required = 10
            item["minimum_samples"] = max(item["minimum_samples"], required)

    validated, contested, dormant = [], [], []
    for key, item in grouped.items():
        support_days = {day for day, _ in item["evidence"].values()}
        if (len(support_days) < minimum_pattern_days
                or len(item["evidence"]) < max(minimum_evidence, item["minimum_samples"])):
            continue
        opposite = grouped.get((key[0], "decrease" if key[1] == "increase" else "increase", key[2])) or {}
        evidence_weight = sum(weight for _, weight in item["evidence"].values())
        opposing_weight = sum(weight for _, weight in (opposite.get("evidence") or {}).values())
        support_ratio = evidence_weight / (evidence_weight + opposing_weight)
        recent_weight = sum(
            weight for day, weight in item["evidence"].values() if day in recent_dates
        )
        recent_opposing = sum(
            weight for day, weight in (opposite.get("evidence") or {}).values()
            if day in recent_dates
        )
        recent_opposing_days = {
            day for day, _ in (opposite.get("evidence") or {}).values()
        } & recent_dates
        recent_conflict = (
            len(recent_opposing_days) >= minimum_pattern_days
            and recent_opposing > recent_weight
        )
        first_seen, last_seen = min(support_days), max(support_days)
        days_since_seen = (cutoff - date.fromisoformat(last_seen)).days
        relevance = 0.5 ** (days_since_seen / half_life_days)
        delta_weight = sum(weight for _, weight in item["daily_deltas"].values())
        delta = sum(value * weight for value, weight in item["daily_deltas"].values()) / delta_weight
        pattern = {
            "pattern_id": sha256(":".join(key).encode()).hexdigest()[:16],
            "scope": item["scope"], "target": item["target"], "action": item["action"],
            "suggested_delta": round(delta * relevance * support_ratio, 4),
            "observed_days": len(support_days), "evidence_matches": len(item["evidence"]),
            "effective_evidence": round(evidence_weight, 2),
            "opposing_evidence_matches": len(opposite.get("evidence") or {}),
            "support_ratio": round(support_ratio, 3),
            "first_seen": first_seen, "last_seen": last_seen,
            "days_since_seen": days_since_seen,
            "relevance": round(relevance, 4),
            "source_dates": sorted(support_days)[-10:],
            "reason": "；".join(item["reasons"][:3])[:500],
            "validation_basis": "cross-day-review-evidence",
            "predictive_validation": False,
        }
        if support_ratio < 2 / 3 or recent_conflict:
            pattern.update({"status": "contested-memory", "usage": "reference-only"})
            contested.append(pattern)
        elif days_since_seen > half_life_days * 3:
            pattern.update({"status": "dormant-memory", "usage": "reference-only"})
            dormant.append(pattern)
        else:
            pattern.update({"status": "historically-validated-memory", "usage": "auxiliary-only"})
            validated.append(pattern)
    for patterns in (validated, contested, dormant):
        patterns.sort(key=lambda item: (
            item["effective_evidence"] * item["support_ratio"], item["last_seen"]
        ), reverse=True)
    return validated, contested, dormant


def build_review_memory(
    reviews: Iterable[Dict[str, Any]],
    before_date: str,
    *,
    window_days: int = 7,
    observation_days: int = 3,
    minimum_pattern_days: int = 2,
    minimum_evidence: int = 10,
    half_life_days: int = 90,
    max_patterns: int = 10,
) -> Dict[str, Any]:
    """Build non-binding observations plus cross-day validated patterns.

    Only reviews with an owner date strictly earlier than ``before_date`` are
    eligible. This keeps backtests and reruns free from future-result leakage.
    """
    target_date = str(before_date or "")[:10]
    by_date = {}
    for review in reviews:
        owner_date = str(review.get("owner_date") or "")[:10]
        try:
            date.fromisoformat(owner_date)
            date.fromisoformat(target_date)
        except ValueError:
            continue
        if owner_date and owner_date < target_date:
            previous = by_date.get(owner_date)
            if previous is None or str(review.get("reviewed_at") or "") > str(previous.get("reviewed_at") or ""):
                by_date[owner_date] = review
    # Select the last review first; an older completed diagnosis must not
    # replace the final run when that final diagnosis is pending or stale.
    by_date = {
        day: review for day, review in by_date.items()
        if (review.get("ai_deep_review") or {}).get("status") == "completed"
        and not review.get("ai_deep_review_stale")
    }
    eligible = list(by_date.values())
    eligible.sort(
        key=lambda item: str(item.get("owner_date") or ""),
        reverse=True,
    )
    # The old window limits recent detail only. Long-term evidence never
    # expires at a fixed lookback boundary.
    rows = eligible[:max(1, int(window_days or 7))]

    observations = []
    for review in rows[:max(1, int(observation_days or 3))]:
        deep = review.get("ai_deep_review") or {}
        summary = deep.get("summary") or {}
        settlement = (
            ((review.get("summary") or {}).get("singles") or {})
        )
        handicap_settlement = (
            ((review.get("summary") or {}).get("handicap") or {})
        )
        special_settlement = (
            ((review.get("summary") or {}).get("special_markets") or {})
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
                "handicap_settled": int(
                    handicap_settlement.get("settled") or 0
                ),
                "handicap_hit_rate": _number(
                    handicap_settlement.get("hit_rate")
                ),
                "handicap_roi": _number(handicap_settlement.get("roi")),
                "total_goals_primary": {
                    key: value for key, value in (
                        ((special_settlement.get("total_goals") or {}).get(
                            "primary"
                        ) or {})
                    ).items() if key in {"settled", "hits", "hit_rate", "roi"}
                },
                "total_goals_two_option": {
                    key: value for key, value in (
                        ((special_settlement.get("total_goals") or {}).get(
                            "two_option"
                        ) or {})
                    ).items() if key in {
                        "settled", "hits", "hit_rate", "equal_stake_roi"
                    }
                },
                "half_full_primary": {
                    key: value for key, value in (
                        ((special_settlement.get("half_full") or {}).get(
                            "primary"
                        ) or {})
                    ).items() if key in {"settled", "hits", "hit_rate", "roi"}
                },
                "half_full_two_option": {
                    key: value for key, value in (
                        ((special_settlement.get("half_full") or {}).get(
                            "two_option"
                        ) or {})
                    ).items() if key in {
                        "settled", "hits", "hit_rate", "equal_stake_roi"
                    }
                },
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
                    "euro", "asian", "sporttery", "total",
                    "special_markets", "consistency"
                )
                if _text(lessons.get(key), 220)
            },
        })

    half_life_days = max(30, int(half_life_days or 90))
    validated_patterns, contested_patterns, dormant_patterns = _long_term_patterns(
        eligible, target_date, half_life_days,
        max(2, int(minimum_pattern_days or 2)),
        max(10, int(minimum_evidence or 10)),
        {str(review.get("owner_date") or "")[:10] for review in rows},
    )

    source_dates = sorted({
        str(item.get("owner_date") or "")[:10] for item in rows
    })
    payload = {
        "version": REVIEW_MEMORY_VERSION,
        "before_date": target_date,
        "source_dates": source_dates,
        "review_days": len(eligible),
        "recent_review_days": len(rows),
        "history_review_days": len(eligible),
        "history_range": {
            "first_date": min(by_date, default=None),
            "last_date": max(by_date, default=None),
        },
        "observation_count": len(observations),
        "validated_pattern_count": len(validated_patterns),
        "contested_pattern_count": len(contested_patterns),
        "dormant_pattern_count": len(dormant_patterns),
        "recent_observations": observations,
        "long_term_performance": _historical_performance(eligible, target_date, half_life_days),
        "validated_patterns": validated_patterns[:max(1, int(max_patterns or 10))],
        "contested_patterns": contested_patterns[:5],
        "dormant_patterns": dormant_patterns[:5],
        "governance": {
            "history_scope": "all-available-before-date",
            "recent_window_days": max(1, int(window_days or 7)),
            "half_life_days": half_life_days,
            "long_term_memory_retained": True,
            "cross_day_repetition_is_not_predictive_validation": True,
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
                "长期记忆使用全部历史复盘，按时效衰减；冲突或休眠模式只作参考；"
                "跨日重复证据不等于样本外预测有效，不能宣称命中率提升；"
                "long_term_performance只是历史表现汇总，不能把整体命中率当作本场概率；"
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
