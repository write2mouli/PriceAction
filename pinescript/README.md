# PineScript — ES Price Action Strategy

Brooks-style price action strategy for /ES, designed for **5-minute** charts
(TradingView's default subscription tier does not include tick charts).

Mirrors `docs/strategy-spec.md`.

## Install

1. Open TradingView, attach a chart for **/ES1!** or **ES** on the **5-minute** timeframe.
2. Open the **Pine Editor** (bottom panel).
3. Paste the contents of `es_priceaction.pine`.
4. Click **Save**, then **Add to chart**.
5. The strategy adds its own Strategy Tester tab — flip to that to see backtest stats.

## Inputs

Mirrors the spec / NinjaScript inputs, organized into:

- **01 Detection** — EMA length, ATR, swing strength, signal bar thresholds, pullback constraints
- **02 Trendline / Range / SR** — TL pierce/break/violation, range lookback/band
- **03 Setups** — toggle 2EL/2ES, F2EL/F2ES, Failed Breakout, HL/LH
- **04 Exits** — StopMode, TargetMode (FIXED_POINTS, R_MULTIPLE, MEASURED_MOVE, ATR, OPPOSITE_KEP, ...), min RR
- **05 Risk** — longs/shorts, daily loss cap, max trades, RTH only
- **06 Visuals** — EMA, trendlines, range box, signal labels

## Notes

- TV's pivot detection (`ta.pivothigh`/`ta.pivotlow`) confirms a pivot **`swing_strength` bars later**. The Python and NinjaScript engines do the same, so timing aligns.
- Trendlines are drawn between the two most recent confirmed swing lows (bull) or highs (bear), extending right.
- The range box is drawn when 2 highs and 2 lows are within the band of the range_lookback extremes.
- 5-min behaves differently from 2000 tick — fewer bars, longer pullbacks measured in minutes, signal bar sizes vary more with news. Tune `min_pullback_ticks`, `max_pullback_bars`, `max_signal_range_atr` per timeframe.

## Backtest

After loading, open the **Strategy Tester** tab. Default account is $25k, 1 contract per signal, $1.25/side commission, 1-tick slippage.

For longer history, switch to **Deep Backtest** if available on your TV plan.
