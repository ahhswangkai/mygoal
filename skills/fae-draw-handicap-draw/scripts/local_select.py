#!/usr/bin/env python3
"""Select ordinary/handicap draws from raw MyGoal markets only.

This script deliberately does not call /api/fae/daily-ai and does not use Ark.
It fetches raw match documents, runs the repository's deterministic FAE core,
then applies the local draw radar and formal-pool guards.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from football_ai import (  # noqa: E402
    ENGINE_VERSION,
    FAEDailyAIAnalyzer,
    FootballAIEngine,
    build_review_memory,
    build_daily_match_input,
)


PLAY_GROUPS = {
    "ordinary": {"主胜", "平局", "客胜"},
    "handicap": {"让胜", "让平", "让负"},
}

DEFAULT_MEMORY_DIR = (
    PROJECT_ROOT / ".local" / "fae-draw-handicap-draw" / "review-memory"
)
MEMORY_WINDOW_DAYS = 7
MEMORY_SCAN_DAYS = 21
MEMORY_CONCEPTS = {
    "no_deepen": ("未升深", "不升盘", "降水不升", "water_drop_without_deepen"),
    "retreat": ("退盘", "退浅", "handicap_retreat"),
    "high_water": ("高水", "升水", "upper_water_rise", "deepen_high_water"),
    "overheated": ("过热", "极热", "热门", "overheated_shallow"),
    "divergence": ("背离", "不一致", "euro_asian_divergence"),
    "low_value": ("负价值", "低价值", "价值不足", "赔率价值"),
    "data_anomaly": ("异常", "跳档", "缺失", "极端水位"),
    "small_rise": ("小升", "微升", "small_rise"),
    "draw_odds": ("平赔", "平局赔率", "draw_odds"),
    "total_under": ("小球", "低总球", "under"),
    "total_over": ("大球", "高总球", "over"),
}
NEGATIVE_MEMORY_MARKERS = (
    "风险", "过热", "退盘", "未升深", "背离", "异常", "不稳",
    "低价值", "负价值", "缺失", "误判", "失手", "降低", "降权",
)


def _number(value: Any) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _fetch_json(url: str, timeout: float) -> Dict[str, Any]:
    # The local workstation may have a development proxy in its environment.
    # Raw MyGoal reads should not silently depend on that proxy being alive.
    opener = build_opener(ProxyHandler({}))
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "MyGoal-Local-Draw-Skill/1.0",
        },
    )
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def load_raw_matches(
    date_str: str,
    base_url: str,
    timeout: float,
    input_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if input_path:
        payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    else:
        query = urlencode({"date": date_str, "page": 1, "page_size": 200})
        payload = _fetch_json(
            f"{base_url.rstrip('/')}/api/matches?{query}", timeout
        )
    if not payload.get("success"):
        raise RuntimeError(str(payload.get("message") or "原始比赛接口返回失败"))
    matches = payload.get("data") or []
    if not isinstance(matches, list):
        raise RuntimeError("原始比赛接口 data 不是数组")
    return [dict(item) for item in matches if isinstance(item, dict)]


def _memory_cache_path(memory_dir: Path, date_str: str) -> Path:
    return memory_dir / f"review-memory-before-{date_str}.json"


def _read_memory_payload(path: Path, before_date: str) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload.get("data"), dict):
        payload = payload["data"]
    if isinstance(payload.get("reviews"), list):
        payload = build_review_memory(payload["reviews"], before_date)
    if not isinstance(payload, dict) or not payload.get("version"):
        raise RuntimeError(f"复盘记忆文件格式不正确：{path}")
    return dict(payload)


def _fetch_review_day(
    date_str: str, base_url: str, timeout: float
) -> Optional[Dict[str, Any]]:
    query = urlencode({"date": date_str})
    payload = _fetch_json(
        f"{base_url.rstrip('/')}/api/fae/daily-ai/review?{query}", timeout
    )
    data = payload.get("data") if payload.get("success") else None
    return dict(data) if isinstance(data, dict) else None


def _fetch_memory_from_reviews(
    before_date: str,
    base_url: str,
    timeout: float,
) -> Dict[str, Any]:
    target = datetime.strptime(before_date, "%Y-%m-%d").date()
    days = [
        (target - timedelta(days=offset)).isoformat()
        for offset in range(1, MEMORY_SCAN_DAYS + 1)
    ]
    reviews: List[Dict[str, Any]] = []
    successful_reads = 0
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(
                _fetch_review_day, day, base_url, min(timeout, 15.0)
            ): day
            for day in days
        }
        for future in as_completed(futures):
            try:
                review = future.result()
            except Exception:
                continue
            successful_reads += 1
            if review:
                reviews.append(review)
    if successful_reads == 0:
        raise RuntimeError("历史复盘接口全部读取失败")
    return build_review_memory(
        reviews,
        before_date,
        window_days=MEMORY_WINDOW_DAYS,
    )


def load_review_memory(
    date_str: str,
    base_url: str,
    timeout: float,
    *,
    memory_input: Optional[str] = None,
    memory_dir: Optional[str] = None,
    no_memory: bool = False,
) -> Dict[str, Any]:
    """Load compact pre-date review memory and retain a local snapshot."""
    if no_memory:
        memory = build_review_memory([], date_str)
        memory["local_source"] = "disabled"
        return memory

    cache_dir = Path(memory_dir) if memory_dir else DEFAULT_MEMORY_DIR
    cache_path = _memory_cache_path(cache_dir, date_str)
    if memory_input:
        memory = _read_memory_payload(Path(memory_input), date_str)
        memory["local_source"] = "input"
        return memory

    errors: List[str] = []
    memory: Optional[Dict[str, Any]] = None
    try:
        query = urlencode({"date": date_str})
        payload = _fetch_json(
            f"{base_url.rstrip('/')}/api/fae/review-memory?{query}", timeout
        )
        if payload.get("success") and isinstance(payload.get("data"), dict):
            memory = dict(payload["data"])
            memory["local_source"] = "review-memory-api"
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        errors.append(str(exc))

    if memory is None:
        try:
            memory = _fetch_memory_from_reviews(date_str, base_url, timeout)
            memory["local_source"] = "daily-review-api-fallback"
        except Exception as exc:
            errors.append(str(exc))

    if memory is not None:
        memory["local_cached_at"] = datetime.now(
            ZoneInfo("Asia/Shanghai")
        ).isoformat()
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(memory, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return memory

    if cache_path.exists():
        memory = _read_memory_payload(cache_path, date_str)
        memory["local_source"] = "local-cache"
        memory["local_load_warnings"] = errors
        return memory

    memory = build_review_memory([], date_str)
    memory["local_source"] = "empty-fallback"
    memory["local_load_warnings"] = errors
    return memory


def _memory_concepts(value: Any) -> set[str]:
    text = str(value or "").lower()
    return {
        concept
        for concept, markers in MEMORY_CONCEPTS.items()
        if any(marker.lower() in text for marker in markers)
    }


def _pattern_matches_candidate(
    pattern: Dict[str, Any], candidate: Dict[str, Any]
) -> bool:
    selection = str(candidate.get("selection") or "")
    target = str(pattern.get("target") or "")
    if "让平" in target and selection != "让平":
        return False
    if "普通平" in target or "平局" in target:
        if selection != "平局":
            return False
    candidate_text = " ".join([
        str(candidate.get("reason") or ""),
        str(candidate.get("risk_pattern_ids") or ""),
        str(candidate.get("draw_odds_band_signal") or ""),
    ])
    pattern_concepts = _memory_concepts(
        f"{target} {pattern.get('reason') or ''}"
    )
    if not pattern_concepts:
        return False
    return bool(pattern_concepts & _memory_concepts(candidate_text))


def apply_review_memory_to_rows(
    rows: List[Dict[str, Any]], memory: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Apply only cross-day validated memory with a tightly capped weight."""
    patterns = [
        dict(item) for item in memory.get("validated_patterns") or []
        if isinstance(item, dict)
        and item.get("status") == "historically-validated-memory"
    ]
    result = []
    for item in rows:
        row = dict(item or {})
        analysis = dict(row.get("analysis") or {})
        radar = dict(analysis.get("draw_radar") or {})
        for key in ("ordinary_draw", "handicap_draw"):
            candidate = dict(radar.get(key) or {})
            matched = [
                pattern for pattern in patterns
                if _pattern_matches_candidate(pattern, candidate)
            ]
            if not matched:
                candidate["review_memory"] = {
                    "memory_hash": memory.get("memory_hash"),
                    "matched_validated_patterns": [],
                    "probability_adjustment_pp": 0.0,
                    "score_adjustment": 0.0,
                }
                radar[key] = candidate
                continue

            probability_adjustment = 0.0
            score_adjustment = 0.0
            applied = []
            for pattern in matched[:3]:
                target_reason = (
                    f"{pattern.get('target') or ''} {pattern.get('reason') or ''}"
                )
                negative = any(
                    marker in target_reason
                    for marker in NEGATIVE_MEMORY_MARKERS
                )
                suggested = abs(_number(pattern.get("suggested_delta")) or 0.0)
                probability_pp = min(1.5, max(0.5, suggested * 20.0))
                direction = -1.0 if negative else 1.0
                probability_adjustment += direction * probability_pp
                score_adjustment += direction * probability_pp * 2.0
                applied.append({
                    "scope": pattern.get("scope"),
                    "target": pattern.get("target"),
                    "direction": "down" if negative else "up",
                    "observed_days": pattern.get("observed_days"),
                    "evidence_matches": pattern.get("evidence_matches"),
                })

            probability_adjustment = max(
                -1.5, min(1.5, probability_adjustment)
            )
            score_adjustment = max(-3.0, min(3.0, score_adjustment))
            probability = _number(candidate.get("probability"))
            odds = _number(candidate.get("odds"))
            score = _number(candidate.get("score"))
            if probability is not None:
                probability = max(
                    0.0, min(100.0, probability + probability_adjustment)
                )
                candidate["probability"] = round(probability, 2)
                if odds is not None:
                    candidate["odds_value"] = round(
                        probability / 100.0 * odds * 100.0 - 100.0, 2
                    )
            if score is not None:
                candidate["score"] = round(
                    max(0.0, min(99.0, score + score_adjustment)), 1
                )
            candidate["review_memory"] = {
                "memory_hash": memory.get("memory_hash"),
                "matched_validated_patterns": applied,
                "probability_adjustment_pp": round(
                    probability_adjustment, 2
                ),
                "score_adjustment": round(score_adjustment, 2),
            }
            note = (
                f"持续复盘记忆校正{probability_adjustment:+g}个百分点/"
                f"{score_adjustment:+g}分（{len(applied)}项跨日验证模式）"
            )
            candidate["reason"] = (
                str(candidate.get("reason") or "").rstrip("。；")
                + "；" + note + "。"
            )
            radar[key] = FAEDailyAIAnalyzer._apply_draw_radar_candidate_guard(
                candidate
            )
        analysis["draw_radar"] = radar
        row["analysis"] = analysis
        result.append(row)
    return result


