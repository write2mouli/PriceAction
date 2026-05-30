# Price Action Strategy Specification

Single source of truth for the Python backtest, NinjaScript, PineScript, and thinkScript
implementations. Every rule in this document must produce the same result on every platform
when given the same bar series.

Source material: Brooks-style price action on /ES 2000-tick with 21 EMA. Course slides
(Trendline Rule, High Probability Setup Rule, Signal Bar Rule) define the canonical rules.

---

## 0. Conventions

- **Bar indexing**: bar 0 is the most recent **closed** bar. Bar 1 is the bar before it. We
  never act on an unclosed bar — all decisions are made on close of bar 0, and entries are
  triggered on the next bar.
- **Tick size**: 0.25 for /ES. **Point value**: $50.
- **EMA**: 21-period exponential moving average of close.
- **Configurable parameters** are listed in §10. All thresholds in this doc are *defaults*
  and must be exposed as inputs in every platform script.

---

## 1. Bar Vocabulary

| Term | Definition |
|---|---|
| **Bull bar** | close > open |
| **Bear bar** | close < open |
| **Doji** | `abs(close − open) ≤ DOJI_FRACTION × (high − low)` (default 0.10) |
| **Body** | `abs(close − open)` |
| **Range** | `high − low` |
| **Close strength (bull)** | `(close − low) / range` — fraction of range above the low |
| **Close strength (bear)** | `(high − close) / range` — fraction of range below the high |
| **H1** | bar with `high > prior bar high` during a pullback in a bull trend (first such bar after a down move) |
| **H2** | second such H1 after another minor pullback — the with-trend second entry trigger long |
| **L1** | bar with `low < prior bar low` during a pullback in a bear trend |
| **L2** | second such L1 — the with-trend second entry trigger short |
| **Signal bar** | the bar whose extreme will be broken to enter |
| **Entry bar** | the bar on which entry stop fills (bar after signal bar) |

### 1.1 H1/H2 detection (bull-trend pullback)

When the market is in a bull trend (§4) and is pulling back:

1. The pullback begins on the first bar that fails to make a new high after a swing high.
2. An **H1** is a bar where `high > high[1]` after at least one prior bar made a lower high.
3. After an H1, if price resumes pulling back (makes at least one lower-or-equal high), then
   the next bar with `high > high[1]` is the **H2**.
4. Within a single pullback there can also be H3, H4, etc. — we only act on H2 (second entry).

### 1.2 L1/L2 detection (bear-trend pullback)

Mirror image:

1. Pullback begins on first bar that fails to make a new low after a swing low.
2. **L1**: `low < low[1]` after at least one prior bar with a higher low.
3. **L2**: after the L1, at least one higher-or-equal low, then next `low < low[1]`.

---

## 2. Signal Bar Validation (Slide V)

> "Enter only with proper signal bar. Signal bar must confirm the direction and momentum."

A bar qualifies as a **valid long signal bar** when ALL of:

1. **Bull bar** (close > open)
2. **Close strength ≥ MIN_CLOSE_STRENGTH** (default 0.60 — close in upper 40% of range)
3. **Body ≥ MIN_BODY_FRACTION × range** (default 0.40 — not a doji)
4. **Range ≤ MAX_SIGNAL_RANGE_ATR × ATR(14)** (default 2.5 — not an oversized climactic bar)

Symmetric for **valid short signal bar**: bear bar, close in lower 40%, body ≥ 40% of range,
range not climactic.

**Context override (Slide V):** "Stronger the context, signal bar is less important."

When all three of: (a) at a KEP (§5), (b) trend agrees, (c) prior bar already showed
rejection wick ≥ 50% of range in entry direction → relax requirements:
- Allow doji signal bar (body ≥ 0.20 × range)
- Allow close strength ≥ 0.40

This relaxation is gated by `STRICT_SIGNAL_BAR` input. When `true`, no context override.

---

## 3. Swing Pivots

Used for trend classification, trendline anchoring, leg counting, S/R levels.

**Swing high** at bar i: `high[i] > high[i±k] for k = 1..SWING_STRENGTH` (default 3).
**Swing low** at bar i: mirror.

Detection is confirmed `SWING_STRENGTH` bars after the pivot — we never claim a pivot until
it's bracketed. Implementations keep a rolling list of confirmed pivots.

