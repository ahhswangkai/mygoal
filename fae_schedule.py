"""Scheduling helpers for paid FAE pre-match analysis."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any, Dict, Optional


def app_timezone() -> tzinfo:
    # China Standard Time has no daylight-saving transitions.  A fixed offset
    # keeps this module compatible with the production Python 3.8 runtime,
    # where stdlib zoneinfo is not available.
    return timezone(timedelta(hours=8), name="Asia/Shanghai")


def _aware(value: datetime) -> datetime:
    zone = app_timezone()
    if value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


def parse_generated_at(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _aware(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def parse_match_datetime(match: Dict[str, Any]) -> Optional[datetime]:
    """Parse the local kick-off time saved by the 500 crawler.

    The crawler normally stores ``MM-DD HH:MM``.  The owner date supplies the
    missing year and also lets New Year fixtures roll into the adjacent year.
    """
    raw = str((match or {}).get("match_time") or "").strip()
    if not raw:
        return None
    zone = app_timezone()
    normalized = raw.replace("/", "-").replace("T", " ")
    normalized = normalized.removesuffix("Z").strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M%z",
    ):
        try:
            parsed = datetime.strptime(normalized, fmt)
            return _aware(parsed)
        except ValueError:
            continue

    owner_text = str((match or {}).get("owner_date") or "")[:10]
    try:
        owner_day = datetime.strptime(owner_text, "%Y-%m-%d").date()
    except ValueError:
        owner_day = datetime.now(zone).date()
    for fmt in ("%m-%d %H:%M:%S", "%m-%d %H:%M"):
        try:
            partial = datetime.strptime(normalized, fmt)
        except ValueError:
            continue
        candidates = [
            datetime(
                owner_day.year + delta,
                partial.month,
                partial.day,
                partial.hour,
                partial.minute,
                partial.second,
                tzinfo=zone,
            )
            for delta in (-1, 0, 1)
        ]
        owner_noon = datetime.combine(owner_day, time(12), tzinfo=zone)
        return min(candidates, key=lambda item: abs(item - owner_noon))
    return None


def analysis_cutoff(match: Dict[str, Any]) -> Optional[datetime]:
    """Return the paid-analysis cutoff for the fixture's lottery owner day."""
    owner_text = str((match or {}).get("owner_date") or "")[:10]
    try:
        owner_day: date = datetime.strptime(owner_text, "%Y-%m-%d").date()
    except ValueError:
        kickoff = parse_match_datetime(match)
        if not kickoff:
            return None
        owner_day = kickoff.date()
    cutoff_hour = 22 if owner_day.weekday() < 5 else 23
    return datetime.combine(
        owner_day,
        # Give the cron invocation scheduled exactly at the cutoff minute time
        # to start; the next minute is already outside the paid window.
        time(cutoff_hour, 0, 59, 999999),
        tzinfo=app_timezone(),
    )


def prematch_analysis_due(
    match: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
    latest_generated_at: Any = None,
    lead_minutes: int = 30,
) -> bool:
    """Whether one fixture needs its one-off paid T-minus analysis now."""
    try:
        if int((match or {}).get("status")) != 0:
            return False
    except (TypeError, ValueError):
        return False
    kickoff = parse_match_datetime(match)
    cutoff = analysis_cutoff(match)
    if not kickoff or not cutoff:
        return False
    current = _aware(now or datetime.now(app_timezone()))
    lead = max(1, min(180, int(lead_minutes or 30)))
    due_at = kickoff - timedelta(minutes=lead)
    # At exactly 22:00/23:00 the job is still allowed; later runs are quiet.
    if due_at > cutoff or current > cutoff:
        return False
    if current < due_at or current >= kickoff:
        return False
    generated = parse_generated_at(latest_generated_at)
    if generated and generated >= due_at:
        return False
    return True
