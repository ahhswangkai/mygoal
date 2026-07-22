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


AI_REVIEW_PROMPT_VERSION = "fae-deep-review-v5-goal-margin-calibration"
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
}


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
        """Build a compact audit input containing only settled predictions."""
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
        matches = []
        for result in review.get("match_results") or []:
            if result.get("status") not in SETTLED_STATUSES:
                continue
            match_id = str(result.get("match_id") or "")
            source = source_by_id.get(match_id) or {}
            analysis = source.get("analysis") or {}
            input_snapshot = source.get("input_snapshot") or {}
            handicap_result = handicap_by_id.get(match_id) or {}
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
                "prediction_time_markets": {
                    key: input_snapshot.get(key) or {}
                    for key in (
                        "euro",
                        "asian",
                        "sporttery_handicap",
                        "total",
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
                    "return": result.get("return"),
                    "profit": result.get("profit"),
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
        return {
            "owner_date": str(review.get("owner_date") or "")[:10],
            "run_id": review.get("run_id"),
            "engine_version": review.get("engine_version"),
            "pre_match_model": snapshot.get("model"),
            "review_summary": review.get("summary") or {},
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
        normalized = self._normalize(parsed, matches)
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
                "settled_handicap_references": int(
                    (((review.get("summary") or {}).get("handicap") or {})
                     .get("settled") or 0)
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
                "consistency": "市场一致性复核",
            },
            "matches": [{
                "match_id": "必须来自输入",
                "verdict": "判断有效/命中但过程有风险/判断失误/走盘",
                "handicap_verdict": "让球参考命中/让球参考未中/让球走盘/未推荐",
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
            "竞彩让球必须严格按保存的让球数计算：主队-1时，赢2球以上为让胜、恰好赢1球为让平、其余为让负；确定性结算结果优先于文字推断。",
            "market_risk_context中的水位模式仅表示赛前风险结构；可以检验该预警是否有效，但不得把退盘、升水或欧亚背离直接写成比赛失利的真实原因。",
            "必须复核historical_goal_margin_model：普通平局只核对0球分差，让平只核对赛前竞彩让球数对应的精确净胜球差，严禁用普通平局赛果替代让平结算。",
            "若historical_calibration.applied=true，要说明它相对core_probability是降低还是提高了概率，以及本场结果是否支持该次校准；单场支持或反对都不得直接升级为规律。",
            "历史相似模型的候选调权必须使用history_calibration范围，至少要求跨日期且不少于30个有效样本，并以Brier Score、对数损失和模拟ROI的样本外结果决定是否发布。",
            "若盘口无明显预警，只能说明现有赛前市场数据无法解释赛果；没有xG、红牌、射门等过程数据时必须明确未知。",
            "调权只能作为候选，单日样本不得直接修改正式权重；每个候选必须给出至少10个样本的验证门槛。",
            "match_id仅允许用于JSON关联字段；结论、做对了什么、需要修正、市场复核、逐场诊断、调权候选和组合复核等所有自然语言必须使用match_number（如周四201），严禁展示原始比赛ID。",
            "输入中的每场已结算比赛必须在matches中恰好出现一次。",
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
    ) -> Dict[str, Any]:
        source_rows = list(source_matches)
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
            handicap_prediction = source.get("handicap_prediction") or {}
            handicap_status = handicap_prediction.get("status")
            fallback_verdict = {
                "hit": "判断有效",
                "miss": "判断失误",
                "push": "走盘",
            }.get(result_status, "判断失误")
            verdict = cls._text(
                generated.get("verdict"), fallback_verdict, 30
            )
            if verdict not in {
                "判断有效", "命中但过程有风险", "判断失误", "走盘"
            }:
                verdict = fallback_verdict
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
                "handicap_verdict": {
                    "hit": "让球参考命中",
                    "miss": "让球参考未中",
                    "push": "让球走盘",
                }.get(handicap_status, "未推荐"),
                "verdict": verdict,
                "diagnosis": cls._text(
                    generated.get("diagnosis"),
                    "模型未返回完整诊断，请以确定性结算结果为准。",
                    600,
                ),
                "correct_signals": cls._list(
                    generated.get("correct_signals"), 6, 220
                ),
                "missed_signals": cls._list(
                    generated.get("missed_signals"), 6, 220
                ),
                "data_quality_issues": cls._list(
                    generated.get("data_quality_issues"), 6, 220
                ),
                "counterfactual": cls._text(
                    generated.get("counterfactual"), "", 500
                ),
                "rule_tags": cls._list(
                    generated.get("rule_tags"), 5, 60
                ),
            })

        summary = (
            parsed.get("summary")
            if isinstance(parsed.get("summary"), dict) else {}
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
            target = cls._text(item.get("target"), "", 160)
            reason = cls._text(item.get("reason"), "", 400)
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
                "conclusion": cls._text(
                    summary.get("conclusion"),
                    "AI 深度复盘已完成，请结合逐场诊断查看。",
                    1000,
                ),
                "what_worked": cls._list(
                    summary.get("what_worked"), 6, 260
                ),
                "what_failed": cls._list(
                    summary.get("what_failed"), 6, 260
                ),
                "risk_patterns": cls._list(
                    summary.get("risk_patterns"), 6, 260
                ),
                "next_actions": cls._list(
                    summary.get("next_actions"), 6, 260
                ),
            },
            "market_lessons": {
                key: cls._text(
                    lessons.get(key), "本次样本不足，暂不调整。", 500
                )
                for key in (
                    "euro", "asian", "sporttery", "total", "consistency"
                )
            },
            "matches": normalized_matches,
            "combination_review": {
                "conclusion": cls._text(
                    combo.get("conclusion"), "暂无已结算组合可复核。", 600
                ),
                "good_choices": cls._list(
                    combo.get("good_choices"), 6, 220
                ),
                "bad_choices": cls._list(
                    combo.get("bad_choices"), 6, 220
                ),
                "construction_advice": cls._list(
                    combo.get("construction_advice"), 6, 220
                ),
            },
            "learning_candidates": candidates[:12],
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