---

## 4. Trend Classification

At every closed bar, classify market state as one of: `BULL_TREND`, `BEAR_TREND`,
`TRADING_RANGE`.

**BULL_TREND** when ALL:
- Close > EMA21 for at least `TREND_BARS_ABOVE_EMA` of last 20 bars (default 14)
- Last two confirmed swing highs are higher (HH), last two confirmed swing lows are higher (HL)
- Active bull trendline (§6) connecting last two swing lows has positive slope and is unbroken

**BEAR_TREND** when ALL (mirror):
- Close < EMA21 for at least 14 of last 20 bars
- Last two swing highs lower (LH), last two swing lows lower (LL)
- Active bear trendline connecting last two swing highs has negative slope and is unbroken

**TRADING_RANGE** otherwise. Trading ranges are also detected explicitly in §7.

**Trend strength score** (0–100) for visualization only:
`50 + 25 × (slope_of_ema_per_bar_normalized) + 25 × (consec_bars_with_trend / 20)`

---

## 5. Key Entry Points (KEPs)

Per Slide IV, the three KEPs are:

### 5.1 EMA KEP

Active when price has pulled back to within `EMA_PROXIMITY_TICKS` (default 4 ticks = 1 pt
on /ES) of EMA21, OR has touched/crossed EMA21 during the current pullback.

### 5.2 Trendline KEP

Active when current bar has touched or pierced the active trendline (§6) by no more than
`TL_PIERCE_TICKS` (default 6 ticks). The pierce must close back on the trend side
within `TL_REJECTION_BARS` (default 2 bars).

### 5.3 S/R Level KEP

Active when current bar overlaps a flagged S/R level (§8) by ≤ `SR_PROXIMITY_TICKS`
(default 4 ticks).

A bar can simultaneously be at multiple KEPs — confluence increases setup grade.

---

## 6. Trendline Detection

Active trendlines are maintained continuously. Drawn on closed-bar data only.

### 6.1 Bull trendline (drawn under swing lows)

1. Find the two most recent confirmed swing lows: `(i1, low1)` and `(i2, low2)` with `i1 < i2`.
2. Require `low2 > low1` (rising).
3. Line equation: `y(x) = low1 + (low2 − low1) × (x − i1) / (i2 − i1)`.
4. Extend forward indefinitely until broken.
5. **Validity check**: no closed bar between i1 and current bar may close more than
   `TL_VIOLATION_TICKS` (default 4 ticks) below the line. If violated, that line is invalid;
   try the next-older pair.

### 6.2 Bear trendline (drawn over swing highs)

Mirror with descending swing highs.

### 6.3 Trendline Break (Slide I)

> "When price breaks the trendline don't counter trend trade just yet. Expect continuation
> of the previous trend. Counter trend trading while trendline is in play is against the rules.
> After correction trend can resume or reverse."

A **trendline break** is registered when a closed bar closes on the wrong side of the active
trendline by more than `TL_BREAK_TICKS` (default 4 ticks):
- Bull TL broken → close below the line by > 4 ticks
- Bear TL broken → close above the line by > 4 ticks

After a break:
1. Set state `TL_BROKEN` and record `last_break_bar`.
2. For the next `TL_TEST_WINDOW` bars (default 20), do NOT take counter-trend trades.
3. Expect a test back toward the prior trend extreme (continuation).
4. If price makes a new trend extreme (higher high in former bull / lower low in former bear)
   → trend resumes, draw a new trendline.
5. If price fails to make a new extreme AND breaks the prior swing pivot in the opposite
   direction → trend has reversed; new counter trendline becomes active.

This rule is **enforced in the entry filter** — no F2EL/F2ES against a trendline that is
"in play" (unbroken or freshly broken but not yet failed/resumed).

---

## 7. Trading Range Detection

Active when price is bracketed by horizontal S/R for an extended period.

A **trading range** is declared when:
- Over the last `RANGE_LOOKBACK` bars (default 30), the highest high and lowest low define
  a range `R`.
- At least `RANGE_TOUCHES_PER_SIDE` swing highs (default 2) are within
  `RANGE_BAND_TICKS` (default 6 ticks) of `R.high`.