def _best_category(
    categories: Iterable[Dict[str, Any]], labels: set[str]
) -> Optional[Dict[str, Any]]:
    rows = [
        item for item in categories
        if isinstance(item, dict) and str(item.get("label") or "") in labels
    ]
    if not rows:
        return None
    return max(
        rows,
        key=lambda item: (
            _number(item.get("prediction_score")) or 0,
            _number(item.get("probability")) or 0,
            _number(item.get("bet_score")) or 0,
        ),
    )


def build_local_row(
    match: Dict[str, Any],
    engine: FootballAIEngine,
    policy: str,
) -> Dict[str, Any]:
    # No source analysis and use_ai=False are intentional: this path consumes
    # current raw markets plus local fixed rules only.
    context = engine.build_context(match=match)
    core_result = engine.generate_from_context(context, use_ai=False)
    snapshot = build_daily_match_input(
        match,
        core_result,
        draw_selection_policy=policy,
    )
    recommendation = (core_result.get("core") or {}).get("recommendation") or {}
    categories = recommendation.get("category_scores") or []
    ordinary = _best_category(categories, PLAY_GROUPS["ordinary"])
    handicap = _best_category(categories, PLAY_GROUPS["handicap"])
    alternatives = recommendation.get("alternatives") or []
    secondary = next(
        (
            str(item.get("label"))
            for item in alternatives
            if isinstance(item, dict) and item.get("label")
        ),
        None,
    )
    analysis = {
        "primary_play": recommendation.get("primary") or "观望",
        "secondary_play": secondary,
        "predicted_result": (ordinary or {}).get("label"),
        "handicap_play": (handicap or {}).get("label"),
        "market_confidence": recommendation.get("market_confidence") or {},
        "decision": recommendation.get("decision") or "观察",
        "no_bet": bool(recommendation.get("no_bet")),
        "no_bet_reasons": recommendation.get("no_bet_reasons") or [],
        "rating": recommendation.get("stars"),
        "local_rule_mode": True,
    }
    return {
        "match_id": str(match.get("match_id") or ""),
        "match_number": match.get("match_number") or match.get("round_id"),
        "owner_date": match.get("owner_date"),
        "home_team": match.get("home_team"),
        "away_team": match.get("away_team"),
        "league": match.get("league"),
        "match_time": match.get("match_time"),
        "current_status": match.get("status"),
        "analysis": analysis,
        "input_snapshot": snapshot,
    }


