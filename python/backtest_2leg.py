"""Backtest the v3 2-legged-pullback detector on the 60-day /ES sample,
breaking down win rate and P&L by quality grade.

This mirrors the v3 Pine indicator (pinescript/es_pa_indicator.pine) EXACTLY:
- Same state machine (IDLE -> LEG1 -> BETWEEN -> LEG2 -> trigger)
- Same quality score components
- Same EMA / proximity / depth gates

Two target configurations are run, each with a fixed 8-tick (2pt) stop:
   1. Target = 4 ticks (1 pt)   -> RR = 1:0.5  (needs > 67% win rate to break even)
   2. Target = 8 ticks (2 pt)   -> RR = 1:1    (needs > 50% win rate)

Fill model:
   - Entry: stop order at prev_high + 1 tick (long) / prev_low - 1 tick (short).
     The trigger bar definitionally reaches that price (since high > prev high).
   - Exit: any bar after entry where high >= target or low <= stop. If both on
     same bar -> conservative: stop first.
"""
from __future__ import annotations
import os
import sys
from dataclasses import dataclass
from typing import Optional
import numpy as np

from config import Config, TICK_SIZE, POINT_VALUE
from data import load_csv, apply_session_filter, Bars
from indicators import ema

# ============================================================================
# Parameters (match v3 Pine indicator defaults)
# ============================================================================
EMA_LENGTH         = 21
EMA_SLOPE_LOOKBACK = 5
EMA_PROX_TICKS     = 8
MIN_PULLBACK_TICKS = 8
MAX_LEG_BARS       = 15
MIN_QUALITY_SCORE  = 0    # let everything through; we filter in the report

STOP_TICKS    = 8         # fixed 2-pt stop
TARGET_TICKS  = [4, 8]    # 1pt and 2pt targets

# ============================================================================
# Signal & trade types
# ============================================================================
@dataclass
class Signal:
    bar_index: int
    side: str             # "LONG" or "SHORT"
    entry_price: float
    score: int            # 0..10
    l1_bars: int
    l1_mom_bars: int      # lower-lows for LONG, higher-highs for SHORT
    l2_bars: int
    l2_mom_bars: int
    l1_extreme: float     # leg1 low (LONG) or leg1 high (SHORT)
    l2_extreme: float
    swing_extreme: float  # swing high (LONG) or swing low (SHORT)


@dataclass
class TradeResult:
    side: str
    score: int
    grade: str
    target_label: str          # "4t" | "8t" | "swing"
    win: bool
    exit_reason: str           # "target", "stop", "no_exit"
    pnl_ticks: float           # signed P&L in ticks (variable for swing target)
    r_multiple: float          # P&L in units of initial risk (stop distance)
    target_ticks_actual: int   # actual target distance in ticks (for reporting)
    bars_held: int


def grade_for(s: int) -> str:
    if s >= 8: return "A"
    if s >= 6: return "B"
    if s >= 4: return "C"
    return "D"


