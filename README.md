# cg-data

Chart Guy's market research toolkit.


A small, careful layer on top of the official [`lse-data`](https://github.com/londonstrategicedge/lse-data) client.
Streaming, raw history, options and company data stay in `lse-data`. This package fixes the traps you hit in practice:

| Trap in the raw API | What chartguy-data does |
|---|---|
| Only `YYYY-MM-DD` dates accepted (400 otherwise) | Converts any date/datetime for you |
| `end` is exclusive, so the last day goes missing | `end` is inclusive |
| Daily forex has Sunday stub candles | Dropped on 1d/1w/1mo by default |
| Early history is close-only (O=H=L=C) | Warns when more than 5% of candles are flat |
| Numbers with no context | Every result has a sample size, date range and warnings |

## Install (Windows)

```powershell
python -m pip install git+https://github.com/muzamil-glitch/cg-data
python -m chartguy_data.cli check
```

If `pip` can't reach GitHub, download the repo as a ZIP, unzip it and run `python -m pip install .` inside the folder.

`LSE_API_KEY` must be set (`setx LSE_API_KEY "..."`, then open a new terminal). The key is never printed.

## Command line

Use `cgdata ...` if your Python `Scripts` folder is on PATH, otherwise `python -m chartguy_data.cli ...`.

```powershell
cgdata candles EUR/USD 1d 2026-09-01 2026-09-30 --csv eurusd_sep.csv
cgdata ranges  EUR/USD 2026-09-01 2026-09-30
cgdata nfp     USD/JPY --years 5 --csv nfp_usdjpy.csv
cgdata event   EUR/USD cpi 2023-01-01 2026-09-30 --exclude core
```

## Python

```python
from chartguy_data import CGData

cg = CGData()                                   # reads LSE_API_KEY
res = cg.nfp_study("USD/JPY", years=5)
print(res.report())                             # summary + warnings
res.to_csv("nfp_usdjpy.csv")
df = res.df()                                   # pandas DataFrame (needs pandas)

cg.daily_ranges("XAU/USD", "2026-09-01", "2026-09-30").summary["weekly"]

times = cg.event_times(["cpi"], "2023-01-01", "2026-09-30", region="US", exclude=["core"])
cg.event_study("EUR/USD", times, timeframe="5m").report()

cg.lse   # the underlying lse.LSE client, for streaming, options, dividends, etc.
```

### Event study columns (pips; points for non-FX)
`move_5m`, `move_15m`, `move_60m`, `range_60m`, `move_to_close`, `range_to_close` (close = 21:00 UTC),
`first5_reversed` (the first 5-minute direction flipped by the close), `flat_candles_first_hour`, and `date_source` (calendar or first-Friday rule).

## Rules baked in
- Results describe the past only. They are not signals.
- Fewer than 30 events → "noisy" warning. NFP dates from the first-Friday fallback are flagged.
- Don't redistribute raw LSE data. Your own summaries and charts are fine.

## Tests
`python tests/test_offline.py` runs against a fake client that copies the API's quirks. No key is needed.

## Repository layout
- `chartguy_data/`: this package (what gets installed).
- `tests/`: offline tests.
- `upstream-lse-data/`: an unmodified copy of London Strategic Edge's MIT-licensed `lse-data` 0.14.0, kept for reference. It is **not** installed by this package; `lse-data` comes from PyPI so it gets upstream fixes.

Not affiliated with London Strategic Edge. Market data comes from their API under your own key and their terms.