def _candidate_status(
    analyzer: type[FAEDailyAIAnalyzer],
    candidate: Dict[str, Any],
    match: Dict[str, Any],
) -> str:
    level = analyzer._radar_official_level(candidate, match)
    if level == "core":
        return "正式核心"
    if level == "small":
        return "小试"
    if candidate.get("tier") == "core":
        return "雷达核心（未过正式池）"
    if candidate.get("tier") == "watch":
        return "观察"
    return "排除"


def _market_snapshot(match: Dict[str, Any]) -> Dict[str, Any]:
    source = match.get("input_snapshot") or {}
    return {
        "euro": source.get("euro") or {},
        "asian": source.get("asian") or {},
        "sporttery_handicap": source.get("sporttery_handicap") or {},
        "total": source.get("total") or {},
    }


def select_draws(
    matches: List[Dict[str, Any]], policy: str, review_memory: Dict[str, Any]
) -> Dict[str, Any]:
    engine = FootballAIEngine()
    rows = [build_local_row(match, engine, policy) for match in matches]
    rows = FAEDailyAIAnalyzer.apply_draw_radar(rows)
    rows = apply_review_memory_to_rows(rows, review_memory)
    by_id = {str(row.get("match_id") or ""): row for row in rows}
    summary = FAEDailyAIAnalyzer.attach_draw_radar_summary({}, rows)
    radar = summary.get("draw_radar") or {}

    def enrich(candidate: Dict[str, Any]) -> Dict[str, Any]:
        row = by_id.get(str(candidate.get("match_id") or "")) or {}
        result = dict(candidate)
        result.update({
            "home_team": row.get("home_team"),
            "away_team": row.get("away_team"),
            "league": row.get("league"),
            "match_time": row.get("match_time"),
            "current_status": row.get("current_status"),
            "local_status": _candidate_status(
                FAEDailyAIAnalyzer, result, row
            ),
            "markets": _market_snapshot(row),
        })
        return result

    return {
        "mode": "local-deterministic-rules",
        "uses_daily_ai": False,
        "uses_ark": False,
        "uses_review_memory": True,
        "engine_version": ENGINE_VERSION,
        "policy": policy,
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "match_count": len(matches),
        "review_memory": {
            "version": review_memory.get("version"),
            "memory_hash": review_memory.get("memory_hash"),
            "before_date": review_memory.get("before_date"),
            "source_dates": review_memory.get("source_dates") or [],
            "review_days": review_memory.get("review_days") or 0,
            "observation_count": (
                review_memory.get("observation_count") or 0
            ),
            "validated_pattern_count": (
                review_memory.get("validated_pattern_count") or 0
            ),
            "validated_patterns": (
                review_memory.get("validated_patterns") or []
            ),
            "recent_observations": (
                review_memory.get("recent_observations") or []
            ),
            "governance": review_memory.get("governance") or {},
            "local_source": review_memory.get("local_source"),
            "local_cached_at": review_memory.get("local_cached_at"),
            "local_load_warnings": (
                review_memory.get("local_load_warnings") or []
            ),
        },
        "ordinary_draw": [
            enrich(item) for item in radar.get("ordinary_draw") or []
        ],
        "handicap_draw": [
            enrich(item) for item in radar.get("handicap_draw") or []
        ],
        "excluded_count": radar.get("excluded_count") or {},
    }