# ============================================================================
# State-machine signal detection (mirror of v3 Pine)
# ============================================================================
def detect_signals(bars: Bars, e: np.ndarray) -> list[Signal]:
    n = len(bars)
    signals: list[Signal] = []
    prox = EMA_PROX_TICKS * TICK_SIZE

    # ---- Bull state ----
    bull_state = 0
    bull_leg_start = 0
    bull_swing_high = float('nan')
    bull_ema_touched = False
    bull_l1_bars = 0; bull_l1_ll = 0; bull_l1_low = float('nan')
    bull_l2_bars = 0; bull_l2_ll = 0; bull_l2_low = float('nan')

    # ---- Bear state ----
    bear_state = 0
    bear_leg_start = 0
    bear_swing_low = float('nan')
    bear_ema_touched = False
    bear_l1_bars = 0; bear_l1_hh = 0; bear_l1_high = float('nan')
    bear_l2_bars = 0; bear_l2_hh = 0; bear_l2_high = float('nan')

    warmup = max(EMA_LENGTH, EMA_SLOPE_LOOKBACK) + 2
    for i in range(warmup, n):
        if np.isnan(e[i]) or np.isnan(e[i - EMA_SLOPE_LOOKBACK]):
            continue

        # Trend
        ema_up   = e[i] > e[i - EMA_SLOPE_LOOKBACK]
        ema_down = e[i] < e[i - EMA_SLOPE_LOOKBACK]
        bull_trend = ema_up   and bars.close[i] > e[i]
        bear_trend = ema_down and bars.close[i] < e[i]

        h, l = bars.high[i], bars.low[i]
        ph, pl = bars.high[i - 1], bars.low[i - 1]
        bull_pb_continues = h <= ph
        bull_pb_breaks    = h >  ph
        bear_pb_continues = l >= pl
        bear_pb_breaks    = l <  pl

        in_session = bool(bars.in_session[i])

        # ===== BULL state machine =====
        if not bull_trend:
            bull_state = 0
            bull_swing_high = float('nan')
            bull_ema_touched = False
            bull_l1_bars = 0; bull_l1_ll = 0; bull_l1_low = float('nan')
            bull_l2_bars = 0; bull_l2_ll = 0; bull_l2_low = float('nan')
        else:
            if bull_state == 0:
                if bull_pb_continues:
                    bull_state = 1
                    bull_leg_start = i
                    bull_swing_high = ph
                    bull_l1_bars = 1
                    bull_l1_ll = 1 if l < pl else 0
                    bull_l1_low = l
                    bull_l2_bars = 0; bull_l2_ll = 0; bull_l2_low = float('nan')
                    bull_ema_touched = (l <= e[i] + prox)
            elif bull_state == 1:
                if h > bull_swing_high:
                    bull_state = 0
                elif i - bull_leg_start > MAX_LEG_BARS:
                    bull_state = 0
                elif bull_pb_breaks:
                    bull_state = 2
                    bull_leg_start = i
                else:
                    bull_l1_bars += 1
                    if l < pl: bull_l1_ll += 1
                    bull_l1_low = min(bull_l1_low, l)
                    if l <= e[i] + prox: bull_ema_touched = True
            elif bull_state == 2:
                if h > bull_swing_high:
                    bull_state = 0
                elif bull_pb_continues:
                    bull_state = 3
                    bull_leg_start = i
                    bull_l2_bars = 1
                    bull_l2_ll = 1 if l < pl else 0
                    bull_l2_low = l
                    if l <= e[i] + prox: bull_ema_touched = True
            elif bull_state == 3:
                if h > bull_swing_high:
                    bull_state = 0
                elif i - bull_leg_start > MAX_LEG_BARS:
                    bull_state = 0
                elif bull_pb_breaks:
                    # ---- TRIGGER 2EL ----
                    l1_mom = bull_l1_ll / bull_l1_bars if bull_l1_bars > 0 else 0
                    l2_mom = bull_l2_ll / bull_l2_bars if bull_l2_bars > 0 else 0
                    s_l1 = round(l1_mom * 3)
                    s_l2 = round(l2_mom * 3)
                    # FLIPPED depth: Brooks-textbook is Leg 2 ABOVE Leg 1 (double-bottom / HL).
                    # Leg 2 making a NEW low = continuation of selling = weaker setup.
                    if not np.isnan(bull_l2_low) and not np.isnan(bull_l1_low):
                        if bull_l2_low > bull_l1_low:
                            s_depth = 3                                     # HL / double-bottom (best)
                        elif bull_l2_low >= bull_l1_low - 2 * TICK_SIZE:
                            s_depth = 1                                     # tied (marginal)
                        else:
                            s_depth = 0                                     # new low (continuation, weakest)
                    else:
                        s_depth = 0
                    s_bars = 1 if (bull_l1_bars >= 2 and bull_l2_bars >= 2) else 0
                    score = s_l1 + s_l2 + s_depth + s_bars

                    # Depth gate vs the deepest leg
                    pb_low_overall = bull_l1_low if np.isnan(bull_l2_low) else min(bull_l1_low, bull_l2_low)
                    depth_ok = (bull_swing_high - pb_low_overall) >= MIN_PULLBACK_TICKS * TICK_SIZE

                    if in_session and bull_ema_touched and depth_ok and score >= MIN_QUALITY_SCORE:
                        entry = ph + TICK_SIZE
                        if h >= entry:
                            signals.append(Signal(
                                bar_index=i, side="LONG", entry_price=entry,
                                score=score,
                                l1_bars=bull_l1_bars, l1_mom_bars=bull_l1_ll,
                                l2_bars=bull_l2_bars, l2_mom_bars=bull_l2_ll,
                                l1_extreme=bull_l1_low,
                                l2_extreme=bull_l2_low if not np.isnan(bull_l2_low) else bull_l1_low,
                                swing_extreme=bull_swing_high,
                            ))
                    bull_state = 0
                    bull_swing_high = float('nan')
                else:
                    bull_l2_bars += 1
                    if l < pl: bull_l2_ll += 1
                    bull_l2_low = min(bull_l2_low, l)
                    if l <= e[i] + prox: bull_ema_touched = True

        # ===== BEAR state machine (mirror) =====
        if not bear_trend:
            bear_state = 0
            bear_swing_low = float('nan')
            bear_ema_touched = False
            bear_l1_bars = 0; bear_l1_hh = 0; bear_l1_high = float('nan')
            bear_l2_bars = 0; bear_l2_hh = 0; bear_l2_high = float('nan')
        else:
            if bear_state == 0:
                if bear_pb_continues:
                    bear_state = 1
                    bear_leg_start = i
                    bear_swing_low = pl
                    bear_l1_bars = 1
                    bear_l1_hh = 1 if h > ph else 0
                    bear_l1_high = h
                    bear_l2_bars = 0; bear_l2_hh = 0; bear_l2_high = float('nan')
                    bear_ema_touched = (h >= e[i] - prox)
            elif bear_state == 1:
                if l < bear_swing_low:
                    bear_state = 0
                elif i - bear_leg_start > MAX_LEG_BARS:
                    bear_state = 0
                elif bear_pb_breaks:
                    bear_state = 2
                    bear_leg_start = i
                else:
                    bear_l1_bars += 1
                    if h > ph: bear_l1_hh += 1
                    bear_l1_high = max(bear_l1_high, h)
                    if h >= e[i] - prox: bear_ema_touched = True
            elif bear_state == 2:
                if l < bear_swing_low:
                    bear_state = 0
                elif bear_pb_continues:
                    bear_state = 3
                    bear_leg_start = i
                    bear_l2_bars = 1
                    bear_l2_hh = 1 if h > ph else 0
                    bear_l2_high = h
                    if h >= e[i] - prox: bear_ema_touched = True
            elif bear_state == 3:
                if l < bear_swing_low:
                    bear_state = 0
                elif i - bear_leg_start > MAX_LEG_BARS:
                    bear_state = 0
                elif bear_pb_breaks:
                    # ---- TRIGGER 2ES ----
                    l1_mom = bear_l1_hh / bear_l1_bars if bear_l1_bars > 0 else 0
                    l2_mom = bear_l2_hh / bear_l2_bars if bear_l2_bars > 0 else 0
                    s_l1 = round(l1_mom * 3)
                    s_l2 = round(l2_mom * 3)
                    # FLIPPED depth: Brooks-textbook is Leg 2 BELOW Leg 1 (double-top / LH).
                    # Leg 2 making a NEW high = continuation of buying = weaker short setup.
                    if not np.isnan(bear_l2_high) and not np.isnan(bear_l1_high):
                        if bear_l2_high < bear_l1_high:
                            s_depth = 3                                     # LH / double-top (best)
                        elif bear_l2_high <= bear_l1_high + 2 * TICK_SIZE:
                            s_depth = 1                                     # tied (marginal)
                        else:
                            s_depth = 0                                     # new high (continuation, weakest)
                    else:
                        s_depth = 0
                    s_bars = 1 if (bear_l1_bars >= 2 and bear_l2_bars >= 2) else 0
                    score = s_l1 + s_l2 + s_depth + s_bars

                    pb_high_overall = bear_l1_high if np.isnan(bear_l2_high) else max(bear_l1_high, bear_l2_high)
                    depth_ok = (pb_high_overall - bear_swing_low) >= MIN_PULLBACK_TICKS * TICK_SIZE

                    if in_session and bear_ema_touched and depth_ok and score >= MIN_QUALITY_SCORE:
                        entry = pl - TICK_SIZE
                        if l <= entry:
                            signals.append(Signal(
                                bar_index=i, side="SHORT", entry_price=entry,
                                score=score,
                                l1_bars=bear_l1_bars, l1_mom_bars=bear_l1_hh,
                                l2_bars=bear_l2_bars, l2_mom_bars=bear_l2_hh,
                                l1_extreme=bear_l1_high,
                                l2_extreme=bear_l2_high if not np.isnan(bear_l2_high) else bear_l1_high,
                                swing_extreme=bear_swing_low,
                            ))
                    bear_state = 0
                    bear_swing_low = float('nan')
                else:
                    bear_l2_bars += 1
                    if h > ph: bear_l2_hh += 1
                    bear_l2_high = max(bear_l2_high, h)
                    if h >= e[i] - prox: bear_ema_touched = True

    return signals


