# PriceAction

Multi-platform price action trading scripts for /ES futures, built around Brooks-style
2-legged pullbacks (2EL/2ES), Failed Second Entries (F2EL/F2ES), trendlines,
trading ranges, and channels on a 21 EMA.

## Layout

```
data/          60 days of /ES 2000-tick bars (Mar 29 – May 28, 2026)
docs/          strategy-spec.md — single source of truth for all platforms
python/        backtest engine for validating logic before porting
ninjatrader/   NinjaScript C# strategy (2000 tick)
pinescript/    PineScript v5 strategy (5-min, TV subscription has no tick)
thinkorswim/   thinkScript study (2000 tick)
```

## Workflow

1. Spec the rules in `docs/strategy-spec.md`
2. Validate on real data with `python/`
3. Port to each platform once the logic is proven

The Python pass exists so all three platform ports agree with each other.
