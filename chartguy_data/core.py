"""CGData: a thin, careful layer over the official `lse-data` client.

It doesn't replace lse-data. It fixes the traps we hit in practice:
  * the API only accepts YYYY-MM-DD dates
  * `end` is exclusive, so the last day silently goes missing
  * daily forex data includes short Sunday candles that drag averages down
  * early history can be close-only (open == high == low == close)
and it attaches the sample size and date range to every result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .helpers import (DateLike, api_date, end_exclusive, first_friday_nfp, is_flat,
                      is_weekend, normalise_candles, pip_size, to_day, to_utc)

try:  # pandas is optional
    import pandas as _pd
except Exception:  # pragma: no cover
    _pd = None

NFP_WORDS = ("nonfarm", "non-farm", "non farm")


class CGDataError(Exception):
    """Raised for bad input or when the data can't support a reliable answer."""


@dataclass
class Result:
    """Rows plus the context you need before trusting them."""
    rows: List[dict]
    summary: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def df(self):
        """Rows as a pandas DataFrame (needs pandas)."""
        if _pd is None:
            raise CGDataError("pandas is not installed: pip install pandas")
        return _pd.DataFrame(self.rows)

    def to_csv(self, path: str) -> str:
        import csv
        if not self.rows:
            raise CGDataError("nothing to save: no rows")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(self.rows[0].keys()))
            w.writeheader()
            for r in self.rows:
                w.writerow({k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()})
        return path

    def report(self) -> str:
        lines = ["===== SUMMARY ====="]
        lines += [f"{k}: {v}" for k, v in self.summary.items()]
        if self.warnings:
            lines.append("----- WARNINGS -----")
            lines += [f"! {w}" for w in self.warnings]
        return "\n".join(lines)


