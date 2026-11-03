"""Prospective paper trading. No paid LLM, actual bets, or retrospective picks."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
from itertools import combinations
import json
import math
from pathlib import Path
import random
from typing import Any, Dict, List

from fae_schedule import app_timezone, parse_generated_at, parse_match_datetime
from .daily_review import FAEDailyAIReviewEngine, _score
from .engine import FootballAIEngine
from .version import DEFAULT_RULE_WEIGHTS, ENGINE_VERSION


TRIAL_VERSION = "paper-two-leg-v1"
# These are pre-registered experimental thresholds, NOT proven profitable ones.
TRIAL_POLICY = {
    "version": TRIAL_VERSION,
    "days": 30,
    "decision_hour": 18,
    "decision_minute": 0,
    "grace_minutes": 5,
    "stake_units": 1,
    "min_leg_odds": 1.5,
    "min_combined_odds": 2.8,
    "max_combined_odds": 3.3,
    "model_weight": 0.35,
    "min_leg_probability": 45,
    "min_leg_expected_return": 1.0,
    "max_heat_deviation_pp": 10,
    "max_market_age_minutes": 60,
    "min_lead_minutes": 10,
    "probabilities_calibrated": False,
    "llm_enabled": False,
    "automatic_learning": False,
}
SELECTIONS = {"ordinary": ("主胜", "平局", "客胜"),
              "handicap": ("让胜", "让平", "让负")}


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             default=str, separators=(",", ":")).encode()).hexdigest()


def model_fingerprint():
    """Pin implementation as well as parameters. Changes suspend new picks."""
    root = Path(__file__).resolve().parent
    files = [root / name for name in (
        "strategy_trial.py", "engine.py", "market_rules.py", "version.py",
        "provider.py", "daily_review.py",
    )] + [root.parent / "fae_schedule.py"]
    return digest({str(path.name): sha256(path.read_bytes()).hexdigest()
                   for path in files})


def start_trial(now=None):
    current = now or datetime.now(app_timezone())
    current = current.replace(tzinfo=app_timezone()) if current.tzinfo is None else current.astimezone(app_timezone())
    day = current.date()
    # Never enrol after the decision time and then claim a same-day forecast.
    if current >= decision_time(day.isoformat(), TRIAL_POLICY):
        day += timedelta(days=1)
    policy = deepcopy(TRIAL_POLICY)
    return {
        "experiment_id": TRIAL_VERSION,
        "created_at": current.isoformat(),
        "start_date": day.isoformat(),
        "end_date": (day + timedelta(days=policy["days"] - 1)).isoformat(),
        "policy": policy,
        "policy_hash": digest(policy),
        "model_fingerprint": model_fingerprint(),
        "engine_version": ENGINE_VERSION,
        "rule_weights": deepcopy(DEFAULT_RULE_WEIGHTS),
        "weights_hash": digest(DEFAULT_RULE_WEIGHTS),
        "mode": "paper_only",
    }


def decision_time(owner_date, policy):
    return datetime.strptime(owner_date, "%Y-%m-%d").replace(
        hour=policy["decision_hour"], minute=policy["decision_minute"],
        tzinfo=app_timezone())


def trial_slot(trial, owner_date, now):
    if not trial["start_date"] <= owner_date <= trial["end_date"]:
        return "outside"
    due = decision_time(owner_date, trial["policy"])
    if now < due:
        return "waiting"
    if now >= due + timedelta(minutes=trial["policy"]["grace_minutes"]):
        return "missed"
    return "due"


def _odds(match, market):
    fields = (("euro_current_win", "euro_current_draw", "euro_current_lose")
              if market == "ordinary" else
              ("hi_current_home_odds", "hi_current_draw_odds", "hi_current_away_odds"))
    values = [number(match.get(field)) for field in fields]
    return values if all(v is not None and v > 1 for v in values) else []


def _market_rows(match, analysis, policy):
    probabilities = analysis.get("probabilities") or {}
    output = []
    for market, labels in SELECTIONS.items():
        prices = _odds(match, market)
        handicap = number(match.get("hi_handicap_value", match.get("handicap")))
        if not prices or (market == "handicap" and (handicap is None or not handicap.is_integer())):
            continue
        denominator = sum(1 / price for price in prices)
        model_values = ([probabilities.get(key) for key in ("home_win", "draw", "away_win")]
                        if market == "ordinary" else
                        [(probabilities.get("hhad") or {}).get(key) for key in ("win", "draw", "lose")])
        for index, (selection, price) in enumerate(zip(labels, prices)):
            market_probability = 100 / price / denominator
            model_probability = number(model_values[index])
            blended = (model_probability * policy["model_weight"] + market_probability * (1 - policy["model_weight"])
                       if model_probability is not None else None)
            support = number(((match.get("betting_ratio") or {}).get(market) or {}).get(
                ("home", "draw", "away")[index] + "_support_rate"))
            output.append({
                "match_id": str(match.get("match_id") or ""),
                "match_number": match.get("match_number") or match.get("round_id"),
                "home_team": match.get("home_team"), "away_team": match.get("away_team"),
                "league": match.get("league"), "match_time": match.get("match_time"),
                "market": market, "selection": selection,
                "selection_text": f"{selection}({handicap:+g})" if market == "handicap" else selection,
                "handicap": handicap if market == "handicap" else None,
                "odds": price,
                "model_probability": model_probability,
                "market_probability": round(market_probability, 4),
                "probability": round(blended, 4) if blended is not None else None,
                "support_rate": support,
                "heat_deviation_pp": round(support - market_probability, 4) if support is not None else None,
                "estimated_return": blended / 100 * price if blended is not None else None,
            })
    return output


def _choose_ticket(candidates, policy, *, baseline=False):
    # Exactly one leading direction per fixture. Never change to a lower-ranked
    # outcome merely to hit the desired product price.
    by_match = {}
    for row in candidates:
        key = "market_probability" if baseline else "probability"
        if number(row.get(key)) is None:
            continue
        current = by_match.get(row["match_id"])
        if current is None or (row[key], row["selection"]) > (current[key], current["selection"]):
            by_match[row["match_id"]] = row
    choices = []
    for picks in combinations(sorted(by_match.values(), key=lambda row: row["match_id"]), 2):
        odds = picks[0]["odds"] * picks[1]["odds"]
        if not policy["min_combined_odds"] <= odds <= policy["max_combined_odds"]:
            continue
        key = "market_probability" if baseline else "probability"
        probability = picks[0][key] * picks[1][key] / 100
        choices.append((probability, odds, picks))
    if not choices:
        return None
    probability, odds, picks = max(choices, key=lambda item: (item[0], -abs(item[1] - 3)))
    return {
        "picks": deepcopy(list(picks)), "combined_odds": round(odds, 6),
        "stake": policy["stake_units"], "status": "pending",
        "estimated_hit_probability": round(probability, 4),
        "breakeven_probability": round(100 / odds, 4),
        "probability_note": "未校准估计，按独立近似相乘，不是真实胜率或盈利承诺",
    }


def build_trial_day(trial, owner_date, matches, fundamentals=None, *, now=None,
                    engine=None, fingerprint=None):
    current = now or datetime.now(app_timezone())
    policy = trial["policy"]
    slot = trial_slot(trial, owner_date, current)
    if slot in {"outside", "waiting"}:
        raise ValueError("尚未到固定出单时刻，或不在试运行日期内")
    record = {
        "experiment_id": trial["experiment_id"], "owner_date": owner_date,
        "scheduled_at": decision_time(owner_date, policy).isoformat(),
        "captured_at": current.isoformat(), "policy_hash": trial["policy_hash"],
        "model_fingerprint": trial["model_fingerprint"], "policy": deepcopy(policy),
        "decision": "no_ticket", "reason": "合格的独立主选不足两场，或合计赔率不在固定范围内",
        "ticket": None, "baseline_ticket": None, "audit": [], "mode": "paper_only",
    }
    if slot == "missed":
        record.update(decision="missed", reason="错过18:00出单窗口，不用后来数据补选")
    elif (digest(policy) != trial["policy_hash"]
          or digest(trial["rule_weights"]) != trial["weights_hash"]
          or (fingerprint or model_fingerprint()) != trial["model_fingerprint"]):
        record.update(decision="suspended", reason="规则或模型代码与冻结版本不一致，暂停出单；原记录仍继续结算")
    else:
        calculator = engine or FootballAIEngine()
        candidates, baseline = [], []
        for match in sorted(matches, key=lambda row: str(row.get("match_id") or "")):
            audit = {"match_id": str(match.get("match_id") or ""),
                     "match_number": match.get("match_number"),
                     "raw_snapshot": deepcopy(match), "rejections": []}
            record["audit"].append(audit)
            kickoff = parse_match_datetime(match)
            captured = match.get("updated_at")
            observed = captured if isinstance(captured, datetime) else parse_generated_at(captured)
            if isinstance(observed, datetime):
                observed = observed.replace(tzinfo=app_timezone()) if observed.tzinfo is None else observed.astimezone(app_timezone())
            if (str(match.get("owner_date") or "")[:10] != owner_date
                    or not match.get("match_id") or match.get("status") not in (0, "0")
                    or not kickoff or kickoff <= current + timedelta(minutes=policy["min_lead_minutes"])):
                audit["rejections"].append("已开始、开赛时间缺失、临近开赛或非当天比赛")
                continue
            if not observed or not 0 <= (current - observed).total_seconds() <= policy["max_market_age_minutes"] * 60:
                audit["rejections"].append("缺少可验证的采集时间，或盘口快照超过60分钟/来自未来")
                continue
            source = deepcopy((fundamentals or {}).get(audit["match_id"]) or {})
            cached = parse_generated_at(source.get("cached_at"))
            if cached and cached > current:
                source = {}
            # Reject future results even if the upstream fundamentals cache is bad.
            for side in ("home", "away"):
                recent = (source.get("recent") or {}).get(side) or []
                if recent:
                    source.setdefault("recent", {})[side] = [r for r in recent if str(r.get("date") or "")[:10] < owner_date]
            context = calculator.build_context(match, source_analysis=source)
            result = calculator.generate_from_context(context, rule_weights=trial["rule_weights"], use_ai=False)
            analysis = result.get("analysis") or {}
            audit.update(context=context, model_result=result)
            rows = [r for r in _market_rows(match, analysis, policy) if r["odds"] >= policy["min_leg_odds"]]
            baseline.extend(rows)
            # Only the top prediction is eligible; failures must not promote
            # the second/third option into a fabricated primary recommendation.
            primary = max(rows, key=lambda row: (row["probability"] or -1, row["selection"]), default=None)
            if not primary:
                audit["rejections"].append("没有赔率不低于1.50的完整胜平负/竞彩让球市场")
                continue
            reasons = audit["rejections"]
            if not _odds(match, "ordinary") or not _odds(match, "handicap"):
                reasons.append("胜平负或竞彩让球市场不完整")
            total = number(match.get("ou_current_total"))
            if (FootballAIEngine._handicap_value(match.get("asian_current_handicap")) is None
                    or total is None or not 0 < total <= 8
                    or number(match.get("asian_current_home_odds")) is None
                    or number(match.get("asian_current_away_odds")) is None
                    or number(match.get("ou_current_over_odds")) is None
                    or number(match.get("ou_current_under_odds")) is None):
                reasons.append("缺少亚盘或大小球，不使用默认盘口补齐")
            ratio_time = parse_generated_at((match.get("betting_ratio") or {}).get("fetched_at"))
            ratio_market = (match.get("betting_ratio") or {}).get(primary["market"]) or {}
            rates = [number(ratio_market.get(side + "_support_rate")) for side in ("home", "draw", "away")]
            valid_rates = (all(rate is not None and 0 <= rate <= 100 for rate in rates)
                           and 98 <= sum(rate or 0 for rate in rates) <= 102)
            if (primary["heat_deviation_pp"] is None or not ratio_time
                    or not valid_rates
                    or not 0 <= (current - ratio_time).total_seconds() <= policy["max_market_age_minutes"] * 60):
                reasons.append("所选市场投注比例缺失或过期")
            elif primary["heat_deviation_pp"] > policy["max_heat_deviation_pp"]:
                reasons.append("主选投注偏离超过+10个百分点")
            if (primary["market"] == "handicap" and ratio_market.get("handicap_value") is not None
                    and number(ratio_market["handicap_value"]) != primary["handicap"]):
                reasons.append("投注比例让球数与当前所选盘口不一致")
            if (analysis.get("risk") or {}).get("dangerous"):
                reasons.append("确定性引擎标记危险盘口")
            if primary["probability"] is None or primary["probability"] < policy["min_leg_probability"]:
                reasons.append("单腿融合估计低于45%")
            if primary["estimated_return"] is None or primary["estimated_return"] < policy["min_leg_expected_return"]:
                reasons.append("单腿未校准模型期望小于1.00")
            audit["primary"] = primary
            if not reasons:
                candidates.append(primary)
        record["baseline_ticket"] = _choose_ticket(baseline, policy, baseline=True)
        record["ticket"] = _choose_ticket(candidates, policy)
        record["eligible_match_count"] = len(candidates)
        if record["ticket"]:
            record.update(decision="ticket", reason="固定门槛通过；仅模拟，不是正式盈利策略")
        elif len(candidates) >= 2:
            record["reason"] = f"{len(candidates)}场通过单腿条件，但保留各场第一方向后，没有2.8～3.3倍组合，不换项凑单"
    record["excluded_matches"] = [{
        "match_id": row["match_id"], "match_number": row.get("match_number"),
        "reasons": row["rejections"], "selection": (row.get("primary") or {}).get("selection"),
    } for row in record["audit"] if row["rejections"]]
    record["snapshot_hash"] = digest(record)
    return record


def settle_ticket(ticket, results):
    if not ticket:
        return None
    output = deepcopy(ticket)
    legs = []
    payout = 1.0
    for pick in ticket["picks"]:
        match = results.get(pick["match_id"]) or {}
        score = _score(match)
        status = ("push" if match.get("status") in (6, "6") else
                  FAEDailyAIReviewEngine._grade(pick["selection"], *score, handicap=pick.get("handicap"), total_line=None)
                  if match.get("status") in (2, "2") and score else "pending")
        leg = {**pick, "status": status, "result_score": f"{score[0]}:{score[1]}" if score else None}
        legs.append(leg)
        payout *= pick["odds"] if status == "hit" else 1 if status == "push" else 0
    # A losing leg is logically enough to lose, but don't mix partial tickets
    # into the chronological financial sample before both fixtures settle.
    statuses = [leg["status"] for leg in legs]
    status = ("pending" if any(s not in {"hit", "miss", "push"} for s in statuses) else "miss" if "miss" in statuses else
              "push" if all(value == "push" for value in statuses) else "hit")
    output.update(picks=legs, status=status,
                  return_units=round(payout * ticket["stake"], 6) if status != "pending" else None,
                  profit_units=round((payout - 1) * ticket["stake"], 6) if status != "pending" else None)
    return output


def trial_report(trial, days, reviews, *, now=None):
    current = now or datetime.now(app_timezone())
    review_by_day = {r["owner_date"]: r for r in reviews}
    rows = []
    for day in sorted(days, key=lambda row: row["owner_date"]):
        review = review_by_day.get(day["owner_date"]) or {}
        if review.get("snapshot_hash") != day["snapshot_hash"]:
            review = {}
        settlement = {key: review[key] for key in ("ticket", "baseline_ticket", "reviewed_at") if key in review}
        rows.append({k: v for k, v in {**day, **settlement}.items() if k != "audit"})
    def summarize(key):
        tickets = [row[key] for row in rows if row.get(key)]
        settled = [t for t in tickets if t["status"] in {"hit", "miss", "push"}]
        decided = [t for t in settled if t["status"] != "push"]
        stake = sum(t["stake"] for t in settled)
        profit = sum(t["profit_units"] for t in settled)
        hits = sum(t["status"] == "hit" for t in decided)
        equity = peak = drawdown = 0.0
        streak = longest = 0
        for ticket in settled:
            equity += ticket["profit_units"]
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
            streak = streak + 1 if ticket["status"] == "miss" else 0
            longest = max(longest, streak)
        interval = None
        roi_interval = None
        if decided:
            n, z = len(decided), 1.96
            p = hits / n
            center = (p + z * z / (2 * n)) / (1 + z * z / n)
            half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
            interval = [round((center - half) * 100, 1), round((center + half) * 100, 1)]
        if len(settled) >= 5:
            # Deterministic bootstrap of ticket returns: descriptive uncertainty
            # only. Selection bias / between-day dependence can still widen it.
            rng = random.Random(731)
            profits = [t["profit_units"] / t["stake"] for t in settled]
            boot = sorted(sum(rng.choice(profits) for _ in profits) / len(profits) * 100 for _ in range(2000))
            roi_interval = [round(boot[49], 1), round(boot[1949], 1)]
        return {"tickets": len(tickets), "settled": len(settled), "hits": hits,
                "pending": len(tickets) - len(settled), "stake_units": round(stake, 4),
                "profit_units": round(profit, 4), "roi": round(profit / stake * 100, 2) if stake else None,
                "hit_rate": round(hits / len(decided) * 100, 2) if decided else None,
                "hit_rate_interval_95": interval, "max_drawdown_units": round(drawdown, 4),
                "roi_interval_95": roi_interval,
                "max_losing_streak": longest,
                "best_ticket_profit_units": max((t["profit_units"] for t in settled), default=None)}
    return {"trial": trial, "days": rows, "summary": summarize("ticket"),
            "baseline": summarize("baseline_ticket"),
            "no_ticket_days": sum(r["decision"] == "no_ticket" for r in rows),
            "missed_days": sum(r["decision"] == "missed" for r in rows),
            "suspended_days": sum(r["decision"] == "suspended" for r in rows),
            "phase": "awaiting_evaluation" if current.date().isoformat() > trial["end_date"] else "collecting",
            "verdict": "尚未证明盈利；30天仅是观察窗口，不自动发布或提升投注金额"}
