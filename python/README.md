# Python Backtest Engine

Validates the price action logic from `docs/strategy-spec.md` against real /ES 2000-tick data
before porting to NinjaScript, PineScript, and thinkScript.

## Files

| File | Purpose |
|---|---|
| `config.py` | All configurable parameters (matches spec §10) |
| `indicators.py` | EMA, ATR, swing pivots |
| `data.py` | CSV loader, session filter |
| `engine.py` | Core analysis: trend, trendlines, S/R, ranges, KEPs, pullback structure, setup detection |
| `backtest.py` | Order fill simulator + performance metrics |
| `plot.py` | Chart rendering (optional, mplfinance/matplotlib) |
| `run.py` | CLI entry point |

## Setup

```bash
cd python
pip install -r requirements.txt
```

## Run

```bash
# Default: RTH only, BEYOND_SIGNAL_BAR stop, R_MULTIPLE=2.0 target
python run.py

# Try a 1-point scalp target
python run.py --target-mode FIXED_POINTS --target-points 1

# Try fixed 4-pt stop with 2-pt target
python run.py --stop-mode FIXED_POINTS --stop-points 4 --target-mode FIXED_POINTS --target-points 2

# Render a single day's chart (saves to out/)
python run.py --plot-day 2026-04-15 --plot-equity --save-signals --save-trades

# Run on overnight too
python run.py --session ETH
```

## Output

Console prints:
- Bar/session counts
- Signal counts per setup type
- Performance: trades, win%, expectancy (points + R), profit factor, max DD, per-setup breakdown

With `--save-signals` and `--save-trades`, CSVs land in `out/`.
With `--plot-day YYYY-MM-DD` and `--plot-equity`, PNGs land in `out/`.

## Iterating

This engine is the reference. When the platform ports disagree with each other or with
your discretionary read of the chart, fix the bug here first, re-run, then propagate
the fix to the affected platform.
