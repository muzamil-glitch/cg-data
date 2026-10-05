"""cgdata command line.

  cgdata check
  cgdata candles EUR/USD 1d 2026-09-01 2026-09-30 [--csv out.csv]
  cgdata ranges  EUR/USD 2026-09-01 2026-09-30   [--csv out.csv]
  cgdata nfp     USD/JPY --years 5               [--csv out.csv] [--timeframe 5m]
  cgdata event   EUR/USD "cpi" 2023-01-01 2026-09-30 [--region US] [--exclude core]
"""
from __future__ import annotations

import argparse
import os
import sys

from .core import CGData, CGDataError, Result


def _finish(res: Result, csv_path: str | None, show_rows: int = 0) -> None:
    for r in res.rows[:show_rows]:
        print(r)
    print(res.report())
    if csv_path:
        print("Saved:", os.path.abspath(res.to_csv(csv_path)))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cgdata", description="Chart Guy data tools on top of lse-data")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="check lse-data, the API key and one live request")

    c = sub.add_parser("candles", help="candles, end date inclusive")
    c.add_argument("symbol"); c.add_argument("timeframe"); c.add_argument("start"); c.add_argument("end")
    c.add_argument("--keep-weekends", action="store_true"); c.add_argument("--csv")

    r = sub.add_parser("ranges", help="daily ranges in pips + weekly averages")
    r.add_argument("symbol"); r.add_argument("start"); r.add_argument("end"); r.add_argument("--csv")

    n = sub.add_parser("nfp", help="event study around US Non-Farm Payrolls")
    n.add_argument("symbol"); n.add_argument("--years", type=int, default=5)
    n.add_argument("--timeframe", default="5m"); n.add_argument("--csv")

    e = sub.add_parser("event", help="event study for any calendar event")
    e.add_argument("symbol"); e.add_argument("keyword"); e.add_argument("start"); e.add_argument("end")
    e.add_argument("--region", default="US"); e.add_argument("--exclude", nargs="*", default=[])
    e.add_argument("--timeframe", default="5m"); e.add_argument("--csv")

    a = p.parse_args(argv)
    try:
        if a.cmd == "check":
            try:
                import lse
                print("lse-data:", getattr(lse, "__version__", "installed"))
            except ImportError:
                print("lse-data NOT installed: python -m pip install lse-data"); return 1
            if not os.environ.get("LSE_API_KEY"):
                print("LSE_API_KEY NOT set. Run: setx LSE_API_KEY \"your_key\" then open a NEW terminal"); return 1
            print("LSE_API_KEY: set (not shown)")
            from datetime import date, timedelta
            today = date.today()
            res = CGData().candles("EUR/USD", "1d", today - timedelta(days=7), today)
            print(res.report())
            print("OK" if res.rows else "No rows came back; check your plan/symbol")
            return 0
        cg = CGData()
        if a.cmd == "candles":
            res = cg.candles(a.symbol, a.timeframe, a.start, a.end,
                             drop_weekends=False if a.keep_weekends else None)
            _finish(res, a.csv, show_rows=5)
        elif a.cmd == "ranges":
            res = cg.daily_ranges(a.symbol, a.start, a.end)
            weekly = res.summary.pop("weekly", {})
            _finish(res, a.csv)
            print("----- WEEKLY (Mon-Fri) -----")
            for w, v in weekly.items():
                print(f"week of {w}: {v['days']} days, avg range {v['avg_range_pips']} pips")
        elif a.cmd == "nfp":
            _finish(cg.nfp_study(a.symbol, years=a.years, timeframe=a.timeframe), a.csv)
        elif a.cmd == "event":
            times = cg.event_times([a.keyword.lower()], a.start, a.end, region=a.region,
                                   exclude=[x.lower() for x in a.exclude])
            print(f"{len(times)} matching events found")
            _finish(cg.event_study(a.symbol, times, timeframe=a.timeframe), a.csv)
    except CGDataError as err:
        print("ERROR:", err); return 1
    except Exception as err:  # API errors from lse-data (bad key, limits, bad symbol)
        print(f"API ERROR ({type(err).__name__}): {err}"); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
