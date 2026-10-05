"""Offline tests: a fake LSE client that mimics the real API's quirks.

Run: python -m pytest tests   (or: python tests/test_offline.py)
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chartguy_data import CGData, CGDataError, api_date, end_exclusive, first_friday_nfp, pip_size  # noqa
from chartguy_data.cli import main as cli_main  # noqa

UTC = timezone.utc


class FakeLSE:
    """Mimics lse-data: YYYY-MM-DD only, exclusive end, Sunday daily candles, Z timestamps."""

    def __init__(self, calendar=None, flat_before=None):
        self.calendar = calendar or []
        self.flat_before = flat_before
        self.calls = []
        self.cal_limit = 5000

    @staticmethod
    def _check(d):
        if len(d) != 10:
            raise ValueError(f"[400] invalid date {d!r} (use YYYY-MM-DD)")
        return date.fromisoformat(d)

    def candles(self, symbol, timeframe, start=None, end=None, **kw):
        s, e = self._check(start), self._check(end)
        self.calls.append((symbol, timeframe, start, end))
        out = []
        if timeframe == "1d":
            d = s
            while d < e:  # exclusive end
                if d.weekday() != 5:  # Saturday has no candle, Sunday does
                    o = 150.0
                    h, l, c = (o + 0.5, o - 0.5, o + 0.2) if d.weekday() != 6 else (o + 0.05, o - 0.05, o)
                    if self.flat_before and d < self.flat_before:
                        o = h = l = c = 150.0
                    out.append(self._row(symbol, datetime(d.year, d.month, d.day, tzinfo=UTC), o, h, l, c))
                d += timedelta(days=1)
        elif timeframe == "5m":
            t = datetime(s.year, s.month, s.day, tzinfo=UTC)
            end_t = datetime(e.year, e.month, e.day, tzinfo=UTC)
            price = 150.0
            while t < end_t:
                # Synthetic NFP: +0.30 jump in the release candle (12:30/13:30), then drift down
                if t.weekday() == 4 and (t.hour, t.minute) in ((12, 30), (13, 30)):
                    o, c = price, price + 0.30
                elif t.weekday() == 4 and t.hour >= 13 and t.hour < 21:
                    o, c = price, price - 0.01
                else:
                    o, c = price, price
                out.append(self._row(symbol, t, o, max(o, c) + 0.02, min(o, c) - 0.02, c))
                price = c
                t += timedelta(minutes=5)
        return out[::1]  # oldest first, like the API

    @staticmethod
    def _row(symbol, t, o, h, l, c):
        return {"symbol": symbol, "timestamp": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "open": o, "high": h, "low": l, "close": c, "volume": 0}

    def economic_calendar(self, region=None, start=None, end=None, **kw):
        s, e = self._check(start), self._check(end)
        rows = [r for r in self.calendar if s <= date.fromisoformat(r["date"][:10]) < e]
        return rows[: kw.get("limit", self.cal_limit)]  # the real API caps rows per call


# ------------------------------------------------------------------ helpers
def test_helpers():
    assert api_date("2026-09-01T12:30:00Z") == "2026-09-01"
    assert end_exclusive("2026-09-30") == "2026-10-01"
    assert pip_size("USD/JPY") == 0.01 and pip_size("EUR/USD") == 0.0001
    assert pip_size("XAU/USD") == 0.1 and pip_size("AAPL") == 1.0
    ff = first_friday_nfp("2026-01-01", "2026-12-31")
    assert len(ff) == 12 and all(t.weekday() == 4 and t.day <= 7 for t in ff)
    assert ff[0].hour == 13 and ff[6].hour == 12  # Jan = winter (13:30 UTC), Jul = summer (12:30)


# ------------------------------------------------------------------ candles
def test_candles_inclusive_end_and_no_weekends():
    fake = FakeLSE()
    res = CGData(fake).candles("EUR/USD", "1d", "2026-09-01", "2026-09-30")
    assert fake.calls[-1][2:] == ("2026-09-01", "2026-10-01")  # dates sent as YYYY-MM-DD, end + 1
    days = [c["time"].date() for c in res.rows]
    assert days[-1] == date(2026, 9, 30)  # last day included
    assert all(d.weekday() < 5 for d in days)
    assert res.summary["weekend_candles_dropped"] == 4  # four Sundays in Sep 2026
    assert len(days) == 22


def test_intraday_keeps_weekends_by_default():
    res = CGData(FakeLSE()).candles("EUR/USD", "5m", "2026-09-06", "2026-09-06")  # a Sunday
    assert len(res.rows) == 288 and res.summary["weekend_candles_dropped"] == 0


def test_datetime_inputs_are_converted():
    fake = FakeLSE()
    CGData(fake).candles("EUR/USD", "1d", datetime(2026, 9, 1, 15, tzinfo=UTC), "2026-09-02T00:00:00Z")
    assert fake.calls[-1][2:] == ("2026-09-01", "2026-09-03")


def test_flat_candle_warning():
    res = CGData(FakeLSE(flat_before=date(2026, 9, 15))).candles("EUR/USD", "1d", "2026-09-01", "2026-09-30")
    assert any("close-only" in w for w in res.warnings)


def test_bad_range():
    try:
        CGData(FakeLSE()).candles("EUR/USD", "1d", "2026-09-30", "2026-09-01")
    except CGDataError:
        return
    raise AssertionError("expected CGDataError")


def test_daily_ranges_weeks_max_five_days():
    res = CGData(FakeLSE()).daily_ranges("USD/JPY", "2026-09-01", "2026-09-30")
    assert all(v["days"] <= 5 for v in res.summary["weekly"].values())
    assert res.summary["avg_daily_range_pips"] == 100.0  # 1.00 yen = 100 pips


# ------------------------------------------------------------------ events
CAL = [
    {"date": "2026-08-07", "datetime": "2026-08-07T12:30:00Z", "event": "Nonfarm Payrolls", "region_code": "US"},
    {"date": "2026-08-05", "datetime": "2026-08-05T12:15:00Z", "event": "ADP Nonfarm Employment Change", "region_code": "US"},
    {"date": "2026-08-07", "datetime": "2026-08-07T12:30:00Z", "event": "Nonfarm Payrolls Private", "region_code": "US"},
    {"date": "2026-09-04", "time": "12:30", "event": "Non-Farm Payrolls", "region_code": "US"},  # no datetime field
]


def test_nfp_times_mix_calendar_and_rule():
    t = CGData(FakeLSE(CAL)).nfp_times("2026-07-01", "2026-09-30")
    assert [x[0].strftime("%Y-%m-%d %H:%M") for x in t] == ["2026-07-03 12:30", "2026-08-07 12:30", "2026-09-04 12:30"]
    assert [x[1] for x in t] == ["first-Friday rule", "calendar", "calendar"]


def test_calendar_is_chunked_past_the_row_cap():
    cal = [{"date": t.strftime("%Y-%m-%d"), "datetime": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "event": "Nonfarm Payrolls"} for t in first_friday_nfp("2022-01-01", "2026-09-30")]
    fake = FakeLSE(cal)
    fake.cal_limit = 3  # one big request would only see 3 rows
    t = CGData(fake).nfp_times("2022-01-01", "2026-09-30")
    assert len(t) == len(cal) and all(src == "calendar" for _, src in t)


def test_event_study_numbers():
    cg = CGData(FakeLSE(CAL))
    res = cg.event_study("USD/JPY", cg.nfp_times("2026-08-01", "2026-09-30"))
    assert res.summary["events"] == 2
    r = res.rows[0]
    assert r["move_5m"] == 30.0  # +0.30 yen in the release candle
    assert r["move_to_close"] < 0 and r["first5_reversed"] is True  # drifted below the pre price by 21:00
    assert "2 of 2" in res.summary["first5_reversed_by_close"]
    assert any("only 2 events" in w for w in res.warnings)


def test_nfp_study_and_csv(tmp_path=None):
    import tempfile
    res = CGData(FakeLSE(CAL)).nfp_study("USD/JPY", years=1, end="2026-09-30")
    assert res.summary["study"] == "NFP, last 1 years" and res.summary["events"] >= 10
    assert any("first-Friday rule" in w for w in res.warnings)
    path = os.path.join(tempfile.mkdtemp(), "nfp.csv")
    res.to_csv(path)
    assert open(path, encoding="utf-8").readline().startswith("release_utc,")
    assert "SUMMARY" in res.report()


def test_dataframe():
    try:
        import pandas  # noqa
    except ImportError:
        return
    df = CGData(FakeLSE()).candles("EUR/USD", "1d", "2026-09-01", "2026-09-05").df()
    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]


def test_cli_with_fake(monkeypatch=None):
    import chartguy_data.cli as cli
    orig = cli.CGData
    cli.CGData = lambda *a, **k: CGData(FakeLSE(CAL))
    try:
        assert cli_main(["ranges", "EUR/USD", "2026-09-01", "2026-09-30"]) == 0
        assert cli_main(["event", "USD/JPY", "payroll", "2026-08-01", "2026-09-30", "--exclude", "adp", "private"]) == 0
    finally:
        cli.CGData = orig


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"all {len(tests)} passed")