- At least 2 swing lows are within 6 ticks of `R.low`.
- Neither bull nor bear trend conditions (§4) are met.

While in a trading range:
- No 2EL/2ES — second entry setups require a trend.
- F2EL/F2ES at the range extremes ARE allowed (these become the "failed breakout" setups).
- Range top/bottom are flagged as S/R levels (§8).

Range is **invalidated** when a closed bar exits the range by more than `RANGE_BREAK_TICKS`
(default 6 ticks) AND price holds outside for `RANGE_BREAK_BARS` (default 3 bars).

---

## 8. S/R Levels

Auto-detected key levels for KEPs and confluence:

- **Swing high/low cluster**: 3+ confirmed swing pivots within `SR_CLUSTER_TICKS`
  (default 4 ticks) of each other → level at the median price.
- **Prior day high / prior day low** (if session boundary detected by gap or by time).
- **Trading range top / bottom** while range is active.

Levels persist until violated by > 8 ticks of closed-bar acceptance.

---

## 9. The Four High-Probability Setups (Slide IV)

### 9.1 Setup A — 2EL / 2ES: Second Entry With Trend at KEP

**2EL (long)** triggers when ALL:

1. Market state = `BULL_TREND` (§4).
2. Current pullback contains a confirmed H1 (§1.1) AND an H2 forming on the signal bar.
3. The H2 bar (signal bar) is at a KEP (§5).
4. Signal bar passes validation (§2).
5. Pullback depth ≥ `MIN_PULLBACK_TICKS` (default 8 ticks) — filter out micro pullbacks.
6. Pullback duration ≤ `MAX_PULLBACK_BARS` (default 15 bars) — stale pullbacks don't count.
7. No active trendline break "in play" against trend direction (§6.3).

**Entry**: buy stop 1 tick above signal bar high.
**Entry expires**: if not filled within `ENTRY_EXPIRY_BARS` bars (default 2), cancel.

**2ES (short)**: mirror — bear trend, L1 + L2, at KEP, valid bear signal bar.

### 9.2 Setup B — F2EL / F2ES: Failed Second Entry Against Trend

The setup OF the trapped counter-trend traders. Triggered on the same bar as the
with-trend second entry but viewed from the failed side.

**F2ES (short trapped → fade by going long)** triggers when ALL:

1. Market state = `BULL_TREND`.
2. A counter-trend (short) second entry just attempted: i.e., during the pullback there were
   two distinct lower-high failures, the second of which formed an L2-short signal that
   was either not triggered OR triggered and reversed within `F2E_REVERSAL_BARS` (default 2).
3. The reversal bar is a valid long signal bar (§2).
4. The reversal bar is at a KEP (§5) — typically EMA or trendline.
5. Trendline rule: the bull trendline is still in play (unbroken or successfully tested).

**Entry**: buy stop 1 tick above the reversal bar high.

**F2EL (long trapped in bear)**: mirror.

In practice F2ES + 2EL often coincide on the same bar (per Slide IV diagram showing
"F2ES, 2EL" labels at the same point). When they do, that's the highest-grade setup.

### 9.3 Setup C — Failed Breakout

In a trading range OR after a fresh range break:

- Price breaks above range high / below range low by ≤ `FAIL_BREAKOUT_MAX_TICKS`
  (default 20 ticks).
- Within `FAIL_BREAKOUT_WINDOW` bars (default 5), a closed bar re-enters the range.
- The first re-entry bar that is a valid signal bar in the fade direction → entry.

**Entry**: stop 1 tick beyond the signal bar in the fade direction.

### 9.3.1 Failed Breakout — Pullback Confirmation variant (FB_PB)

The course's downtrend chart annotates a "2ES" at the TR top AND a "LH Breakout pullback"
on the very next bar — telling us the actionable entry is the LH that follows the failed
breakout, not the FB bar itself. The FB bar marks the trap; the LH pullback is the
confirmation that the trapped breakout traders are getting flushed.

When `FB_REQUIRE_PULLBACK_CONFIRMATION` is `true` (default), Failed Breakout works in two
stages:

1. **Arm**: detect the failed-BO excursion (excursion beyond the range extreme by
   ≤ `FAIL_BREAKOUT_MAX_TICKS`, then a closed bar re-enters the range). Set
   `fb_armed[side] = (excursion_extreme, armed_at_bar)`.
