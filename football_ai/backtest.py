"""Leakage-safe rolling and shadow backtests for daily FAE snapshots."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Dict, Iterable, List, Optional

from .daily_analysis import FAEDailyAIAnalyzer
from .daily_review import FAEDailyAIReviewEngine
from .version import ENGINE_VERSION


BACKTEST_SCHEMA_VERSION = "1.0"
SHADOW_BASELINE_VERSION = "2.13.6"
SHADOW_CANDIDATE_VERSION = ENGINE_VERSION
SHADOW_MIN_SETTLED = 30
SHADOW_MIN_REVIEW_DAYS = 5
SHADOW_VALIDATION_START_DATE = "2026-08-24"


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _variant_summary(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = sorted(
        list(events),
        key=lambda item: (
            str(item.get("owner_date") or ""),
            str(item.get("match_time") or ""),
            str(item.get("match_number") or ""),
        ),
    )
    settled = [
        row for row in rows
        if row.get("status") in {"hit", "miss", "push"}
        and _number(row.get("return")) is not None
    ]
    decided = [
        row for row in settled if row.get("status") in {"hit", "miss"}
    ]
    hits = sum(row.get("status") == "hit" for row in decided)
    stake = sum(_number(row.get("stake")) or 0 for row in settled)
    returns = sum(_number(row.get("return")) or 0 for row in settled)
    profit = returns - stake
    consecutive_misses = 0
    max_consecutive_misses = 0
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for row in settled:
        if row.get("status") == "miss":
            consecutive_misses += 1
            max_consecutive_misses = max(
                max_consecutive_misses, consecutive_misses
            )
        elif row.get("status") == "hit":
            consecutive_misses = 0
        equity += _number(row.get("profit")) or 0
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return {
        "recommendations": len(rows),
        "settled": len(settled),
        "pending": len(rows) - len(settled),
        "hits": hits,
        "misses": len(decided) - hits,
        "pushes": sum(row.get("status") == "push" for row in settled),
        "hit_rate": round(hits / len(decided) * 100, 1) if decided else 0,
        "stake": round(stake, 2),
        "return": round(returns, 2),
        "profit": round(profit, 2),
        "roi": round(profit / stake * 100, 1) if stake else 0,
        "max_consecutive_misses": max_consecutive_misses,
        "max_drawdown_units": round(max_drawdown, 2),
        "review_days": len({
            str(row.get("owner_date") or "") for row in settled
            if row.get("owner_date")
        }),
    }


def _group_summary(
    events: Iterable[Dict[str, Any]], field: str
) -> Dict[str, Dict[str, Any]]:
    rows = list(events)
    labels = sorted({
        str(row.get(field) or "未知") for row in rows
    })
    return {
        label: _variant_summary([
            row for row in rows
            if str(row.get(field) or "未知") == label
        ])
        for label in labels
    }


class FAEShadowBacktestEngine:
    """Replay old/new deterministic guards on immutable prematch runs."""

    BASELINE = "baseline"
    CANDIDATE = "candidate"

    def __init__(self):
        self.review_engine = FAEDailyAIReviewEngine()

    @staticmethod
    def _replay_matches(
        snapshot: Dict[str, Any], variant: str
    ) -> List[Dict[str, Any]]:
        matches = deepcopy(snapshot.get("matches") or [])
        for row in matches:
            analysis = dict(row.get("analysis") or {})
            guard = analysis.get("secondary_selection_guard") or {}
            generated_secondary = (
                guard.get("generated_secondary")
                or analysis.get("secondary_play")
            )
            fresh = FAEDailyAIAnalyzer._secondary_play_decision(
                row.get("input_snapshot") or {},
                str(analysis.get("primary_play") or ""),
                generated_secondary,
            )
            if (
                variant == FAEShadowBacktestEngine.BASELINE
                and (fresh.get("value_protection") or {}).get("triggered")
            ):
                coverage_selection = str(
                    (fresh.get("value_protection") or {}).get(
                        "coverage_selection"
                    ) or ""
                )
                if coverage_selection:
                    fresh["selection"] = coverage_selection
                    fresh["strategy"] = "hhad-model-market-coverage-v1"
            analysis["secondary_play"] = fresh.get("selection")
            analysis["secondary_selection_guard"] = fresh
            row["analysis"] = analysis
        return FAEDailyAIAnalyzer.apply_two_option_recommendations(matches)

    def _settle_variant(
        self,
        snapshot: Dict[str, Any],
        results: Dict[str, Dict[str, Any]],
        variant: str,
    ) -> List[Dict[str, Any]]:
        matches = self._replay_matches(snapshot, variant)
        replay_snapshot = {
            **snapshot,
            "matches": matches,
            "daily_summary": {"recommended_combinations": []},
        }
        review = self.review_engine.review(replay_snapshot, results)
        settled_by_key = {}
        for row in review.get("two_option_results") or []:
            key = (str(row.get("match_id") or ""), row.get("result_type"))
            settled_by_key.setdefault(key, row)
        events = []
        for row in matches:
            analysis = row.get("analysis") or {}
            profile = analysis.get("two_option_recommendation") or {}
            if not profile.get("actionable"):
                continue
            result_type = (
                "two_option_handicap"
                if profile.get("market") == "竞彩让球"
                else "two_option_main"
            )
            settled = settled_by_key.get((
                str(row.get("match_id") or ""), result_type
            )) or {}
            status = str(settled.get("status") or "pending")
            pair_stake = _number(settled.get("equal_stake_stake"))
            pair_return = _number(settled.get("equal_stake_return"))
            if pair_stake is None and status in {"hit", "miss", "push"}:
                pair_stake = 2.0
            profit = (
                pair_return - pair_stake
                if pair_return is not None and pair_stake is not None
                else None
            )
            events.append({
                "owner_date": str(snapshot.get("owner_date") or "")[:10],
                "run_id": snapshot.get("run_id"),
                "match_id": str(row.get("match_id") or ""),
                "match_number": row.get("match_number"),
                "match_time": row.get("match_time"),
                "league": row.get("league") or "未知",
                "market": profile.get("market"),
                "selection_text": profile.get("selection_text"),
                "status": status,
                "hit_selection": settled.get("hit_selection"),
                "hit_odds": settled.get("hit_odds"),
                "result_score": settled.get("result_score"),
                "stake": round(pair_stake, 2)
                if pair_stake is not None else None,
                "return": round(pair_return, 2)
                if pair_return is not None else None,
                "profit": round(profit, 2) if profit is not None else None,
                "rank_score": profile.get("rank_score"),
            })
        return events

    @staticmethod
    def _release_guard(
        baseline: Dict[str, Any], candidate: Dict[str, Any]
    ) -> Dict[str, Any]:
        reasons = []
        if candidate.get("settled", 0) < SHADOW_MIN_SETTLED:
            reasons.append(
                f"候选样本{candidate.get('settled', 0)}场，"
                f"少于{SHADOW_MIN_SETTLED}场"
            )
        if candidate.get("review_days", 0) < SHADOW_MIN_REVIEW_DAYS:
            reasons.append(
                f"候选仅覆盖{candidate.get('review_days', 0)}个比赛日，"
                f"少于{SHADOW_MIN_REVIEW_DAYS}日"
            )
        enough_samples = (
            candidate.get("settled", 0) >= SHADOW_MIN_SETTLED
            and candidate.get("review_days", 0) >= SHADOW_MIN_REVIEW_DAYS
        )
        if enough_samples:
            if candidate.get("roi", 0) <= baseline.get("roi", 0):
                reasons.append("候选ROI尚未超过基线")
            if candidate.get("hit_rate", 0) < baseline.get("hit_rate", 0):
                reasons.append("候选覆盖命中率低于基线")
            if (
                candidate.get("max_drawdown_units", 0)
                > baseline.get("max_drawdown_units", 0)
            ):
                reasons.append("候选最大回撤高于基线")
        return {
            "status": "eligible" if not reasons else "shadow_only",
            "can_promote": not reasons,
            "minimum_settled": SHADOW_MIN_SETTLED,
            "minimum_review_days": SHADOW_MIN_REVIEW_DAYS,
            "reasons": reasons or ["样本、收益与回撤均达到候选发布门槛"],
            "note": "影子结果只用于版本验证，不会自动修改线上推荐。",
        }

    def build(
        self,
        snapshot_days: Iterable[Dict[str, Any]],
        *,
        requested_days: int = 28,
        validation_start_date: str = SHADOW_VALIDATION_START_DATE,
    ) -> Dict[str, Any]:
        days = sorted(
            list(snapshot_days),
            key=lambda item: str(
                (item.get("snapshot") or {}).get("owner_date") or ""
            ),
        )[-max(1, int(requested_days)):]
        events = {self.BASELINE: [], self.CANDIDATE: []}
        source_rows = []
        for item in days:
            snapshot = item.get("snapshot") or {}
            results = {
                str(key): value for key, value in (
                    item.get("results") or {}
                ).items()
            }
            source_rows.append({
                "owner_date": snapshot.get("owner_date"),
                "run_id": snapshot.get("run_id"),
                "result_fingerprint": sorted(
                    (
                        match_id,
                        result.get("status"),
                        result.get("home_score"),
                        result.get("away_score"),
                    )
                    for match_id, result in results.items()
                ),
            })
            for variant in events:
                events[variant].extend(self._settle_variant(
                    snapshot, results, variant
                ))

        baseline = _variant_summary(events[self.BASELINE])
        candidate = _variant_summary(events[self.CANDIDATE])
        validation_start = str(validation_start_date or "")[:10]
        validation_events = {
            variant: [
                row for row in variant_events
                if str(row.get("owner_date") or "") >= validation_start
            ]
            for variant, variant_events in events.items()
        }
        validation_baseline = _variant_summary(
            validation_events[self.BASELINE]
        )
        validation_candidate = _variant_summary(
            validation_events[self.CANDIDATE]
        )
        baseline_ids = {
            (row.get("owner_date"), row.get("match_id")): row
            for row in events[self.BASELINE]
        }
        candidate_ids = {
            (row.get("owner_date"), row.get("match_id")): row
            for row in events[self.CANDIDATE]
        }
        removed = [
            row for key, row in baseline_ids.items() if key not in candidate_ids
        ]
        added = [
            row for key, row in candidate_ids.items() if key not in baseline_ids
        ]
        rolling_windows = {}
        available_dates = sorted({
            str(row.get("owner_date") or "")
            for rows in events.values() for row in rows
            if row.get("owner_date")
        })
        for window in (7, 14, 28):
            selected_dates = set(available_dates[-window:])
            rolling_windows[str(window)] = {
                self.BASELINE: _variant_summary([
                    row for row in events[self.BASELINE]
                    if row.get("owner_date") in selected_dates
                ]),
                self.CANDIDATE: _variant_summary([
                    row for row in events[self.CANDIDATE]
                    if row.get("owner_date") in selected_dates
                ]),
            }
        input_hash = sha256(json.dumps(
            source_rows,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")).hexdigest()
        return {
            "schema_version": BACKTEST_SCHEMA_VERSION,
            "report_id": (
                f"shadow-{SHADOW_BASELINE_VERSION}-{SHADOW_CANDIDATE_VERSION}-"
                f"{requested_days}d"
            ),
            "mode": "immutable_pregame_walk_forward",
            "baseline_version": SHADOW_BASELINE_VERSION,
            "candidate_version": SHADOW_CANDIDATE_VERSION,
            "validation_start_date": validation_start,
            "requested_days": int(requested_days),
            "source_dates": [
                str((item.get("snapshot") or {}).get("owner_date") or "")
                for item in days
            ],
            "input_hash": input_hash,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "baseline": {
                **baseline,
                "by_market": _group_summary(
                    events[self.BASELINE], "market"
                ),
                "by_league": _group_summary(
                    events[self.BASELINE], "league"
                ),
            },
            "candidate": {
                **candidate,
                "by_market": _group_summary(
                    events[self.CANDIDATE], "market"
                ),
                "by_league": _group_summary(
                    events[self.CANDIDATE], "league"
                ),
            },
            "comparison": {
                "recommendation_delta": (
                    candidate["recommendations"]
                    - baseline["recommendations"]
                ),
                "hit_rate_delta": round(
                    candidate["hit_rate"] - baseline["hit_rate"], 1
                ),
                "roi_delta": round(candidate["roi"] - baseline["roi"], 1),
                "max_drawdown_delta": round(
                    candidate["max_drawdown_units"]
                    - baseline["max_drawdown_units"], 2
                ),
                "removed_count": len(removed),
                "added_count": len(added),
                "removed": removed[:20],
                "added": added[:20],
            },
            "rolling_windows": rolling_windows,
            "validation": {
                self.BASELINE: validation_baseline,
                self.CANDIDATE: validation_candidate,
            },
            "release_guard": self._release_guard(
                validation_baseline, validation_candidate
            ),
        }
