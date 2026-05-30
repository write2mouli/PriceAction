# thinkScript — ES Price Action Study

Brooks-style price action **study** for /ES on a 2000 tick chart in ThinkorSwim.

Mirrors `docs/strategy-spec.md` to the extent thinkScript allows.

## Install

1. Open ThinkorSwim → **Studies → Edit Studies** → **Create...** (or **Import...** in newer versions).
2. Paste the contents of `es_priceaction.ts` into a new study named **ES_PriceAction**.
3. Save. Apply it to a 2000 tick /ES chart.
4. Configure inputs in the study's properties panel.

## Limitations vs. NinjaScript / Python / PineScript

thinkScript can't:

- Place orders or run a strategy backtest — this is a **study**, not a strategy.
- Maintain arbitrary mutable state (no real arrays, no while loops).
- Detect overlapping trendlines beyond the simple "last 2 swing lows" line.
- Manage stops/targets/trails — for execution rules use NinjaTrader.

What it CAN do:

- Plot the 21 EMA, confirm trend regime (BULL / BEAR / RANGE label)
- Detect signal bars meeting the spec's strength / body / range criteria
- Mark 2EL / 2ES / F2EL / F2ES / Failed Breakout / HL-LH bars with up/down arrows + bubbles
- Fire **Alert(...)** when each setup fires (bell, chime, ding, ring by setup)
- RTH-only filter via `SecondsFromTime` / `SecondsTillTime`

## Setup approximations

Because thinkScript can't track the exact H2/L2 leg-counting state machine, the 2EL/2ES
detection here uses a proxy: "pullback touched EMA recently + current bar makes a higher
high (or lower low) over a 3-bar sequence". F2EL/F2ES requires a recent valid
counter-trend signal bar that didn't follow through. These rules are slightly looser than
the Python / NinjaScript / PineScript implementations.

When in doubt, treat thinkScript signals as **alert candidates** and confirm against the
NinjaScript implementation, which is the canonical port.

## Iterating

The Python engine is the canonical reference. If thinkScript fires more or fewer signals
than NinjaScript on the same day's bars, the gap is typically in the simplified
H2/L2 and trendline detection. Don't tighten thinkScript blindly — fix the spec first,
re-validate in Python, then sync NinjaScript, then approximate in thinkScript.
