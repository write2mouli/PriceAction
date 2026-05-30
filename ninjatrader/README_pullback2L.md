# ESPullback2L - NinjaScript Strategy

Focused 2-legged-pullback strategy for /ES on a 2000-tick chart. Mirrors
`pinescript/es_pa_strategy.pine` (v1).

Distinct from the full `ESPriceAction.cs` strategy (which has all 4 setups, trendlines,
trading ranges, KEPs). This one only does the 2-legged pullback with quality scoring.
Easier to reason about and tune.

## Install

1. Copy `ESPullback2L.cs` to:
   `Documents\NinjaTrader 8\bin\Custom\Strategies\ESPullback2L.cs`
2. Open the **NinjaScript Editor** in NinjaTrader (Tools -> NinjaScript Editor).
3. Right-click the Strategies folder -> **Compile** (or press F5).
4. Open a **/ES 2000-tick** chart.
5. Right-click the chart -> Strategies -> ESPullback2L -> **Enable**.

## Defaults

Pre-set to the best cell from the 60-day Python backtest:

| Input | Default | Why |
|---|---|---|
| Allow longs | true | Profitable on the dataset |
| Allow shorts | false | Lost money in bull-dominant period; opt-in carefully |
| Min quality score | 6 | Grades A and B (captures the +$588 LONG B cell) |
| Stop mode | Structural | 1 tick beyond leg-2 low/high |
| Target mode | Swing | Back to the high/low of the pullback |
| RTH only | true | 09:30-16:00 ET |
| Max trades / day | 6 | Avoid over-trading |
| Max daily loss | $750 | Auto-stops the day |
| Entry expiry | 2 bars | Cancels unfilled buy stops after 2 bars (no FOMO) |

## What it draws

- **21 EMA** (blue)
- **Leg dots**: cyan circle under Leg 1 bars, yellow under Leg 2 bars (sanity-check the
  state machine visually)
- **Entry / stop / target lines** at each signal:
  - White dashed = entry
  - Red dotted = stop
  - Lime dotted = target
- **Trade arrows** drawn natively by NinjaTrader on Strategy Analyzer / live

## Backtest

Open **Strategy Analyzer** -> select **ESPullback2L** -> data series = /ES 2000-tick ->
choose history range (60+ days recommended). Run. The Strategy Analyzer reports:
- Trade list with each entry/exit timestamped
- Performance summary: profit factor, win%, average win/loss, max drawdown
- Equity curve

## Tuning experiments

| To test | Change |
|---|---|
| A-grade only (highest quality) | `Min quality score = 8` |
| Take everything | `Min quality score = 0` |
| Fixed 2pt stop comparison | `Stop mode = FixedTicks`, `Stop ticks = 8` |
| Tight target scalp | `Target mode = Fixed1Pt`, `Min quality score = 8` |
| Enable shorts (with caveat) | `Allow shorts = true` |
| Extended session | `RTH only = false` |

## What's printed to the Output tab

Each trigger logs:
```
[2026-04-15 11:23:08.450] 2EL grade B7  entry=7042.50  stop=7038.25  tgt=7050.75
```

Useful for cross-referencing Strategy Analyzer trades against detection logic.

## Troubleshooting: I don't see any visuals after enabling the strategy

Run through this checklist in order. Most "no visuals" issues are one of these:

### 1. Confirm the strategy is actually ENABLED, not just added

- Right-click chart -> **Strategies...**
- In the dialog, find ESPullback2L in the "Configured strategies" list
- The Enabled column must show **True** (not "Configured but disabled")
- Click **Apply** to commit

After this, you should see the **status label in the top-right corner of the chart**:
```
ESPullback2L  |  bar=12345  |  BULL  |  bull state=1 score=4  |  bear state=0 score=-1  |  trades today=0
```

That label appearing means the strategy is RUNNING and processing bars.

### 2. If no status label appears, check the Output window

- **Tools -> Output Window -> NinjaScript Output**
- Look for any red error messages
- If the strategy crashed in OnStateChange or OnBarUpdate, it gets disabled silently

Common compile/runtime errors and fixes:
- `cannot convert from 'System.Windows.Media.Brush' to 'NinjaTrader.Gui.Stroke'` -- old NT version; replace `AddPlot(new Stroke(...))` with `AddPlot(Brushes.DodgerBlue, "EMA21")`
- Object reference exceptions in DataLoaded -- usually means EMA isn't constructed; check `EmaLength` input is > 0

### 3. Load enough historical bars

The strategy needs at least `BarsRequiredToTrade` (50) bars before it does anything. On a 2000-tick /ES chart, 50 bars is maybe 1-2 hours of trading.

- Right-click chart -> **Data Series** -> set **Days to load** to at least 5
- Or right-click chart -> **Reload all historical data**

### 4. EMA not showing as a blue line on the chart

This is now drawn as a strategy plot (Values[0][0]). It should appear automatically. If it doesn't:
- Right-click chart -> **Strategies...** -> select ESPullback2L -> check that the "EMA21" plot is enabled in the plot list
- Or temporarily set "Show status label" to true to confirm the strategy is at least running

### 5. No leg markers (cyan/yellow triangles) appearing

The triangles only paint when the state machine is in LEG1 (cyan) or LEG2 (yellow) state. Possible reasons none appear:
- **No trending bars**: trend requires EMA slope > 0 over 5 bars AND price on the right side of EMA. On a sideways day, neither bull_trend nor bear_trend is true and the state machine sits at 0.
  - Check the status label - if it shows "FLAT" for hours, that's why.
- **EmaSlopeLookback too aggressive**: try lowering from 5 to 3 to detect trend earlier.

### 6. No entry/stop/target lines (no signals firing)

Even with a clean 2-legged pullback, signals are gated by:
- Quality score >= `Min quality score` (default 6 = grade B+)
- EMA touched during the pullback
- Pullback depth >= `Min pullback ticks`
- RTH session (if `RTH only` = true)
- Daily trade cap not hit
- Daily loss cap not hit

To temporarily see EVERYTHING the state machine flags (for debugging):
- Set `Min quality score = 0`
- Set `RTH only = false`
- Set `Allow shorts = true`
- Enable `Verbose debug print` to see trend state in Output every 50 bars

### 7. Still nothing - last resort

- Reload the strategy: right-click chart -> Strategies... -> Remove -> Apply -> re-add
- Recompile: NinjaScript Editor -> right-click Strategies folder -> Compile
- Verify the file ended up in: `Documents\NinjaTrader 8\bin\Custom\Strategies\ESPullback2L.cs`

## Sanity-check workflow

1. Compile clean
2. Apply to /ES 2000-tick with default inputs
3. Walk through a recent trend day in **Playback** mode (Connections -> Playback Connection)
4. Confirm:
   - Cyan/yellow dots appear on bars matching your discretionary read of leg 1 / leg 2
   - Entry / stop / target lines land where you'd expect
   - Trade arrows fire on the trigger bar
5. If detection looks right but backtest is weak, tune Min quality score / target mode

## Iterating

The Python engine (`../python/backtest_2leg.py`) is the canonical reference. If
NinjaScript triggers differ from Python on the same bars, the spec or one of the
implementations has drifted. Fix the spec first.