# ============================================================================
# Backtest: walk bars forward from each signal, exit on target or stop
# ============================================================================
def backtest_scaleout(bars: Bars, signals: list[Signal],
                       first_target_ticks: int = 4,
                       move_to_breakeven: bool = True,
                       horizon_bars: int = 500) -> list[TradeResult]:
    """Scale-out: half off at first_target (default 1pt), runner to swing-high target,
    structural stop (1 tick beyond leg-2 extreme). Optionally move stop to breakeven
    after the first scale.
    """
    n = len(bars)
    results: list[TradeResult] = []
    for sig in signals:
        entry = sig.entry_price

        if sig.side == "LONG":
            first_target = entry + first_target_ticks * TICK_SIZE
            runner_target = sig.swing_extreme
            initial_stop = sig.l2_extreme - TICK_SIZE
            r_dist = entry - initial_stop
            runner_dist = runner_target - entry
        else:
            first_target = entry - first_target_ticks * TICK_SIZE
            runner_target = sig.swing_extreme
            initial_stop = sig.l2_extreme + TICK_SIZE
            r_dist = initial_stop - entry
            runner_dist = entry - runner_target
        if r_dist <= 0 or runner_dist <= 0:
            continue
        stop_ticks_actual = round(r_dist / TICK_SIZE)
        runner_ticks_actual = round(runner_dist / TICK_SIZE)

        # State: have we hit the first target?
        first_hit = False
        stop = initial_stop
        end = min(sig.bar_index + horizon_bars, n)

        final_pnl_ticks = 0.0
        exit_reason = "no_exit"
        bars_held = 0

        for j in range(sig.bar_index, end):
            h, l = bars.high[j], bars.low[j]

            if sig.side == "LONG":
                stop_hit  = l <= stop
                t1_hit    = h >= first_target
                t2_hit    = h >= runner_target
            else:
                stop_hit  = h >= stop
                t1_hit    = l <= first_target
                t2_hit    = l <= runner_target

            if not first_hit:
                # Bar can hit stop, first target, or both
                if stop_hit and t1_hit:
                    # Conservative: stop first -> full loss
                    final_pnl_ticks = -stop_ticks_actual
                    exit_reason = "stop_before_t1"
                    bars_held = j - sig.bar_index
                    break
                if stop_hit:
                    final_pnl_ticks = -stop_ticks_actual
                    exit_reason = "stop_before_t1"
                    bars_held = j - sig.bar_index
                    break
                if t1_hit:
                    first_hit = True
                    if move_to_breakeven:
                        stop = entry
                    # Continue to find runner exit; locked-in is +first_target_ticks * 0.5
                    if t2_hit:
                        # Both first AND runner hit on same bar; assume scale-out then runner
                        final_pnl_ticks = 0.5 * first_target_ticks + 0.5 * runner_ticks_actual
                        exit_reason = "scale+runner_same_bar"
                        bars_held = j - sig.bar_index
                        break
                    continue
            else:
                # Already scaled out the first half; trailing the runner
                if sig.side == "LONG":
                    stop_hit_now = l <= stop
                    target_hit_now = h >= runner_target
                else:
                    stop_hit_now = h >= stop
                    target_hit_now = l <= runner_target

                if stop_hit_now and target_hit_now:
                    # Conservative: stop first on runner
                    pnl_runner_ticks = (round((stop - entry) / TICK_SIZE) if sig.side == "LONG"
                                        else round((entry - stop) / TICK_SIZE))
                    final_pnl_ticks = 0.5 * first_target_ticks + 0.5 * pnl_runner_ticks
                    exit_reason = "scale+stop_runner"
                    bars_held = j - sig.bar_index
                    break
                if stop_hit_now:
                    pnl_runner_ticks = (round((stop - entry) / TICK_SIZE) if sig.side == "LONG"
                                        else round((entry - stop) / TICK_SIZE))
                    final_pnl_ticks = 0.5 * first_target_ticks + 0.5 * pnl_runner_ticks
                    exit_reason = "scale+stop_runner" if pnl_runner_ticks < 0 else "scale+BE"
                    bars_held = j - sig.bar_index
                    break
                if target_hit_now:
                    final_pnl_ticks = 0.5 * first_target_ticks + 0.5 * runner_ticks_actual
                    exit_reason = "scale+runner"
                    bars_held = j - sig.bar_index
                    break

        if exit_reason == "no_exit":
            # Horizon hit. If we'd scaled out, lock in +0.5 * first_target_ticks; runner unrealized.
            if first_hit:
                final_pnl_ticks = 0.5 * first_target_ticks
                exit_reason = "horizon_after_scale"
            else:
                final_pnl_ticks = 0.0
                exit_reason = "horizon_no_scale"
            bars_held = end - sig.bar_index

        r_multiple = final_pnl_ticks / stop_ticks_actual if stop_ticks_actual > 0 else 0.0
        won = final_pnl_ticks > 0
        results.append(TradeResult(
            side=sig.side, score=sig.score, grade=grade_for(sig.score),
            target_label="scale" + ("+BE" if move_to_breakeven else ""),
            win=won, exit_reason=exit_reason,
            pnl_ticks=final_pnl_ticks, r_multiple=r_multiple,
            target_ticks_actual=runner_ticks_actual,
            bars_held=bars_held,
        ))
    return results