def _fmt(value: Any, suffix: str = "") -> str:
    number = _number(value)
    return f"{number:g}{suffix}" if number is not None else "--"


def print_text(result: Dict[str, Any], date_str: str) -> None:
    print(
        f"本地平/让平规则筛选 {date_str}｜{result['match_count']}场｜"
        f"FAE {result['engine_version']}｜策略 {result['policy']}"
    )
    print("数据源：/api/matches 原始盘口；未读取 /api/fae/daily-ai；未调用 Ark")
    memory = result.get("review_memory") or {}
    print(
        "复盘记忆："
        f"{memory.get('review_days') or 0}个历史比赛日｜"
        f"{memory.get('validated_pattern_count') or 0}项跨日验证模式｜"
        f"{memory.get('observation_count') or 0}项近期观察｜"
        f"来源 {memory.get('local_source') or '--'}"
    )
    for key, title in (
        ("ordinary_draw", "普通平局"),
        ("handicap_draw", "竞彩让平"),
    ):
        print(f"\n{title}")
        candidates = result.get(key) or []
        if not candidates:
            print("- 无候选")
            continue
        for index, item in enumerate(candidates, 1):
            handicap = (item.get("markets") or {}).get(
                "sporttery_handicap", {}
            ).get("value")
            handicap_text = (
                f"｜让球{_fmt(handicap)}" if key == "handicap_draw" else ""
            )
            print(
                f"{index}. {item.get('match_number') or item.get('match_id')} "
                f"{item.get('home_team')} vs {item.get('away_team')}｜"
                f"{item.get('local_status')}｜概率{_fmt(item.get('probability'), '%')}｜"
                f"赔率{_fmt(item.get('odds'))}｜价值{_fmt(item.get('odds_value'), '%')}｜"
                f"雷达{_fmt(item.get('score'))}分{handicap_text}"
            )
            print(f"   {str(item.get('reason') or '').strip()}")
            vetoes = item.get("official_veto_reasons") or []
            if vetoes:
                print("   降级：" + "；".join(str(value) for value in vetoes))
            adjustment = item.get("review_memory") or {}
            if adjustment.get("matched_validated_patterns"):
                print(
                    "   记忆：概率"
                    f"{_fmt(adjustment.get('probability_adjustment_pp'), 'pp')}，"
                    f"雷达{_fmt(adjustment.get('score_adjustment'), '分')}"
                )


