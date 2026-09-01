"""Ark-powered post-match diagnosis for immutable FAE daily judgements."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Dict, Iterable, List, Optional

from json_repair import repair_json

from .provider import ArkNarrativeClient, FAEOutputError
from .version import ENGINE_VERSION


AI_REVIEW_PROMPT_VERSION = "fae-deep-review-v13-special-market-audit"
SETTLED_STATUSES = {"hit", "miss", "push"}
LEARNING_SCOPES = {
    "euro",
    "asian",
    "sporttery",
    "total",
    "consistency",
    "risk",
    "guardrail",
    "combination",
    "history_calibration",
    "special_markets",
}


def _deterministic_handicap_settlement(
    score: Any,
    handicap: Any,
) -> Dict[str, Any]:
    """Calculate the Sporttery handicap outcome from immutable facts."""
    parsed = re.fullmatch(
        r"\s*(\d+)\s*[:-]\s*(\d+)\s*", str(score or "")
    )
    try:
        handicap_value = float(handicap)
    except (TypeError, ValueError):
        handicap_value = None
    if not parsed or handicap_value is None:
        return {}
    home, away = int(parsed.group(1)), int(parsed.group(2))
    adjusted_home = home + handicap_value
    actual = (
        "让胜" if adjusted_home > away
        else "让平" if adjusted_home == away
        else "让负"
    )
    handicap_text = f"{handicap_value:+g}"
    return {
        "handicap": handicap_value,
        "score": f"{home}:{away}",
        "adjusted_score": f"{adjusted_home:g}:{away}",
        "actual_outcome": actual,
        "explanation": (
            f"主队竞彩让球{handicap_text}，全场{home}:{away}，"
            f"让球后{adjusted_home:g}:{away}，确定性结果为{actual}"
        ),
    }


def _deterministic_ordinary_outcome(score: Any) -> Optional[str]:
    """Return the immutable 1X2 outcome for a finished score."""
    parsed = re.fullmatch(
        r"\s*(\d+)\s*[:-]\s*(\d+)\s*", str(score or "")
    )
    if not parsed:
        return None
    home, away = int(parsed.group(1)), int(parsed.group(2))
    return "主胜" if home > away else "客胜" if home < away else "平局"


def _metric_snapshot(value: Any) -> Dict[str, Any]:
    """Copy only deterministic settlement metrics into the AI audit."""
    source = value if isinstance(value, dict) else {}
    keys = (
        "total", "settled", "hits", "misses", "pushes", "hit_rate",
        "equal_stake", "equal_stake_return", "equal_stake_profit",
        "equal_stake_roi", "total_lines", "settled_lines",
        "pending_lines", "winning_lines", "losing_lines", "stake_units",
        "return_units", "profit_units", "roi",
    )
    return {
        key: (
            int(source.get(key))
            if isinstance(source.get(key), bool) else source.get(key)
        )
        for key in keys if key in source
    }


def _verified_settlement_summary(review: Dict[str, Any]) -> Dict[str, Any]:
    """Expose the program settlement as the only aggregate source of truth."""
    summary = review.get("summary") or {}
    radar = summary.get("draw_radar") or {}
    two_option = summary.get("two_option") or {}
    draw_tickets = summary.get("draw_tickets") or {}
    special = summary.get("special_markets") or {}
    return {
        "main_direction": _metric_snapshot(summary.get("singles")),
        "official_bets": _metric_snapshot(summary.get("official_bets")),
        "handicap_reference": _metric_snapshot(summary.get("handicap")),
        "ordinary_draw": _metric_snapshot(radar.get("ordinary_draw")),
        "handicap_draw": _metric_snapshot(radar.get("handicap_draw")),
        "two_option": _metric_snapshot(two_option.get("overall")),
        "draw_two_three": _metric_snapshot(
            draw_tickets.get("draw-two-three")
        ),
        "draw_two_leg": _metric_snapshot(
            draw_tickets.get("draw-two-leg")
        ),
        "total_goals_primary": _metric_snapshot(
            (special.get("total_goals") or {}).get("primary")
        ),
        "total_goals_two_option": _metric_snapshot(
            (special.get("total_goals") or {}).get("two_option")
        ),
        "half_full_primary": _metric_snapshot(
            (special.get("half_full") or {}).get("primary")
        ),
        "half_full_two_option": _metric_snapshot(
            (special.get("half_full") or {}).get("two_option")
        ),
        "source": "deterministic-program-settlement",
    }


def _verified_settlement_text(value: Dict[str, Any]) -> str:
    labels = (
        ("主选/观察", "main_direction"),
        ("正式投注池", "official_bets"),
        ("普通平", "ordinary_draw"),
        ("让平", "handicap_draw"),
        ("双选", "two_option"),
        ("平/让平3场2、3关", "draw_two_three"),
        ("平/让平二串一", "draw_two_leg"),
        ("总进球首选", "total_goals_primary"),
        ("总进球双选", "total_goals_two_option"),
        ("半全场首选", "half_full_primary"),
        ("半全场双选", "half_full_two_option"),
    )
    parts = []
    for label, key in labels:
        metric = value.get(key) or {}
        settled = metric.get("settled")
        hits = metric.get("hits")
        if settled is None:
            settled = metric.get("settled_lines")
            hits = metric.get("winning_lines")
        if settled in (None, 0) or hits is None:
            continue
        detail = f"{label}{hits}/{settled}"
        roi = metric.get("equal_stake_roi")
        if roi is None:
            roi = metric.get("roi")
        if key in {"two_option", "draw_two_three", "draw_two_leg"} and roi is not None:
            detail += f"，等额ROI{float(roi):+g}%"
        parts.append(detail)
    return (
        "确定性结算：" + "；".join(parts) + "。"
        if parts else "确定性结算：暂无可核验的已结算统计。"
    )


_HANDICAP_RESULT_CLAIM = re.compile(
    r"(?P<prefix>"
    r"(?:竞彩让球|让球)?(?:实际|最终|确定性)?(?:结果|结算)"
    r"\s*(?:是|为|：|:)?\s*"
    r"|(?:实际|最终|确定性)\s*(?:是|为|：|:)?\s*"
    r"|归为\s*|归\s*|判为\s*"
    r")(?P<label>让胜|让平|让负)"
)

_HANDICAP_MULTI_HIT_CLAIM = re.compile(
    r"(?:让胜|让平|让负)(?:\([^)]*\))?\s*"
    r"(?:和|与|、|/)\s*"
    r"(?:让胜|让平|让负)(?:\([^)]*\))?\s*"
    r"(?:同时)?命中"
)

_ORDINARY_DIRECTION_CLAIM = re.compile(
    r"(?P<label>主胜|平局|客胜)"
    r"(?P<middle>方向|判断|选择|预测)?"
    r"(?P<status>命中|未中|失手|正确|错误)"
)


def _sanitize_handicap_result_claim(
    value: Any,
    actual_outcome: Any,
) -> tuple[str, bool]:
    """Replace only explicit result claims that contradict settlement."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    actual = str(actual_outcome or "")
    if actual not in {"让胜", "让平", "让负"} or not text:
        return text, False
    changed = False

    def replace_multi(_: re.Match) -> str:
        nonlocal changed
        changed = True
        return f"确定性让球赛果为{actual}"

    text = _HANDICAP_MULTI_HIT_CLAIM.sub(replace_multi, text)

    def replace(match: re.Match) -> str:
        nonlocal changed
        label = match.group("label")
        if label == actual:
            return match.group(0)
        changed = True
        return f"{match.group('prefix')}{actual}"

    return _HANDICAP_RESULT_CLAIM.sub(replace, text), changed