def backtest(bars: Bars, signals: list[Signal],
              stop_mode: str, target_mode: str,
              stop_ticks: int = 0, target_ticks: int = 0,
              horizon_bars: int = 500) -> list[TradeResult]:
    """
    stop_mode:   'fixed' (stop_ticks from entry) or 'structural' (1 tick beyond leg-2 extreme)
    target_mode: 'fixed' (target_ticks from entry) or 'swing' (back to pullback swing extreme)
    """
    n = len(bars)
    results: list[TradeResult] = []

    for sig in signals:
        entry = sig.entry_price

        # Compute target
        if target_mode == "fixed":
            target_pts = target_ticks * TICK_SIZE
            target = entry + target_pts if sig.side == "LONG" else entry - target_pts
        elif target_mode == "swing":
            target = sig.swing_extreme
        else:
            raise ValueError(target_mode)

        # Compute stop
        if stop_mode == "fixed":
            stop_pts = stop_ticks * TICK_SIZE
            stop = entry - stop_pts if sig.side == "LONG" else entry + stop_pts
        elif stop_mode == "structural":
            if sig.side == "LONG":
                stop = sig.l2_extreme - TICK_SIZE    # 1 tick below leg-2 low
            else:
                stop = sig.l2_extreme + TICK_SIZE    # 1 tick above leg-2 high
        else:
            raise ValueError(stop_mode)

        # Compute distances + R-multiple basis
        if sig.side == "LONG":
            actual_target_ticks = round((target - entry) / TICK_SIZE)
            actual_stop_ticks = round((entry - stop) / TICK_SIZE)
        else:
            actual_target_ticks = round((entry - target) / TICK_SIZE)
            actual_stop_ticks = round((stop - entry) / TICK_SIZE)

        if actual_target_ticks <= 0 or actual_stop_ticks <= 0:
            continue

        target_label = "swing" if target_mode == "swing" else f"{target_ticks}t"

        result: Optional[TradeResult] = None
        end = min(sig.bar_index + horizon_bars, n)
        for j in range(sig.bar_index, end):
            h, l = bars.high[j], bars.low[j]
            if sig.side == "LONG":
                stop_hit   = l <= stop
                target_hit = h >= target
            else:
                stop_hit   = h >= stop
                target_hit = l <= target

            # On the very entry bar, only count exits that the bar's range supports
            # (already handled by stop_hit/target_hit above). Conservative: stop wins ties.
            if stop_hit and target_hit:
                pnl = -actual_stop_ticks
                r = -1.0
                reason = "stop"
                won = False
            elif stop_hit:
                pnl = -actual_stop_ticks
                r = -1.0
                reason = "stop"
                won = False
            elif target_hit:
                pnl = actual_target_ticks
                r = actual_target_ticks / actual_stop_ticks
                reason = "target"
                won = True
            else:
                continue

            result = TradeResult(
                side=sig.side, score=sig.score, grade=grade_for(sig.score),
                target_label=target_label, win=won, exit_reason=reason,
                pnl_ticks=float(pnl), r_multiple=r,
                target_ticks_actual=actual_target_ticks,
                bars_held=j - sig.bar_index,
            )
            break

        if result is None:
            result = TradeResult(
                side=sig.side, score=sig.score, grade=grade_for(sig.score),
                target_label=target_label, win=False, exit_reason="no_exit",
                pnl_ticks=0.0, r_multiple=0.0,
                target_ticks_actual=actual_target_ticks,
                bars_held=end - sig.bar_index,
            )
        results.append(result)
    return results


