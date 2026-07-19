"""Helpers for sorting matches whose source time may omit the year."""

from datetime import datetime


def parse_match_datetime(match, now=None):
    """Return a complete naive datetime for a stored match, if possible."""
    value = str((match or {}).get('match_time') or '').strip()
    if not value:
        return None

    for fmt in (
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%Y/%m/%d %H:%M',
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue

    owner_value = str((match or {}).get('owner_date') or '')[:10]
    try:
        owner_date = datetime.strptime(owner_value, '%Y-%m-%d')
    except ValueError:
        owner_date = None

    if owner_date:
        for fmt in ('%m-%d %H:%M:%S', '%m-%d %H:%M'):
            try:
                candidate = datetime.strptime(
                    '{}-{}'.format(owner_date.year, value),
                    '%Y-' + fmt,
                )
            except ValueError:
                continue

            # A betting business date can cross midnight or New Year.
            day_delta = (candidate.date() - owner_date.date()).days
            if day_delta < -183:
                candidate = candidate.replace(year=candidate.year + 1)
            elif day_delta > 183:
                candidate = candidate.replace(year=candidate.year - 1)
            return candidate

        for fmt in ('%H:%M:%S', '%H:%M'):
            try:
                parsed_time = datetime.strptime(value, fmt).time()
                return datetime.combine(owner_date.date(), parsed_time)
            except ValueError:
                continue

    current_year = (now or datetime.now()).year
    for fmt in ('%m-%d %H:%M:%S', '%m-%d %H:%M'):
        try:
            return datetime.strptime(
                '{}-{}'.format(current_year, value),
                '%Y-' + fmt,
            )
        except ValueError:
            continue
    return None


def sort_matches_by_datetime(matches, descending=False):
    """Sort matches chronologically while always placing missing times last."""
    fallback = datetime.min if descending else datetime.max
    return sorted(
        matches,
        key=lambda match: parse_match_datetime(match) or fallback,
        reverse=descending,
    )