def parse_args() -> argparse.Namespace:
    today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    parser = argparse.ArgumentParser(
        description="从 MyGoal 原始盘口在本地筛选普通平局和竞彩让平"
    )
    parser.add_argument("--date", default=today, help="owner_date，YYYY-MM-DD")
    parser.add_argument("--base-url", default="https://mygoal.top")
    parser.add_argument("--input", help="离线读取 /api/matches 保存的 JSON")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--memory-input", help="离线读取复盘记忆 JSON")
    parser.add_argument(
        "--memory-dir",
        help="本地复盘记忆缓存目录，默认项目 .local 目录",
    )
    parser.add_argument(
        "--no-memory",
        action="store_true",
        help="仅用于对照测试：禁用复盘记忆",
    )
    parser.add_argument(
        "--policy",
        choices=("conservative", "balanced", "aggressive"),
        default="conservative",
    )
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        matches = load_raw_matches(
            args.date, args.base_url, args.timeout, args.input
        )
        review_memory = load_review_memory(
            args.date,
            args.base_url,
            args.timeout,
            memory_input=args.memory_input,
            memory_dir=args.memory_dir,
            no_memory=args.no_memory,
        )
        result = select_draws(matches, args.policy, review_memory)
    except Exception as exc:  # CLI boundary: show one actionable error.
        print(f"本地规则筛选失败：{exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text(result, args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
