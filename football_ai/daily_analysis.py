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


DAILY_PROMPT_VERSION = "five-market-daily-v13-historical-market-rules"

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


def _clean_handicap(value: Any) -> str:
    return re.sub(r"(?:[↑↓]|升|降)+$", "", str(value or "").strip())


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
) -> Dict[str, Any]:
    """Create a compact, auditable input for the daily Ark request."""
    analysis = (fae_result or {}).get("analysis") or {}
    core = (fae_result or {}).get("core") or {}
    initial_total = _number(match.get("ou_initial_total"))
    current_total = _number(match.get("ou_current_total"))
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
    return {
        "match_id": str(match.get("match_id") or ""),
        "match_number": match.get("match_number") or match.get("round_id"),
        "league": match.get("league"),
        "match_time": match.get("match_time"),
        "home_team": match.get("home_team"),
        "away_team": match.get("away_team"),
        "rank": {
            "home": available_rank("home"),
            "away": available_rank("away"),
        },
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
        "current_asian_risk": classify_asian_risk_patterns(match),
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
    ) -> str:
        """Stable cache key for one day's exact market snapshot."""
        rows = sorted(
            [dict(item) for item in match_inputs if item.get("match_id")],
            key=lambda item: str(item.get("match_id") or ""),
        )
        return sha256(json.dumps(
            {
                "date": str(owner_date)[:10],
                "prompt_version": DAILY_PROMPT_VERSION,
                "model": self.client.model,
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
    ) -> Dict[str, Any]:
        rows = [dict(item) for item in match_inputs if item.get("match_id")]
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
            owner_date, rows, review_memory=memory
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
            "大小球跳动达到0.75或以上时优先标记数据异常，不得据此强推方向。",
            "不得伪造近期状态、伤停、首发、天气、战意和赛程；输入缺失必须明确说明。",
            "fundamentals来自500赛前页：recent、history、team_rankings、future可作基本面证据；lineups.status=predicted仅表示预计阵容，禁止称为官方首发；injuries.status=no_listed_players仅表示页面未列出球员，禁止称为确认无伤停。",
            "fundamentals.cache_status=stale时代表刷新失败后的过期缓存，只能低权重引用并必须提示时效风险。",
            "历史复盘记忆只用于提醒曾经出现的误判和风险，不是当前比赛事实，不得据此直接推荐。",
            "联赛历史画像来自当前比赛日期之前的完场数据并带时间衰减；只允许把eligible_for_adjustment=true且分段样本充足的内容作为低到中权重基线。",
            "联赛画像中的命中率、让平率、进球率是历史条件频率，不是真实胜率；不得单独据此推荐，必须与当天五项市场证据一致。",
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
            "historical_goal_margin_model将普通平局定义为0球分差，将让平定义为当前竞彩让球数对应的精确净胜球差；两种玩法必须分开引用。仅eligible_for_adjustment=true且effective_sample达标时允许参与校准。",
            "historical_odds_rules是固定历史回放的有限修正规则；只能引用matched_rule_ids中已命中项及其sample、hit_rate、market_probability和adjustment_pp，不得写成必出规律。",
            "主队+1且让平赔2.70-3.19对应客队恰好赢1球；最低欧赔1.70-1.89只作用于普通平局，两者不得混用。",
            "引用相似历史模型时必须同时比较market_probability、blended_probability、odds与value_edge；它以市场为先验且不是因果规律，不得写成必出或真实胜率。",
            "market_surprise是明确热门方未赢球的历史频率，必须拆分热门打平和弱方爆冷；仅当当前赔率分段样本不少于20时用于降低热门置信度或增加防选，禁止单独据此反买。",
            "current_asian_risk只描述本场赛前水位结构；只有联赛画像中相同asian_risk_patterns模式样本不少于20时才能辅助降级或增加防选，禁止把市场预警写成赛果真实原因。",
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
        secondary_play = cls._secondary_play(
            source,
            effective_primary_play,
            None if guard.get("triggered") else generated.get("secondary_play"),
        )
        verdict = cls._text(
            generated.get("verdict"),
            (
                "火山全日研判未返回本场完整内容，暂时保留FAE核心结果。"
                if not has_model_output else ""
            ),
            900,
        )
        if guard.get("triggered"):
            verdict = cls._text(
                f"{guard['reason']}。以下保留原始AI说明供审计：{verdict}",
                "",
                1100,
            )
        risks = list(dict.fromkeys(
            cls._list(generated.get("risks"), 5, 220)
            + ([guard["reason"]] if guard.get("triggered") else [])
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

        minimum_probability = 26 if selection == "平局" else 23
        core = bool(
            history_eligible
            and probability is not None
            and probability >= minimum_probability
            and odds_value is not None
            and odds_value >= 0
            and score >= 70
            and confidence_score >= 55
            and not profile.get("no_bet")
            and not severe_data_risk
        )
        watch = bool(
            not core
            and profile
            and (
                (
                    history_eligible
                    and score >= 52
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
        current_score = float(
            current.get("bet_score") or current.get("score") or 0
        )
        best_score = float(
            best.get("bet_score") or best.get("score") or 0
        )
        triggered = (
            best_selection != model_selection
            and best_score >= 62
            and (
                not current
                or current.get("no_bet")
                or best_score - current_score >= 12
            )
        )
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
            analysis["model_primary_play"] = model_primary_play
            analysis["primary_play"] = effective_primary_play
            analysis["value_guard"] = value_guard
            analysis["consistency_guard"] = guard
            if guard.get("triggered") or value_guard.get("triggered"):
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
            if value_guard.get("no_bet_only"):
                no_bet_reasons.append("全部玩法均未达到投注门槛")
            if not value_profile:
                no_bet_reasons.append("缺少主选对应的赔率价值数据")
            if risk.get("dangerous"):
                no_bet = True
                no_bet_reasons.append("风险模型判定危险")
            if severe_jump or extreme_water:
                no_bet = True
                no_bet_reasons.append("盘口或水位异常尚未核验")
            if inferred_divergence and bet_score < 70:
                no_bet = True
                no_bet_reasons.append("欧亚背离且投注分不足")
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
            "away_small_win": "客胜",
            "handicap_lose": "让负",
        }
        pools = {}
        for key, items in (result.get("pools") or {}).items():
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
                if float((item.get("analysis") or {}).get("rating") or 0) >= 3.5
                and (item.get("analysis") or {}).get("primary_play") != "观望"
                and not (item.get("analysis") or {}).get("no_bet")
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
            if core_parts else "校准后核心：今天没有达到3.5星的核心场次。"
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
            "handicap_draw", "draw", "away_small_win", "handicap_lose"
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
