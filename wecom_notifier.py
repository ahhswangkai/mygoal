"""Secure Enterprise WeChat group-robot delivery helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Dict, Iterable, Optional
from urllib.parse import parse_qs, urlparse

import requests

BEIJING_TIMEZONE = timezone(timedelta(hours=8))


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit - 1]}…"


def delivery_key(event_type: str, identity: Any) -> str:
    raw = f"{event_type}:{identity}".encode("utf-8")
    return f"{event_type}:{sha256(raw).hexdigest()[:24]}"


def _format_beijing_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "未知"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BEIJING_TIMEZONE)
        return parsed.astimezone(BEIJING_TIMEZONE).strftime(
            "%Y-%m-%d %H:%M"
        )
    except ValueError:
        return _clip(text, 32)


class WeComNotifier:
    """Deliver text or markdown without exposing the webhook secret."""

    def __init__(
        self,
        webhook_url: Optional[str],
        *,
        session: Optional[requests.Session] = None,
        timeout: int = 10,
    ):
        self.webhook_url = str(webhook_url or "").strip()
        self.session = session or requests.Session()
        self.timeout = max(1, int(timeout))

    @property
    def configured(self) -> bool:
        try:
            parsed = urlparse(self.webhook_url)
            key = (parse_qs(parsed.query).get("key") or [""])[0]
            return bool(
                parsed.scheme == "https"
                and parsed.hostname == "qyapi.weixin.qq.com"
                and parsed.path == "/cgi-bin/webhook/send"
                and key
            )
        except (TypeError, ValueError):
            return False

    def send_markdown(self, content: Any) -> Dict[str, Any]:
        return self._send({
            "msgtype": "markdown",
            "markdown": {"content": _clip(content, 3800)},
        })

    def send_text(self, content: Any) -> Dict[str, Any]:
        return self._send({
            "msgtype": "text",
            "text": {"content": _clip(content, 1900)},
        })

    def _send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.configured:
            return {
                "success": False,
                "status": "not_configured",
                "message": "企业微信机器人 Webhook 尚未配置",
            }
        try:
            response = self.session.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout,
            )
            try:
                body = response.json()
            except ValueError:
                body = {}
            errcode = body.get("errcode")
            success = response.status_code == 200 and errcode == 0
            return {
                "success": success,
                "status": "sent" if success else "failed",
                "status_code": response.status_code,
                "errcode": errcode,
                "message": (
                    "企业微信消息发送成功"
                    if success else _clip(
                        body.get("errmsg") or "企业微信返回异常", 180
                    )
                ),
            }
        except requests.RequestException as exc:
            return {
                "success": False,
                "status": "failed",
                "message": f"企业微信请求失败：{_clip(exc, 160)}",
            }


def format_daily_ai_message(result: Dict[str, Any]) -> str:
    owner_date = str(result.get("owner_date") or "")[:10]
    generated_at = _format_beijing_time(result.get("generated_at"))
    summary = result.get("daily_summary") or {}
    matches = result.get("matches") or []
    candidates = []
    for item in matches:
        analysis = item.get("analysis") or {}
        if (
            analysis.get("no_bet")
            or analysis.get("primary_play") in (None, "", "观望")
        ):
            continue
        candidates.append({
            "match_number": (
                item.get("match_number")
                or (item.get("input_snapshot") or {}).get("match_number")
                or "未知场次"
            ),
            "play": analysis.get("primary_play"),
            "rating": float(analysis.get("rating") or 0),
            "odds": analysis.get("odds"),
        })
    candidates.sort(
        key=lambda item: (item["rating"], float(item.get("odds") or 0)),
        reverse=True,
    )
    lines = [
        f"## FAE 全日研判 · {owner_date}",
        (
            f"> 研判时间：{generated_at}（北京时间）"
            f" · 共 {int(result.get('match_count') or len(matches))} 场比赛"
        ),
    ]
    conclusion = _clip(summary.get("core_conclusion"), 520)
    if conclusion:
        lines.extend(["", "**核心结论**", conclusion])
    if candidates:
        lines.extend(["", "**重点方向**"])
        for item in candidates[:5]:
            odds = (
                f" @{float(item['odds']):.2f}"
                if item.get("odds") not in (None, "") else ""
            )
            lines.append(
                f"- {item['match_number']}：{item['play']}"
                f" · {item['rating']:g}星{odds}"
            )
    warnings = [
        _clip(item, 160)
        for item in (summary.get("warnings") or [])[:3]
        if item
    ]
    if warnings:
        lines.extend(["", "**风险提醒**"])
        lines.extend(f"- {item}" for item in warnings)
    lines.extend(["", "> 仅供个人研究，请理性决策"])
    return "\n".join(lines)


def format_review_message(review: Dict[str, Any]) -> str:
    owner_date = str(review.get("owner_date") or "")[:10]
    singles = (review.get("summary") or {}).get("singles") or {}
    ai_summary = (
        (review.get("ai_deep_review") or {}).get("summary") or {}
    )
    settled = int(singles.get("settled") or 0)
    hits = int(singles.get("hits") or 0)
    rate = round(hits / settled * 100, 1) if settled else 0
    lines = [
        f"## FAE 赛后复盘 · {owner_date}",
        f"> 单场 {hits}/{settled} 命中 · {rate:g}%",
    ]
    conclusion = _clip(ai_summary.get("conclusion"), 520)
    if conclusion:
        lines.extend(["", "**复盘结论**", conclusion])
    failed = [
        _clip(item, 160)
        for item in (ai_summary.get("what_failed") or [])[:3]
        if item
    ]
    if failed:
        lines.extend(["", "**需要修正**"])
        lines.extend(f"- {item}" for item in failed)
    actions = [
        _clip(item, 160)
        for item in (ai_summary.get("next_actions") or [])[:3]
        if item
    ]
    if actions:
        lines.extend(["", "**下一轮改进**"])
        lines.extend(f"- {item}" for item in actions)
    lines.extend(["", "> 复盘候选需经过足量历史样本验证"])
    return "\n".join(lines)


def format_live_alert_message(matches: Iterable[Dict[str, Any]]) -> str:
    rows = list(matches)
    lines = [
        f"## 比赛提醒 · {datetime.now().strftime('%m-%d %H:%M')}",
        f"> 发现 {len(rows)} 场符合平局/让平观察条件",
    ]
    for match in rows[:12]:
        draw_odds = (
            match.get("euro_current_draw")
            or match.get("euro_initial_draw")
        )
        handicap = match.get("hi_handicap_value")
        tags = []
        if draw_odds not in (None, ""):
            tags.append(f"平 {draw_odds}")
        if handicap not in (None, ""):
            tags.append(f"让球 {handicap}")
        match_time = str(match.get("match_time") or "")
        lines.append(
            "- {} {} {} vs {}{}".format(
                match.get("match_number") or "未知场次",
                match_time[-5:] if len(match_time) >= 5 else match_time,
                match.get("home_team") or "",
                match.get("away_team") or "",
                f"（{' · '.join(tags)}）" if tags else "",
            )
        )
    return "\n".join(lines)
