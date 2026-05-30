# NinjaScript — ESPriceAction Strategy

A Brooks-style price action strategy for /ES on a 2000 tick chart.
Mirrors `docs/strategy-spec.md`.

## Install

1. Copy `ESPriceAction.cs` to:
   `Documents\NinjaTrader 8\bin\Custom\Strategies\ESPriceAction.cs`
2. In NinjaTrader, open the **NinjaScript Editor** (Tools → NinjaScript Editor).
3. Right-click the **Strategies** folder → **Compile**, or press **F5**.
4. Resolve any reference warnings, then in NinjaTrader open a 2000 tick /ES chart.
5. Right-click chart → **Strategies** → **ESPriceAction** → **Enable**.

## Inputs

Inputs are grouped:

| Group | Purpose |
|---|---|
| 01 Detection | EMA/ATR/swing/signal-bar parameters |
| 02 TL/Range/SR | Trendline, trading range, S/R thresholds |
| 03 Setups | Toggle 2EL/2ES, F2EL/F2ES, Failed Breakout, HL/LH individually |
| 04 Exits | StopMode, TargetMode, TrailMode and their parameters |
| 05 Risk | Contracts, longs/shorts, daily loss cap, RTH only |
| 06 Visuals | EMA, trendlines, range box, signal markers |

## What it draws

- 21 EMA (via `AddChartIndicator`)
- Active bull trendline (green) and bear trendline (orange-red)
- Trading range box (steel blue, when active)
- Setup markers (triangle up/down at signal bar):
  - 2EL / F2EL / FB_LONG / HLLH_LONG → upward triangles in green/yellow/cyan/blue
  - 2ES / F2ES / FB_SHORT / HLLH_SHORT → downward triangles in red/orange/magenta/pink

## Backtest

In NinjaTrader: **Tools → Strategy Analyzer → ESPriceAction → 2000 tick on /ES**.
Use 60+ days of history for meaningful metrics. Strategy Analyzer reports the same
metrics as the Python backtest plus NinjaTrader-native stats.

## Iterating

The Python engine in `../python/` is the reference. When NinjaScript behaviour
diverges from your discretionary read of the chart, validate against the Python
backtest first, then mirror the change here.
