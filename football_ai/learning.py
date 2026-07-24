"""FAE post-match review and rule evaluation."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, Optional, Tuple

from .version import ENGINE_VERSION


class FAEReviewEngine:
    """Grade one saved FAE analysis against a finished match."""

    def review(
        self, analysis: Dict[str, Any], match: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        score = self._score(match)
        if not score:
            return None
        home_goals, away_goals = score
        core = analysis.get("core") or {}
        recommendation = core.get("recommendation") or (analysis.get("analysis") or {}).get("recommendation") or {}
        primary = str(recommendation.get("primary") or "")
        handicap = self._number(
            match.get("hi_handicap_value")
            if match.get("hi_handicap_value") not in (None, "")
            else match.get("handicap")
        )
        total_line = ((core.get("probabilities") or {}).get("total_line"))
        recommendation_result = (
            "skipped"
            if recommendation.get("no_bet") else
            self._grade_label(
                primary, home_goals, away_goals, handicap, total_line
            )
        )
        actual_outcome = "home" if home_goals > away_goals else "draw" if home_goals == away_goals else "away"
        rule_results = []
        for signal in core.get("rule_signals") or []:
            market = signal.get("market")
            prediction = signal.get("prediction")
            hit = None
            if signal.get("risk"):
                if recommendation_result in {"hit", "miss"}:
                    hit = recommendation_result == "miss"
            elif market == "outcome" and prediction in {"home", "draw", "away"}:
                hit = prediction == actual_outcome
            elif (
                market == "historical_outcome"
                and prediction in {"home", "draw", "away"}
            ):
                hit = prediction == actual_outcome
            elif market == "hhad" and prediction in {"win", "draw", "lose"}:
                signal_handicap = self._number(signal.get("handicap"))
                if signal_handicap is not None:
                    adjusted = home_goals + signal_handicap - away_goals
                    actual_hhad = (
                        "win" if adjusted > 0
                        else "draw" if adjusted == 0
                        else "lose"
                    )
                    hit = prediction == actual_hhad
            elif market == "total" and prediction in {"over", "under"} and total_line is not None:
                actual_total = home_goals + away_goals
                if actual_total != float(total_line):
                    hit = (prediction == "over" and actual_total > float(total_line)) or (
                        prediction == "under" and actual_total < float(total_line)
                    )
            rule_results.append({
                "rule_id": signal.get("rule_id"),
                "market": market,
                "prediction": prediction,
                "risk_rule": bool(signal.get("risk")),
                "hit": hit,
                "reason": signal.get("reason"),
                "weight_at_prediction": signal.get("weight"),
            })
        wrong_reasons = [
            item.get("reason") for item in rule_results
            if item.get("hit") is False and item.get("reason")
        ]
        correct_reasons = [
            item.get("reason") for item in rule_results
            if item.get("hit") is True and item.get("reason")
        ]
        return {
            "match_id": str(analysis.get("match_id") or match.get("match_id") or ""),
            "owner_date": str(analysis.get("owner_date") or match.get("owner_date") or "")[:10],
            "match_number": analysis.get("match_number") or match.get("match_number") or match.get("round_id"),
            "engine_version": ((analysis.get("engine") or {}).get("version") or ENGINE_VERSION),
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "result": {
                "score": f"{home_goals}:{away_goals}",
                "home_goals": home_goals,
                "away_goals": away_goals,
                "outcome": actual_outcome,
            },
            "prediction": {
                "primary": primary,
                "score": recommendation.get("score"),
                "bet_score": recommendation.get("bet_score"),
                "value_score": recommendation.get("value_score"),
                "no_bet": recommendation.get("no_bet", False),
                "stars": recommendation.get("stars"),
                "result": recommendation_result,
                "correct": True if recommendation_result == "hit" else False if recommendation_result == "miss" else None,
            },
            "diagnosis": {
                "why_wrong": wrong_reasons[:6],
                "what_worked": correct_reasons[:6],
            },
            "rule_results": rule_results,
        }

    @staticmethod
    def _grade_label(
        label: str,
        home_goals: int,
        away_goals: int,
        handicap: Optional[float],
        total_line: Optional[float],
    ) -> str:
        if label == "主胜":
            return "hit" if home_goals > away_goals else "miss"
        if label == "平局":
            return "hit" if home_goals == away_goals else "miss"
        if label == "客胜":
            return "hit" if home_goals < away_goals else "miss"
        if label in {"让胜", "让平", "让负"} and handicap is not None:
            adjusted = home_goals + handicap - away_goals
            result = "让胜" if adjusted > 0 else "让平" if adjusted == 0 else "让负"
            return "hit" if label == result else "miss"
        if label in {"大球", "小球"} and total_line is not None:
            total = home_goals + away_goals
            if total == float(total_line):
                return "push"
            result = "大球" if total > float(total_line) else "小球"
            return "hit" if label == result else "miss"
        return "ungraded"

    @classmethod
    def _score(cls, match: Dict[str, Any]) -> Optional[Tuple[int, int]]:
        home = cls._integer(match.get("home_score"))
        away = cls._integer(match.get("away_score"))
        if home is not None and away is not None:
            return home, away
        score = re.fullmatch(r"\s*(\d+)\s*[:-]\s*(\d+)\s*", str(match.get("score") or ""))
        return (int(score.group(1)), int(score.group(2))) if score else None

    @staticmethod
    def _integer(value: Any) -> Optional[int]:
        try:
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None
