"""Review engine for draw, handicap-draw and 2/3-leg daily plans."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .version import ENGINE_VERSION


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _score(match: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    home = _integer(match.get("home_score"))
    away = _integer(match.get("away_score"))
    return (home, away) if home is not None and away is not None else None


def summarize_settled(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    row_list = list(rows)
    settled = [
        row for row in row_list if row.get("status") in {"hit", "miss"}
    ]
    hits = sum(1 for row in settled if row.get("status") == "hit")
    stake = len(settled)
    returns = round(sum(_number(row.get("return")) or 0 for row in settled), 2)
    profit = round(returns - stake, 2)
    return {
        "total": len(row_list),
        "settled": stake,
        "pending": max(0, len(row_list) - stake),
        "hits": hits,
        "misses": stake - hits,
        "hit_rate": round(hits / stake * 100, 1) if stake else 0,
        "stake": stake,
        "return": returns,
        "profit": profit,
        "roi": round(profit / stake * 100, 1) if stake else 0,
    }


class FAEDrawReviewEngine:
    """Settle the immutable daily draw-plan snapshot against final results."""

    def review(
        self,
        snapshot: Dict[str, Any],
        matches_by_id: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        match_results = [
            self._settle_pick(pick, matches_by_id.get(str(pick.get("match_id"))) or {})
            for pick in snapshot.get("match_recommendations") or []
        ]
        result_by_pick = {
            (str(row.get("match_id")), str(row.get("selection"))): row
            for row in match_results
        }
        combo_results = []
        for field in ("two_leg", "three_leg"):
            for index, combo in enumerate(snapshot.get(field) or [], 1):
                pick_results = [
                    result_by_pick.get(
                        (str(pick.get("match_id")), str(pick.get("selection")))
                    )
                    for pick in combo.get("picks") or []
                ]
                statuses = [
                    item.get("status") if item else "pending" for item in pick_results
                ]
                if "miss" in statuses:
                    status = "miss"
                elif statuses and all(value == "hit" for value in statuses):
                    status = "hit"
                else:
                    status = "pending"
                combined_odds = _number(combo.get("combined_odds"))
                payout = combined_odds if status == "hit" and combined_odds else 0
                combo_results.append({
                    "key": f"{combo.get('play')}-{index}",
                    "play": combo.get("play") or (
                        "2串1" if field == "two_leg" else "3串1"
                    ),
                    "legs": combo.get("legs") or len(pick_results),
                    "picks": [dict(item) for item in (combo.get("picks") or [])],
                    "status": status,
                    "combined_odds": combined_odds,
                    "return": round(payout, 2),
                    "profit": (
                        round(payout - 1, 2) if status in {"hit", "miss"} else None
                    ),
                })

        by_selection = {
            label: summarize_settled([
                row for row in match_results if row.get("selection") == label
            ])
            for label in ("平局", "让平")
        }
        by_play = {
            play: summarize_settled([
                row for row in combo_results if row.get("play") == play
            ])
            for play in ("2串1", "3串1")
        }
        pending_matches = sum(
            1 for row in match_results if row.get("status") not in {"hit", "miss"}
        )
        return {
            "owner_date": snapshot.get("date") or snapshot.get("owner_date"),
            "engine_version": snapshot.get("engine_version") or ENGINE_VERSION,
            "snapshot_hash": snapshot.get("snapshot_hash"),
            "snapshot_at": snapshot.get("generated_at"),
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "completed": pending_matches == 0,
            "pending_matches": pending_matches,
            "match_results": match_results,
            "combo_results": combo_results,
            "summary": {
                "singles": summarize_settled(match_results),
                "by_selection": by_selection,
                "combos": summarize_settled(combo_results),
                "by_play": by_play,
            },
        }

    @staticmethod
    def _settle_pick(
        pick: Dict[str, Any], match: Dict[str, Any]
    ) -> Dict[str, Any]:
        result = dict(pick)
        final_score = _score(match)
        if match.get("status") not in (2, "2") or not final_score:
            result.update({"status": "pending", "return": None, "profit": None})
            return result

        home_goals, away_goals = final_score
        selection = str(pick.get("selection") or pick.get("recommendation") or "")
        if selection == "平局":
            hit = home_goals == away_goals
        elif selection == "让平":
            handicap = _number(pick.get("handicap"))
            if handicap is None:
                result.update({
                    "status": "ungraded",
                    "result_score": f"{home_goals}:{away_goals}",
                    "return": None,
                    "profit": None,
                })
                return result
            hit = home_goals + handicap == away_goals
        else:
            result.update({"status": "ungraded", "return": None, "profit": None})
            return result

        odds = _number(pick.get("odds"))
        payout = odds if hit and odds else 0
        result.update({
            "status": "hit" if hit else "miss",
            "result_score": f"{home_goals}:{away_goals}",
            "return": round(payout, 2),
            "profit": round(payout - 1, 2),
        })
        return result


def aggregate_draw_reviews(
    reviews: Iterable[Dict[str, Any]],
    strategy_weights: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    review_list = list(reviews)
    match_results: List[Dict[str, Any]] = []
    combo_results: List[Dict[str, Any]] = []
    for review in review_list:
        match_results.extend(review.get("match_results") or [])
        combo_results.extend(review.get("combo_results") or [])
    reviewed_days = sum(
        1 for review in review_list
        if (
            ((review.get("summary") or {}).get("singles") or {}).get("settled", 0)
            or ((review.get("summary") or {}).get("combos") or {}).get("settled", 0)
        )
    )
    return {
        "reviewed_days": reviewed_days,
        "singles": summarize_settled(match_results),
        "by_selection": {
            label: summarize_settled([
                row for row in match_results if row.get("selection") == label
            ])
            for label in ("平局", "让平")
        },
        "combos": summarize_settled(combo_results),
        "by_play": {
            play: summarize_settled([
                row for row in combo_results if row.get("play") == play
            ])
            for play in ("2串1", "3串1")
        },
        "strategy_weights": strategy_weights or {},
    }