class CGData:
    """Wraps an `lse.LSE` client. `CGData()` creates one from LSE_API_KEY."""

    def __init__(self, client: Any = None, api_key: Optional[str] = None):
        if client is None:
            try:
                from lse import LSE
            except ImportError as e:  # pragma: no cover
                raise CGDataError("lse-data is not installed: pip install lse-data") from e
            client = LSE(api_key=api_key) if api_key else LSE()
        self.lse = client

    # ---------------------------------------------------------------- candles
    def candles(self, symbol: str, timeframe: str, start: DateLike, end: DateLike,
                drop_weekends: Optional[bool] = None) -> Result:
        """Candles between two dates, both inclusive.

        drop_weekends defaults to True for daily-or-higher timeframes (removes the
        Sunday stub candle) and False for intraday (Sunday evening is real trading).
        """
        if to_day(end) < to_day(start):
            raise CGDataError("end is before start")
        daily = timeframe.lower() in ("1d", "1w", "1mo")
        if drop_weekends is None:
            drop_weekends = daily
        raw = self.lse.candles(symbol, timeframe, start=api_date(start), end=end_exclusive(end))
        rows = normalise_candles(raw)
        dropped = 0
        if drop_weekends:
            kept = [c for c in rows if not is_weekend(c["time"])]
            dropped = len(rows) - len(kept)
            rows = kept
        warnings = []
        flat = sum(1 for c in rows if is_flat(c))
        if rows and flat / len(rows) > 0.05:
            warnings.append(f"{flat} of {len(rows)} candles are close-only (open=high=low=close); "
                            "ranges and wicks are unreliable for this period")
        if not rows:
            warnings.append("no candles returned; check the symbol and dates")
        summary = {
            "symbol": symbol, "timeframe": timeframe,
            "requested": f"{api_date(start)} -> {api_date(end)} (inclusive)",
            "returned": f"{rows[0]['time']:%Y-%m-%d %H:%M} -> {rows[-1]['time']:%Y-%m-%d %H:%M} UTC" if rows else "none",
            "candles": len(rows), "weekend_candles_dropped": dropped, "flat_candles": flat,
        }
        return Result(rows, summary, warnings)

    # ----------------------------------------------------------- daily ranges
    def daily_ranges(self, symbol: str, start: DateLike, end: DateLike,
                     pip: Optional[float] = None) -> Result:
        """Daily high-low range in pips plus per-week (Mon-Fri) averages."""
        pip = pip or pip_size(symbol)
        res = self.candles(symbol, "1d", start, end, drop_weekends=True)
        rows = []
        for c in res.rows:
            monday = (c["time"] - timedelta(days=c["time"].weekday())).date()
            rows.append({"date": c["time"].date().isoformat(), "week_of": monday.isoformat(),
                         "range_pips": round((c["high"] - c["low"]) / pip, 1)})
        weeks: Dict[str, List[float]] = {}
        for r in rows:
            weeks.setdefault(r["week_of"], []).append(r["range_pips"])
        weekly = {w: {"days": len(v), "avg_range_pips": round(mean(v), 1)} for w, v in sorted(weeks.items())}
        summary = dict(res.summary)
        summary.update({
            "pip_size": pip, "trading_days": len(rows),
            "avg_daily_range_pips": round(mean(r["range_pips"] for r in rows), 1) if rows else None,
            "weekly": weekly,
        })
        return Result(rows, summary, list(res.warnings))

    # ----------------------------------------------------------------- events
    def event_times(self, keywords: Sequence[str], start: DateLike, end: DateLike,
                    region: Optional[str] = "US", exclude: Sequence[str] = ()) -> List[Tuple[datetime, str]]:
        """Release times for calendar events whose name contains any keyword."""
        # The API caps each response (limit=5000), so long ranges silently lose rows.
        # Ask in 60-day chunks instead.
        raw: List[dict] = []
        s, e = to_day(start), to_day(end)
        while s <= e:
            chunk_end = min(s + timedelta(days=59), e)
            try:
                part = self.lse.economic_calendar(region=region, start=api_date(s),
                                                  end=end_exclusive(chunk_end))
            except Exception as err:
                raise CGDataError(f"calendar request failed ({s} -> {chunk_end}): {err}") from err
            if hasattr(part, "to_dict"):
                part = part.to_dict("records")
            raw.extend(part or [])
            s = chunk_end + timedelta(days=1)
        found = {}
        for r in raw or []:
            name = str(r.get("event", "")).lower()
            if any(k in name for k in keywords) and not any(x in name for x in exclude):
                stamp = r.get("datetime")
                if not stamp and r.get("date"):
                    stamp = f"{str(r['date'])[:10]}T{r.get('time') or '00:00'}"
                try:
                    found.setdefault(to_utc(stamp), "calendar")
                except Exception:
                    continue
        return sorted(found.items())

    def nfp_times(self, start: DateLike, end: DateLike) -> List[Tuple[datetime, str]]:
        """NFP release times: calendar where available, first-Friday rule for the rest."""
        cal = self.event_times(["payroll"], start, end, region="US",
                               exclude=("adp", "private", "benchmark", "revision"))
        cal_months = {(t.year, t.month) for t, _ in cal}
        all_rule = first_friday_nfp(start, end)
        # When the calendar covers most months, trust it completely: a missing
        # month is real (e.g. the Oct 2025 report that never came out during
        # the US government shutdown), and the first-Friday rule would invent it.
        if all_rule and len(cal_months) >= 0.8 * len(all_rule):
            return sorted(cal)
        rule = [(t, "first-Friday rule") for t in all_rule
                if (t.year, t.month) not in cal_months]
        return sorted(cal + rule)

    def event_study(self, symbol: str, events: Iterable[Tuple[datetime, str]] | Iterable[datetime],
                    timeframe: str = "5m", pre_minutes: int = 120, session_close_utc: int = 21,
                    pip: Optional[float] = None, big_move: float = 10.0) -> Result:
        """How `symbol` moved around each event time.

        For every event: move after 5/15/60 minutes, first-hour range, move and
        range to the session close (21:00 UTC = New York close), and whether the
        first 5-minute direction held or reversed by the close.
        Describes the past only. It's not a signal.
        """
        pip = pip or pip_size(symbol)
        rows, skipped, warnings = [], [], []
        for item in events:
            t0, src = (item if isinstance(item, tuple) else (item, "given"))
            t0 = to_utc(t0)
            try:
                day = self.candles(symbol, timeframe, t0, t0, drop_weekends=False).rows
            except Exception as e:
                skipped.append(f"{t0:%Y-%m-%d}: {e}")
                continue
            close_t = t0.replace(hour=session_close_utc, minute=0, second=0, microsecond=0)
            pre = [c for c in day if t0 - timedelta(minutes=pre_minutes) <= c["time"] < t0]
            post = [c for c in day if t0 <= c["time"] < close_t]
            if not pre or len(post) < 12:
                skipped.append(f"{t0:%Y-%m-%d}: not enough candles")
                continue
            p0 = pre[-1]["close"]

            def after(m: int) -> float:
                return [c for c in post if c["time"] < t0 + timedelta(minutes=m)][-1]["close"]

            hour = [c for c in post if c["time"] < t0 + timedelta(minutes=60)]
            d5 = (after(5) - p0) / pip
            dc = (post[-1]["close"] - p0) / pip
            rows.append({
                "release_utc": t0, "date_source": src, "pre_price": p0,
                "move_5m": round(d5, 1), "move_15m": round((after(15) - p0) / pip, 1),
                "move_60m": round((after(60) - p0) / pip, 1),
                "range_60m": round((max(c["high"] for c in hour) - min(c["low"] for c in hour)) / pip, 1),
                "move_to_close": round(dc, 1),
                "range_to_close": round((max(c["high"] for c in post) - min(c["low"] for c in post)) / pip, 1),
                "first5_reversed": d5 * dc < 0,
                "flat_candles_first_hour": sum(1 for c in hour if is_flat(c)),
            })
        n = len(rows)
        if n < 30:
            warnings.append(f"only {n} events: treat percentages as noisy")
        rule_n = sum(1 for r in rows if r["date_source"] == "first-Friday rule")
        if rule_n:
            warnings.append(f"{rule_n} event dates come from the first-Friday rule, not the calendar; "
                            "irregular release dates may be wrong")
        bad = sum(1 for r in rows if r["flat_candles_first_hour"] > 3)
        if bad:
            warnings.append(f"{bad} events have suspicious flat candles; check them manually")
        if skipped:
            warnings.append(f"{len(skipped)} events skipped (first: {skipped[0]})")
        summary: Dict[str, Any] = {"symbol": symbol, "pip_size": pip, "events": n}
        if n:
            abs5 = [abs(r["move_5m"]) for r in rows]
            rng = [r["range_60m"] for r in rows]
            rev = sum(1 for r in rows if r["first5_reversed"])
            big = [r for r in rows if abs(r["move_5m"]) >= big_move]
            big_rev = sum(1 for r in big if r["first5_reversed"])
            summary.update({
                "period": f"{rows[0]['release_utc']:%Y-%m-%d} -> {rows[-1]['release_utc']:%Y-%m-%d}",
                "first_5m_move_median": round(median(abs5), 1),
                "first_5m_move_average": round(mean(abs5), 1),
                "first_hour_range_median": round(median(rng), 1),
                "first_hour_range_max": round(max(rng), 1),
                "first5_reversed_by_close": f"{rev} of {n} ({100 * rev / n:.0f}%)",
                "big_first_move": f"5m move >= {big_move:g}",
                "big_first_move_reversed": f"{big_rev} of {len(big)}"
                                           + (f" ({100 * big_rev / len(big):.0f}%)" if big else ""),
            })
        return Result(rows, summary, warnings)

    def nfp_study(self, symbol: str, years: int = 5, end: Optional[DateLike] = None, **kw) -> Result:
        """Event study around US Non-Farm Payrolls for the last `years` years."""
        end_d = to_day(end) if end else datetime.now(timezone.utc).date()
        start_d = end_d.replace(year=end_d.year - years)
        res = self.event_study(symbol, self.nfp_times(start_d, end_d), **kw)
        res.summary = {"study": f"NFP, last {years} years", **res.summary}
        return res