2. **Confirm**: within `FB_PULLBACK_WINDOW_BARS` (default 8) bars of arming, watch for:
   - Short side: a bar that prints a high `<` excursion extreme by ≥
     `FB_LH_HL_MIN_TICKS` (default 4), AND is a valid bear signal bar (§2).
   - Long side: mirror — a higher low above the excursion extreme + valid bull signal bar.
   When seen, fire entry tagged `FB_PB_SHORT` / `FB_PB_LONG`. Entry trigger and stop use
   the LH/HL pivot bar's extreme (not the original FB bar).

When `FB_REQUIRE_PULLBACK_CONFIRMATION` is `false`, the original §9.3 immediate-fade
behavior runs.

The two variants do not coexist on the same setup — armed FBs supersede immediate FBs.

### 9.4 Setup D — HL/LH Confirmation

After a trendline break and successful test of prior extreme that failed to make a new
extreme, the first confirmed HL (in former bear) or LH (in former bull) becomes a
reversal entry:

- Wait for trendline break (§6.3).
- Wait for test back toward prior extreme (continuation attempt) that fails to break it
  by more than `EXTREME_FAIL_TICKS` (default 4 ticks).
- First swing pivot that confirms the reversal structure (HL in bull reversal,
  LH in bear reversal) — buy/sell the breakout of that pivot's signal bar.

---

## 10. Configurable Parameters (inputs in every script)

### 10.1 Detection parameters

| Name | Default | Range | Notes |
|---|---:|---|---|
| `EMA_LENGTH` | 21 | 5–200 | |
| `SWING_STRENGTH` | 3 | 1–10 | bars on each side |
| `EMA_PROXIMITY_TICKS` | 4 | 0–40 | |
| `MIN_CLOSE_STRENGTH` | 0.60 | 0–1 | signal bar |
| `MIN_BODY_FRACTION` | 0.40 | 0–1 | signal bar |
| `MAX_SIGNAL_RANGE_ATR` | 2.5 | 1–5 | climax filter |
| `STRICT_SIGNAL_BAR` | true | bool | disable context override |
| `MIN_PULLBACK_TICKS` | 8 | 0–80 | |
| `MAX_PULLBACK_BARS` | 15 | 3–50 | |
| `ENTRY_EXPIRY_BARS` | 2 | 1–10 | |
| `TL_PIERCE_TICKS` | 6 | 0–40 | |
| `TL_BREAK_TICKS` | 4 | 0–40 | |
| `TL_VIOLATION_TICKS` | 4 | 0–40 | |
| `TL_TEST_WINDOW` | 20 | 5–100 | no counter-trend trades |
| `SR_PROXIMITY_TICKS` | 4 | 0–40 | |
| `SR_CLUSTER_TICKS` | 4 | 0–40 | |
| `RANGE_LOOKBACK` | 30 | 10–200 | |
| `RANGE_BAND_TICKS` | 6 | 0–40 | |
| `RANGE_BREAK_TICKS` | 6 | 0–40 | |
| `RANGE_BREAK_BARS` | 3 | 1–20 | |
| `FAIL_BREAKOUT_MAX_TICKS` | 20 | 0–200 | |
| `FAIL_BREAKOUT_WINDOW` | 5 | 1–20 | |
| `F2E_REVERSAL_BARS` | 2 | 1–10 | |

### 10.2 Position management

| Name | Default | Notes |
|---|---|---|
| `CONTRACTS` | 1 | size per signal |
| `ALLOW_LONGS` | true | |
| `ALLOW_SHORTS` | true | |
| `MAX_CONCURRENT` | 1 | open positions cap |
| `SESSION_FILTER` | "RTH" | one of "RTH", "ETH", "CUSTOM" |
| `SESSION_START` | "09:30" | ET, if CUSTOM |
| `SESSION_END` | "16:00" | ET, if CUSTOM |

### 10.3 Stop placement (one of)

`STOP_MODE` selects:

