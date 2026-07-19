"""Versioned FAE Skill definitions and review-driven candidate evaluation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional

from .version import DEFAULT_RULE_WEIGHTS, ENGINE_VERSION


SKILL_SCHEMA_VERSION = "1.0"

SKILL_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "euro-odds": {
        "label": "欧赔与市场共识",
        "description": "识别胜平负赔率变化，以及欧赔与亚洲盘的一致性。",
        "rule_ids": (
            "euro-home-support",
            "euro-away-support",
            "euro-draw-support",
            "market-consensus-home",
            "market-consensus-away",
        ),
        "guidance": "只解释可验证的欧赔变化和多市场共识，不把热度直接等同于赛果。",
    },
    "asian-handicap": {
        "label": "亚洲盘口",
        "description": "评估升降盘、主客水位与让球方向。",
        "rule_ids": (
            "asian-line-home",
            "asian-line-away",
            "asian-home-water",
            "asian-away-water",
        ),
        "guidance": "盘口名称与升降走势必须分开表达，优先识别盘口和水位是否同向。",
    },
    "over-under": {
        "label": "大小球",
        "description": "根据进球盘口与水位变化评估大小球倾向。",
        "rule_ids": ("total-over", "total-under"),
        "guidance": "大小球只引用已录入的初盘、即时盘和水位变化。",
    },
    "form-history": {
        "label": "状态与交锋",
        "description": "使用近期状态和历史交锋补充市场判断。",
        "rule_ids": (
            "recent-form-home",
            "recent-form-away",
            "history-home",
            "history-away",
        ),
        "guidance": "近期比赛必须确认球队身份，交锋只作为低权重辅助证据。",
    },
    "risk-control": {
        "label": "风险控制",
        "description": "识别过热、退盘、深盘高水、杯赛波动和数据缺口。",
        "rule_ids": (
            "hot-overheat",
            "handicap-drop",
            "euro-asian-divergence",
            "market-data-anomaly",
            "deep-high-water",
            "cup-variance",
            "data-quality",
        ),
        "guidance": "风险信号用于降低置信度，不得反向伪造新的推荐方向。",
    },
    "draw-strategy": {
        "label": "平局 / 让平组合",
        "description": "根据专项复盘的命中率和 ROI 调整平局、让平组合排序。",
        "rule_ids": (),
        "guidance": "只影响平局与让平候选的排序，不改变单场 FAE 核心概率。",
        "parameter_kind": "strategy_weights",
    },
}


def next_patch_version(version: str) -> str:
    """Return the next patch version, falling back to 1.0.1."""
    try:
        major, minor, patch = [int(part) for part in str(version).split(".", 2)]
    except (TypeError, ValueError):
        return "1.0.1"
    return f"{major}.{minor}.{patch + 1}"


def baseline_skill_documents(
    rule_weights: Optional[Dict[str, float]] = None,
    strategy_weights: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """Build the initial active document for every built-in Skill."""
    rules = {**DEFAULT_RULE_WEIGHTS, **(rule_weights or {})}
    strategies = {"平局": 1.0, "让平": 1.0, **(strategy_weights or {})}
    documents = []
    for skill_id, definition in SKILL_DEFINITIONS.items():
        kind = definition.get("parameter_kind") or "rule_weights"
        parameters = (
            {"strategy_weights": {
                key: round(float(strategies.get(key, 1.0)), 3)
                for key in ("平局", "让平")
            }}
            if kind == "strategy_weights"
            else {"rule_weights": {
                rule_id: round(float(rules.get(rule_id, 1.0)), 3)
                for rule_id in definition["rule_ids"]
            }}
        )
        documents.append({
            "skill_id": skill_id,
            "label": definition["label"],
            "description": definition["description"],
            "guidance": definition["guidance"],
            "schema_version": SKILL_SCHEMA_VERSION,
            "engine_version": ENGINE_VERSION,
            "version": "1.0.0",
            "status": "active",
            "parameters": parameters,
            "learning_snapshot": {},
            "source": "system-baseline",
        })
    return documents


def build_rule_skill_candidate(
    active_skill: Dict[str, Any],
    rule_stats: Iterable[Dict[str, Any]],
    minimum_samples: int = 10,
    minimum_new_samples: int = 10,
) -> Optional[Dict[str, Any]]:
    """Propose and validate a rule-weight Skill candidate from review outcomes."""
    current = dict(
        ((active_skill.get("parameters") or {}).get("rule_weights") or {})
    )
    if not current:
        return None
    snapshot = active_skill.get("learning_snapshot") or {}
    stats_by_rule = {
        str(item.get("rule_id")): item
        for item in rule_stats
        if item.get("rule_id")
    }
    proposed = dict(current)
    changes = []
    evidence_snapshot = dict(snapshot)
    evaluated_rows = []
    for rule_id, old_value in current.items():
        stats = stats_by_rule.get(rule_id) or {}
        samples = int(stats.get("samples") or 0)
        hits = int(stats.get("hits") or 0)
        accuracy = hits / samples if samples else 0
        previous_samples = int(snapshot.get(rule_id) or 0)
        if samples < minimum_samples or samples - previous_samples < minimum_new_samples:
            continue
        target = float(old_value)
        action = "hold"
        if accuracy >= 0.80:
            target = min(1.30, target + 0.05)
            action = "increase"
        elif accuracy < 0.60:
            target = max(0.70, target - 0.05)
            action = "decrease"
        if round(target, 3) == round(float(old_value), 3):
            continue
        proposed[rule_id] = round(target, 3)
        evidence_snapshot[rule_id] = samples
        changes.append({
            "parameter": f"rule_weights.{rule_id}",
            "rule_id": rule_id,
            "previous": round(float(old_value), 3),
            "proposed": round(target, 3),
            "action": action,
            "samples": samples,
            "new_samples": samples - previous_samples,
            "hits": hits,
            "accuracy": round(accuracy, 4),
            "reason": (
                f"命中率{accuracy * 100:.1f}%达到升权标准"
                if action == "increase"
                else f"命中率{accuracy * 100:.1f}%低于保留标准"
            ),
        })
        evaluated_rows.append({
            "samples": samples,
            "accuracy": accuracy,
            "before": float(old_value),
            "after": target,
        })
    if not changes:
        return None

    total_samples = sum(row["samples"] for row in evaluated_rows)
    baseline_utility = sum(
        (2 * row["accuracy"] - 1) * row["before"] * row["samples"]
        for row in evaluated_rows
    ) / max(1, total_samples)
    candidate_utility = sum(
        (2 * row["accuracy"] - 1) * row["after"] * row["samples"]
        for row in evaluated_rows
    ) / max(1, total_samples)
    delta = candidate_utility - baseline_utility
    passed = delta > 0
    return {
        "skill_id": active_skill.get("skill_id"),
        "label": active_skill.get("label"),
        "parent_version": active_skill.get("version"),
        "proposed_version": next_patch_version(active_skill.get("version")),
        "parameters": {"rule_weights": proposed},
        "guidance": active_skill.get("guidance"),
        "learning_snapshot": evidence_snapshot,
        "changes": changes,
        "evaluation": {
            "method": "historical-rule-replay",
            "passed": passed,
            "sample_count": total_samples,
            "baseline_utility": round(baseline_utility, 4),
            "candidate_utility": round(candidate_utility, 4),
            "improvement": round(delta, 4),
            "limitations": "规则级历史重放；发布后仍需观察完整推荐的线上表现",
        },
        "status": "validated" if passed else "rejected",
        "source": "fae-review-learning",
    }


def build_draw_skill_candidate(
    active_skill: Dict[str, Any],
    selection_stats: Dict[str, Dict[str, Any]],
    minimum_samples: int = 10,
    minimum_new_samples: int = 10,
) -> Optional[Dict[str, Any]]:
    """Propose draw-strategy weights from historical ROI."""
    current = dict(
        ((active_skill.get("parameters") or {}).get("strategy_weights") or {})
    )
    if not current:
        return None
    snapshot = active_skill.get("learning_snapshot") or {}
    proposed = deepcopy(current)
    evidence_snapshot = dict(snapshot)
    changes = []
    baseline_utility = 0.0
    candidate_utility = 0.0
    total_samples = 0
    for selection in ("平局", "让平"):
        summary = selection_stats.get(selection) or {}
        samples = int(summary.get("settled") or 0)
        previous_samples = int(snapshot.get(selection) or 0)
        roi = float(summary.get("roi") or 0)
        if (
            samples < minimum_samples
            or samples - previous_samples < minimum_new_samples
            or abs(roi) < 5
        ):
            continue
        old_value = float(current.get(selection) or 1.0)
        steps = min(6, max(1, int(abs(roi) / 10)))
        target = (
            min(1.30, 1 + steps * 0.05)
            if roi > 0
            else max(0.70, 1 - steps * 0.05)
        )
        if round(target, 3) == round(old_value, 3):
            continue
        proposed[selection] = round(target, 3)
        evidence_snapshot[selection] = samples
        changes.append({
            "parameter": f"strategy_weights.{selection}",
            "selection": selection,
            "previous": round(old_value, 3),
            "proposed": round(target, 3),
            "action": "increase" if target > old_value else "decrease",
            "samples": samples,
            "new_samples": samples - previous_samples,
            "hit_rate": float(summary.get("hit_rate") or 0),
            "roi": roi,
            "reason": f"专项历史ROI为{roi:+.1f}%",
        })
        baseline_utility += roi * old_value * samples
        candidate_utility += roi * target * samples
        total_samples += samples
    if not changes:
        return None
    baseline_utility /= max(1, total_samples)
    candidate_utility /= max(1, total_samples)
    delta = candidate_utility - baseline_utility
    passed = delta > 0
    return {
        "skill_id": active_skill.get("skill_id"),
        "label": active_skill.get("label"),
        "parent_version": active_skill.get("version"),
        "proposed_version": next_patch_version(active_skill.get("version")),
        "parameters": {"strategy_weights": proposed},
        "guidance": active_skill.get("guidance"),
        "learning_snapshot": evidence_snapshot,
        "changes": changes,
        "evaluation": {
            "method": "historical-roi-replay",
            "passed": passed,
            "sample_count": total_samples,
            "baseline_utility": round(baseline_utility, 4),
            "candidate_utility": round(candidate_utility, 4),
            "improvement": round(delta, 4),
            "limitations": "基于历史单场ROI重放；串关相关性需继续在线观察",
        },
        "status": "validated" if passed else "rejected",
        "source": "fae-draw-review-learning",
    }
