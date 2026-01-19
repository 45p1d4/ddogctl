from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from dateutil import parser as dateutil_parser

_REL_RE = re.compile(r"^-(\d+)([smhd])$")


def parse_time(expr: str, now: Optional[datetime] = None) -> datetime:
    """
    Supported inputs:
    - 'now' or 'now-<rel>' (e.g., now-15m)
    - relatives: -15m, -1h, -2d, -30s
    - ISO datetimes (parsed with dateutil)
    Returns timezone-aware UTC datetime.
    """
    reference = now or datetime.now(timezone.utc)
    lower = expr.lower()
    if lower == "now":
        return reference
    if lower.startswith("now-"):
        # Transform 'now-15m' -> '-15m'
        lower = "-" + lower.split("now-", 1)[1]
    m = _REL_RE.match(lower)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        if unit == "s":
            delta = timedelta(seconds=amount)
        elif unit == "m":
            delta = timedelta(minutes=amount)
        elif unit == "h":
            delta = timedelta(hours=amount)
        else:
            delta = timedelta(days=amount)
        return reference - delta
    # ISO / absolute datetime
    dt = dateutil_parser.parse(expr)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_iso8601(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()

