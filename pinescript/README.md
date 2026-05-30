# PineScript - ES 2-Legged Pullback

Brooks-style 2-legged pullback for /ES, designed for **5-minute** charts
(TradingView's default subscription tier does not include tick charts).

Two files - same state machine, different roles:

| File | Role |
|---|---|
| **`es_pa_strategy.pine`** | Full strategy with `strategy.entry` / `strategy.exit` wired up. Shows up in TradingView's Strategy Tester. |
| **`es_pa_indicator.pine`** | Visual-only companion. Same detection, no orders. Useful as a second study to see scoring details (leg counts, EMA proximity, grade label per signal). |

## Install

1. Open TradingView, attach a chart for **/ES1!** or **ES** on the **5-minute** timeframe.
2. Open the **Pine Editor** (bottom panel).
3. Paste the contents of `es_pa_strategy.pine`.
4. Click **Save**, then **Add to chart**.
5. The strategy appears in the **Strategy Tester** tab (bottom) - that's where backtest results live.
6. Optionally, also paste `es_pa_indicator.pine` as a separate study for richer visuals.

## State machine

```
IDLE -> LEG1 -> BETWEEN -> LEG2 -> trigger
```

- **LEG1**: consecutive bars where `high <= prior high` (bull case) / `low >= prior low` (bear).
- **BETWEEN**: the small bounce after Leg 1 ends.
- **LEG2**: a second sequence of lower-or-equal highs (bull) / higher-or-equal lows (bear).
- **Trigger**: a bar that breaks the leg direction (high > prior high for 2EL, low < prior low for 2ES).

## Quality score (0-10, Brooks-textbook)

| Component | Points | Reward |
|---|---:|---|
| Leg 1 momentum | 0-3 | fraction of leg-1 bars with lower lows (bull) / higher highs (bear) |
| Leg 2 momentum | 0-3 | same for leg 2 |
| Leg 2 depth | 0/1/3 | **HL / double-bottom = 3**; tied = 1; new low = 0 |
| Both legs >= 2 bars | 0-1 | filters single-bar legs |

Grades: A 8-10, B 6-7, C 4-5, D 0-3.

## Inputs (grouped)

- **01 Detection**: EMA length (21), EMA slope lookback (5), EMA proximity ticks (8), min pullback ticks (8), max bars per leg (15), signal bar criteria
- **02 Quality / sides**: min quality score (6), allow longs/shorts
- **03 Exits**: stop mode (Structural / FixedTicks), target mode (Swing / Fixed1Pt / Fixed2Pt), entry expiry
- **04 Risk / session**: RTH only, max daily trades, max daily loss
- **05 Visuals**: EMA, leg dots, entry/stop/target lines

## Default config (best cell from Python backtest)

- LONG only (60-day /ES dataset was bull-dominant; shorts opt-in)
- Min quality 6 (A + B)
- Structural stop (1 tick beyond Leg 2 extreme)
- Swing-high target (top of pullback)
- RTH 09:30-16:00 ET

## Notes for 5-min charts vs 2000-tick

The Python backtest was on 2000-tick bars. Pine runs on 5-min. The state machine is bar-agnostic, but the *signal frequency* and *typical move size* differ. Tune `min_pullback_ticks` and `max_leg_bars` if 5-min produces too few or too many signals.

## Backtest

After loading, open the **Strategy Tester** tab. Default account is $25k, 1 contract per signal, $1.25/side commission, 1-tick slippage.

For longer history, switch to **Deep Backtest** if your TradingView plan supports it.
