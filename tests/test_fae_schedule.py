from datetime import datetime

from fae_schedule import (
    analysis_cutoff,
    app_timezone,
    parse_match_datetime,
    prematch_analysis_due,
)


ZONE = app_timezone()


def dt(value):
    return datetime.fromisoformat(value).replace(tzinfo=ZONE)


def match(owner_date, match_time, status=0):
    return {
        "owner_date": owner_date,
        "match_time": match_time,
        "status": status,
    }


def test_parse_short_match_time_uses_owner_year():
    parsed = parse_match_datetime(match("2026-08-08", "08-08 18:00"))
    assert parsed == dt("2026-08-08T18:00:00")


def test_parse_short_match_time_rolls_over_new_year():
    parsed = parse_match_datetime(match("2026-12-31", "01-01 00:30"))
    assert parsed == dt("2027-01-01T00:30:00")


def test_weekday_analysis_runs_at_exact_cutoff():
    item = match("2026-08-10", "08-10 22:30")  # Monday
    assert analysis_cutoff(item).hour == 22
    assert prematch_analysis_due(item, now=dt("2026-08-10T22:00:30"))


def test_weekday_analysis_does_not_run_after_22():
    item = match("2026-08-10", "08-10 22:35")
    assert not prematch_analysis_due(item, now=dt("2026-08-10T22:05:00"))


def test_weekend_analysis_runs_at_23_but_not_later():
    allowed = match("2026-08-08", "08-08 23:30")  # Saturday
    blocked = match("2026-08-08", "08-08 23:35")
    assert prematch_analysis_due(allowed, now=dt("2026-08-08T23:00:00"))
    assert not prematch_analysis_due(blocked, now=dt("2026-08-08T23:05:00"))


def test_cross_midnight_fixture_is_blocked_by_owner_day_cutoff():
    item = match("2026-08-07", "08-08 00:30")  # Friday lottery day
    assert not prematch_analysis_due(item, now=dt("2026-08-08T00:00:00"))


def test_analysis_generated_in_prematch_window_is_not_repeated():
    item = match("2026-08-08", "08-08 18:00")
    assert not prematch_analysis_due(
        item,
        now=dt("2026-08-08T17:40:00"),
        latest_generated_at="2026-08-08T09:31:00Z",
    )


def test_earlier_daily_analysis_does_not_suppress_prematch_refresh():
    item = match("2026-08-08", "08-08 18:00")
    assert prematch_analysis_due(
        item,
        now=dt("2026-08-08T17:35:00"),
        latest_generated_at="2026-08-08T04:00:00Z",
    )