| Mode | Definition |
|---|---|
| `BEYOND_SIGNAL_BAR` | 1 tick beyond signal bar extreme (default) |
| `FIXED_POINTS` | `STOP_POINTS` away from entry (default 4 pts) |
| `FIXED_TICKS` | `STOP_TICKS` away from entry |
| `ATR` | `STOP_ATR_MULT × ATR(14)` away from entry |
| `BEYOND_SWING` | beyond the prior swing pivot creating the pullback |

### 10.4 Target / exit (one of, mix-and-match allowed)

`TARGET_MODE`:

| Mode | Definition |
|---|---|
| `FIXED_POINTS` | `TARGET_POINTS` (default 2 pts — for 1-pt-style scalping use 1) |
| `FIXED_TICKS` | `TARGET_TICKS` |
| `R_MULTIPLE` | `TARGET_R × initial_risk` (default R = 2) |
| `MEASURED_MOVE` | leg 1 distance projected from entry |
| `ATR` | `TARGET_ATR_MULT × ATR(14)` |
| `OPPOSITE_KEP` | exit at the opposite trendline / range extreme |
| `SCALE_OUT` | exit half at `T1_R` (default 1R), trail rest behind structure |

`TRAIL_MODE`:

| Mode | Definition |
|---|---|
| `NONE` | no trail (default) |
| `EMA` | trail behind EMA21 (long: lowest EMA in last N bars; short: highest) |
| `SWING` | trail behind most recent confirmed opposite-side swing pivot |
| `CHANDELIER` | `TRAIL_ATR_MULT × ATR(14)` from highest high (long) / lowest low (short) |

### 10.5 Risk filters

| Name | Default | Notes |
|---|---|---|
| `MAX_DAILY_LOSS_R` | 3.0 | stop trading after N R lost in day |
| `MAX_DAILY_TRADES` | 6 | cap trades per session |
| `COOLDOWN_BARS_AFTER_LOSS` | 5 | wait N bars after a stopout |
| `MIN_RR_TO_ENTER` | 1.0 | reject if planned reward/risk < this |

---

## 11. Execution Model

**Order types**:
- Entry: stop order placed at signal-bar trigger price, GTC for `ENTRY_EXPIRY_BARS` bars.
- Stop: stop-loss attached to filled position.
- Target: limit order at planned target.

**Fill assumptions in backtest**:
- Entry stops fill at trigger price + `SLIPPAGE_TICKS` (default 1 tick) in adverse direction.
- Target limits fill only if `high ≥ target` (long) or `low ≤ target` (short).
- Stops fill at stop price + slippage adverse.
- If both stop and target are touched on the same bar:
  - If signal bar was bull and entry bar opened above target → assume target hit first (gap).
  - If entry bar opened below stop → assume stop hit first.
  - Otherwise: assume **stop hit first** (conservative).

**Bar-by-bar processing**:
1. On close of each bar, run detection (swings, trend, KEPs, range, setups).
2. Place orders for the next bar.
3. On next bar, process orders against H/L/O/C of that bar.
4. After fill, manage stop/target/trail every bar until exit.

---

## 12. Performance Metrics

Computed by Python backtester and displayed by each platform's strategy:

- Total trades, winners, losers, win %
- Average winner, average loser, expectancy (per trade in points and in R)
- Profit factor (gross profit / gross loss)
- Max drawdown (% and $)
- Sharpe ratio (daily returns)
- Per-setup breakdown (2EL, 2ES, F2EL, F2ES, Failed Breakout, HL/LH)
- Per-session breakdown (by hour of day)
- Equity curve

---

## 13. Visual output (every platform)

- 21 EMA line
- Confirmed swing pivots (small dots)
- Active trendlines (extended forward)
- Active S/R levels (horizontal lines)
- Trading range top/bottom (shaded box)
- Setup markers:
  - 2EL: green up-triangle below signal bar
  - 2ES: red down-triangle above signal bar
  - F2EL: yellow up-triangle below signal bar
  - F2ES: orange down-triangle above signal bar
  - Failed breakout / HL-LH: cyan / magenta marker
- Entry / stop / target lines while position open

---

## 13.5 Regime-specific rules (course slides — Uptrend & Trading Range)

These slides constrain WHEN to trade — they sit on top of the setup detectors.
Every gate below is enforced before a signal is emitted.

### 13.5.1 Uptrend rules (mirror in Downtrend)

