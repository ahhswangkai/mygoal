"""Deterministic Football AI Engine with optional Ark narrative output."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .market_rules import evaluate_historical_market_rules
from .provider import ArkNarrativeClient, FAEError, FAEOutputError
from .version import (
    DEFAULT_RULE_WEIGHTS,
    DIMENSION_WEIGHTS,
    ENGINE_CODE,
    ENGINE_NAME,
    ENGINE_VERSION,
    SCHEMA_VERSION,
)


class FootballAIEngine:
    """Calculate, explain, review and version football market analysis."""

    MATCH_FIELDS = (
        "match_id", "owner_date", "league", "round", "round_id",
        "match_number", "match_time", "status", "status_text", "home_team",
        "away_team", "home_rank", "away_rank", "handicap",
        "euro_initial_win", "euro_initial_draw", "euro_initial_lose",
        "euro_current_win", "euro_current_draw", "euro_current_lose",
        "asian_initial_home_odds", "asian_initial_handicap",
        "asian_initial_away_odds", "asian_current_home_odds",
        "asian_current_handicap", "asian_current_away_odds",
        "ou_initial_over_odds", "ou_initial_total", "ou_initial_under_odds",
        "ou_current_over_odds", "ou_current_total", "ou_current_under_odds",
        "hi_handicap_value", "hi_initial_home_odds", "hi_initial_draw_odds",
        "hi_initial_away_odds", "hi_current_home_odds",
        "hi_current_draw_odds", "hi_current_away_odds",
        "is_derby", "motivation", "injuries", "lineups", "weather",
    )

    HANDICAP_VALUES = {
        "平手": 0.0, "平/半": 0.25, "平手/半球": 0.25,
        "半球": 0.5, "半/一": 0.75, "半球/一球": 0.75,
        "一球": 1.0, "一/球半": 1.25, "一球/球半": 1.25,
        "球半": 1.5, "球半/两": 1.75, "球半/两球": 1.75,
        "两球": 2.0, "两/两球半": 2.25, "两球/两球半": 2.25,
        "两球半": 2.5, "两球半/三球": 2.75, "三球": 3.0,
        "三球/三球半": 3.25, "三球半": 3.5,
        "四球": 4.0, "四球半": 4.5,
    }

    DIMENSION_LABELS = {
        "handicap": "盘口", "euro": "欧赔", "over_under": "大小球",
        "sporttery": "竞彩", "motivation": "战意", "injuries": "伤停",
        "history": "历史", "form": "状态",
    }

    def __init__(self, client: Optional[ArkNarrativeClient] = None):
        self.client = client or ArkNarrativeClient()

    @property
    def configured(self) -> bool:
        return self.client.configured

    def build_context(
        self,
        match: Dict[str, Any],
        source_analysis: Optional[Dict[str, Any]] = None,
        prediction: Optional[Dict[str, Any]] = None,
        external_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        source = source_analysis or {}
        external = external_data or source.get("external") or {}
        match_data = {
            field: self._json_safe(match.get(field))
            for field in self.MATCH_FIELDS
            if match.get(field) not in (None, "")
        }
        recent = source.get("recent") or {}
        history = self._limited_list(source.get("history"), 10)
        home_team = str(match.get("home_team") or "")
        away_team = str(match.get("away_team") or "")
        source_teams = source.get("teams") or []
        if not isinstance(source_teams, (list, tuple)):
            source_teams = []
        source_home_team = (
            str(source_teams[0]) if len(source_teams) > 0 else home_team
        )
        source_away_team = (
            str(source_teams[1]) if len(source_teams) > 1 else away_team
        )
        home_form, home_issues = self._form_summary(
            source_home_team, recent.get("home") or [], "home"
        )
        away_form, away_issues = self._form_summary(
            source_away_team, recent.get("away") or [], "away"
        )
        history_summary = self._history_summary(
            source_home_team, source_away_team, history
        )

        injuries = external.get("injuries", source.get("injuries", match.get("injuries")))
        lineups = external.get("lineups", source.get("lineups", match.get("lineups")))
        weather = external.get("weather", source.get("weather", match.get("weather")))
        motivation = external.get(
            "motivation", source.get("motivation", match.get("motivation"))
        )
        standings = self._limited_list(
            external.get("standings", source.get("standings")), 30
        )
        future = {
            "home": self._limited_list((source.get("future") or {}).get("home"), 5),
            "away": self._limited_list((source.get("future") or {}).get("away"), 5),
        }
        quality_issues = list(dict.fromkeys(home_issues + away_issues))
        if not home_form["valid_matches"]:
            quality_issues.append("缺少主队可验证的近期比赛")
        if not away_form["valid_matches"]:
            quality_issues.append("缺少客队可验证的近期比赛")
        if not standings:
            quality_issues.append("缺少积分排名数据")
        if not injuries:
            quality_issues.append("缺少伤停数据")
        elif (
            isinstance(injuries, dict)
            and injuries.get("status") == "no_listed_players"
        ):
            quality_issues.append(
                "500伤病/停赛栏目未列出球员，不等同于官方确认无伤停"
            )
        if not lineups:
            quality_issues.append("缺少首发数据")
        elif (
            isinstance(lineups, dict)
            and lineups.get("status") == "predicted"
        ):
            quality_issues.append("当前阵容为500预计阵容，非官方确认首发")
        if not weather:
            quality_issues.append("缺少天气数据")

        return {
            "match": match_data,
            "markets": {
                "movement": self._odds_movement(match),
                "sporttery_handicap": self._number(
                    match.get("hi_handicap_value")
                    if match.get("hi_handicap_value") not in (None, "")
                    else match.get("handicap")
                ),
            },
            "fundamentals": {
                "source": source.get("source"),
                "home_form": home_form,
                "away_form": away_form,
                "history": history_summary,
                "standings": standings,
                "future": future,
                "injuries": self._json_safe(injuries),
                "lineups": self._json_safe(lineups),
                "weather": self._json_safe(weather),
                "motivation": self._json_safe(motivation),
                "statistics": self._json_safe(
                    external.get("statistics", source.get("statistics"))
                ),
            },
            "local_prediction": self._prediction_context(prediction or {}),
            "data_quality": {
                "issues": list(dict.fromkeys(quality_issues)),
                "completeness": self._data_completeness(
                    match, home_form, away_form, history_summary,
                    standings, injuries, lineups, weather,
                ),
            },
        }

    def generate_from_context(
        self,
        context: Dict[str, Any],
        rule_weights: Optional[Dict[str, float]] = None,
        use_ai: bool = True,
        active_skills: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        weights = dict(DEFAULT_RULE_WEIGHTS)
        for key, value in (rule_weights or {}).items():
            try:
                weights[str(key)] = max(0.5, min(1.5, float(value)))
            except (TypeError, ValueError):
                continue

        context = dict(context or {})
        markets = dict(context.get("markets") or {})
        markets["historical_odds_rules"] = evaluate_historical_market_rules(
            context.get("match") or {},
            weights,
        )
        context["markets"] = markets
        core = self._analyze_core(context, weights)
        narrative = self._deterministic_narrative(context, core)
        provider_meta: Dict[str, Any] = {"mode": "deterministic"}
        if use_ai and self.client.configured:
            try:
                raw_text, provider_meta = self.client.generate(
                    self._build_narrative_prompt(context, core, active_skills)
                )
                narrative = self._normalize_narrative(self._extract_json(raw_text), narrative)
                provider_meta["mode"] = "ark-narrative"
            except FAEError as exc:
                provider_meta = {
                    "mode": "deterministic-fallback",
                    "error": str(exc)[:240],
                }
                narrative["risks"] = self._unique_list(
                    narrative["risks"] + ["生成式说明暂不可用，本次展示确定性引擎结果"], 8
                )

        match = context.get("match") or {}
        skill_manifest = [
            {
                "skill_id": item.get("skill_id"),
                "label": item.get("label"),
                "version": item.get("version"),
                "guidance": item.get("guidance"),
            }
            for item in (active_skills or [])
            if item.get("skill_id")
        ]
        data_hash = self.context_hash(
            context, weights, self.client.model, skill_manifest
        )
        analysis = {
            "engine_name": ENGINE_NAME,
            "engine_code": ENGINE_CODE,
            "engine_version": ENGINE_VERSION,
            "result_tendency": core["recommendation"]["primary"],
            "confidence": core["recommendation"]["confidence"],
            "asian_tendency": core["dimension_scores"]["handicap"]["tendency"],
            "over_under_tendency": core["dimension_scores"]["over_under"]["tendency"],
            "score_candidates": core["score_candidates"],
            "evidence": narrative["evidence"],
            "risks": narrative["risks"],
            "summary": narrative["summary"],
            "market_types": core["market_types"],
            "dimension_scores": core["dimension_scores"],
            "overall_score": core["overall_score"],
            "overall_stars": core["overall_stars"],
            "probabilities": core["probabilities"],
            "probability_basis": core["probability_basis"],
            "historical_odds_rules": core["historical_odds_rules"],
            "recommendation": core["recommendation"],
            "risk": core["risk"],
            "modules": [
                "data-layer", "market-classifier", "scoring-engine",
                "probability-engine", "recommendation-engine", "risk-control",
                "historical-market-rules", "review-learning", "version-control",
            ],
            "disclaimer": "仅基于现有数据进行分析，不构成投注建议",
        }
        return {
            "match_id": str(match.get("match_id") or ""),
            "owner_date": str(match.get("owner_date") or "")[:10],
            "match_number": match.get("match_number") or match.get("round_id"),
            "home_team": match.get("home_team"),
            "away_team": match.get("away_team"),
            "engine": {
                "name": ENGINE_NAME,
                "code": ENGINE_CODE,
                "version": ENGINE_VERSION,
                "schema_version": SCHEMA_VERSION,
            },
            "model": self.client.model if provider_meta.get("mode") == "ark-narrative" else None,
            "provider": "volcengine-ark" if provider_meta.get("mode") == "ark-narrative" else "fae-core",
            "rule_weights": weights,
            "skill_versions": {
                str(item.get("skill_id")): str(item.get("version"))
                for item in skill_manifest
                if item.get("skill_id") and item.get("version")
            },
            "skills": skill_manifest,
            "data_hash": data_hash,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "core": core,
            "analysis": analysis,
            "provider_meta": provider_meta,
            "review_status": "pending",
        }

    @staticmethod
    def context_hash(
        context: Dict[str, Any],
        rule_weights: Dict[str, float],
        model: str,
        skills: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        payload = {
            "context": context,
            "rule_weights": rule_weights,
            "engine_version": ENGINE_VERSION,
            "model": model,
            "skills": skills or [],
        }
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return sha256(encoded).hexdigest()

    def _analyze_core(
        self, context: Dict[str, Any], weights: Dict[str, float]
    ) -> Dict[str, Any]:
        base_probabilities = self._implied_probabilities(context)
        signals = self._rule_signals(context, base_probabilities, weights)
        probabilities = self._adjust_probabilities(base_probabilities, signals)
        probabilities = self._apply_historical_draw_adjustment(
            context, probabilities
        )
        market_types = self._classify_market(context, probabilities)
        risk = self._risk_profile(context, market_types, signals)
        dimensions = self._dimension_scores(context, probabilities, signals)
        overall_score = round(sum(
            dimensions[key]["score"] * weight
            for key, weight in DIMENSION_WEIGHTS.items()
        ))
        overall_score = max(0, min(100, overall_score - round(risk["score"] * 0.08)))
        distribution = self._score_distribution(context, probabilities, signals)
        market_probabilities = self._market_probabilities(context, probabilities, distribution)
        recommendation = self._recommendation(
            context, market_probabilities, overall_score, risk, signals,
            market_types,
        )
        score_candidates = self._select_score_candidates(
            context, distribution, recommendation
        )
        return {
            "market_types": market_types,
            "dimension_scores": dimensions,
            "overall_score": overall_score,
            "overall_stars": self._stars(overall_score),
            "probabilities": market_probabilities,
            "probability_basis": {
                "label": "市场去水概率 + FAE规则 + 历史赔率区间校准",
                "calibrated": False,
                "market_implied_no_vig": {
                    "home_win": round(base_probabilities["home"] * 100, 1),
                    "draw": round(base_probabilities["draw"] * 100, 1),
                    "away_win": round(base_probabilities["away"] * 100, 1),
                },
                "note": (
                    "由欧赔去除返还率后归一化，并结合已录入盘口信号调整；"
                    "历史赔率规则只做有限幅度修正；尚未经过长期独立赛果校准，"
                    "不等同于真实发生概率。"
                ),
                "historical_market_rules": (
                    (context.get("markets") or {}).get(
                        "historical_odds_rules"
                    ) or {}
                ),
            },
            "recommendation": recommendation,
            "risk": risk,
            "rule_signals": signals,
            "historical_odds_rules": (
                (context.get("markets") or {}).get("historical_odds_rules")
                or {}
            ),
            "score_candidates": score_candidates,
            "data_quality": context.get("data_quality") or {},
        }

    def _implied_probabilities(self, context: Dict[str, Any]) -> Dict[str, float]:
        match = context.get("match") or {}
        odds = [
            self._number(match.get("euro_current_win")),
            self._number(match.get("euro_current_draw")),
            self._number(match.get("euro_current_lose")),
        ]
        if any(value is None or value <= 1 for value in odds):
            return {"home": 0.40, "draw": 0.29, "away": 0.31}
        inverse = [1 / value for value in odds]
        total = sum(inverse)
        return dict(zip(("home", "draw", "away"), [value / total for value in inverse]))

    def _rule_signals(
        self,
        context: Dict[str, Any],
        probabilities: Dict[str, float],
        weights: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        match = context.get("match") or {}
        movement = ((context.get("markets") or {}).get("movement") or {})
        signals: List[Dict[str, Any]] = []

        def add(
            rule_id: str, dimension: str, market: str, prediction: Optional[str],
            strength: float, reason: str, risk: bool = False,
        ) -> None:
            strength = max(1, min(100, round(strength)))
            weight = weights.get(rule_id, 1.0)
            signals.append({
                "rule_id": rule_id,
                "dimension": dimension,
                "market": market,
                "prediction": prediction,
                "strength": strength,
                "weight": round(weight, 3),
                "contribution": round(strength * weight, 2),
                "reason": reason,
                "risk": risk,
            })

        euro = movement.get("euro") or {}
        home_change = (euro.get("win") or {}).get("change")
        draw_change = (euro.get("draw") or {}).get("change")
        away_change = (euro.get("lose") or {}).get("change")
        if home_change is not None and home_change < 0:
            add("euro-home-support", "euro", "outcome", "home", abs(home_change) * 240 + 35,
                f"主胜欧赔由{euro['win']['initial']:.2f}降至{euro['win']['current']:.2f}")
        if away_change is not None and away_change < 0:
            add("euro-away-support", "euro", "outcome", "away", abs(away_change) * 240 + 35,
                f"客胜欧赔由{euro['lose']['initial']:.2f}降至{euro['lose']['current']:.2f}")
        if draw_change is not None and draw_change < 0:
            add("euro-draw-support", "euro", "outcome", "draw", abs(draw_change) * 220 + 30,
                f"平局欧赔由{euro['draw']['initial']:.2f}降至{euro['draw']['current']:.2f}")

        asian = movement.get("asian") or {}
        line = asian.get("handicap") or {}
        line_change = line.get("change")
        if line_change is not None and line_change > 0:
            add("asian-line-home", "handicap", "outcome", "home", abs(line_change) * 130 + 40,
                f"亚盘由{line.get('initial')}调整至{line.get('current')}，方向偏向主队")
        elif line_change is not None and line_change < 0:
            add("asian-line-away", "handicap", "outcome", "away", abs(line_change) * 130 + 40,
                f"亚盘由{line.get('initial')}调整至{line.get('current')}，方向偏向客队")

        home_water = asian.get("home_water") or {}
        away_water = asian.get("away_water") or {}
        if home_water.get("change") is not None and home_water["change"] < -0.03:
            add("asian-home-water", "handicap", "outcome", "home", abs(home_water["change"]) * 180 + 25,
                f"主水由{home_water['initial']:.2f}降至{home_water['current']:.2f}")
        if away_water.get("change") is not None and away_water["change"] < -0.03:
            add("asian-away-water", "handicap", "outcome", "away", abs(away_water["change"]) * 180 + 25,
                f"客水由{away_water['initial']:.2f}降至{away_water['current']:.2f}")

        favorite = max(probabilities, key=probabilities.get)
        initial_line = self._handicap_value(
            match.get("asian_initial_handicap")
        )
        current_line = self._handicap_value(match.get("asian_current_handicap"))
        favorite_initial_depth = (
            initial_line if favorite == "home"
            else -initial_line if favorite == "away" and initial_line is not None
            else None
        )
        favorite_current_depth = (
            current_line if favorite == "home"
            else -current_line if favorite == "away" and current_line is not None
            else None
        )
        favorite_retreated = (
            favorite_initial_depth is not None
            and favorite_current_depth is not None
            and favorite_current_depth < favorite_initial_depth - 0.01
        )
        favorite_euro_change = (
            (euro.get("win") or {}).get("change")
            if favorite == "home" else
            (euro.get("lose") or {}).get("change")
            if favorite == "away" else None
        )
        if favorite_retreated:
            favorite_name = "主队" if favorite == "home" else "客队"
            add(
                "handicap-drop", "handicap", "risk", None,
                abs(favorite_current_depth - favorite_initial_depth) * 120 + 42,
                f"{favorite_name}作为欧赔热门方，但亚洲盘由"
                f"{line.get('initial')}退至{line.get('current')}",
                True,
            )
            if probabilities.get(favorite, 0) >= 0.50:
                add(
                    "euro-asian-divergence", "risk", "risk", None,
                    abs(favorite_current_depth - favorite_initial_depth) * 110
                    + (48 if favorite_euro_change is None or favorite_euro_change <= 0 else 36),
                    "欧赔热门方向保持或增强，但亚洲盘对同一方向退盘，形成欧亚背离",
                    True,
                )
        favorite_supported = (
            (favorite == "home" and current_line is not None and current_line > 0)
            or (favorite == "away" and current_line is not None and current_line < 0)
        )
        if favorite_supported and probabilities[favorite] >= 0.43:
            add(
                f"market-consensus-{favorite}", "sporttery", "outcome", favorite,
                (probabilities[favorite] - 0.33) * 180 + 35,
                "欧赔强弱方向与亚盘让球方向一致",
            )

        current_waters = [
            self._number(match.get("asian_current_home_odds")),
            self._number(match.get("asian_current_away_odds")),
        ]
        extreme_waters = [
            value for value in current_waters
            if value is not None and (value < 0.60 or value > 1.25)
        ]
        total_line_change = (
            ((movement.get("over_under") or {}).get("line") or {})
            .get("change")
        )
        if extreme_waters or (
            total_line_change is not None and abs(total_line_change) >= 0.75
        ):
            reasons = []
            if extreme_waters:
                reasons.append("亚洲盘出现极端水位")
            if total_line_change is not None and abs(total_line_change) >= 0.75:
                reasons.append("大小球盘口跳档达到0.75球")
            add(
                "market-data-anomaly", "risk", "risk", None, 88,
                "、".join(reasons) + "，需先核验盘口切换或采集口径",
                True,
            )

        fundamentals = context.get("fundamentals") or {}
        home_form = fundamentals.get("home_form") or {}
        away_form = fundamentals.get("away_form") or {}
        if home_form.get("valid_matches", 0) >= 3 and away_form.get("valid_matches", 0) >= 3:
            delta = float(home_form.get("points_per_game", 0)) - float(away_form.get("points_per_game", 0))
            if delta >= 0.45:
                add("recent-form-home", "form", "outcome", "home", delta * 28 + 35,
                    f"主队近期场均积分{home_form['points_per_game']:.2f}高于客队{away_form['points_per_game']:.2f}")
            elif delta <= -0.45:
                add("recent-form-away", "form", "outcome", "away", abs(delta) * 28 + 35,
                    f"客队近期场均积分{away_form['points_per_game']:.2f}高于主队{home_form['points_per_game']:.2f}")

        history = fundamentals.get("history") or {}
        if history.get("valid_matches", 0) >= 3:
            home_rate = float(history.get("home_team_win_rate", 0))
            away_rate = float(history.get("away_team_win_rate", 0))
            if home_rate - away_rate >= 0.25:
                add("history-home", "history", "outcome", "home", (home_rate - away_rate) * 90 + 30,
                    f"近{history['valid_matches']}次交锋主队胜率更高")
            elif away_rate - home_rate >= 0.25:
                add("history-away", "history", "outcome", "away", (away_rate - home_rate) * 90 + 30,
                    f"近{history['valid_matches']}次交锋客队胜率更高")

        total = movement.get("over_under") or {}
        total_line = total.get("line") or {}
        if total_line.get("change") is not None and total_line["change"] > 0:
            add("total-over", "over_under", "total", "over", abs(total_line["change"]) * 80 + 40,
                f"大小球由{total_line['initial']}升至{total_line['current']}")
        elif total_line.get("change") is not None and total_line["change"] < 0:
            add("total-under", "over_under", "total", "under", abs(total_line["change"]) * 80 + 40,
                f"大小球由{total_line['initial']}降至{total_line['current']}")
        else:
            over_odds = self._number(match.get("ou_current_over_odds"))
            under_odds = self._number(match.get("ou_current_under_odds"))
            if over_odds is not None and under_odds is not None and abs(over_odds - under_odds) >= 0.12:
                target = "over" if over_odds < under_odds else "under"
                add(
                    "total-over" if target == "over" else "total-under",
                    "over_under", "total", target, abs(over_odds - under_odds) * 90 + 30,
                    "大小球两侧水位差形成方向信号",
                )

        quality = context.get("data_quality") or {}
        issue_count = len(quality.get("issues") or [])
        if issue_count:
            add("data-quality", "risk", "risk", None, min(95, 25 + issue_count * 10),
                f"存在{issue_count}项数据缺口或质量问题", True)

        historical_rules = (
            (context.get("markets") or {}).get("historical_odds_rules") or {}
        )
        grouped_rules = (
            ("ordinary_draw", "historical_outcome", "draw"),
            ("handicap_draw", "hhad", "draw"),
        )
        for group_key, market, prediction in grouped_rules:
            for rule in (
                (historical_rules.get(group_key) or {}).get("signals") or []
            ):
                adjustment = float(rule.get("adjustment_pp") or 0)
                add(
                    str(rule.get("rule_id") or ""),
                    "history",
                    market,
                    prediction,
                    35 + min(55, abs(adjustment) * 18),
                    str(rule.get("reason") or ""),
                    bool(rule.get("risk")),
                )
                signals[-1].update({
                    "historical_rule": True,
                    "selection": rule.get("selection"),
                    "adjustment_pp": adjustment,
                    "sample": rule.get("sample"),
                    "historical_hit_rate": rule.get("hit_rate"),
                    "market_probability": rule.get("market_probability"),
                    "historical_roi": rule.get("roi"),
                    "evidence_confidence": rule.get("confidence"),
                    "handicap": (
                        (historical_rules.get("handicap_draw") or {})
                        .get("handicap")
                    ) if group_key == "handicap_draw" else None,
                })
        for rule in historical_rules.get("favorite_risks") or []:
            add(
                str(rule.get("rule_id") or ""),
                "risk", "risk", None, 72,
                str(rule.get("reason") or ""),
                True,
            )
            signals[-1].update({
                "historical_rule": True,
                "selection": rule.get("selection"),
                "sample": rule.get("sample"),
                "historical_hit_rate": rule.get("hit_rate"),
                "market_probability": rule.get("market_probability"),
                "historical_roi": rule.get("roi"),
                "evidence_confidence": rule.get("confidence"),
            })
        return signals

    def _adjust_probabilities(
        self, base: Dict[str, float], signals: Iterable[Dict[str, Any]]
    ) -> Dict[str, float]:
        logits = {key: math.log(max(0.01, value)) for key, value in base.items()}
        for signal in signals:
            target = signal.get("prediction")
            if signal.get("market") != "outcome" or target not in logits or signal.get("risk"):
                continue
            delta = min(0.18, signal["strength"] / 100 * 0.10 * signal["weight"])
            logits[target] += delta
        values = {key: math.exp(value) for key, value in logits.items()}
        total = sum(values.values())
        return {key: value / total for key, value in values.items()}

    @staticmethod
    def _shift_draw_probability(
        probabilities: Dict[str, float],
        adjustment_pp: float,
        *,
        draw_key: str,
        other_keys: Tuple[str, str],
    ) -> Dict[str, float]:
        result = dict(probabilities)
        current_draw = float(result.get(draw_key) or 0)
        target_draw = max(
            0.05,
            min(0.55, current_draw + float(adjustment_pp or 0) / 100),
        )
        current_other = sum(float(result.get(key) or 0) for key in other_keys)
        target_other = max(0, 1 - target_draw)
        if current_other > 0:
            scale = target_other / current_other
            for key in other_keys:
                result[key] = float(result.get(key) or 0) * scale
        else:
            for key in other_keys:
                result[key] = target_other / len(other_keys)
        result[draw_key] = target_draw
        return result

    def _apply_historical_draw_adjustment(
        self,
        context: Dict[str, Any],
        probabilities: Dict[str, float],
    ) -> Dict[str, float]:
        profile = (
            ((context.get("markets") or {}).get("historical_odds_rules") or {})
            .get("ordinary_draw") or {}
        )
        adjustment = float(profile.get("adjustment_pp") or 0)
        if not profile.get("eligible_for_adjustment") or not adjustment:
            return probabilities
        return self._shift_draw_probability(
            probabilities,
            adjustment,
            draw_key="draw",
            other_keys=("home", "away"),
        )

    def _classify_market(
        self, context: Dict[str, Any], probabilities: Dict[str, float]
    ) -> List[Dict[str, Any]]:
        match = context.get("match") or {}
        movement = ((context.get("markets") or {}).get("movement") or {})
        line = self._handicap_value(match.get("asian_current_handicap"))
        initial_line = self._handicap_value(match.get("asian_initial_handicap"))
        favorite = max(probabilities, key=probabilities.get)
        favorite_probability = probabilities[favorite]
        favorite_water = self._number(
            match.get("asian_current_home_odds" if favorite == "home" else "asian_current_away_odds")
        )
        euro = movement.get("euro") or {}
        favorite_change = (euro.get("win" if favorite == "home" else "lose") or {}).get("change")
        league = str(match.get("league") or "")
        types = []

        def add(code: str, name: str, confidence: int, reason: str) -> None:
            types.append({"code": code, "name": name, "confidence": confidence, "reason": reason})

        if line is not None and 0.75 <= abs(line) <= 1.25:
            add("A", "赢一球盘口", 78, "当前亚盘处于半一至一球/球半区间")
        if line is not None and abs(line) >= 1.25 and favorite_probability >= 0.55 and (favorite_water or 9) <= 1.0:
            add("B", "穿盘盘口", 74, "深盘、强势欧赔与低水方向形成配合")
        if line is None or abs(line) <= 0.25 or max(probabilities.values()) - min(probabilities.values()) < 0.12:
            add("C", "均势盘口", 80, "盘口较浅或胜平负概率差距有限")
        favorite_initial_depth = (
            initial_line if favorite == "home"
            else -initial_line if favorite == "away" and initial_line is not None
            else None
        )
        favorite_current_depth = (
            line if favorite == "home"
            else -line if favorite == "away" and line is not None
            else None
        )
        line_not_deeper = (
            favorite_initial_depth is not None
            and favorite_current_depth is not None
            and favorite_current_depth <= favorite_initial_depth
        )
        if favorite_probability >= 0.55 and favorite_change is not None and favorite_change <= -0.06 and (line_not_deeper or (favorite_water or 0) >= 0.98):
            add("D", "热门过热盘口", 82, "热门方向欧赔压低，但盘口未同步升深或水位偏高")
        if line is not None and abs(line) >= 1.0 and favorite_water is not None and favorite_water >= 0.98:
            add("E", "深盘高水盘口", 84, "让球较深且热门一侧维持高水")
        if any(keyword in league for keyword in ("杯", "淘汰", "附加赛")):
            add("F", "杯赛盘口", 95, "赛事属于杯赛或淘汰赛，波动模型单独处理")
        if match.get("is_derby"):
            add("G", "德比盘口", 95, "数据源明确标记为德比比赛")
        if not types:
            add("C", "均势盘口", 55, "未触发其他特殊模型，按常规均势模型处理")
        return types

    def _dimension_scores(
        self,
        context: Dict[str, Any],
        probabilities: Dict[str, float],
        signals: List[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        match = context.get("match") or {}
        fundamentals = context.get("fundamentals") or {}
        quality = context.get("data_quality") or {}
        movement = ((context.get("markets") or {}).get("movement") or {})
        favorite = max(probabilities, key=probabilities.get)
        favorite_cn = {"home": "主队", "draw": "平局", "away": "客队"}[favorite]

        def dimension(key: str, score: float, tendency: str, status: str = "available") -> Dict[str, Any]:
            value = max(0, min(100, round(score)))
            return {
                "key": key,
                "label": self.DIMENSION_LABELS[key],
                "score": value,
                "stars": self._stars(value),
                "tendency": tendency,
                "data_status": status,
            }

        line = self._handicap_value(match.get("asian_current_handicap"))
        handicap_signals = [s for s in signals if s["dimension"] == "handicap" and not s["risk"]]
        handicap_score = 30 if line is None else 52 + min(28, abs(line) * 10 + sum(s["strength"] for s in handicap_signals) * 0.12)
        handicap_tendency = "数据不足" if line is None else (
            f"{match.get('home_team')}方向" if line > 0 else
            f"{match.get('away_team')}方向" if line < 0 else "平手均势"
        )

        euro_available = all(self._number(match.get(key)) for key in (
            "euro_current_win", "euro_current_draw", "euro_current_lose"
        ))
        spread = max(probabilities.values()) - min(probabilities.values())
        euro_score = 30 if not euro_available else 55 + min(36, spread * 105 + len(movement.get("euro") or {}) * 2)

        total_line = self._number(match.get("ou_current_total"))
        total_signals = [s for s in signals if s["market"] == "total"]
        total_target = max(total_signals, key=lambda s: s["contribution"])["prediction"] if total_signals else None
        total_score = 30 if total_line is None else 54 + min(30, sum(s["strength"] for s in total_signals) * 0.18)
        total_tendency = "数据不足" if total_line is None else (
            f"大球 {total_line:g}" if total_target == "over" else
            f"小球 {total_line:g}" if total_target == "under" else f"{total_line:g}球附近"
        )

        sporttery_handicap = (context.get("markets") or {}).get("sporttery_handicap")
        hi_values = [self._number(match.get(key)) for key in (
            "hi_current_home_odds", "hi_current_draw_odds", "hi_current_away_odds"
        )]
        sporttery_score = 30
        sporttery_status = "missing"
        sporttery_tendency = "竞彩数据不足"
        if sporttery_handicap is not None:
            sporttery_score = 52
            sporttery_status = "partial"
            sporttery_tendency = f"竞彩让球 {sporttery_handicap:+g}"
        if all(value is not None for value in hi_values):
            sporttery_score = 72
            sporttery_status = "available"
            labels = ("让胜", "让平", "让负")
            sporttery_tendency = labels[hi_values.index(min(hi_values))]

        motivation = fundamentals.get("motivation") or match.get("motivation")
        is_cup = any(keyword in str(match.get("league") or "") for keyword in ("杯", "淘汰", "附加赛"))
        motivation_score = 65 if motivation else 48 if is_cup else 32
        motivation_tendency = str(motivation)[:80] if motivation else ("杯赛战意需结合轮次确认" if is_cup else "缺少明确战意数据")

        injuries = fundamentals.get("injuries")
        injury_status = (
            injuries.get("status") if isinstance(injuries, dict) else None
        )
        if injury_status == "no_listed_players":
            injuries_score = 45
            injuries_data_status = "partial"
            injuries_tendency = "500伤停栏目未列出球员，尚非官方确认"
        elif injuries:
            injuries_score = 62
            injuries_data_status = "available"
            injuries_tendency = "已接入伤停数据"
        else:
            injuries_score = 30
            injuries_data_status = "missing"
            injuries_tendency = "缺少伤停数据"

        history = fundamentals.get("history") or {}
        history_games = int(history.get("valid_matches") or 0)
        history_score = 30 if history_games == 0 else min(82, 42 + history_games * 5)
        history_tendency = history.get("tendency") or "历史交锋样本不足"

        home_form = fundamentals.get("home_form") or {}
        away_form = fundamentals.get("away_form") or {}
        valid_form = min(home_form.get("valid_matches", 0), away_form.get("valid_matches", 0))
        form_score = 30 if valid_form == 0 else min(85, 45 + valid_form * 4)
        form_tendency = "近期状态数据不足"
        if valid_form:
            delta = home_form.get("points_per_game", 0) - away_form.get("points_per_game", 0)
            form_tendency = "主队近期状态占优" if delta > 0.3 else "客队近期状态占优" if delta < -0.3 else "双方近期状态接近"

        return {
            "handicap": dimension("handicap", handicap_score, handicap_tendency, "available" if line is not None else "missing"),
            "euro": dimension("euro", euro_score, f"{favorite_cn}价格更低", "available" if euro_available else "missing"),
            "over_under": dimension("over_under", total_score, total_tendency, "available" if total_line is not None else "missing"),
            "sporttery": dimension("sporttery", sporttery_score, sporttery_tendency, sporttery_status),
            "motivation": dimension("motivation", motivation_score, motivation_tendency, "available" if motivation else "partial" if is_cup else "missing"),
            "injuries": dimension("injuries", injuries_score, injuries_tendency, injuries_data_status),
            "history": dimension("history", history_score, history_tendency, "available" if history_games else "missing"),
            "form": dimension("form", form_score, form_tendency, "available" if valid_form else "missing"),
            "data_completeness": {
                "key": "data_completeness", "label": "数据完整度",
                "score": quality.get("completeness", 0),
                "stars": self._stars(quality.get("completeness", 0)),
                "tendency": f"{len(quality.get('issues') or [])}项缺口",
                "data_status": "available",
            },
        }

    def _risk_profile(
        self,
        context: Dict[str, Any],
        market_types: List[Dict[str, Any]],
        signals: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        score = 0
        reasons = []
        type_scores = {"D": 26, "E": 24, "F": 10, "G": 15}
        for item in market_types:
            value = type_scores.get(item["code"], 0)
            if value:
                score += value
                reasons.append(item["reason"])
        for signal in signals:
            if signal.get("risk"):
                score += round(signal["strength"] * signal["weight"] * 0.18)
                reasons.append(signal["reason"])
        issues = (context.get("data_quality") or {}).get("issues") or []
        score += min(25, len(issues) * 3)
        reasons.extend(issues[:4])
        score = max(0, min(100, score))
        level = "高" if score >= 65 else "中" if score >= 35 else "低"
        return {
            "score": score,
            "level": level,
            "dangerous": score >= 65,
            "star_text": self._star_text(self._stars(score)),
            "reasons": self._unique_list(reasons, 8),
        }

    def _score_distribution(
        self,
        context: Dict[str, Any],
        probabilities: Dict[str, float],
        signals: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        match = context.get("match") or {}
        total_line = self._number(match.get("ou_current_total")) or 2.5
        total_shift = sum(
            (1 if s.get("prediction") == "over" else -1) * s["strength"] * s["weight"] / 1000
            for s in signals if s.get("market") == "total"
        )
        expected_total = max(1.5, min(4.5, total_line + total_shift))
        decisive = probabilities["home"] + probabilities["away"]
        home_share = 0.5 if decisive <= 0 else probabilities["home"] / decisive
        home_share = max(0.28, min(0.72, 0.34 + home_share * 0.32))
        home_lambda = expected_total * home_share
        away_lambda = expected_total - home_lambda
        rows = []
        total_probability = 0.0
        for home_goals in range(8):
            for away_goals in range(8):
                probability = self._poisson(home_goals, home_lambda) * self._poisson(away_goals, away_lambda)
                total_probability += probability
                rows.append({
                    "home": home_goals,
                    "away": away_goals,
                    "score": f"{home_goals}:{away_goals}",
                    "probability": probability,
                })
        for row in rows:
            row["probability"] = row["probability"] / total_probability
        raw_outcomes = {
            "home": sum(
                row["probability"] for row in rows
                if row["home"] > row["away"]
            ),
            "draw": sum(
                row["probability"] for row in rows
                if row["home"] == row["away"]
            ),
            "away": sum(
                row["probability"] for row in rows
                if row["home"] < row["away"]
            ),
        }
        multipliers = {
            key: probabilities[key] / raw_outcomes[key]
            if raw_outcomes[key] else 1.0
            for key in ("home", "draw", "away")
        }
        for row in rows:
            outcome = (
                "home" if row["home"] > row["away"]
                else "draw" if row["home"] == row["away"]
                else "away"
            )
            row["probability"] *= multipliers[outcome]
        calibrated_total = sum(row["probability"] for row in rows)
        if calibrated_total:
            for row in rows:
                row["probability"] /= calibrated_total
        rows.sort(key=lambda row: row["probability"], reverse=True)
        return rows

    def _market_probabilities(
        self,
        context: Dict[str, Any],
        outcome: Dict[str, float],
        distribution: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        poisson_home = sum(row["probability"] for row in distribution if row["home"] > row["away"])
        poisson_away = sum(row["probability"] for row in distribution if row["home"] < row["away"])
        home_by_one_ratio = (
            sum(row["probability"] for row in distribution if row["home"] - row["away"] == 1) / poisson_home
            if poisson_home else 0.55
        )
        away_by_one_ratio = (
            sum(row["probability"] for row in distribution if row["away"] - row["home"] == 1) / poisson_away
            if poisson_away else 0.55
        )
        handicap = (context.get("markets") or {}).get("sporttery_handicap")
        hhad = {"win": 0.0, "draw": 0.0, "lose": 0.0}
        if handicap is not None:
            for row in distribution:
                adjusted_home = row["home"] + handicap
                key = "win" if adjusted_home > row["away"] else "draw" if adjusted_home == row["away"] else "lose"
                hhad[key] += row["probability"]
            historical_hhad = (
                ((context.get("markets") or {}).get("historical_odds_rules") or {})
                .get("handicap_draw") or {}
            )
            hhad_adjustment = float(
                historical_hhad.get("adjustment_pp") or 0
            )
            if (
                historical_hhad.get("eligible_for_adjustment")
                and hhad_adjustment
            ):
                hhad = self._shift_draw_probability(
                    hhad,
                    hhad_adjustment,
                    draw_key="draw",
                    other_keys=("win", "lose"),
                )

        total_line = self._number((context.get("match") or {}).get("ou_current_total"))
        totals = {"over": 0.0, "push": 0.0, "under": 0.0}
        if total_line is not None:
            for row in distribution:
                goals = row["home"] + row["away"]
                key = "over" if goals > total_line else "push" if goals == total_line else "under"
                totals[key] += row["probability"]

        return {
            "home_win": round(outcome["home"] * 100),
            "draw": round(outcome["draw"] * 100),
            "away_win": 100 - round(outcome["home"] * 100) - round(outcome["draw"] * 100),
            "home_win_by_one": round(outcome["home"] * home_by_one_ratio * 100),
            "home_win_by_two_plus": round(outcome["home"] * (1 - home_by_one_ratio) * 100),
            "away_win_by_one": round(outcome["away"] * away_by_one_ratio * 100),
            "away_win_by_two_plus": round(outcome["away"] * (1 - away_by_one_ratio) * 100),
            "sporttery_handicap": handicap,
            "hhad": {key: round(value * 100) for key, value in hhad.items()} if handicap is not None else None,
            "total_line": total_line,
            "over_under": {key: round(value * 100) for key, value in totals.items()} if total_line is not None else None,
        }

    def _recommendation(
        self,
        context: Dict[str, Any],
        probabilities: Dict[str, Any],
        overall_score: int,
        risk: Dict[str, Any],
        signals: List[Dict[str, Any]],
        market_types: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        candidates = []

        match = context.get("match") or {}
        odds_by_label = {
            "主胜": self._number(match.get("euro_current_win"))
            or self._number(match.get("euro_initial_win")),
            "平局": self._number(match.get("euro_current_draw"))
            or self._number(match.get("euro_initial_draw")),
            "客胜": self._number(match.get("euro_current_lose"))
            or self._number(match.get("euro_initial_lose")),
            "让胜": self._number(match.get("hi_current_home_odds"))
            or self._number(match.get("hi_initial_home_odds")),
            "让平": self._number(match.get("hi_current_draw_odds"))
            or self._number(match.get("hi_initial_draw_odds")),
            "让负": self._number(match.get("hi_current_away_odds"))
            or self._number(match.get("hi_initial_away_odds")),
            "大球": self._asian_decimal_odds(
                match.get("ou_current_over_odds")
                if match.get("ou_current_over_odds") not in (None, "")
                else match.get("ou_initial_over_odds")
            ),
            "小球": self._asian_decimal_odds(
                match.get("ou_current_under_odds")
                if match.get("ou_current_under_odds") not in (None, "")
                else match.get("ou_initial_under_odds")
            ),
        }
        market_groups = {
            "胜平负": ("主胜", "平局", "客胜"),
            "竞彩让球": ("让胜", "让平", "让负"),
            "大小球": ("大球", "小球"),
        }
        implied_by_label: Dict[str, float] = {}
        for labels in market_groups.values():
            valid = {
                label: odds_by_label.get(label)
                for label in labels
                if odds_by_label.get(label) is not None
                and odds_by_label[label] > 1
            }
            inverse_total = sum(1 / value for value in valid.values())
            if inverse_total:
                for label, value in valid.items():
                    implied_by_label[label] = round(
                        (1 / value) / inverse_total * 100, 2
                    )

        market_confidence = self._market_confidence(
            risk, signals, market_types or []
        )
        market_type_codes = {
            str(item.get("code") or "")
            for item in (market_types or []) if isinstance(item, dict)
        }

        def add(label: str, probability: int, baseline: float, market: str) -> None:
            edge = max(0, probability - baseline)
            prediction_score = round(
                48 + edge * 0.75 + overall_score * 0.25
                - risk["score"] * 0.16
            )
            prediction_score = max(0, min(99, prediction_score))
            confidence = min(
                88, max(35, round((prediction_score + probability) / 2))
            )
            odds = odds_by_label.get(label)
            implied_probability = implied_by_label.get(label)
            value_probability = None
            expected_return = None
            value_edge = None
            if implied_probability is not None and odds is not None:
                # FAE probability is not yet long-term calibrated. Shrink most
                # of its difference back toward the no-vig market baseline.
                value_probability = round(
                    implied_probability
                    + (probability - implied_probability) * 0.35,
                    2,
                )
                value_edge = round(
                    value_probability - implied_probability, 2
                )
                expected_return = round(value_probability / 100 * odds, 3)
                value_score = round(
                    55 + value_edge * 1.8
                    + (expected_return - 1) * 30
                )
            else:
                value_score = 38
            value_score = max(0, min(99, value_score))
            bet_score = round(
                value_score * 0.55
                + market_confidence["score"] * 0.30
                + prediction_score * 0.15
            )
            bet_score = max(0, min(99, bet_score))
            no_bet_reasons = []
            if odds is None:
                no_bet_reasons.append("缺少可核验赔率")
            if value_score < 52:
                no_bet_reasons.append("赔率价值不足")
            if market_confidence["score"] < 50:
                no_bet_reasons.append("盘口一致性偏低")
            if (
                market_type_codes.intersection({"D", "E"})
                and value_score < 65
            ):
                no_bet_reasons.append("热门异常盘口缺少足够价值补偿")
            if risk.get("dangerous"):
                no_bet_reasons.append("危险盘口")
            if bet_score < 55:
                no_bet_reasons.append("综合投注分未达门槛")
            no_bet = bool(no_bet_reasons)
            stars = self._bet_stars(bet_score)
            candidates.append({
                "label": label,
                "probability": probability,
                "prediction_score": prediction_score,
                "score": bet_score,
                "bet_score": bet_score,
                "confidence": confidence,
                "stars": stars,
                "star_text": self._star_text(stars),
                "market": market,
                "odds": round(odds, 3) if odds is not None else None,
                "market_implied_probability": implied_probability,
                "value_probability": value_probability,
                "value_edge": value_edge,
                "expected_return": expected_return,
                "value_score": value_score,
                "no_bet": no_bet,
                "no_bet_reasons": no_bet_reasons,
            })

        outcome_labels = {
            "home_win": "主胜", "draw": "平局", "away_win": "客胜"
        }
        for key, label in outcome_labels.items():
            add(label, probabilities[key], 34, "胜平负")

        hhad = probabilities.get("hhad")
        if hhad:
            hhad_labels = {"win": "让胜", "draw": "让平", "lose": "让负"}
            for key, label in hhad_labels.items():
                add(label, hhad[key], 34, "竞彩让球")

        totals = probabilities.get("over_under")
        if totals:
            add("大球", totals["over"], 50, "大小球")
            add("小球", totals["under"], 50, "大小球")

        candidates.sort(
            key=lambda item: (
                not item["no_bet"],
                item["bet_score"],
                item["value_score"],
                item["probability"],
            ),
            reverse=True,
        )
        primary = candidates[0]
        supporting = [s for s in signals if not s.get("risk")]
        supporting.sort(key=lambda signal: signal["contribution"], reverse=True)
        return {
            "primary": primary["label"],
            "market": primary["market"],
            "score": primary["score"],
            "prediction_score": primary["prediction_score"],
            "bet_score": primary["bet_score"],
            "value_score": primary["value_score"],
            "confidence": primary["confidence"],
            "probability": primary["probability"],
            "odds": primary["odds"],
            "market_implied_probability": primary[
                "market_implied_probability"
            ],
            "value_probability": primary["value_probability"],
            "value_edge": primary["value_edge"],
            "expected_return": primary["expected_return"],
            "market_confidence": market_confidence,
            "no_bet": primary["no_bet"],
            "no_bet_reasons": primary["no_bet_reasons"],
            "decision": "不下注" if primary["no_bet"] else "可考虑",
            "stars": primary["stars"],
            "star_text": primary["star_text"],
            "alternatives": candidates[1:3],
            "category_scores": candidates,
            "reasons": [signal["reason"] for signal in supporting[:4]],
        }

    @classmethod
    def _asian_decimal_odds(cls, value: Any) -> Optional[float]:
        odds = cls._number(value)
        if odds is None or odds <= 0:
            return None
        return odds + 1 if odds < 1.5 else odds

    @staticmethod
    def _bet_stars(score: Any) -> float:
        try:
            value = float(score)
        except (TypeError, ValueError):
            value = 0
        if value >= 84:
            return 5.0
        if value >= 76:
            return 4.5
        if value >= 64:
            return 4.0
        if value >= 56:
            return 3.5
        if value >= 52:
            return 3.0
        if value >= 44:
            return 2.5
        if value >= 36:
            return 2.0
        return 1.5

    @staticmethod
    def _market_confidence(
        risk: Dict[str, Any],
        signals: List[Dict[str, Any]],
        market_types: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        signal_ids = {
            str(item.get("rule_id") or "")
            for item in signals if isinstance(item, dict)
        }
        score = 68
        reasons = []
        if signal_ids.intersection({
            "market-consensus-home", "market-consensus-away"
        }):
            score += 12
            reasons.append("欧赔与亚盘方向一致")
        if "euro-asian-divergence" in signal_ids:
            score -= 28
            reasons.append("欧亚方向背离")
        if "handicap-drop" in signal_ids:
            score -= 20
            reasons.append("热门方向退盘")
        if "market-data-anomaly" in signal_ids:
            score -= 24
            reasons.append("盘口或水位存在异常")
        if "hot-overheat" in signal_ids:
            score -= 12
            reasons.append("热门方向过热")
        if "deep-high-water" in signal_ids:
            score -= 10
            reasons.append("深盘高水")
        market_codes = {
            str(item.get("code") or "")
            for item in market_types if isinstance(item, dict)
        }
        if "D" in market_codes:
            score -= 18
            reasons.append("热门方向未获盘口同步确认")
        if "E" in market_codes:
            score -= 15
            reasons.append("深盘高水结构")
        if "F" in market_codes:
            score -= 8
            reasons.append("杯赛波动较高")
        if risk.get("dangerous"):
            score -= 18
        elif str(risk.get("level") or "") == "高":
            score -= 12
        elif str(risk.get("level") or "") == "中":
            score -= 5
        score = max(0, min(100, score))
        level = "高" if score >= 75 else "中" if score >= 55 else "低"
        if not reasons:
            reasons.append("主要市场未发现明显背离")
        return {
            "score": score,
            "level": level,
            "reasons": reasons[:5],
        }

    def _select_score_candidates(
        self,
        context: Dict[str, Any],
        distribution: List[Dict[str, Any]],
        recommendation: Dict[str, Any],
    ) -> List[str]:
        """Keep score suggestions consistent with the engine's primary market call."""
        primary = recommendation.get("primary")
        handicap = (context.get("markets") or {}).get("sporttery_handicap")
        total_line = self._number((context.get("match") or {}).get("ou_current_total"))

        def matches(row: Dict[str, Any]) -> bool:
            home, away = row["home"], row["away"]
            if primary == "主胜":
                return home > away
            if primary == "平局":
                return home == away
            if primary == "客胜":
                return home < away
            if primary in {"让胜", "让平", "让负"} and handicap is not None:
                adjusted = home + handicap
                return (
                    (primary == "让胜" and adjusted > away)
                    or (primary == "让平" and adjusted == away)
                    or (primary == "让负" and adjusted < away)
                )
            if primary in {"大球", "小球"} and total_line is not None:
                total = home + away
                return (
                    (primary == "大球" and total > total_line)
                    or (primary == "小球" and total < total_line)
                )
            return True

        selected = [row for row in distribution if matches(row)][:2]

        # The third score is a nearby risk scenario, useful without contradicting
        # the main two suggestions. Handicap and totals keep all three on-market.
        if recommendation.get("market") == "胜平负" and len(selected) < 3:
            if primary in {"主胜", "客胜"}:
                alternatives = [row for row in distribution if row["home"] == row["away"]]
            else:
                alternatives = [row for row in distribution if row["home"] != row["away"]]
            selected.extend(alternatives[:1])

        if len(selected) < 3:
            selected.extend(row for row in distribution if matches(row) and row not in selected)
        if len(selected) < 3:
            selected.extend(row for row in distribution if row not in selected)
        return [row["score"] for row in selected[:3]]

    def _deterministic_narrative(
        self, context: Dict[str, Any], core: Dict[str, Any]
    ) -> Dict[str, Any]:
        match = context.get("match") or {}
        recommendation = core["recommendation"]
        types = "、".join(item["name"] for item in core["market_types"])
        summary = (
            f"FAE v{ENGINE_VERSION} 将本场识别为{types}。"
            f"八维综合评分{core['overall_score']}分，当前首选{recommendation['primary']}，"
            f"FAE估算概率{recommendation['probability']}%（未校准），"
            f"赔率价值{recommendation.get('value_score', 0)}分，"
            f"盘口可信度{(recommendation.get('market_confidence') or {}).get('score', 0)}分，"
            f"投注结论{recommendation.get('decision', '观望')}。"
            f"结论由{match.get('home_team', '主队')}与{match.get('away_team', '客队')}的盘口、"
            "欧赔、大小球、竞彩及已接入基本面共同计算；缺失数据已在风险中降权。"
        )
        signals = [s for s in core["rule_signals"] if not s.get("risk")]
        signals.sort(key=lambda item: item["contribution"], reverse=True)
        evidence = [item["reason"] for item in signals[:6]]
        if not evidence:
            evidence = ["当前有效市场变化不足，推荐主要依据即时赔率的归一化概率"]
        return {
            "summary": summary,
            "evidence": self._unique_list(evidence, 6),
            "risks": self._unique_list(core["risk"]["reasons"], 8),
        }

    def _build_narrative_prompt(
        self,
        context: Dict[str, Any],
        core: Dict[str, Any],
        active_skills: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        schema = {
            "summary": "100到220字中文说明",
            "evidence": ["最多6条，只能引用输入或FAE计算结果"],
            "risks": ["最多8条数据缺口、冲突或危险盘口"],
        }
        return "\n\n".join([
            f"你是 {ENGINE_NAME} v{ENGINE_VERSION} 的说明层，不是决策层。",
            "FAE 核心分数、概率、推荐、星级、盘口分类均已由确定性代码计算。"
            "不得修改、重新推荐或伪造伤停、首发、天气、战意和命中率。"
            "升/降属于走势，不属于盘口名称。只输出合法 JSON。",
            "# 输出结构\n" + json.dumps(schema, ensure_ascii=False, indent=2),
            "# 当前Skill版本\n" + json.dumps([
                {
                    "skill_id": item.get("skill_id"),
                    "version": item.get("version"),
                    "guidance": item.get("guidance"),
                }
                for item in (active_skills or [])
            ], ensure_ascii=False, indent=2),
            "# FAE 核心结果\n" + json.dumps(core, ensure_ascii=False, indent=2),
            "# 输入数据\n" + json.dumps(context, ensure_ascii=False, indent=2),
        ])

    @classmethod
    def _normalize_narrative(
        cls, data: Dict[str, Any], fallback: Dict[str, Any]
    ) -> Dict[str, Any]:
        summary = cls._clean_text(data.get("summary"), 700) or fallback["summary"]
        evidence = cls._clean_list(data.get("evidence"), 6, 180) or fallback["evidence"]
        risks = cls._clean_list(data.get("risks"), 8, 180) or fallback["risks"]
        return {"summary": summary, "evidence": evidence, "risks": risks}

    @staticmethod
    def _extract_json(raw_text: str) -> Dict[str, Any]:
        text = raw_text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise FAEOutputError("AI 输出不是 JSON 对象")
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise FAEOutputError(f"AI 输出 JSON 解析失败: {exc.msg}") from exc
        if not isinstance(data, dict):
            raise FAEOutputError("AI 输出必须是 JSON 对象")
        return data

    @classmethod
    def _form_summary(
        cls, team: str, rows: Any, side: str
    ) -> Tuple[Dict[str, Any], List[str]]:
        valid = []
        issues = []
        for row in rows if isinstance(rows, list) else []:
            score = cls._parse_score(row.get("score")) if isinstance(row, dict) else None
            if not score:
                continue
            home = str(row.get("home_team") or row.get("homeTeamShortName") or "")
            away = str(row.get("away_team") or row.get("awayTeamShortName") or "")
            if team and team not in (home, away):
                continue
            home_goals, away_goals = score
            is_home = team == home
            scored, conceded = (home_goals, away_goals) if is_home else (away_goals, home_goals)
            points = 3 if scored > conceded else 1 if scored == conceded else 0
            valid.append({"points": points, "scored": scored, "conceded": conceded, "is_home": is_home})
            if len(valid) >= 10:
                break
        raw_finished = sum(
            1 for row in rows if isinstance(rows, list) and isinstance(row, dict) and cls._parse_score(row.get("score"))
        )
        if raw_finished >= 3 and len(valid) < max(1, raw_finished // 2):
            issues.append(f"{('主队' if side == 'home' else '客队')}近期数据疑似混入其他球队比赛")
        count = len(valid)
        return {
            "valid_matches": count,
            "wins": sum(1 for item in valid if item["points"] == 3),
            "draws": sum(1 for item in valid if item["points"] == 1),
            "losses": sum(1 for item in valid if item["points"] == 0),
            "goals_for": sum(item["scored"] for item in valid),
            "goals_against": sum(item["conceded"] for item in valid),
            "points_per_game": round(sum(item["points"] for item in valid) / count, 2) if count else 0,
            "goals_for_per_game": round(sum(item["scored"] for item in valid) / count, 2) if count else 0,
            "goals_against_per_game": round(sum(item["conceded"] for item in valid) / count, 2) if count else 0,
        }, issues

    @classmethod
    def _history_summary(
        cls, home_team: str, away_team: str, rows: List[Any]
    ) -> Dict[str, Any]:
        valid = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            score = cls._parse_score(row.get("score"))
            home = str(row.get("home_team") or "")
            away = str(row.get("away_team") or "")
            if not score or {home, away} != {home_team, away_team}:
                continue
            home_goals, away_goals = score
            if home_goals == away_goals:
                winner = "draw"
            elif (home_goals > away_goals and home == home_team) or (away_goals > home_goals and away == home_team):
                winner = "home_team"
            else:
                winner = "away_team"
            valid.append(winner)
        count = len(valid)
        home_wins = valid.count("home_team")
        away_wins = valid.count("away_team")
        tendency = "历史交锋样本不足"
        if count >= 3:
            tendency = "主队历史交锋占优" if home_wins > away_wins else "客队历史交锋占优" if away_wins > home_wins else "历史交锋接近"
        return {
            "valid_matches": count,
            "home_team_wins": home_wins,
            "draws": valid.count("draw"),
            "away_team_wins": away_wins,
            "home_team_win_rate": round(home_wins / count, 3) if count else 0,
            "away_team_win_rate": round(away_wins / count, 3) if count else 0,
            "tendency": tendency,
        }

    @classmethod
    def _data_completeness(
        cls, match, home_form, away_form, history, standings,
        injuries, lineups, weather,
    ) -> int:
        checks = [
            all(match.get(key) not in (None, "") for key in ("euro_current_win", "euro_current_draw", "euro_current_lose")),
            match.get("asian_current_handicap") not in (None, ""),
            match.get("ou_current_total") not in (None, ""),
            match.get("handicap") not in (None, ""),
            home_form.get("valid_matches", 0) >= 3,
            away_form.get("valid_matches", 0) >= 3,
            history.get("valid_matches", 0) >= 3,
            bool(standings), bool(injuries), bool(lineups), bool(weather),
        ]
        return round(sum(bool(value) for value in checks) / len(checks) * 100)

    @classmethod
    def _odds_movement(cls, match: Dict[str, Any]) -> Dict[str, Any]:
        movement: Dict[str, Any] = {}
        euro = {}
        for name, initial_key, current_key in (
            ("win", "euro_initial_win", "euro_current_win"),
            ("draw", "euro_initial_draw", "euro_current_draw"),
            ("lose", "euro_initial_lose", "euro_current_lose"),
        ):
            item = cls._numeric_change(match.get(initial_key), match.get(current_key))
            if item:
                euro[name] = item
        if euro:
            movement["euro"] = euro

        asian = {}
        initial_line = cls._handicap_value(match.get("asian_initial_handicap"))
        current_line = cls._handicap_value(match.get("asian_current_handicap"))
        if initial_line is not None and current_line is not None:
            diff = round(current_line - initial_line, 2)
            asian["handicap"] = {
                "initial": cls._clean_handicap(match.get("asian_initial_handicap")),
                "current": cls._clean_handicap(match.get("asian_current_handicap")),
                "change": diff,
                "direction": cls._handicap_movement_direction(
                    initial_line, current_line
                ),
                "toward": "主队" if diff > 0 else "客队" if diff < 0 else "不变",
            }
        for side in ("home", "away"):
            item = cls._numeric_change(
                match.get(f"asian_initial_{side}_odds"),
                match.get(f"asian_current_{side}_odds"),
            )
            if item:
                asian[f"{side}_water"] = item
        if asian:
            movement["asian"] = asian

        total = {}
        line_item = cls._numeric_change(match.get("ou_initial_total"), match.get("ou_current_total"))
        if line_item:
            total["line"] = line_item
        for side in ("over", "under"):
            item = cls._numeric_change(
                match.get(f"ou_initial_{side}_odds"),
                match.get(f"ou_current_{side}_odds"),
            )
            if item:
                total[side] = item
        if total:
            movement["over_under"] = total
        return movement

    @classmethod
    def _handicap_value(cls, value: Any) -> Optional[float]:
        text = re.sub(r"\s+", "", cls._clean_handicap(value))
        if not text:
            return None
        number = cls._number(text)
        if number is not None and re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
            return number
        receiving = text.startswith("受")
        key = text[1:] if receiving else text
        if key not in cls.HANDICAP_VALUES:
            return None
        result = cls.HANDICAP_VALUES[key]
        return -result if receiving else result

    @staticmethod
    def _handicap_movement_direction(
        initial: float, current: float
    ) -> str:
        if current == initial:
            return "不变"
        if initial == 0 or current == 0 or initial * current > 0:
            if abs(current) > abs(initial):
                return "升盘"
            if abs(current) < abs(initial):
                return "降盘"
        return "转向主队" if current > initial else "转向客队"

    @staticmethod
    def _clean_handicap(value: Any) -> str:
        return re.sub(r"(?:[↑↓]|升|降)+$", "", str(value or "").strip())

    @classmethod
    def _numeric_change(cls, initial: Any, current: Any) -> Optional[Dict[str, Any]]:
        initial_number, current_number = cls._number(initial), cls._number(current)
        if initial_number is None or current_number is None:
            return None
        change = round(current_number - initial_number, 3)
        return {
            "initial": initial_number, "current": current_number,
            "change": change,
            "direction": "升" if change > 0 else "降" if change < 0 else "不变",
        }

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        try:
            text = re.sub(r"[^\d.+-]", "", str(value))
            return float(text) if text not in ("", "+", "-", ".") else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_score(value: Any) -> Optional[Tuple[int, int]]:
        match = re.fullmatch(r"\s*(\d+)\s*[:-]\s*(\d+)\s*", str(value or ""))
        return (int(match.group(1)), int(match.group(2))) if match else None

    @staticmethod
    def _poisson(goals: int, expected: float) -> float:
        return math.exp(-expected) * expected ** goals / math.factorial(goals)

    @staticmethod
    def _stars(score: Any) -> float:
        try:
            value = max(0, min(100, float(score)))
        except (TypeError, ValueError):
            value = 0
        return max(1.0, min(5.0, round((value / 20) * 2) / 2))

    @staticmethod
    def _star_text(stars: float) -> str:
        rating = max(0.0, min(5.0, float(stars or 0)))
        full = max(0, min(5, int(rating)))
        text = "★" * full + "☆" * (5 - full)
        return f"{text} · {rating:g}星" if rating % 1 else text

    @staticmethod
    def _prediction_context(prediction: Dict[str, Any]) -> Dict[str, Any]:
        fields = (
            "win_prediction", "win_confidence", "asian_prediction",
            "asian_handicap", "asian_confidence", "ou_prediction", "ou_total",
            "ou_confidence", "predicted_home_score", "predicted_away_score",
        )
        return {key: prediction.get(key) for key in fields if prediction.get(key) not in (None, "")}

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items() if str(key) != "_id"}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        if isinstance(value, datetime):
            return value.isoformat()
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    @classmethod
    def _limited_list(cls, value: Any, limit: int) -> List[Any]:
        return [cls._json_safe(item) for item in value[:limit]] if isinstance(value, list) else []

    @staticmethod
    def _clean_text(value: Any, max_length: int) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()[:max_length]

    @classmethod
    def _clean_list(cls, value: Any, max_items: int, max_length: int) -> List[str]:
        if not isinstance(value, list):
            return []
        return cls._unique_list([cls._clean_text(item, max_length) for item in value], max_items)

    @staticmethod
    def _unique_list(values: Iterable[Any], limit: int) -> List[str]:
        result = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in result:
                result.append(text)
            if len(result) >= limit:
                break
        return result
