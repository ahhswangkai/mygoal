"""Post-match settlement for immutable FAE daily Ark judgements."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


SUPPORTED_SELECTIONS = (
    "主胜", "平局", "客胜", "让胜", "让平", "让负", "大球", "小球"
)
TWO_OPTION_SELECTIONS = {
    "主胜", "平局", "客胜", "让胜", "让平", "让负"
}


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
    if home is not None and away is not None:
        return home, away
    result = re.fullmatch(
        r"\s*(\d+)\s*[:-]\s*(\d+)\s*", str(match.get("score") or "")
    )
    return (int(result.group(1)), int(result.group(2))) if result else None


def _snapshot_value(
    snapshot: Dict[str, Any], market: str, index: int
) -> Optional[float]:
    source = snapshot.get(market) or {}
    values = source.get("current") or source.get("initial") or []
    return _number(values[index]) if len(values) > index else None


def _selection_terms(
    snapshot: Dict[str, Any], selection: str
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Return odds, handicap and total line fixed at prediction time."""
    if selection in {"主胜", "平局", "客胜"}:
        return (
            _snapshot_value(
                snapshot, "euro", {"主胜": 0, "平局": 1, "客胜": 2}[selection]
            ),
            None,
            None,
        )
    if selection in {"让胜", "让平", "让负"}:
        return (
            _snapshot_value(
                snapshot,
                "sporttery_handicap",
                {"让胜": 0, "让平": 1, "让负": 2}[selection],
            ),
            _number(
                (snapshot.get("sporttery_handicap") or {}).get("value")
            ),
            None,
        )
    if selection in {"大球", "小球"}:
        return (
            _snapshot_value(
                snapshot, "total", 0 if selection == "大球" else 2
            ),
            None,
            _snapshot_value(snapshot, "total", 1),
        )
    return None, None, None