# ============================================================================
# Reporting
# ============================================================================
def report(results: list[TradeResult], stop_ticks: int, header: str):
    print()
    print("=" * 90)
    print(f"  {header}")
    print("=" * 90)

    def block(label: str, ts: list[TradeResult]):
        n = len(ts)
        if n == 0:
            print(f"  {label:>9}  no trades")
            return
        wins = sum(1 for t in ts if t.win)
        gross_ticks = sum(t.pnl_ticks for t in ts)
        avg_ticks = gross_ticks / n
        total_r = sum(t.r_multiple for t in ts)
        avg_r = total_r / n
        dollars = gross_ticks * TICK_SIZE * POINT_VALUE
        no_exit = sum(1 for t in ts if t.exit_reason == "no_exit")
        target_dists = [t.target_ticks_actual for t in ts]
        avg_tgt = sum(target_dists) / len(target_dists) if target_dists else 0
        print(f"  {label:>9}  n={n:>4}  win={wins:>3} ({wins/n*100:>5.1f}%)  "
              f"net={gross_ticks:>+7.0f}t ({gross_ticks * TICK_SIZE:+7.2f}pt)  "
              f"avg={avg_ticks:>+5.2f}t  R={avg_r:>+5.2f}  ${dollars:>+8.0f}  "
              f"tgt~{avg_tgt:>4.1f}t  no_exit={no_exit}")

    print()
    print("  by GRADE:")
    by_grade = {"A": [], "B": [], "C": [], "D": []}
    for t in results:
        by_grade[t.grade].append(t)
    for g in ["A", "B", "C", "D"]:
        block(g, by_grade[g])
    block("TOTAL", results)

    print()
    print("  by RAW SCORE:")
    by_score: dict[int, list[TradeResult]] = {}
    for t in results:
        by_score.setdefault(t.score, []).append(t)
    for s in sorted(by_score.keys(), reverse=True):
        block(f"score {s}", by_score[s])

    print()
    print("  by SIDE x GRADE:")
    for side in ["LONG", "SHORT"]:
        for g in ["A", "B", "C", "D"]:
            ts = [t for t in results if t.side == side and t.grade == g]
            block(f"{side} {g}", ts)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.normpath(os.path.join(here, "..", "data", "ES_2000tick_sample.csv"))

    cfg = Config()
    cfg.session_filter = "RTH"

    print(f"Loading {csv_path} ...")
    bars = load_csv(csv_path)
    bars = apply_session_filter(bars, cfg)
    print(f"  {len(bars):,} bars, {bars.times[0]} -> {bars.times[-1]}")
    print(f"  in-session: {int(bars.in_session.sum()):,}")

    print("\nDetecting 2-legged pullbacks (v3 state machine) ...")
    e = ema(bars.close, EMA_LENGTH)
    signals = detect_signals(bars, e)
    n = len(signals)
    print(f"  {n} signals total ({sum(1 for s in signals if s.side == 'LONG')} long, "
          f"{sum(1 for s in signals if s.side == 'SHORT')} short)")

    by_grade_cnt: dict[str, int] = {}
    by_score_cnt: dict[int, int] = {}
    for s in signals:
        by_grade_cnt[grade_for(s.score)] = by_grade_cnt.get(grade_for(s.score), 0) + 1
        by_score_cnt[s.score] = by_score_cnt.get(s.score, 0) + 1
    print("  Grade distribution:")
    for g in ["A", "B", "C", "D"]:
        print(f"    {g}: {by_grade_cnt.get(g, 0):>4}")
    print("  Raw score distribution:")
    for s in sorted(by_score_cnt.keys(), reverse=True):
        print(f"    {s:>2}: {by_score_cnt[s]:>4}")

    # ----- Structural stop, three target modes -----
    target_configs = [
        ("1pt (4t)",   dict(target_mode="fixed", target_ticks=4)),
        ("2pt (8t)",   dict(target_mode="fixed", target_ticks=8)),
        ("swing high", dict(target_mode="swing")),
    ]
    all_runs: list[tuple[str, list[TradeResult]]] = []
    for tgt_label, tgt_kwargs in target_configs:
        results = backtest(bars, signals, stop_mode="structural", **tgt_kwargs)
        all_runs.append((tgt_label, results))

    # ----- Per-config: side x grade breakdown -----
    for tgt_label, results in all_runs:
        avg_tgt = sum(t.target_ticks_actual for t in results) / len(results)
        header = f"TARGET = {tgt_label}   STOP = STRUCTURAL (leg-2 +/-1t)   avg target ~{avg_tgt:.1f}t"
        report(results, stop_ticks=STOP_TICKS, header=header)

    # ----- Cross-summary tables -----
    def _stats(ts: list[TradeResult]) -> tuple[int, float, float, float, float]:
        if not ts:
            return 0, 0.0, 0.0, 0.0, 0.0
        n = len(ts)
        wins = sum(1 for t in ts if t.win)
        win_pct = wins / n * 100
        avg_r = sum(t.r_multiple for t in ts) / n
        net_pts = sum(t.pnl_ticks for t in ts) * TICK_SIZE
        dollars = net_pts * POINT_VALUE
        return n, win_pct, avg_r, net_pts, dollars

    def _row(label: str, ts: list[TradeResult]):
        n, win_pct, avg_r, net_pts, dollars = _stats(ts)
        if n == 0:
            print(f"  {label:<14}  no trades")
        else:
            print(f"  {label:<14} {n:>5} {win_pct:>6.1f}% {avg_r:>+7.2f} {net_pts:>+9.2f} {dollars:>+9.0f}")

    def _print_summary(title: str, filter_fn):
        print()
        print("=" * 80)
        print(f"  {title}")
        print("=" * 80)
        print(f"  {'config':<14} {'n':>5} {'win%':>7} {'avg R':>7} {'net pts':>9} {'$':>9}")
        print(f"  {'-'*14} {'-'*5} {'-'*7} {'-'*7} {'-'*9} {'-'*9}")
        for tgt_label, results in all_runs:
            ts = [t for t in results if filter_fn(t)]
            _row(tgt_label, ts)

    _print_summary("ALL LONGS  (structural stop)", lambda t: t.side == "LONG")
    _print_summary("ALL SHORTS (structural stop)", lambda t: t.side == "SHORT")

    # ----- Scale-out experiment -----
    scale_results = backtest_scaleout(bars, signals, first_target_ticks=4, move_to_breakeven=True)
    all_runs.append(("scale 1pt+BE", scale_results))
    scale_noBE = backtest_scaleout(bars, signals, first_target_ticks=4, move_to_breakeven=False)
    all_runs.append(("scale 1pt", scale_noBE))

    print()
    print("=" * 80)
    print("  SCALE-OUT EXPERIMENT  (half off at 1pt, runner to swing high, structural stop)")
    print("=" * 80)
    print(f"  {'config':<18} {'n':>5} {'win%':>7} {'avg R':>7} {'net pts':>9} {'$':>9}")
    print(f"  {'-'*18} {'-'*5} {'-'*7} {'-'*7} {'-'*9} {'-'*9}")
    for label, results in [
        ("scale+BE all",       scale_results),
        ("scale (no BE) all",  scale_noBE),
        ("scale+BE LONGS",     [t for t in scale_results if t.side == "LONG"]),
        ("scale+BE SHORTS",    [t for t in scale_results if t.side == "SHORT"]),
    ]:
        _row(label, results)

    # Per side x grade for scale+BE
    print()
    print("=" * 80)
    print("  SCALE+BE  -  by side x grade")
    print("=" * 80)
    print(f"  {'side+grade':<12} {'n':>4} {'win%':>7} {'avg R':>7} {'net pts':>9} {'$':>9}")
    print(f"  {'-'*12} {'-'*4} {'-'*7} {'-'*7} {'-'*9} {'-'*9}")
    for side in ["LONG", "SHORT"]:
        for grade in ["A", "B", "C", "D"]:
            ts = [t for t in scale_results if t.side == side and t.grade == grade]
            if ts:
                n, win_pct, avg_r, net_pts, dollars = _stats(ts)
                print(f"  {side+' '+grade:<12} {n:>4} {win_pct:>6.1f}% {avg_r:>+6.2f} {net_pts:>+8.2f} {dollars:>+8.0f}")

    # Per side x grade
    for side in ["LONG", "SHORT"]:
        print()
        print("=" * 80)
        print(f"  {side} by GRADE  (structural stop, all three targets)")
        print("=" * 80)
        print(f"  {'grade':<8} {'target':<12} {'n':>4} {'win%':>7} {'avg R':>7} {'net pts':>9} {'$':>9}")
        print(f"  {'-'*8} {'-'*12} {'-'*4} {'-'*7} {'-'*7} {'-'*9} {'-'*9}")
        for grade in ["A", "B", "C", "D"]:
            for tgt_label, results in all_runs:
                ts = [t for t in results if t.side == side and t.grade == grade]
                if not ts:
                    continue
                n, win_pct, avg_r, net_pts, dollars = _stats(ts)
                print(f"  {grade:<8} {tgt_label:<12} {n:>4} {win_pct:>6.1f}% {avg_r:>+6.2f} {net_pts:>+8.2f} {dollars:>+8.0f}")


if __name__ == "__main__":
    main()
