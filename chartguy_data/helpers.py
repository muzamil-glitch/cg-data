"""Small, dependency-free helpers: dates, pips, weekends, flat candles."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable, List, Optional, Union

DateLike = Union[str, date, datetime]


def to_day(value: DateLike) -> date:
    """Turn '2026-09-01', '2026-09-01T12:30:00Z', a date or a datetime into a date.

    The LSE API only accepts YYYY-MM-DD, so every date we send goes through here.
    """
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        raise ValueError("empty date")
    if len(text) == 10:
        return date.fromisoformat(text)
    return to_utc(text).date()


def api_date(value: DateLike) -> str:
    """Format any date-like value as the YYYY-MM-DD string the API expects."""
    return to_day(value).isoformat()


def to_utc(value: Union[str, datetime]) -> datetime:
    """Parse an ISO timestamp (with 'Z', an offset, or naive) into an aware UTC datetime.

    Naive timestamps are treated as UTC, which is what the LSE API returns.
    """
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def end_exclusive(end: DateLike) -> str:
    """The API treats `end` as exclusive. Pass the day after so `end` is included."""
    return (to_day(end) + timedelta(days=1)).isoformat()


def pip_size(symbol: str) -> float:
    """Pip size by convention.

    JPY pairs 0.01, gold 0.1, silver 0.01, other FX 0.0001. For anything else
    (indices, stocks, crypto) a "pip" isn't standard; we return 1.0 so results
    read as plain price points. Pass pip= explicitly to override.
    """
    s = symbol.upper().replace("/", "").replace("_", "")
    if "JPY" in s:
        return 0.01
    if s.startswith("XAU"):
        return 0.1
    if s.startswith("XAG"):
        return 0.01
    if len(s) == 6 and s.isalpha():
        return 0.0001
    return 1.0


def is_weekend(ts: datetime) -> bool:
    return ts.weekday() >= 5


def is_flat(c: dict) -> bool:
    """True if a candle is close-only (open == high == low == close)."""
    return c["open"] == c["high"] == c["low"] == c["close"]


def normalise_candles(rows: Optional[Iterable[dict]]) -> List[dict]:
    """Accept a list of dicts or a DataFrame and return sorted dicts with UTC 'time'."""
    if rows is None:
        return []
    if hasattr(rows, "to_dict"):
        rows = rows.to_dict("records")
    out = []
    for r in rows:
        out.append({
            "time": to_utc(r.get("timestamp") or r.get("time") or r.get("datetime")),
            "open": float(r["open"]), "high": float(r["high"]),
            "low": float(r["low"]), "close": float(r["close"]),
            "volume": float(r.get("volume") or 0.0),
        })
    out.sort(key=lambda c: c["time"])
    return out


def first_friday_nfp(start: DateLike, end: DateLike) -> List[datetime]:
    """Approximate NFP release times: first Friday of each month, 08:30 New York.

    A fallback only. It misses irregular releases (holiday shifts, shutdown
    delays, and months where the first Friday falls too early).
    """
    s, e = to_day(start), to_day(end)
    out = []
    month = date(s.year, s.month, 1)
    while month <= e:
        ff = month + timedelta(days=(4 - month.weekday()) % 7)
        mar = date(ff.year, 3, 1)
        dst_start = mar + timedelta(days=(6 - mar.weekday()) % 7 + 7)   # 2nd Sun of March
        nov = date(ff.year, 11, 1)
        dst_end = nov + timedelta(days=(6 - nov.weekday()) % 7)         # 1st Sun of November
        hour = 12 if dst_start <= ff < dst_end else 13
        if s <= ff <= e:
            out.append(datetime(ff.year, ff.month, ff.day, hour, 30, tzinfo=timezone.utc))
        month = (month + timedelta(days=32)).replace(day=1)
    return out