def _sanitize_ordinary_result_claim(
    value: Any,
    actual_outcome: Any,
) -> tuple[str, bool]:
    """Correct explicit 1X2 hit/miss claims from the immutable score."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    actual = str(actual_outcome or "")
    if actual not in {"主胜", "平局", "客胜"} or not text:
        return text, False
    changed = False

    def replace(match: re.Match) -> str:
        nonlocal changed
        expected = "命中" if match.group("label") == actual else "未中"
        if match.group("status") == expected:
            return match.group(0)
        changed = True
        return (
            f"{match.group('label')}{match.group('middle') or ''}{expected}"
        )

    return _ORDINARY_DIRECTION_CLAIM.sub(replace, text), changed


class FAEAIReviewAnalyzer:
    """Diagnose settled predictions without directly changing live weights."""

    def __init__(self, client: Optional[ArkNarrativeClient] = None):
        self.client = client or ArkNarrativeClient()

    @property
    def configured(self) -> bool:
        return self.client.configured

    def build_input(
        self,
        snapshot: Dict[str, Any],
        review: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build a compact audit input for every finished daily-AI match.

        Official bet ROI still uses hit/miss/push rows only. Deep review must
        audit no-bet and pure observation rows as well, because those decisions
        are part of the model behaviour.
        """
        source_by_id = {
            str(item.get("match_id") or ""): item
            for item in snapshot.get("matches") or []
            if item.get("match_id")
        }
        handicap_by_id = {
            str(item.get("match_id") or ""): item
            for item in review.get("handicap_results") or []
            if item.get("match_id")
        }
        two_options_by_id: Dict[str, List[Dict[str, Any]]] = {}
        for item in review.get("two_option_results") or []:
            match_id = str(item.get("match_id") or "")
            if match_id:
                two_options_by_id.setdefault(match_id, []).append(item)
        radar_by_id: Dict[str, List[Dict[str, Any]]] = {}
        for item in review.get("draw_radar_results") or []:
            match_id = str(item.get("match_id") or "")
            if match_id:
                radar_by_id.setdefault(match_id, []).append(item)
        special_by_id: Dict[str, List[Dict[str, Any]]] = {}
        for item in review.get("special_market_results") or []:
            match_id = str(item.get("match_id") or "")
            if match_id:
                special_by_id.setdefault(match_id, []).append(item)
        matches = []
        for result in review.get("match_results") or []:
            status = result.get("status")
            is_finished_observation = (
                status == "skipped"
                and bool(result.get("result_score"))
            )
            if status not in SETTLED_STATUSES and not is_finished_observation:
                continue
            match_id = str(result.get("match_id") or "")
            source = source_by_id.get(match_id) or {}
            analysis = source.get("analysis") or {}
            input_snapshot = source.get("input_snapshot") or {}
            handicap_result = handicap_by_id.get(match_id) or {}
            sporttery_handicap = input_snapshot.get(
                "sporttery_handicap"
            ) or {}
            handicap_settlement = _deterministic_handicap_settlement(
                result.get("result_score"),
                sporttery_handicap.get("value"),
            )
            matches.append({
                "match_id": match_id,
                "match_number": result.get("match_number"),
                "league": result.get("league"),
                "home_team": result.get("home_team"),
                "away_team": result.get("away_team"),
                "prediction": {
                    "selection": result.get("selection"),
                    "selection_text": result.get("selection_text"),
                    "model_selection": result.get("model_selection"),
                    "rating": result.get("rating"),
                    "odds": result.get("odds"),
                    "no_bet": bool(result.get("no_bet")),
                    "no_bet_reasons": result.get("no_bet_reasons") or [],
                    "decision": analysis.get("decision"),
                    "guardrail_triggered": result.get(
                        "guardrail_triggered"
                    ),
                    "guardrail": result.get("guardrail") or {},
                    "verdict": analysis.get("verdict"),
                    "market_analysis": analysis.get(
                        "market_analysis"
                    ) or {},
                    "evidence": analysis.get("evidence") or [],
                    "risks": analysis.get("risks") or [],
                    "score_candidates": analysis.get(
                        "score_candidates"
                    ) or [],
                },
                "handicap_prediction": {
                    "selection": handicap_result.get("selection"),
                    "selection_text": handicap_result.get("selection_text"),
                    "odds": handicap_result.get("odds"),
                    "handicap": handicap_result.get("handicap"),
                    "status": handicap_result.get("status"),
                    "return": handicap_result.get("return"),
                    "profit": handicap_result.get("profit"),
                } if handicap_result else {},
                "two_option_predictions": [{
                    "market": item.get("market"),
                    "selection": item.get("selection"),
                    "selection_text": item.get("selection_text"),
                    "selections": item.get("selections") or [],
                    "status": item.get("status"),
                    "hit_selection": item.get("hit_selection"),
                    "hit_selection_text": item.get("hit_selection_text"),
                    "result_type": item.get("result_type"),
                } for item in two_options_by_id.get(match_id, [])],
                "draw_radar_predictions": [{
                    "selection": item.get("selection"),
                    "selection_text": item.get("selection_text"),
                    "tier": item.get("tier"),
                    "rating": item.get("rating"),
                    "radar_score": item.get("radar_score"),
                    "probability": item.get("probability"),
                    "market_probability": item.get("market_probability"),
                    "odds": item.get("odds"),
                    "odds_value": item.get("odds_value"),
                    "effective_sample": item.get("effective_sample"),
                    "status": item.get("status"),
                    "return": item.get("return"),
                    "profit": item.get("profit"),
                    "reason": item.get("reason"),
                } for item in radar_by_id.get(match_id, [])],
                "special_market_predictions": [dict(item) for item in (
                    special_by_id.get(match_id, [])
                )],
                "prediction_time_markets": {
                    key: input_snapshot.get(key) or {}
                    for key in (
                        "euro",
                        "asian",
                        "sporttery_handicap",
                        "total",
                        "special_markets",
                    )
                },
                "market_risk_context": {
                    "current_asian_risk": input_snapshot.get(
                        "current_asian_risk"
                    ) or {},
                    "league_history_profile": input_snapshot.get(
                        "league_history_profile"
                    ) or {},
                    "historical_goal_margin_model": input_snapshot.get(
                        "historical_goal_margin_model"
                    ) or {},
                    "historical_calibration": analysis.get(
                        "historical_calibration"
                    ) or {},
                },
                "data_warnings": input_snapshot.get(
                    "data_warnings"
                ) or [],
                "result": {
                    "score": result.get("result_score"),
                    "status": result.get("status"),
                    "observation_status": result.get("observation_status"),
                    "return": result.get("return"),
                    "profit": result.get("profit"),
                    "handicap_settlement": handicap_settlement,
                },
            })
        combinations = [{
            "play": item.get("play"),
            "status": item.get("status"),
            "combined_odds": item.get("combined_odds"),
            "profit": item.get("profit"),
            "reason": item.get("reason"),
            "picks": [{
                "match_id": pick.get("match_id"),
                "match_number": (
                    source_by_id.get(str(pick.get("match_id") or "")) or {}
                ).get("match_number"),
                "selection": pick.get("selection"),
                "selection_text": pick.get("selection_text"),
                "status": pick.get("status"),
                "odds": pick.get("odds"),
            } for pick in item.get("picks") or []],
        } for item in review.get("combo_results") or []
            if item.get("status") in SETTLED_STATUSES]
        for ticket in review.get("draw_ticket_results") or []:
            for line in ticket.get("line_results") or []:
                if line.get("status") not in SETTLED_STATUSES:
                    continue
                combinations.append({
                    "ticket_key": ticket.get("key"),
                    "ticket_title": ticket.get("title"),
                    "play": line.get("play"),
                    "status": line.get("status"),
                    "combined_odds": line.get("combined_odds"),
                    "profit": line.get("profit"),
                    "reason": (
                        f"{ticket.get('title') or '平/让平专项票'}独立结算"
                    ),
                    "picks": [{
                        "match_id": pick.get("match_id"),
                        "match_number": pick.get("match_number"),
                        "selection": pick.get("selection"),
                        "selection_text": pick.get("selection_text"),
                        "status": pick.get("status"),
                        "odds": pick.get("odds"),
                    } for pick in line.get("picks") or []],
                })
        return {
            "owner_date": str(review.get("owner_date") or "")[:10],
            "run_id": review.get("run_id"),
            "engine_version": review.get("engine_version"),
            "pre_match_model": snapshot.get("model"),
            "review_summary": review.get("summary") or {},
            "verified_settlement_summary": (
                _verified_settlement_summary(review)
            ),
            "matches": sorted(
                matches, key=lambda item: str(item.get("match_id") or "")
            ),
            "combinations": combinations,
        }

    def input_hash(
        self,
        snapshot: Dict[str, Any],
        review: Dict[str, Any],
    ) -> str:
        payload = self.build_input(snapshot, review)
        return sha256(json.dumps(
            {
                "prompt_version": AI_REVIEW_PROMPT_VERSION,
                "model": self.client.model,
                "input": payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")).hexdigest()

    def analyze(
        self,
        snapshot: Dict[str, Any],
        review: Dict[str, Any],
    ) -> Dict[str, Any]:
        payload = self.build_input(snapshot, review)
        matches = payload.get("matches") or []
        if not matches:
            raise FAEOutputError("尚无已结算比赛，无法运行 AI 深度复盘")
        if not self.configured:
            raise FAEOutputError("火山方舟尚未配置，无法运行 AI 深度复盘")
        prompt = self._build_prompt(payload)
        text, metadata = self.client.generate(prompt)
        parsed = self._extract_json(text)
        normalized = self._normalize(
            parsed,
            matches,
            payload.get("verified_settlement_summary") or {},
        )
        normalized = self.humanize_review_match_ids(normalized, matches)
        return {
            "status": "completed",
            "input_hash": self.input_hash(snapshot, review),
            "model": self.client.model,
            "provider": "volcengine-ark",
            "prompt_version": AI_REVIEW_PROMPT_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "coverage": {
                "settled_matches": len(matches),
                "reviewed_matches": len(matches),
                "no_bet_matches": sum(
                    1 for item in matches
                    if ((item.get("prediction") or {}).get("no_bet"))
                ),
                "official_bet_matches": sum(
                    1 for item in matches
                    if not ((item.get("prediction") or {}).get("no_bet"))
                    and ((item.get("result") or {}).get("status"))
                    in SETTLED_STATUSES
                ),
                "settled_handicap_references": int(
                    (((review.get("summary") or {}).get("handicap") or {})
                     .get("settled") or 0)
                ),
                "settled_draw_radar_rows": int(
                    (((((review.get("summary") or {}).get("draw_radar") or {})
                       .get("overall") or {}).get("settled")) or 0)
                ),
                "settled_special_market_rows": sum(
                    1 for row in review.get("special_market_results") or []
                    if row.get("status") in SETTLED_STATUSES
                ),
                "total_matches": len(snapshot.get("matches") or []),
                "review_completed": bool(review.get("completed")),
            },
            **normalized,
            "provider_meta": metadata,
            "governance": {
                "mode": "candidate-only",
                "formal_weights_changed": False,
                "note": (
                    "AI 只提出复盘与调权候选；正式 Skill 参数仍需历史"
                    "样本验证并经过现有发布流程。"
                ),
            },
        }

    def _build_prompt(self, payload: Dict[str, Any]) -> str:
        schema = {
            "summary": {
                "conclusion": "80到220字的复盘结论",
                "what_worked": ["有效的赛前判断，最多5条"],
                "what_failed": ["需要修正的判断，最多5条"],
                "risk_patterns": ["重复出现的风险模式，最多5条"],
                "next_actions": ["下一轮可验证的改进方向，最多5条"],
            },
            "market_lessons": {
                "euro": "欧赔复核",
                "asian": "亚盘复核",
                "sporttery": "竞彩让球复核",
                "total": "大小球复核",
                "special_markets": "总进球与半全场独立玩法复核",
                "consistency": "市场一致性复核",
            },
            "matches": [{
                "match_id": "必须来自输入",
                "verdict": "判断有效/命中但过程有风险/判断失误/走盘/不下注合理/不下注过保守/观望复盘",
                "handicap_verdict": "让球参考命中/让球参考未中/让球走盘/未推荐",
                "two_option_verdict": "双选覆盖命中/双选覆盖未中/双选未形成",
                "diagnosis": "60到180字",
                "correct_signals": ["赛前已记录且有效的信号"],
                "missed_signals": ["赛前已记录但误读或忽略的信号"],
                "data_quality_issues": ["数据缺失或异常"],
                "counterfactual": "下次遇到同类结构应如何处理",
                "rule_tags": ["最多5个简短标签"],
            }],
            "combination_review": {
                "conclusion": "2、3关组合复核",
                "good_choices": ["有效选择"],
                "bad_choices": ["拖累组合的选择"],
                "construction_advice": ["下一次组单建议"],
            },
            "learning_candidates": [{
                "scope": (
                    "euro/asian/sporttery/total/consistency/risk/"
                    "guardrail/combination"
                    "/history_calibration"
                    "/special_markets"
                ),
                "target": "需要验证的规则或信号",
                "action": "increase/decrease/hold",
                "delta": "建议幅度，-0.15到0.15",
                "confidence": "low/medium/high",
                "minimum_samples": "至少需要的历史样本数，不低于10",
                "reason": "为什么提出候选",
                "evidence_match_ids": ["必须来自输入"],
            }],
        }
        rules = [
            "这是赛后审计任务，不输出隐藏思维链，只输出合法JSON。",
            "只能使用输入中保存的赛前盘口、赛前研判和最终赛果，不得编造临场盘口、伤停、首发、xG或比赛过程。",
            "严格区分结果好坏与决策质量：命中不必然代表过程正确，未中也不必然代表规则错误。",
            "不得把最终比分反推成赛前必然信号，所有有效或遗漏信号必须能在输入的赛前记录中找到。",
            "固定复核欧赔、亚盘真实升深、竞彩让球、大小球和市场一致性五项。",
            "升降属于走势而非盘口名；让平必须结合输入中的具体让球数解释。",
            "必须分别复核正式主选与handicap_prediction中的竞彩让球参考；普通主胜命中不能掩盖让胜、让平或让负未中。",
            "必须单独复核two_option_predictions：主选+防选或让球双选任一命中即为覆盖命中；这只评估方向覆盖，不得包装成单注ROI。",
            "review_summary.two_option.overall已经按比赛去重，并额外提供equal_stake_roi；不得使用raw_rows重复计算同一场，也不得只报覆盖率而忽略等额双选收益。",
            "所有总场数、命中数、命中率和ROI只能逐字引用verified_settlement_summary；禁止自行计数。该字段与模型判断冲突时，以程序确定性统计为准。",
            "所有已完赛比赛都必须复盘，包括prediction.no_bet=true的不下注比赛和selection=观望的观察比赛；不下注不是跳过，而是风险控制决策，需要判断合理还是过保守。",
            "prediction.no_bet=true时，result.status通常为skipped，result.observation_status表示如果按赛前观察方向下注会命中、未中或走盘；若observation_status=miss，优先复核不下注是否避免错误，若observation_status=hit，必须复核是否过度保守。",
            "selection=观望且没有具体下注方向时，不能按命中率评价，只复核是否正确识别了数据不足、盘口冲突或风险。",
            "必须单独复核draw_radar_predictions：核心候选与观察候选分开统计；观察命中不能事后包装成正式推荐，核心未中也必须记录。",
            "必须把平/让平3场2、3关与平/让平二串一当作两张独立票复核；3场2、3关按3个2串1和1个3串1共4注结算，禁止与独立二串一合并命中率或ROI。",
            "必须单独复核special_market_predictions中的总进球和半全场：primary_status统计首选，coverage_status统计首选+次选覆盖；半全场没有半场比分时只能标记未结算，不得用全场比分猜测半场结果。",
            "竞彩让球必须严格按保存的让球数计算：主队-1时，赢2球以上为让胜、恰好赢1球为让平、其余为让负；确定性结算结果优先于文字推断。",
            "每场result.handicap_settlement.actual_outcome是程序计算的唯一让球赛果，禁止自行重算或改写；诊断中提到让球赛果时必须逐字使用该字段。",
            "market_risk_context中的水位模式仅表示赛前风险结构；可以检验该预警是否有效，但不得把退盘、升水或欧亚背离直接写成比赛失利的真实原因。",
            "必须复核historical_goal_margin_model：普通平局只核对0球分差，让平只核对赛前竞彩让球数对应的精确净胜球差，严禁用普通平局赛果替代让平结算。",
            "若historical_calibration.applied=true，要说明它相对core_probability是降低还是提高了概率，以及本场结果是否支持该次校准；单场支持或反对都不得直接升级为规律。",
            "历史相似模型的候选调权必须使用history_calibration范围，至少要求跨日期且不少于30个有效样本，并以Brier Score、对数损失和模拟ROI的样本外结果决定是否发布。",
            "若盘口无明显预警，只能说明现有赛前市场数据无法解释赛果；没有xG、红牌、射门等过程数据时必须明确未知。",
            "调权只能作为候选，单日样本不得直接修改正式权重；每个候选必须给出至少10个样本的验证门槛。",
            "match_id仅允许用于JSON关联字段；结论、做对了什么、需要修正、市场复核、逐场诊断、调权候选和组合复核等所有自然语言必须使用match_number（如周四201），严禁展示原始比赛ID。",
            "输入中的每场已完赛比赛必须在matches中恰好出现一次，包括不下注和观望。",
        ]
        return "\n\n".join([
            f"你是 Football AI Engine v{ENGINE_VERSION} 的 AI 深度复盘层。",
            "# 审计规则\n" + "\n".join(f"- {item}" for item in rules),
            "# 输出JSON结构\n" + json.dumps(
                schema, ensure_ascii=False, indent=2
            ),
            "# 赛前快照与确定性结算\n" + json.dumps(
                payload, ensure_ascii=False, indent=2, default=str
            ),
        ])

    @classmethod
    def _normalize(
        cls,
        parsed: Dict[str, Any],
        source_matches: Iterable[Dict[str, Any]],
        verified_settlement_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        source_rows = list(source_matches)
        source_by_id = {
            str(item.get("match_id") or ""): item for item in source_rows
        }
        allowed_ids = {
            str(item.get("match_id") or "") for item in source_rows
        }
        generated_by_id = {
            str(item.get("match_id") or ""): item
            for item in parsed.get("matches") or []
            if isinstance(item, dict)
            and str(item.get("match_id") or "") in allowed_ids
        }
        normalized_matches = []
        for source in source_rows:
            match_id = str(source.get("match_id") or "")
            generated = generated_by_id.get(match_id) or {}
            result_status = ((source.get("result") or {}).get("status"))
            observation_status = (
                (source.get("result") or {}).get("observation_status")
            )
            no_bet = bool((source.get("prediction") or {}).get("no_bet"))
            handicap_prediction = source.get("handicap_prediction") or {}
            handicap_status = handicap_prediction.get("status")
            handicap_settlement = (
                (source.get("result") or {}).get("handicap_settlement") or {}
            )
            actual_handicap_outcome = handicap_settlement.get(
                "actual_outcome"
            )
            actual_ordinary_outcome = _deterministic_ordinary_outcome(
                (source.get("result") or {}).get("score")
            )
            two_options = source.get("two_option_predictions") or []
            two_option_hit = any(
                item.get("status") == "hit" for item in two_options
            )
            two_option_settled = any(
                item.get("status") in SETTLED_STATUSES
                for item in two_options
            )
            fallback_two_option_verdict = (
                "双选覆盖命中" if two_option_hit
                else "双选覆盖未中" if two_option_settled
                else "双选未形成"
            )
            if no_bet:
                fallback_verdict = {
                    "hit": "不下注过保守",
                    "miss": "不下注合理",
                    "push": "走盘",
                }.get(observation_status, "不下注合理")
            elif result_status == "skipped":
                fallback_verdict = "观望复盘"
            else:
                fallback_verdict = {
                    "hit": "判断有效",
                    "miss": "判断失误",
                    "push": "走盘",
                }.get(result_status, "观望复盘")
            verdict = cls._text(
                generated.get("verdict"), fallback_verdict, 30
            )
            if verdict not in {
                "判断有效",
                "命中但过程有风险",
                "判断失误",
                "走盘",
                "不下注合理",
                "不下注过保守",
                "观望复盘",
            }:
                verdict = fallback_verdict
            def sanitized_text(value: Any, fallback: str, limit: int):
                clean, handicap_changed = _sanitize_handicap_result_claim(
                    cls._text(value, fallback, limit),
                    actual_handicap_outcome,
                )
                clean, ordinary_changed = _sanitize_ordinary_result_claim(
                    clean, actual_ordinary_outcome
                )
                return clean, handicap_changed or ordinary_changed

            diagnosis, diagnosis_corrected = sanitized_text(
                generated.get("diagnosis"),
                "模型未返回完整诊断，请以确定性结算结果为准。",
                600,
            )

            def sanitized_list(value: Any) -> tuple[List[str], bool]:
                values = cls._list(value, 6, 220)
                result_values = []
                corrected = False
                for entry in values:
                    clean, changed = sanitized_text(
                        entry, "", 220
                    )
                    result_values.append(clean)
                    corrected = corrected or changed
                return result_values, corrected

            correct_signals, correct_corrected = sanitized_list(
                generated.get("correct_signals")
            )
            missed_signals, missed_corrected = sanitized_list(
                generated.get("missed_signals")
            )
            counterfactual, counterfactual_corrected = sanitized_text(
                generated.get("counterfactual"), "", 500
            )
            semantic_corrected = bool(
                diagnosis_corrected
                or correct_corrected
                or missed_corrected
                or counterfactual_corrected
            )
            if semantic_corrected:
                diagnosis = (
                    f"{handicap_settlement.get('explanation')}。"
                    f"{diagnosis}"
                )[:600]
            normalized_matches.append({
                "match_id": match_id,
                "match_number": source.get("match_number"),
                "home_team": source.get("home_team"),
                "away_team": source.get("away_team"),
                "selection_text": (
                    (source.get("prediction") or {}).get("selection_text")
                ),
                "result_score": (source.get("result") or {}).get("score"),
                "result_status": result_status,
                "handicap_selection_text": handicap_prediction.get(
                    "selection_text"
                ),
                "handicap_result_status": handicap_status,
                "actual_handicap_outcome": actual_handicap_outcome,
                "actual_ordinary_outcome": actual_ordinary_outcome,
                "handicap_settlement": handicap_settlement,
                "two_option_predictions": two_options,
                "special_market_predictions": source.get(
                    "special_market_predictions"
                ) or [],
                # Coverage is a deterministic settlement; never allow the
                # narrative model to override it.
                "two_option_verdict": fallback_two_option_verdict,
                "no_bet": no_bet,
                "no_bet_reasons": (
                    (source.get("prediction") or {}).get("no_bet_reasons")
                    or []
                ),
                "observation_status": observation_status,
                "handicap_verdict": {
                    "hit": "让球参考命中",
                    "miss": "让球参考未中",
                    "push": "让球走盘",
                }.get(handicap_status, "未推荐"),
                "verdict": verdict,
                "diagnosis": diagnosis,
                "correct_signals": correct_signals,
                "missed_signals": missed_signals,
                "data_quality_issues": cls._list(
                    generated.get("data_quality_issues"), 6, 220
                ),
                "counterfactual": counterfactual,
                "rule_tags": cls._list(
                    generated.get("rule_tags"), 5, 60
                ),
                "semantic_guard": {
                    "triggered": semantic_corrected,
                    "actual_handicap_outcome": actual_handicap_outcome,
                    "actual_ordinary_outcome": actual_ordinary_outcome,
                    "reason": (
                        "大模型文字与确定性胜平负/竞彩让球结算冲突，已程序校正"
                        if semantic_corrected else None
                    ),
                },
            })

        semantic_conflict_ids = {
            str(item.get("match_id") or "")
            for item in normalized_matches
            if (item.get("semantic_guard") or {}).get("triggered")
        }
        blocked_learning_candidates = 0

        def sanitize_global(value: Any) -> str:
            text = re.sub(r"\s+", " ", str(value or "")).strip()
            # Validate one natural-language clause at a time.  Applying one
            # match's settlement to a paragraph containing several fixtures
            # could itself corrupt valid text, so ambiguous clauses are left
            # untouched and the Sporttery aggregate is generated separately.
            chunks = re.split(r"([。；，！？\n])", text)
            for index in range(0, len(chunks), 2):
                chunk = chunks[index]
                matched_sources = []
                for source in source_rows:
                    identifiers = {
                        str(source.get("match_number") or ""),
                        str(source.get("home_team") or ""),
                        str(source.get("away_team") or ""),
                    }
                    if any(value and value in chunk for value in identifiers):
                        matched_sources.append(source)
                if len(matched_sources) != 1:
                    continue
                source = matched_sources[0]
                settlement = (
                    (source.get("result") or {})
                    .get("handicap_settlement") or {}
                )
                chunk, _ = _sanitize_handicap_result_claim(
                    chunk, settlement.get("actual_outcome")
                )
                chunk, _ = _sanitize_ordinary_result_claim(
                    chunk,
                    _deterministic_ordinary_outcome(
                        (source.get("result") or {}).get("score")
                    ),
                )
                chunks[index] = chunk
            return "".join(chunks)

        def deterministic_sporttery_lesson() -> str:
            counts = {"让胜": 0, "让平": 0, "让负": 0}
            examples = {"让胜": [], "让平": [], "让负": []}
            for source in source_rows:
                settlement = (
                    (source.get("result") or {})
                    .get("handicap_settlement") or {}
                )
                outcome = str(settlement.get("actual_outcome") or "")
                if outcome not in counts:
                    continue
                counts[outcome] += 1
                if len(examples[outcome]) < 4:
                    examples[outcome].append(
                        str(source.get("match_number") or "")
                    )
            parts = [
                f"确定性竞彩让球结算：让胜{counts['让胜']}场、"
                f"让平{counts['让平']}场、让负{counts['让负']}场。"
            ]
            for outcome in ("让平", "让胜", "让负"):
                labels = "、".join(value for value in examples[outcome] if value)
                if labels:
                    parts.append(f"{outcome}示例：{labels}。")
            parts.append(
                "该统计只描述最终赛果；规则学习仍须结合赛前盘口并通过历史样本验证。"
            )
            return "".join(parts)

        def sanitize_global_list(
            value: Any, limit: int, item_limit: int
        ) -> List[str]:
            return [
                sanitize_global(item)[:item_limit]
                for item in cls._list(value, limit, item_limit)
            ]

        summary = (
            parsed.get("summary")
            if isinstance(parsed.get("summary"), dict) else {}
        )
        verified_summary = dict(verified_settlement_summary or {})
        verified_text = _verified_settlement_text(verified_summary)
        ai_conclusion = sanitize_global(cls._text(
            summary.get("conclusion"), "", 1000
        ))
        # Aggregate arithmetic belongs to the deterministic review engine.
        # Keep only qualitative model clauses after the verified headline so
        # a narrative miscount can never become the displayed daily result.
        qualitative_clauses = [
            clause.strip()
            for clause in re.split(r"[。；\n]+", ai_conclusion)
            if clause.strip()
            and not re.search(r"\d+\s*(?:/|场|%|次)", clause)
        ]
        qualitative_conclusion = "；".join(qualitative_clauses[:3])
        verified_conclusion = (
            f"{verified_text} AI诊断：{qualitative_conclusion}。"
            if qualitative_conclusion else verified_text
        )
        lessons = (
            parsed.get("market_lessons")
            if isinstance(parsed.get("market_lessons"), dict) else {}
        )
        combo = (
            parsed.get("combination_review")
            if isinstance(parsed.get("combination_review"), dict) else {}
        )
        candidates = []
        for item in parsed.get("learning_candidates") or []:
            if not isinstance(item, dict):
                continue
            scope = str(item.get("scope") or "").strip().lower()
            if scope not in LEARNING_SCOPES:
                continue
            action = str(item.get("action") or "hold").strip().lower()
            if action not in {"increase", "decrease", "hold"}:
                action = "hold"
            try:
                delta = float(item.get("delta") or 0)
            except (TypeError, ValueError):
                delta = 0.0
            delta = round(max(-0.15, min(0.15, delta)), 3)
            if action == "increase" and delta < 0:
                delta = abs(delta)
            elif action == "decrease" and delta > 0:
                delta = -delta
            elif action == "hold":
                delta = 0.0
            confidence = str(
                item.get("confidence") or "low"
            ).strip().lower()
            if confidence not in {"low", "medium", "high"}:
                confidence = "low"
            try:
                minimum_samples = max(
                    10, min(500, int(item.get("minimum_samples") or 10))
                )
            except (TypeError, ValueError):
                minimum_samples = 10
            evidence_ids = list(dict.fromkeys(
                str(value) for value in (
                    item.get("evidence_match_ids") or []
                )
                if str(value) in allowed_ids
            ))[:12]
            if semantic_conflict_ids.intersection(evidence_ids):
                blocked_learning_candidates += 1
                continue
            target = cls._text(item.get("target"), "", 160)
            reason = cls._text(item.get("reason"), "", 400)
            candidate_semantic_conflict = False
            if len(evidence_ids) == 1:
                evidence_source = source_by_id.get(evidence_ids[0]) or {}
                settlement = (
                    (evidence_source.get("result") or {})
                    .get("handicap_settlement") or {}
                )
                target, target_handicap_changed = _sanitize_handicap_result_claim(
                    target, settlement.get("actual_outcome")
                )
                reason, reason_handicap_changed = _sanitize_handicap_result_claim(
                    reason, settlement.get("actual_outcome")
                )
                ordinary_outcome = _deterministic_ordinary_outcome(
                    (evidence_source.get("result") or {}).get("score")
                )
                target, target_ordinary_changed = _sanitize_ordinary_result_claim(
                    target, ordinary_outcome
                )
                reason, reason_ordinary_changed = _sanitize_ordinary_result_claim(
                    reason, ordinary_outcome
                )
                candidate_semantic_conflict = any((
                    target_handicap_changed,
                    reason_handicap_changed,
                    target_ordinary_changed,
                    reason_ordinary_changed,
                ))
            if candidate_semantic_conflict:
                blocked_learning_candidates += 1
                continue
            if not target or not reason:
                continue
            candidates.append({
                "scope": scope,
                "target": target,
                "action": action,
                "delta": delta,
                "confidence": confidence,
                "minimum_samples": minimum_samples,
                "reason": reason,
                "evidence_match_ids": evidence_ids,
                "status": "proposed",
            })

        return {
            "summary": {
                "conclusion": verified_conclusion,
                "what_worked": sanitize_global_list(
                    summary.get("what_worked"), 6, 260
                ),
                "what_failed": sanitize_global_list(
                    summary.get("what_failed"), 6, 260
                ),
                "risk_patterns": sanitize_global_list(
                    summary.get("risk_patterns"), 6, 260
                ),
                "next_actions": sanitize_global_list(
                    summary.get("next_actions"), 6, 260
                ),
            },
            "market_lessons": {
                key: (
                    deterministic_sporttery_lesson()
                    if key == "sporttery"
                    else sanitize_global(cls._text(
                        lessons.get(key), "本次样本不足，暂不调整。", 500
                    ))
                )
                for key in (
                    "euro", "asian", "sporttery", "total",
                    "special_markets", "consistency"
                )
            },
            "matches": normalized_matches,
            "combination_review": {
                "conclusion": sanitize_global(cls._text(
                    combo.get("conclusion"), "暂无已结算组合可复核。", 600
                )),
                "good_choices": sanitize_global_list(
                    combo.get("good_choices"), 6, 220
                ),
                "bad_choices": sanitize_global_list(
                    combo.get("bad_choices"), 6, 220
                ),
                "construction_advice": sanitize_global_list(
                    combo.get("construction_advice"), 6, 220
                ),
            },
            "learning_candidates": candidates[:12],
            "semantic_guard": {
                "triggered": bool(semantic_conflict_ids),
                "corrected_match_ids": sorted(semantic_conflict_ids),
                "blocked_learning_candidates": blocked_learning_candidates,
            },
            "verified_settlement_summary": verified_summary,
        }

    @classmethod
    def humanize_review_match_ids(
        cls,
        review: Dict[str, Any],
        source_matches: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Replace raw match IDs in prose while preserving identifier fields."""
        sources = list(source_matches or review.get("matches") or [])
        labels = {
            str(item.get("match_id") or ""): str(
                item.get("match_number") or item.get("match_id") or ""
            )
            for item in sources
            if item.get("match_id")
        }

        def humanize(value: Any) -> str:
            text = str(value or "")
            protected = {}
            for index, label in enumerate(dict.fromkeys(labels.values())):
                if not label:
                    continue
                token = f"__FAE_MATCH_LABEL_{index}__"
                if label in text:
                    text = text.replace(label, token)
                    protected[token] = label
            for match_id in sorted(labels, key=len, reverse=True):
                label = labels[match_id]
                if not match_id or not label or match_id == label:
                    continue
                text = re.sub(
                    rf"(?<!\d){re.escape(match_id)}(?!\d)", label, text
                )
            for token, label in protected.items():
                text = text.replace(token, label)
            return text

        def text_list(value: Any) -> List[str]:
            return [humanize(item) for item in value or []]

        result = dict(review or {})
        summary = dict(result.get("summary") or {})
        summary["conclusion"] = humanize(summary.get("conclusion"))
        for key in (
            "what_worked", "what_failed", "risk_patterns", "next_actions"
        ):
            summary[key] = text_list(summary.get(key))
        result["summary"] = summary
        result["market_lessons"] = {
            key: humanize(value)
            for key, value in (result.get("market_lessons") or {}).items()
        }
        result["matches"] = [{
            **item,
            "diagnosis": humanize(item.get("diagnosis")),
            "correct_signals": text_list(item.get("correct_signals")),
            "missed_signals": text_list(item.get("missed_signals")),
            "data_quality_issues": text_list(
                item.get("data_quality_issues")
            ),
            "counterfactual": humanize(item.get("counterfactual")),
        } for item in result.get("matches") or []]
        combination = dict(result.get("combination_review") or {})
        combination["conclusion"] = humanize(
            combination.get("conclusion")
        )
        for key in (
            "good_choices", "bad_choices", "construction_advice"
        ):
            combination[key] = text_list(combination.get(key))
        result["combination_review"] = combination
        result["learning_candidates"] = [{
            **item,
            "target": humanize(item.get("target")),
            "reason": humanize(item.get("reason")),
        } for item in result.get("learning_candidates") or []]
        return result

    @staticmethod
    def _extract_json(raw_text: str) -> Dict[str, Any]:
        text = str(raw_text or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise FAEOutputError("AI 深度复盘输出不是JSON对象")
        source = text[start:end + 1]
        try:
            data = json.loads(source)
        except json.JSONDecodeError as exc:
            try:
                data = repair_json(source, return_objects=True)
            except Exception as repair_exc:
                raise FAEOutputError(
                    f"AI 深度复盘JSON解析失败: {exc.msg}"
                ) from repair_exc
        if not isinstance(data, dict):
            raise FAEOutputError("AI 深度复盘输出必须是JSON对象")
        return data

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