1. **Bias first** — only take with-trend setups (2EL, F2EL) when `trend_state == BULL_TREND`.
   F2ES (which is a long fade of a trapped short) is also a with-trend long, so it's allowed.
2. **No counter-trend trades** — never short into a bull trend. Enforced by the trendline rule
   (§6.3) plus the `bull_trend → block_shorts` flag.
3. **Trendline is a KEP** — already in §5.2.
4. **Short-term counter-trend trendlines** — during a bull-trend pullback, draw a short-term
   BEAR trendline connecting the swing highs of the pullback. Its break adds a "PB_TL"
   KEP class. See §6.4.
5. **Stay away from congestions** — see §7.5. Blocks ALL new entries while in congestion.
6. **No entries near the top of the move** — `pullback_depth_from_extreme` must meet
   `MIN_PULLBACK_DEPTH_FRAC` of the last impulse leg. See §13.5.3.
7. **2nd entries only off TL/EMA (preferably both)** — 2EL/2ES require KEP confluence
   (TL+EMA or any with SR) for the highest grade. F2EL/F2ES are the secondary high-prob trades.
8. **No FOMO chasing** — handled by `ENTRY_EXPIRY_BARS` (already in spec): after the entry
   stop is placed it lives for N bars, then it's cancelled. No re-issuing on the next bar.
9. **After a new high, buying is on hold** — see §13.5.4 (new-extreme cooldown).
10. **Good signal bar** — already in §2.

### 13.5.2 Trading Range rules

When `trend_state == TRADING_RANGE`:

1. **Locate key levels** — handled by S/R detection (§8).
2. **Trendlines are still KEPs** — short-term intra-range TLs (§6.4) still apply.
3. **Careful in the middle of the range** — see §13.5.5. New entries are blocked when
   `close` is in the middle band of the TR.
4. **Fade the breakout** — TR breakouts usually fail → Failed Breakout (§9.3) is the
   primary TR setup. Per-setup tilt: in a TR, F2EL/F2ES + Failed Breakout are weighted
   higher; 2EL/2ES are deprioritized (and effectively suppressed because they require a
   trend).
5. **Buy low, sell high** — explicitly enforced by §13.5.5.
6. **Look for entries at EMA** — already in §5.1.
7. **2nd entries with trend AND failed 2nd entries against the trend** — even inside a TR,
   on the short bull/bear legs within the range, 2EL/2ES against the F2EL/F2ES on the
   counter-trend leg ARE allowed (this is what the slide chart shows with `2EL` near the
   range bottom and `F2ES` near the range top labelled "Failed Breakout").

### 13.5.3 Pullback depth filter

> "The deeper the correction is, there is a higher chance of succeeding."

For 2EL / 2ES the pullback must retrace at least `MIN_PULLBACK_DEPTH_FRAC` (default 0.30)
of the last impulse leg from the prior swing low to the swing high.

- **Bull (2EL)**: let `leg = swing_high_price − prior_swing_low_price`. Pullback depth =
  `swing_high_price − bull_pb_extreme_low`. Require `depth / leg ≥ MIN_PULLBACK_DEPTH_FRAC`.
- **Bear (2ES)**: mirror.

If swing-low/high pivots aren't both available, fall back to the existing
`MIN_PULLBACK_TICKS` gate only.

### 13.5.4 New-extreme cooldown

> "After new high, buying is on hold for a short period of time until more confirmation."

After a fresh trend extreme (a new swing high in a bull trend / swing low in a bear trend
that exceeds the prior confirmed swing extreme by ≥ `NEW_EXTREME_MIN_TICKS`, default 4),
block 2EL/2ES, F2EL/F2ES for `NEW_EXTREME_COOLDOWN_BARS` (default 5) bars.

HL/LH reversal entries are NOT blocked — those are reversal trades that benefit from a
fresh failed extreme. Failed Breakout is also unblocked (the new-extreme bar IS the
breakout to fade).

### 13.5.5 Middle-of-range filter

> "Careful trading in the middle of the trading range. Buy low and sell high."