def summarize_ai_settled(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    row_list = list(rows)
    settled = [
        row for row in row_list
        if row.get("status") in {"hit", "miss", "push"}
    ]
    decided = [
        row for row in settled if row.get("status") in {"hit", "miss"}
    ]
    hits = sum(1 for row in decided if row.get("status") == "hit")
    pushes = sum(1 for row in settled if row.get("status") == "push")
    financial = [
        row for row in settled if _number(row.get("return")) is not None
    ]
    stake = len(financial)
    returns = round(
        sum(_number(row.get("return")) or 0 for row in financial), 2
    )
    profit = round(returns - stake, 2)
    return {
        "total": len(row_list),
        "settled": len(settled),
        "pending": sum(
            1 for row in row_list if row.get("status") == "pending"
        ),
        "ungraded": sum(
            1 for row in row_list
            if row.get("status") in {"ungraded", "skipped"}
        ),
        "hits": hits,
        "misses": len(decided) - hits,
        "pushes": pushes,
        "hit_rate": round(hits / len(decided) * 100, 1) if decided else 0,
        "stake": stake,
        "return": returns,
        "profit": profit,
        "roi": round(profit / stake * 100, 1) if stake else 0,
    }


def summarize_two_option(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize coverage accuracy for primary + hedge selections.

    A two-option row is an analytical coverage check.  Coverage accuracy and
    the explicitly labelled equal-stake return are both reported: staking one
    unit on each option is not the same as treating the pair as one ticket.
    """
    row_list = list(rows)
    settled = [
        row for row in row_list
        if row.get("status") in {"hit", "miss", "push"}
    ]
    decided = [
        row for row in settled if row.get("status") in {"hit", "miss"}
    ]
    hits = sum(1 for row in decided if row.get("status") == "hit")
    pushes = sum(1 for row in settled if row.get("status") == "push")
    option_counts = [
        len(row.get("selections") or [])
        for row in row_list
        if row.get("selections")
    ]
    financial = [
        row for row in settled
        if _number(row.get("equal_stake_stake")) is not None
        and _number(row.get("equal_stake_return")) is not None
    ]
    equal_stake = round(sum(
        _number(row.get("equal_stake_stake")) or 0 for row in financial
    ), 2)
    equal_return = round(sum(
        _number(row.get("equal_stake_return")) or 0 for row in financial
    ), 2)
    equal_profit = round(equal_return - equal_stake, 2)
    return {
        "total": len(row_list),
        "settled": len(settled),
        "pending": sum(
            1 for row in row_list if row.get("status") == "pending"
        ),
        "ungraded": sum(
            1 for row in row_list
            if row.get("status") in {"ungraded", "skipped"}
        ),
        "hits": hits,
        "misses": len(decided) - hits,
        "pushes": pushes,
        "hit_rate": round(hits / len(decided) * 100, 1) if decided else 0,
        "average_options": (
            round(sum(option_counts) / len(option_counts), 2)
            if option_counts else 0
        ),
        "equal_stake": equal_stake,
        "equal_stake_return": equal_return,
        "equal_stake_profit": equal_profit,
        "equal_stake_roi": (
            round(equal_profit / equal_stake * 100, 1)
            if equal_stake else 0
        ),
    }


def unique_two_option_rows(
    rows: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return one canonical two-option decision per match.

    The same handicap pair can be emitted once as ``主选防选`` and again as
    ``竞彩让球双选``.  Keep those raw rows for category diagnostics, while the
    overall headline uses the main pair first and falls back to the handicap
    pair.  This prevents one match from inflating the daily hit rate twice.
    """
    by_match: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        match_id = str(row.get("match_id") or "")
        if not match_id:
            continue
        current = by_match.get(match_id)
        if current is None or (
            current.get("result_type") != "two_option_main"
            and row.get("result_type") == "two_option_main"
        ):
            by_match[match_id] = row
    return list(by_match.values())


def summarize_history_calibration(
    rows: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compare pre-match raw and history-calibrated probabilities."""
    records = []
    for row in rows:
        if row.get("status") not in {"hit", "miss"}:
            continue
        calibration = row.get("historical_calibration") or {}
        raw = _number(calibration.get("core_probability"))
        calibrated = _number(calibration.get("calibrated_probability"))
        if not calibration.get("applied") or raw is None or calibrated is None:
            continue
        actual = 1.0 if row.get("status") == "hit" else 0.0
        records.append((
            actual,
            raw / 100,
            calibrated / 100,
            str(row.get("review_owner_date") or "")[:10],
        ))
    if not records:
        return {
            "sample": 0,
            "review_days": 0,
            "core_brier": None,
            "calibrated_brier": None,
            "brier_improvement": None,
            "validated": False,
        }
    core_brier = sum(
        (actual - probability) ** 2
        for actual, probability, _, _ in records
    ) / len(records)
    calibrated_brier = sum(
        (actual - probability) ** 2
        for actual, _, probability, _ in records
    ) / len(records)
    improvement = core_brier - calibrated_brier
    return {
        "sample": len(records),
        "review_days": len({date for *_, date in records if date}),
        "core_brier": round(core_brier, 5),
        "calibrated_brier": round(calibrated_brier, 5),
        "brier_improvement": round(improvement, 5),
        "validated": (
            len(records) >= 30
            and len({date for *_, date in records if date}) >= 5
            and improvement > 0
        ),
        "instruction": (
            "brier_improvement大于0才表示历史校准优于原始概率；"
            "至少30个跨日样本后才允许发布调权。"
        ),
    }


class FAEDailyAIReviewEngine:
    """Grade Ark's effective selections and its global 2/3-leg plans."""

    def review(
        self,
        snapshot: Dict[str, Any],
        matches_by_id: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        matches = snapshot.get("matches") or []
        match_results = [
            self._settle_match(
                item, matches_by_id.get(str(item.get("match_id"))) or {}
            )
            for item in matches
        ]
        handicap_results = [
            result
            for item in matches
            for result in [self._settle_handicap_reference(
                item, matches_by_id.get(str(item.get("match_id"))) or {}
            )]
            if result is not None
        ]
        two_option_results = [
            result
            for item in matches
            for result in self._settle_two_option_references(
                item, matches_by_id.get(str(item.get("match_id"))) or {}
            )
        ]
        unique_two_options = unique_two_option_rows(two_option_results)
        draw_radar_results = [
            result
            for item in matches
            for result in self._settle_draw_radar(
                item, matches_by_id.get(str(item.get("match_id"))) or {}
            )
        ]
        snapshot_by_id = {
            str(item.get("match_id")): item for item in matches
        }
        combo_results = []
        for index, combo in enumerate(
            ((snapshot.get("daily_summary") or {}).get(
                "recommended_combinations"
            ) or []),
            1,
        ):
            picks = []
            for pick in combo.get("picks") or []:
                match_id = str(pick.get("match_id") or "")
                source = snapshot_by_id.get(match_id) or {}
                selection = str(pick.get("selection") or "")
                settled = self._settle_selection(
                    source,
                    matches_by_id.get(match_id) or {},
                    selection,
                )
                picks.append(settled)
            statuses = [item.get("status") for item in picks]
            if not statuses or any(
                value in {"ungraded", "skipped"} for value in statuses
            ):
                status = "ungraded"
            elif "miss" in statuses:
                status = "miss"
            elif "pending" in statuses:
                status = "pending"
            elif all(value == "push" for value in statuses):
                status = "push"
            else:
                status = "hit"
            combined_odds = 1.0
            valid_odds = True
            for pick in picks:
                odds = _number(pick.get("odds"))
                if pick.get("status") == "push":
                    continue
                if odds is None:
                    valid_odds = False
                    break
                combined_odds *= odds
            combined_odds = round(combined_odds, 2) if valid_odds else None
            payout = (
                combined_odds if status == "hit"
                else 1.0 if status == "push"
                else 0.0 if status == "miss"
                else None
            )
            combo_results.append({
                "key": f"{combo.get('play') or len(picks)}-{index}",
                "play": combo.get("play") or f"{len(picks)}串1",
                "picks": picks,
                "reason": combo.get("reason"),
                "status": status,
                "combined_odds": combined_odds,
                "return": round(payout, 2) if payout is not None else None,
                "profit": round(payout - 1, 2) if payout is not None else None,
            })

        pending = sum(
            1 for row in match_results if row.get("status") == "pending"
        )
        selections = sorted({
            str(row.get("selection")) for row in match_results
            if row.get("selection") in SUPPORTED_SELECTIONS
        })
        conflicts = [
            row for row in match_results if row.get("guardrail_triggered")
        ]
        return {
            "owner_date": str(snapshot.get("owner_date") or "")[:10],
            "run_id": snapshot.get("run_id"),
            "engine_version": snapshot.get("engine_version"),
            "input_hash": snapshot.get("input_hash"),
            "model": snapshot.get("model"),
            "prompt_version": snapshot.get("prompt_version"),
            "snapshot_at": snapshot.get("generated_at"),
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "primary_source": "fae-daily-ai",
            "completed": pending == 0,
            "pending_matches": pending,
            "match_results": match_results,
            "handicap_results": handicap_results,
            "two_option_results": two_option_results,
            "draw_radar_results": draw_radar_results,
            "combo_results": combo_results,
            "conflicts": conflicts,
            "summary": {
                "singles": summarize_ai_settled(match_results),
                "handicap": summarize_ai_settled(handicap_results),
                "two_option": {
                    "overall": summarize_two_option(unique_two_options),
                    "raw_rows": len(two_option_results),
                    "unique_matches": len(unique_two_options),
                    "main": summarize_two_option([
                        row for row in two_option_results
                        if row.get("result_type") == "two_option_main"
                    ]),
                    "handicap": summarize_two_option([
                        row for row in two_option_results
                        if row.get("result_type") == "two_option_handicap"
                    ]),
                    "by_pair": {
                        pair: summarize_two_option([
                            row for row in two_option_results
                            if row.get("pair_key") == pair
                        ])
                        for pair in sorted({
                            str(row.get("pair_key") or "")
                            for row in two_option_results
                            if row.get("pair_key")
                        })
                    },
                },
                "draw_radar": {
                    "overall": summarize_ai_settled(draw_radar_results),
                    "ordinary_draw": summarize_ai_settled([
                        row for row in draw_radar_results
                        if row.get("selection") == "平局"
                    ]),
                    "handicap_draw": summarize_ai_settled([
                        row for row in draw_radar_results
                        if row.get("selection") == "让平"
                    ]),
                    "core": summarize_ai_settled([
                        row for row in draw_radar_results
                        if row.get("tier") == "core"
                    ]),
                    "watch": summarize_ai_settled([
                        row for row in draw_radar_results
                        if row.get("tier") == "watch"
                    ]),
                },
                "handicap_by_selection": {
                    label: summarize_ai_settled([
                        row for row in handicap_results
                        if row.get("selection") == label
                    ])
                    for label in ("让胜", "让平", "让负")
                },
                "by_selection": {
                    label: summarize_ai_settled([
                        row for row in match_results
                        if row.get("selection") == label
                    ])
                    for label in selections
                },
                "by_rating": {
                    label: summarize_ai_settled([
                        row for row in match_results
                        if row.get("rating_bucket") == label
                    ])
                    for label in ("4.5+", "4.0", "3.5", "<3.5")
                },
                "combos": summarize_ai_settled(combo_results),
                "by_play": {
                    play: summarize_ai_settled([
                        row for row in combo_results
                        if row.get("play") == play
                    ])
                    for play in ("2串1", "3串1")
                },
                "guardrail_conflicts": len(conflicts),
            },
        }

    def _settle_match(
        self, source: Dict[str, Any], match: Dict[str, Any]
    ) -> Dict[str, Any]:
        analysis = source.get("analysis") or {}
        result = self._settle_selection(
            source,
            match,
            str(
                analysis.get("single_play")
                or analysis.get("primary_play")
                or "观望"
            ),
        )
        result["value_selection"] = analysis.get("primary_play")
        result["single_probability"] = analysis.get(
            "single_probability"
        )
        result["single_probability_profile"] = analysis.get(
            "single_probability_profile"
        ) or {}
        if analysis.get("no_bet"):
            if result.get("status") == "pending":
                result["no_bet"] = True
                result["no_bet_reasons"] = (
                    analysis.get("no_bet_reasons") or []
                )
                return result
            result["observation_status"] = result.get("status")
            result["status"] = "skipped"
            result["return"] = None
            result["profit"] = None
            result["no_bet"] = True
            result["no_bet_reasons"] = analysis.get("no_bet_reasons") or []
        return result

    def _settle_two_option_references(
        self, source: Dict[str, Any], match: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Settle primary + hedge coverage without treating it as ROI."""
        analysis = source.get("analysis") or {}
        rows: List[Dict[str, Any]] = []
        groups = {
            "main": {
                "type": "two_option_main",
                "market": "主选防选",
                "selections": [
                    analysis.get("primary_play"),
                    analysis.get("secondary_play"),
                ],
            },
            "handicap": {
                "type": "two_option_handicap",
                "market": "竞彩让球双选",
                "selections": [
                    value for value in (
                        analysis.get("primary_play"),
                        analysis.get("secondary_play"),
                        analysis.get("handicap_play"),
                    )
                    if value in {"让胜", "让平", "让负"}
                ],
            },
        }
        for group in groups.values():
            selections = []
            for value in group["selections"]:
                selection = str(value or "")
                if (
                    selection in TWO_OPTION_SELECTIONS
                    and selection not in selections
                ):
                    selections.append(selection)
            if len(selections) < 2:
                continue
            if not self._same_market(selections):
                continue
            settled = [
                self._settle_selection(source, match, selection)
                for selection in selections
            ]
            statuses = [item.get("status") for item in settled]
            if not statuses:
                status = "ungraded"
            elif "hit" in statuses:
                status = "hit"
            elif "pending" in statuses:
                status = "pending"
            elif "push" in statuses:
                status = "push"
            elif all(value == "miss" for value in statuses):
                status = "miss"
            else:
                status = "ungraded"
            hit_row = next(
                (item for item in settled if item.get("status") == "hit"),
                None,
            )
            first = settled[0] if settled else {}
            financial = [
                item for item in settled
                if item.get("status") in {"hit", "miss", "push"}
                and _number(item.get("return")) is not None
            ]
            equal_stake = (
                float(len(financial))
                if len(financial) == len(settled) else None
            )
            equal_return = (
                round(sum(_number(item.get("return")) or 0 for item in financial), 2)
                if equal_stake is not None else None
            )
            equal_profit = (
                round(equal_return - equal_stake, 2)
                if equal_return is not None and equal_stake is not None
                else None
            )
            rows.append({
                "match_id": str(source.get("match_id") or ""),
                "match_number": source.get("match_number"),
                "home_team": source.get("home_team"),
                "away_team": source.get("away_team"),
                "league": source.get("league"),
                "result_type": group["type"],
                "market": group["market"],
                "selection": " / ".join(selections),
                "selection_text": " / ".join(
                    item.get("selection_text") or item.get("selection")
                    for item in settled
                ),
                "selections": selections,
                "selection_results": settled,
                "hit_selection": (
                    hit_row.get("selection") if hit_row else None
                ),
                "hit_selection_text": (
                    hit_row.get("selection_text") if hit_row else None
                ),
                "hit_odds": hit_row.get("odds") if hit_row else None,
                "status": status,
                "result_score": first.get("result_score"),
                "pair_key": " / ".join(selections),
                "rating": first.get("rating"),
                "rating_bucket": first.get("rating_bucket"),
                "no_bet": bool(analysis.get("no_bet")),
                "no_bet_reasons": analysis.get("no_bet_reasons") or [],
                "return": None,
                "profit": None,
                "equal_stake_stake": equal_stake,
                "equal_stake_return": equal_return,
                "equal_stake_profit": equal_profit,
                "equal_stake_roi": (
                    round(equal_profit / equal_stake * 100, 1)
                    if equal_profit is not None and equal_stake else None
                ),
            })
        return rows

    @staticmethod
    def _same_market(selections: Iterable[str]) -> bool:
        market_sets = (
            {"主胜", "平局", "客胜"},
            {"让胜", "让平", "让负"},
            {"大球", "小球"},
        )
        selection_set = set(selections)
        return any(selection_set <= market for market in market_sets)

    def _settle_draw_radar(
        self, source: Dict[str, Any], match: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Settle core/watch draw radar rows without rewriting official ROI."""
        radar = (source.get("analysis") or {}).get("draw_radar") or {}
        rows = []
        for key, selection in (
            ("ordinary_draw", "平局"),
            ("handicap_draw", "让平"),
        ):
            candidate = radar.get(key) or {}
            tier = str(candidate.get("tier") or "exclude")
            if tier not in {"core", "watch"}:
                continue
            result = self._settle_selection(source, match, selection)
            rating = _number(candidate.get("rating")) or 0
            result.update({
                "result_type": "draw_radar",
                "radar_key": key,
                "tier": tier,
                "official_bet": tier == "core",
                "rating": rating,
                "rating_bucket": (
                    "4.5+" if rating >= 4.5
                    else "4.0" if rating >= 4
                    else "3.5" if rating >= 3.5
                    else "<3.5"
                ),
                "radar_score": candidate.get("score"),
                "probability": candidate.get("probability"),
                "market_probability": candidate.get(
                    "market_probability"
                ),
                "odds_value": candidate.get("odds_value"),
                "effective_sample": candidate.get("effective_sample"),
                "reason": candidate.get("reason"),
            })
            rows.append(result)
        return rows

    def _settle_handicap_reference(
        self, source: Dict[str, Any], match: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Settle the displayed Sporttery handicap reference separately."""
        analysis = source.get("analysis") or {}
        selection = str(analysis.get("handicap_play") or "")
        if selection not in {"让胜", "让平", "让负"}:
            return None
        result = self._settle_selection(source, match, selection)
        result["result_type"] = "handicap_reference"
        if analysis.get("no_bet"):
            if result.get("status") == "pending":
                result["no_bet"] = True
                result["no_bet_reasons"] = (
                    analysis.get("no_bet_reasons") or []
                )
                return result
            result["observation_status"] = result.get("status")
            result["status"] = "skipped"
            result["return"] = None
            result["profit"] = None
            result["no_bet"] = True
            result["no_bet_reasons"] = analysis.get("no_bet_reasons") or []
        return result

    @classmethod
    def _settle_selection(
        cls,
        source: Dict[str, Any],
        match: Dict[str, Any],
        selection: str,
    ) -> Dict[str, Any]:
        analysis = source.get("analysis") or {}
        snapshot = source.get("input_snapshot") or {}
        odds, handicap, total_line = _selection_terms(snapshot, selection)
        rating = _number(analysis.get("rating")) or 0
        guard = analysis.get("consistency_guard") or {}
        result = {
            "match_id": str(source.get("match_id") or ""),
            "match_number": source.get("match_number"),
            "home_team": source.get("home_team"),
            "away_team": source.get("away_team"),
            "league": source.get("league"),
            "selection": selection,
            "selection_text": (
                f"{selection}({handicap:+g})"
                if selection.startswith("让") and handicap is not None
                else selection
            ),
            "model_selection": analysis.get("model_primary_play") or selection,
            "rating": rating,
            "rating_bucket": (
                "4.5+" if rating >= 4.5
                else "4.0" if rating >= 4
                else "3.5" if rating >= 3.5
                else "<3.5"
            ),
            "odds": odds,
            "handicap": handicap,
            "total_line": total_line,
            "guardrail_triggered": bool(guard.get("triggered")),
            "guardrail": guard,
            "historical_calibration": analysis.get(
                "historical_calibration"
            ) or {},
            "historical_goal_margin": analysis.get(
                "historical_goal_margin"
            ) or {},
        }
        final_score = _score(match)
        if match.get("status") not in (2, "2") or not final_score:
            result.update({"status": "pending", "return": None, "profit": None})
            return result
        home, away = final_score
        if selection == "观望":
            result.update({
                "status": "skipped",
                "result_score": f"{home}:{away}",
                "return": None,
                "profit": None,
            })
            return result
        grade = cls._grade(
            selection, home, away, handicap=handicap, total_line=total_line
        )
        payout = (
            odds if grade == "hit" and odds is not None
            else 1.0 if grade == "push"
            else 0.0 if grade == "miss"
            else None
        )
        result.update({
            "status": grade,
            "result_score": f"{home}:{away}",
            "return": round(payout, 2) if payout is not None else None,
            "profit": round(payout - 1, 2) if payout is not None else None,
        })
        return result

    @staticmethod
    def _grade(
        selection: str,
        home: int,
        away: int,
        handicap: Optional[float],
        total_line: Optional[float],
    ) -> str:
        if selection == "主胜":
            return "hit" if home > away else "miss"
        if selection == "平局":
            return "hit" if home == away else "miss"
        if selection == "客胜":
            return "hit" if home < away else "miss"
        if selection in {"让胜", "让平", "让负"} and handicap is not None:
            adjusted = home + handicap - away
            actual = "让胜" if adjusted > 0 else "让平" if adjusted == 0 else "让负"
            return "hit" if selection == actual else "miss"
        if selection in {"大球", "小球"} and total_line is not None:
            total = home + away
            if total == total_line:
                return "push"
            actual = "大球" if total > total_line else "小球"
            return "hit" if selection == actual else "miss"
        return "ungraded"


def aggregate_daily_ai_reviews(
    reviews: Iterable[Dict[str, Any]],
    strategy_weights: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    rows = list(reviews)
    matches: List[Dict[str, Any]] = []
    handicap_results: List[Dict[str, Any]] = []
    two_option_results: List[Dict[str, Any]] = []
    draw_radar_results: List[Dict[str, Any]] = []
    combos: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    for review in rows:
        owner_date = str(review.get("owner_date") or "")[:10]
        matches.extend({
            **row,
            "review_owner_date": owner_date,
        } for row in review.get("match_results") or [])
        handicap_results.extend({
            **row,
            "review_owner_date": owner_date,
        } for row in review.get("handicap_results") or [])
        two_option_results.extend({
            **row,
            "review_owner_date": owner_date,
        } for row in review.get("two_option_results") or [])
        draw_radar_results.extend({
            **row,
            "review_owner_date": owner_date,
        } for row in review.get("draw_radar_results") or [])
        combos.extend(review.get("combo_results") or [])
        conflicts.extend(review.get("conflicts") or [])
    labels = sorted({
        row.get("selection") for row in matches
        if row.get("selection") in SUPPORTED_SELECTIONS
    })
    history_rows_by_key = {}
    for row in matches + handicap_results:
        selection = str(row.get("selection") or "")
        if selection not in {"平局", "让平"}:
            continue
        key = (str(row.get("match_id") or ""), selection)
        history_rows_by_key.setdefault(key, row)
    history_rows = list(history_rows_by_key.values())
    return {
        "primary_source": "fae-daily-ai",
        "reviewed_days": sum(
            1 for review in rows
            if ((review.get("summary") or {}).get("singles") or {}).get(
                "settled", 0
            )
        ),
        "singles": summarize_ai_settled(matches),
        "handicap": summarize_ai_settled(handicap_results),
        "two_option": {
            "overall": summarize_two_option(two_option_results),
            "main": summarize_two_option([
                row for row in two_option_results
                if row.get("result_type") == "two_option_main"
            ]),
            "handicap": summarize_two_option([
                row for row in two_option_results
                if row.get("result_type") == "two_option_handicap"
            ]),
            "by_pair": {
                pair: summarize_two_option([
                    row for row in two_option_results
                    if row.get("pair_key") == pair
                ])
                for pair in sorted({
                    str(row.get("pair_key") or "")
                    for row in two_option_results
                    if row.get("pair_key")
                })
            },
        },
        "draw_radar": {
            "overall": summarize_ai_settled(draw_radar_results),
            "ordinary_draw": summarize_ai_settled([
                row for row in draw_radar_results
                if row.get("selection") == "平局"
            ]),
            "handicap_draw": summarize_ai_settled([
                row for row in draw_radar_results
                if row.get("selection") == "让平"
            ]),
            "core": summarize_ai_settled([
                row for row in draw_radar_results
                if row.get("tier") == "core"
            ]),
            "watch": summarize_ai_settled([
                row for row in draw_radar_results
                if row.get("tier") == "watch"
            ]),
        },
        "handicap_by_selection": {
            label: summarize_ai_settled([
                row for row in handicap_results
                if row.get("selection") == label
            ])
            for label in ("让胜", "让平", "让负")
        },
        "by_selection": {
            label: summarize_ai_settled([
                row for row in matches if row.get("selection") == label
            ])
            for label in labels
        },
        "by_rating": {
            bucket: summarize_ai_settled([
                row for row in matches if row.get("rating_bucket") == bucket
            ])
            for bucket in ("4.5+", "4.0", "3.5", "<3.5")
        },
        "combos": summarize_ai_settled(combos),
        "by_play": {
            play: summarize_ai_settled([
                row for row in combos if row.get("play") == play
            ])
            for play in ("2串1", "3串1")
        },
        "guardrail_conflicts": len(conflicts),
        "history_calibration": {
            "overall": summarize_history_calibration(history_rows),
            "ordinary_draw": summarize_history_calibration([
                row for row in history_rows
                if row.get("selection") == "平局"
            ]),
            "handicap_draw": summarize_history_calibration([
                row for row in history_rows
                if row.get("selection") == "让平"
            ]),
        },
        "strategy_weights": strategy_weights or {},
    }