When the active trading range is `R`:
- Block new long entries when `(close − R.bottom) / (R.top − R.bottom) > MID_RANGE_HIGH_FRAC`
  (default 0.60 — don't buy in the upper 40%).
- Block new short entries when `(close − R.bottom) / (R.top − R.bottom) < MID_RANGE_LOW_FRAC`
  (default 0.40 — don't sell in the lower 40%).
- Failed Breakout entries are exempt (they fire BY DEFINITION at the extreme).

### 13.5.7 Prior-trend bias inside a Trading Range

The downtrend slide chart shows a TR that forms after a clear bear move. The TR top is
flagged as the high-probability short zone (2ES + FB_PB_SHORT). The TR bottom is NOT
flagged as a long zone. This is the **prior-trend bias** rule:

When a TR is detected within `PRIOR_TREND_MEMORY_BARS` (default 60) of a clear prior
trend (a `BULL_TREND` or `BEAR_TREND` state that lasted ≥ `PRIOR_TREND_MIN_BARS`, default
20), the TR is in the "fade-counter-side" regime:

| Prior trend | Favored TR entries | Deprioritized TR entries |
|---|---|---|
| BEAR_TREND | 2ES, FB_PB_SHORT, F2ES at TR top | 2EL, FB_PB_LONG, F2EL at TR bottom |
| BULL_TREND | 2EL, FB_PB_LONG, F2EL at TR bottom | 2ES, FB_PB_SHORT, F2ES at TR top |

"Deprioritized" means: still allowed but flagged in the signal notes. The user can choose
to disable them entirely via `TR_DISABLE_AGAINST_PRIOR_TREND` (default `false`).

This is the formal statement of "the TR is just a continuation pattern of the prior trend."

### 13.5.8 "Far from KEP" — already enforced

The downtrend slide labels middle-of-channel bars with "Far from key entry point" — a no-trade
zone. This is enforced by §5: every setup requires `at_kep(idx, side) != None`. A bar that
isn't within `EMA_PROXIMITY_TICKS` of the EMA, within `TL_PIERCE_TICKS` of an active TL, or
within `SR_PROXIMITY_TICKS` of an S/R level returns `None` and the setup is rejected.

If too many real signals are still being rejected as "far from KEP," loosen the proximity
ticks rather than removing the gate.

### 13.5.9 "Bad signal bar" — already enforced

The same slide labels a bull bar near the prior-trend high as a "Bad signal bar" — meaning
it looks like a long signal but isn't, because the context (topping after a fresh HH) is
bearish-leaning. The context-override clause in §2 captures this: when context disagrees,
the strict signal bar criteria stand and the bar is rejected.

In practice, the bar in the chart fails because:
- It's after the new-extreme cooldown bar (§13.5.4) → 2EL blocked
- The bull pullback machinery never armed (no prior pullback to enter on a 2nd entry)

### 13.5.6 Downtrend rules — explicit mirror

The course distributes Uptrend rules and Downtrend rules as two separate slide decks.
**They are exact mirrors.** Every rule in §13.5.1 has a sign-flipped downtrend equivalent:

| Uptrend rule (§13.5.1) | Downtrend equivalent |
|---|---|
| No counter-trend trades (no shorts) | No counter-trend trades (no longs) |
| Draw short-term bear trendlines for bullish-style pullbacks | Draw short-term bull trendlines for bearish-style pullbacks |
| No entries near the TOP of the move (deeper pullback better) | No entries near the BOTTOM of the move (deeper pullback better) |
| Long setups only via 2EL or F2EL at TL/EMA confluence | Short setups only via 2ES or F2ES at TL/EMA confluence |
| After a new HIGH, buying on hold for cooldown | After a new LOW, selling on hold for cooldown |
| HL (higher low) is a continuation/reversal signal | LH (lower high) is a continuation/reversal signal |

The implementation reflects this:

- Every `BULL_TREND` branch in engine.py / ESPriceAction.cs / es_pa_strategy.pine /
  es_priceaction.ts has an equivalent `BEAR_TREND` branch.
- Every 2EL/F2EL/FB_LONG/HLLH_LONG detector has a 2ES/F2ES/FB_SHORT/HLLH_SHORT mirror.
- The new-extreme cooldown (§13.5.4) tracks fresh highs AND fresh lows independently.
- The mid-range filter (§13.5.5) blocks longs above the upper band AND shorts below the
  lower band.
- The pullback depth filter (§13.5.3) uses prior swing-low → swing-high for bull and
  prior swing-high → swing-low for bear.

There are no "downtrend-specific" code paths because the spec is intentionally symmetric.
If a downtrend behavior diverges from its uptrend mirror in any implementation, that's
a bug — fix it by mirroring the bull side.

## 7.5 Congestion (separate from Trading Range)

A **congestion** is tight sideways action — narrower and shorter than a proper trading
range. Per the slides ("Stay away from Congestions"), all new entries are blocked while
in congestion. Detection:

- Over the last `CONGESTION_LOOKBACK` bars (default 10), the total range
  `max(high) − min(low)` ≤ `CONGESTION_MAX_RANGE_TICKS` (default 16 ticks = 4 pts).
- AND `|close − close[CONGESTION_LOOKBACK]| ≤ CONGESTION_MAX_DRIFT_TICKS` (default 8 ticks).
- AND no swing pivot confirmed within the lookback window.

While in congestion: **block 2EL, 2ES, F2EL, F2ES, FB, HL/LH**. Period. The strategy waits
for a breakout (genuine or failed) before re-engaging.

Congestion is a transient state — re-evaluated every bar.

## 6.4 Short-term counter-trend (pullback) trendlines

During a major bull trend's pullback, the pullback itself has its own bearish micro-trend.
Drawing a short-term bear trendline across the pullback's swing highs gives you the
**pullback-end signal** when price breaks back through it.

- Detection: during an active bull pullback (§1.1), maintain a bear "PB_TL" connecting the
  last two intra-pullback lower highs. Require at least 2 such lower highs.
- The break of this short TL (`close > PB_TL + TL_BREAK_TICKS × tick`) adds the **"PB_TL"
  KEP** class for the current bar.
- Mirror for bear-trend pullbacks → bull PB_TL across pullback lower lows.

PB_TL counts as a KEP for §9.1 (2EL/2ES) — strengthens entries that ALSO touched EMA or
the main TL.

## 14. Out of scope (for v1)

- Higher-timeframe context filter (daily trend bias)
- News-time blackout
- Volume profile / VWAP integration
- Multi-instrument
- Tick-by-tick fill simulation (we use OHLC bar approximation)

These are candidates for v2 once the v1 logic is validated on 60 days of /ES tick data.

---

## 15. Reference charts from the course

| Chart | Shows | Where covered |
|---|---|---|
| Trendline Rule | Bear trendline broken → continuation expected before reversal | §6.3 |
| High Probability Setup Rule (uptrend) | 2EL/2ES at EMA + channel; F2ES coincident with 2EL | §9.1, §9.2 |
| High Probability Setup Rule (downtrend) | Downtrend channel + EMA + TL break before reversal | §6.3 (mirror), §13.5.6 |
| High Probability Setup Rule (definitions) | The four setup types, three KEPs | §9, §5 |
| Signal Bar Rule | Bull bars for longs, bear bars for shorts; context overrides | §2 |
| Uptrend example (Price Action Rules For Uptrend) | Channel + 2EL after small consolidation | §13.5.1 |
| Uptrend annotated (2EL + F2ES + HL) | F2ES and 2EL frequently coincide; HL as structural confirmation | §9.2, §9.4 |
| Trading Range Rules + chart | TR with 2EL bouncing off bottom, F2ES + Failed Breakout at top | §7, §9.3, §13.5.2 |
| Uptrend Rules (10-rule list) | Bias, no counter-trend, TL KEP, deep pullback, second entries, no FOMO | §13.5.1 |
| Trading Range Rules (11-rule list) | Key levels, fade breakouts, buy low / sell high, EMA entries | §13.5.2 |
| Downtrend Rules (10-rule list) | Mirror of uptrend rules | §13.5.6 |
| Downtrend annotated chart ("Bad signal bar", "Far from KEP", 2ES at LH) | Context overrides signal bar; KEP gate enforced | §2, §5, §13.5.8, §13.5.9 |
| Downtrend → TR transition charts (2ES, "LH Breakout pullback") | FB needs LH/HL pullback confirmation, not immediate fade | §9.3.1 |

When more course material lands, add a row here and link to the spec section that
absorbs it. If a slide can't be mapped to an existing section, that's the signal to
add a new section — never let a rule live only as an image.
