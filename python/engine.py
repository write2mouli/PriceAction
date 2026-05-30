"""Price action analysis engine — pure numpy.

Processes bars in order, maintains causal state (trend, trendlines, ranges,
pullbacks), and emits Signal events when setups trigger.

All logic follows docs/strategy-spec.md. When porting to NinjaScript / PineScript /
thinkScript, this file is the reference.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import math
import numpy as np

from config import Config, TICK_SIZE
from data import Bars
from indicators import ema, atr, swing_pivots


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BarView:
    """Lightweight read-only view of one bar — used in signal-bar checks."""
    i: int
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    in_session: bool
    ema: float
    atr: float

    @property
    def is_bull(self) -> bool: return self.close > self.open

    @property
    def is_bear(self) -> bool: return self.close < self.open

    @property
    def body(self) -> float: return abs(self.close - self.open)

    @property
    def range(self) -> float: return self.high - self.low

    def close_strength_bull(self) -> float:
        return (self.close - self.low) / self.range if self.range > 0 else 0.0

    def close_strength_bear(self) -> float:
        return (self.high - self.close) / self.range if self.range > 0 else 0.0


@dataclass
class Trendline:
    x0: int; y0: float
    x1: int; y1: float
    kind: str  # "BULL" | "BEAR"
    broken_at: Optional[int] = None

    @property
    def slope(self) -> float:
        return (self.y1 - self.y0) / max(self.x1 - self.x0, 1)

    def value_at(self, x: int) -> float:
        return self.y0 + self.slope * (x - self.x0)


@dataclass
class SRLevel:
    price: float
    kind: str
    created_at: int
    invalidated_at: Optional[int] = None


@dataclass
class Range:
    start: int
    top: float
    bottom: float
    broken_at: Optional[int] = None


@dataclass
class Signal:
    bar_index: int
    time: datetime
    setup: str
    side: str
    signal_bar_high: float
    signal_bar_low: float
    entry_trigger: float
    planned_stop: float
    planned_target: float
    initial_risk: float
    kep: str
    notes: str = ""


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class PriceActionEngine:
    def __init__(self, bars: Bars, cfg: Config):
        self.cfg = cfg
        self.b = bars
        # Indicators
        bars.ema = ema(bars.close, cfg.ema_length)
        bars.atr = atr(bars.high, bars.low, bars.close, cfg.atr_length)
        # Swing pivots
        self.sh_idx, self.sl_idx = swing_pivots(bars.high, bars.low, cfg.swing_strength)

        # State
        self.active_bull_tl: Optional[Trendline] = None
        self.active_bear_tl: Optional[Trendline] = None
        self.broken_bull_tl_at: Optional[int] = None
        self.broken_bear_tl_at: Optional[int] = None
        self.active_range: Optional[Range] = None
        self.sr_levels: list[SRLevel] = []

        self._reset_pullback()
        self.signals: list[Signal] = []

        # New-extreme cooldown state
        self.last_new_extreme_bar_long: int = -10_000  # last fresh HH bar
        self.last_new_extreme_bar_short: int = -10_000 # last fresh LL bar
        self.last_confirmed_swing_high_price: float = -math.inf
        self.last_confirmed_swing_low_price: float = math.inf

        # FB-armed state (§9.3.1)
        self.fb_armed_long: Optional[tuple[int, float]] = None   # (bar_idx, excursion_low)
        self.fb_armed_short: Optional[tuple[int, float]] = None  # (bar_idx, excursion_high)

        # Prior-trend memory (§13.5.7)
        self.last_bull_trend_end: int = -10_000
        self.last_bear_trend_end: int = -10_000
        self.bull_trend_run_bars: int = 0
        self.bear_trend_run_bars: int = 0
        self.prev_trend: str = "UNDEFINED"

    def _reset_pullback(self):
        self.bull_pb_active = False
        self.bull_pb_start = -1
        self.bull_pb_extreme_low = math.inf
        self.bull_pb_h1_seen = False
        self.bull_pb_h2_seen = False
        self.bull_pb_last_high = -math.inf

        self.bear_pb_active = False
        self.bear_pb_start = -1
        self.bear_pb_extreme_high = -math.inf
        self.bear_pb_l1_seen = False
        self.bear_pb_l2_seen = False
        self.bear_pb_last_low = math.inf

    # ------------------------------------------------------------------
    def _bar(self, i: int) -> BarView:
        b = self.b
        return BarView(
            i=i, time=b.times[i],
            open=float(b.open[i]), high=float(b.high[i]),
            low=float(b.low[i]), close=float(b.close[i]),
            volume=float(b.volume[i]),
            in_session=bool(b.in_session[i]),
            ema=float(b.ema[i]) if not math.isnan(b.ema[i]) else float("nan"),
            atr=float(b.atr[i]) if not math.isnan(b.atr[i]) else float("nan"),
        )

    def _confirmed_swings_at(self, idx: int) -> tuple[list[int], list[int]]:
        cutoff = idx - self.cfg.swing_strength
        sh = [p for p in self.sh_idx if p <= cutoff]
        sl = [p for p in self.sl_idx if p <= cutoff]
        return sh, sl

    # ------------------------------------------------------------------
    # Trendlines (§6)
    # ------------------------------------------------------------------
    def _update_trendlines(self, idx: int):
        b = self.b
        sh, sl = self._confirmed_swings_at(idx)
        viol_tol = self.cfg.tl_violation_ticks * TICK_SIZE

        new_bull = None
        if len(sl) >= 2:
            for j in range(len(sl) - 1, 0, -1):
                i2 = sl[j]; i1 = sl[j - 1]
                p2 = b.low[i2]; p1 = b.low[i1]
                if p2 <= p1: continue
                tl = Trendline(i1, p1, i2, p2, "BULL")
                violated = False
                for k in range(i1, idx + 1):
                    if b.close[k] < tl.value_at(k) - viol_tol:
                        violated = True; break
                if not violated:
                    new_bull = tl; break

        new_bear = None
        if len(sh) >= 2:
            for j in range(len(sh) - 1, 0, -1):
                i2 = sh[j]; i1 = sh[j - 1]
                p2 = b.high[i2]; p1 = b.high[i1]
                if p2 >= p1: continue
                tl = Trendline(i1, p1, i2, p2, "BEAR")
                violated = False
                for k in range(i1, idx + 1):
                    if b.close[k] > tl.value_at(k) + viol_tol:
                        violated = True; break
                if not violated:
                    new_bear = tl; break

        # Detect trendline breaks before swapping in new lines
        bar_close = b.close[idx]
        brk_tol = self.cfg.tl_break_ticks * TICK_SIZE
        if self.active_bull_tl is not None:
            if bar_close < self.active_bull_tl.value_at(idx) - brk_tol:
                self.active_bull_tl.broken_at = idx
                self.broken_bull_tl_at = idx
                self.active_bull_tl = None
        if self.active_bear_tl is not None:
            if bar_close > self.active_bear_tl.value_at(idx) + brk_tol:
                self.active_bear_tl.broken_at = idx
                self.broken_bear_tl_at = idx
                self.active_bear_tl = None

        if self.active_bull_tl is None and new_bull is not None:
            self.active_bull_tl = new_bull
        if self.active_bear_tl is None and new_bear is not None:
            self.active_bear_tl = new_bear

    def _trendline_in_play(self, side: str, idx: int) -> bool:
        if side == "BULL":
            if self.active_bull_tl is not None: return True
            if self.broken_bull_tl_at is not None and idx - self.broken_bull_tl_at < self.cfg.tl_test_window:
                return False
            return False
        else:
            if self.active_bear_tl is not None: return True
            if self.broken_bear_tl_at is not None and idx - self.broken_bear_tl_at < self.cfg.tl_test_window:
                return False
            return False

    # ------------------------------------------------------------------
    # Trend (§4)
    # ------------------------------------------------------------------
    def _trend_state(self, idx: int) -> str:
        if idx < 20: return "UNDEFINED"
        b = self.b
        if math.isnan(b.ema[idx]): return "UNDEFINED"

        closes = b.close[idx - 19:idx + 1]
        emas = b.ema[idx - 19:idx + 1]
        above = int(np.sum(closes > emas))
        below = int(np.sum(closes < emas))

        sh, sl = self._confirmed_swings_at(idx)
        hh_hl = False; ll_lh = False
        if len(sh) >= 2 and len(sl) >= 2:
            hh_hl = (b.high[sh[-1]] > b.high[sh[-2]]) and (b.low[sl[-1]] > b.low[sl[-2]])
            ll_lh = (b.high[sh[-1]] < b.high[sh[-2]]) and (b.low[sl[-1]] < b.low[sl[-2]])

        if above >= self.cfg.trend_bars_above_ema and hh_hl and self.active_bull_tl is not None:
            return "BULL_TREND"
        if below >= self.cfg.trend_bars_above_ema and ll_lh and self.active_bear_tl is not None:
            return "BEAR_TREND"
        return "TRADING_RANGE"

    # ------------------------------------------------------------------
    # Trading range (§7)
    # ------------------------------------------------------------------
    def _update_range(self, idx: int):
        cfg = self.cfg; b = self.b
        if idx < cfg.range_lookback: return

        if self.active_range is not None:
            r = self.active_range
            brk_tol = cfg.range_break_ticks * TICK_SIZE
            c = b.close[idx]
            if c > r.top + brk_tol or c < r.bottom - brk_tol:
                window = b.close[idx - cfg.range_break_bars + 1:idx + 1]
                if (window > r.top + brk_tol).all() or (window < r.bottom - brk_tol).all():
                    r.broken_at = idx
                    self.active_range = None
            return

        lo = idx - cfg.range_lookback + 1
        top = float(b.high[lo:idx + 1].max())
        bot = float(b.low[lo:idx + 1].min())
        band = cfg.range_band_ticks * TICK_SIZE

        sh, sl = self._confirmed_swings_at(idx)
        sh_in = [p for p in sh if lo <= p <= idx and abs(b.high[p] - top) <= band]
        sl_in = [p for p in sl if lo <= p <= idx and abs(b.low[p] - bot) <= band]

        if len(sh_in) >= 2 and len(sl_in) >= 2:
            self.active_range = Range(start=lo, top=top, bottom=bot)
            self.sr_levels.append(SRLevel(price=top, kind="RANGE_TOP", created_at=idx))
            self.sr_levels.append(SRLevel(price=bot, kind="RANGE_BOT", created_at=idx))

    # ------------------------------------------------------------------
    # KEPs (§5)
    # ------------------------------------------------------------------
    def _at_kep(self, idx: int, side: str) -> Optional[str]:
        bar = self._bar(idx)
        if math.isnan(bar.ema): return None
        keps: list[str] = []
        prox_e = self.cfg.ema_proximity_ticks * TICK_SIZE

        if side == "LONG":
            if bar.low <= bar.ema + prox_e: keps.append("EMA")
        else:
            if bar.high >= bar.ema - prox_e: keps.append("EMA")

        prox_t = self.cfg.tl_pierce_ticks * TICK_SIZE
        if side == "LONG" and self.active_bull_tl is not None:
            tlv = self.active_bull_tl.value_at(idx)
            if bar.low <= tlv + prox_t and bar.close >= tlv - prox_t:
                keps.append("TRENDLINE")
        if side == "SHORT" and self.active_bear_tl is not None:
            tlv = self.active_bear_tl.value_at(idx)
            if bar.high >= tlv - prox_t and bar.close <= tlv + prox_t:
                keps.append("TRENDLINE")

        prox_s = self.cfg.sr_proximity_ticks * TICK_SIZE
        for lv in self.sr_levels:
            if lv.invalidated_at is not None: continue
            if side == "LONG" and abs(bar.low - lv.price) <= prox_s:
                keps.append("SR"); break
            if side == "SHORT" and abs(bar.high - lv.price) <= prox_s:
                keps.append("SR"); break

        # PB_TL (§6.4) — short counter-trend TL break
        if self._pb_tl_broken(idx, side):
            keps.append("PB_TL")

        if not keps: return None
        if len(keps) >= 2: return "CONFLUENCE"
        return keps[0]

    # ------------------------------------------------------------------
    # Congestion (§7.5) — block ALL entries while in tight sideways
    # ------------------------------------------------------------------
    def _in_congestion(self, idx: int) -> bool:
        if not self.cfg.enable_congestion_filter:
            return False
        lb = self.cfg.congestion_lookback
        if idx < lb:
            return False
        b = self.b
        lo = float(b.low[idx - lb + 1:idx + 1].min())
        hi = float(b.high[idx - lb + 1:idx + 1].max())
        if (hi - lo) > self.cfg.congestion_max_range_ticks * TICK_SIZE:
            return False
        drift = abs(b.close[idx] - b.close[idx - lb])
        if drift > self.cfg.congestion_max_drift_ticks * TICK_SIZE:
            return False
        # No confirmed swing pivot inside the window
        sh, sl = self._confirmed_swings_at(idx)
        for p in sh + sl:
            if p >= idx - lb + 1:
                return False
        return True

    # ------------------------------------------------------------------
    # New-extreme cooldown (§13.5.4)
    # ------------------------------------------------------------------
    def _update_new_extreme(self, idx: int):
        """Detect fresh swing extremes exceeding prior confirmed swing extremes."""
        sh, sl = self._confirmed_swings_at(idx)
        thr = self.cfg.new_extreme_min_ticks * TICK_SIZE
        if sh:
            top = float(self.b.high[sh[-1]])
            if top > self.last_confirmed_swing_high_price + thr:
                self.last_new_extreme_bar_long = sh[-1]
                self.last_confirmed_swing_high_price = top
        if sl:
            bot = float(self.b.low[sl[-1]])
            if bot < self.last_confirmed_swing_low_price - thr:
                self.last_new_extreme_bar_short = sl[-1]
                self.last_confirmed_swing_low_price = bot

    def _new_extreme_block(self, idx: int, side: str) -> bool:
        cool = self.cfg.new_extreme_cooldown_bars
        if side == "LONG":
            return (idx - self.last_new_extreme_bar_long) < cool
        else:
            return (idx - self.last_new_extreme_bar_short) < cool

    # ------------------------------------------------------------------
    # Middle-of-range filter (§13.5.5) — "buy low, sell high"
    # ------------------------------------------------------------------
    def _mid_range_block(self, idx: int, side: str) -> bool:
        if self.active_range is None:
            return False
        r = self.active_range
        height = r.top - r.bottom
        if height <= 0:
            return False
        pos = (self.b.close[idx] - r.bottom) / height
        if side == "LONG" and pos > self.cfg.mid_range_high_frac:
            return True  # too high to buy
        if side == "SHORT" and pos < self.cfg.mid_range_low_frac:
            return True  # too low to sell
        return False

    # ------------------------------------------------------------------
    # Prior-trend memory (§13.5.7) — track trend regime transitions
    # ------------------------------------------------------------------
    def _update_trend_memory(self, idx: int, trend: str):
        if trend == "BULL_TREND":
            self.bull_trend_run_bars += 1
            if self.prev_trend != "BULL_TREND":
                self.bear_trend_run_bars = 0
        elif trend == "BEAR_TREND":
            self.bear_trend_run_bars += 1
            if self.prev_trend != "BEAR_TREND":
                self.bull_trend_run_bars = 0
        else:
            # Trend ending — record if it was long enough
            if self.prev_trend == "BULL_TREND" and self.bull_trend_run_bars >= self.cfg.prior_trend_min_bars:
                self.last_bull_trend_end = idx
            if self.prev_trend == "BEAR_TREND" and self.bear_trend_run_bars >= self.cfg.prior_trend_min_bars:
                self.last_bear_trend_end = idx
            self.bull_trend_run_bars = 0
            self.bear_trend_run_bars = 0
        self.prev_trend = trend

    def _prior_trend_bias(self, idx: int) -> str:
        """Return 'BULL', 'BEAR', or 'NONE' based on most-recent qualifying trend regime."""
        mem = self.cfg.prior_trend_memory_bars
        bull_recent = (idx - self.last_bull_trend_end) < mem
        bear_recent = (idx - self.last_bear_trend_end) < mem
        if bull_recent and not bear_recent: return "BULL"
        if bear_recent and not bull_recent: return "BEAR"
        if bull_recent and bear_recent:
            return "BULL" if self.last_bull_trend_end > self.last_bear_trend_end else "BEAR"
        return "NONE"

    # ------------------------------------------------------------------
    # Pullback depth filter (§13.5.3) — "deeper correction = better"
    # ------------------------------------------------------------------
    def _pullback_depth_ok(self, idx: int, side: str) -> bool:
        sh, sl = self._confirmed_swings_at(idx)
        if not sh or not sl:
            return True  # fall through to MIN_PULLBACK_TICKS gate
        b = self.b
        if side == "LONG":
            swing_high = float(b.high[sh[-1]])
            # find swing low BEFORE the swing high
            prior_sls = [p for p in sl if p < sh[-1]]
            if not prior_sls:
                return True
            prior_low = float(b.low[prior_sls[-1]])
            leg = swing_high - prior_low
            if leg <= 0:
                return True
            depth = swing_high - self.bull_pb_extreme_low
            return (depth / leg) >= self.cfg.min_pullback_depth_frac
        else:
            swing_low = float(b.low[sl[-1]])
            prior_shs = [p for p in sh if p < sl[-1]]
            if not prior_shs:
                return True
            prior_high = float(b.high[prior_shs[-1]])
            leg = prior_high - swing_low
            if leg <= 0:
                return True
            depth = self.bear_pb_extreme_high - swing_low
            return (depth / leg) >= self.cfg.min_pullback_depth_frac

    # ------------------------------------------------------------------
    # Short-term counter-trend trendline (§6.4) — "PB_TL" KEP
    # ------------------------------------------------------------------
    def _pb_tl_broken(self, idx: int, side: str) -> bool:
        """Return True if a short-term counter-trend TL across pullback's lower-highs
        (bull pullback) or higher-lows (bear pullback) was just broken on this bar.
        """
        if not self.cfg.enable_pb_tl:
            return False
        b = self.b
        if side == "LONG" and self.bull_pb_active and idx - self.bull_pb_start >= 4:
            start = self.bull_pb_start
            # Find at least two lower highs inside the pullback
            highs = []
            for k in range(start, idx):
                if k == start or b.high[k] < b.high[k - 1]:
                    highs.append((k, float(b.high[k])))
            # take last two with strictly decreasing prices
            if len(highs) >= 2:
                lh2 = highs[-1]; lh1 = highs[-2]
                if lh2[1] < lh1[1] and lh2[0] > lh1[0]:
                    slope = (lh2[1] - lh1[1]) / max(1, lh2[0] - lh1[0])
                    tl_now = lh2[1] + slope * (idx - lh2[0])
                    return float(b.close[idx]) > tl_now + self.cfg.tl_break_ticks * TICK_SIZE
        if side == "SHORT" and self.bear_pb_active and idx - self.bear_pb_start >= 4:
            start = self.bear_pb_start
            lows = []
            for k in range(start, idx):
                if k == start or b.low[k] > b.low[k - 1]:
                    lows.append((k, float(b.low[k])))
            if len(lows) >= 2:
                hl2 = lows[-1]; hl1 = lows[-2]
                if hl2[1] > hl1[1] and hl2[0] > hl1[0]:
                    slope = (hl2[1] - hl1[1]) / max(1, hl2[0] - hl1[0])
                    tl_now = hl2[1] + slope * (idx - hl2[0])
                    return float(b.close[idx]) < tl_now - self.cfg.tl_break_ticks * TICK_SIZE
        return False

    # ------------------------------------------------------------------
    # Signal bar validation (§2)
    # ------------------------------------------------------------------
    def _valid_signal_bar(self, bar: BarView, side: str) -> bool:
        cfg = self.cfg
        if bar.range <= 0: return False
        if not math.isnan(bar.atr) and bar.range > cfg.max_signal_range_atr * bar.atr:
            return False
        body_ok = bar.body >= cfg.min_body_fraction * bar.range
        if side == "LONG":
            return bar.is_bull and bar.close_strength_bull() >= cfg.min_close_strength and body_ok
        else:
            return bar.is_bear and bar.close_strength_bear() >= cfg.min_close_strength and body_ok

    # ------------------------------------------------------------------
    # Pullback tracking (§1)
    # ------------------------------------------------------------------
    def _update_pullbacks(self, idx: int, trend: str):
        if trend != "BULL_TREND" and self.bull_pb_active:
            self.bull_pb_active = False
        if trend != "BEAR_TREND" and self.bear_pb_active:
            self.bear_pb_active = False
        if idx == 0: return

        bar = self._bar(idx); prev = self._bar(idx - 1)

        if trend == "BULL_TREND":
            if not self.bull_pb_active:
                if bar.high <= prev.high and bar.close < prev.high:
                    self.bull_pb_active = True
                    self.bull_pb_start = idx
                    self.bull_pb_extreme_low = bar.low
                    self.bull_pb_h1_seen = False
                    self.bull_pb_h2_seen = False
                    self.bull_pb_last_high = bar.high
            else:
                if bar.low < self.bull_pb_extreme_low:
                    self.bull_pb_extreme_low = bar.low
                    self.bull_pb_h1_seen = False
                if not self.bull_pb_h1_seen:
                    if bar.high > prev.high and idx - self.bull_pb_start >= 1:
                        self.bull_pb_h1_seen = True
                        self.bull_pb_last_high = bar.high
                else:
                    if bar.high > prev.high and prev.high <= self.bull_pb_last_high:
                        if prev.high < self.bull_pb_last_high:
                            self.bull_pb_h2_seen = True
                if idx - self.bull_pb_start > self.cfg.max_pullback_bars:
                    self.bull_pb_active = False

        if trend == "BEAR_TREND":
            if not self.bear_pb_active:
                if bar.low >= prev.low and bar.close > prev.low:
                    self.bear_pb_active = True
                    self.bear_pb_start = idx
                    self.bear_pb_extreme_high = bar.high
                    self.bear_pb_l1_seen = False
                    self.bear_pb_l2_seen = False
                    self.bear_pb_last_low = bar.low
            else:
                if bar.high > self.bear_pb_extreme_high:
                    self.bear_pb_extreme_high = bar.high
                    self.bear_pb_l1_seen = False
                if not self.bear_pb_l1_seen:
                    if bar.low < prev.low and idx - self.bear_pb_start >= 1:
                        self.bear_pb_l1_seen = True
                        self.bear_pb_last_low = bar.low
                else:
                    if bar.low < prev.low and prev.low >= self.bear_pb_last_low:
                        if prev.low > self.bear_pb_last_low:
                            self.bear_pb_l2_seen = True
                if idx - self.bear_pb_start > self.cfg.max_pullback_bars:
                    self.bear_pb_active = False

    # ------------------------------------------------------------------
    # Emit signal helpers
    # ------------------------------------------------------------------
    def _emit_long(self, idx: int, setup: str, kep: str, notes: str = ""):
        bar = self._bar(idx)
        entry = bar.high + TICK_SIZE
        stop = self._compute_stop(idx, "LONG")
        target = self._compute_target(idx, "LONG", entry, stop)
        risk = entry - stop
        if risk <= 0: return
        rr = (target - entry) / risk
        if rr < self.cfg.min_rr_to_enter: return
        self.signals.append(Signal(
            bar_index=idx, time=bar.time, setup=setup, side="LONG",
            signal_bar_high=bar.high, signal_bar_low=bar.low,
            entry_trigger=entry, planned_stop=stop, planned_target=target,
            initial_risk=risk, kep=kep, notes=notes,
        ))

    def _emit_short(self, idx: int, setup: str, kep: str, notes: str = ""):
        bar = self._bar(idx)
        entry = bar.low - TICK_SIZE
        stop = self._compute_stop(idx, "SHORT")
        target = self._compute_target(idx, "SHORT", entry, stop)
        risk = stop - entry
        if risk <= 0: return
        rr = (entry - target) / risk
        if rr < self.cfg.min_rr_to_enter: return
        self.signals.append(Signal(
            bar_index=idx, time=bar.time, setup=setup, side="SHORT",
            signal_bar_high=bar.high, signal_bar_low=bar.low,
            entry_trigger=entry, planned_stop=stop, planned_target=target,
            initial_risk=risk, kep=kep, notes=notes,
        ))

    def _compute_stop(self, idx: int, side: str) -> float:
        cfg = self.cfg; b = self.b; bar = self._bar(idx)
        entry = (bar.high + TICK_SIZE) if side == "LONG" else (bar.low - TICK_SIZE)
        if cfg.stop_mode == "BEYOND_SIGNAL_BAR":
            return (bar.low - TICK_SIZE) if side == "LONG" else (bar.high + TICK_SIZE)
        if cfg.stop_mode == "FIXED_POINTS":
            return (entry - cfg.stop_points) if side == "LONG" else (entry + cfg.stop_points)
        if cfg.stop_mode == "FIXED_TICKS":
            off = cfg.stop_ticks * TICK_SIZE
            return (entry - off) if side == "LONG" else (entry + off)
        if cfg.stop_mode == "ATR":
            off = cfg.stop_atr_mult * (bar.atr if not math.isnan(bar.atr) else 0.0)
            return (entry - off) if side == "LONG" else (entry + off)
        if cfg.stop_mode == "BEYOND_SWING":
            sh, sl = self._confirmed_swings_at(idx)
            if side == "LONG" and sl: return float(b.low[sl[-1]]) - TICK_SIZE
            if side == "SHORT" and sh: return float(b.high[sh[-1]]) + TICK_SIZE
            return (bar.low - TICK_SIZE) if side == "LONG" else (bar.high + TICK_SIZE)
        return (bar.low - TICK_SIZE) if side == "LONG" else (bar.high + TICK_SIZE)

    def _compute_target(self, idx: int, side: str, entry: float, stop: float) -> float:
        cfg = self.cfg; b = self.b; bar = self._bar(idx)
        risk = abs(entry - stop)
        if cfg.target_mode == "FIXED_POINTS":
            return (entry + cfg.target_points) if side == "LONG" else (entry - cfg.target_points)
        if cfg.target_mode == "FIXED_TICKS":
            off = cfg.target_ticks * TICK_SIZE
            return (entry + off) if side == "LONG" else (entry - off)
        if cfg.target_mode == "R_MULTIPLE":
            return (entry + cfg.target_r * risk) if side == "LONG" else (entry - cfg.target_r * risk)
        if cfg.target_mode == "ATR":
            off = cfg.target_atr_mult * (bar.atr if not math.isnan(bar.atr) else 0.0)
            return (entry + off) if side == "LONG" else (entry - off)
        if cfg.target_mode == "MEASURED_MOVE":
            sh, sl = self._confirmed_swings_at(idx)
            if sh and sl:
                leg = float(b.high[sh[-1]]) - float(b.low[sl[-1]])
                return (entry + leg) if side == "LONG" else (entry - leg)
            return (entry + 2 * risk) if side == "LONG" else (entry - 2 * risk)
        if cfg.target_mode == "OPPOSITE_KEP":
            if self.active_range is not None:
                return self.active_range.top if side == "LONG" else self.active_range.bottom
            return (entry + 2 * risk) if side == "LONG" else (entry - 2 * risk)
        if cfg.target_mode == "SCALE_OUT":
            return (entry + 2 * risk) if side == "LONG" else (entry - 2 * risk)
        return (entry + 2 * risk) if side == "LONG" else (entry - 2 * risk)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self) -> list[Signal]:
        b = self.b
        n = len(b)
        warmup = max(self.cfg.ema_length, self.cfg.atr_length, self.cfg.range_lookback) + 5

        for i in range(n):
            if i >= self.cfg.swing_strength + 2:
                self._update_trendlines(i)
                self._update_range(i)
            if i < warmup: continue

            trend = self._trend_state(i)
            self._update_pullbacks(i, trend)
            self._update_new_extreme(i)
            self._update_trend_memory(i, trend)

            if not bool(b.in_session[i]): continue

            # Congestion blocks ALL new entries (§7.5)
            in_congestion = self._in_congestion(i)

            bar = self._bar(i)

            # --- 2EL / 2ES ---
            if self.cfg.enable_2el_2es and not in_congestion:
                if (trend == "BULL_TREND" and self.cfg.allow_longs
                        and self.bull_pb_active and self.bull_pb_h2_seen
                        and (bar.high - self.bull_pb_extreme_low) >= self.cfg.min_pullback_ticks * TICK_SIZE
                        and self._pullback_depth_ok(i, "LONG")
                        and not self._new_extreme_block(i, "LONG")
                        and not self._mid_range_block(i, "LONG")):
                    kep = self._at_kep(i, "LONG")
                    if kep is not None and self._valid_signal_bar(bar, "LONG"):
                        self._emit_long(i, "2EL", kep)
                        self.bull_pb_active = False

                if (trend == "BEAR_TREND" and self.cfg.allow_shorts
                        and self.bear_pb_active and self.bear_pb_l2_seen
                        and (self.bear_pb_extreme_high - bar.low) >= self.cfg.min_pullback_ticks * TICK_SIZE
                        and self._pullback_depth_ok(i, "SHORT")
                        and not self._new_extreme_block(i, "SHORT")
                        and not self._mid_range_block(i, "SHORT")):
                    kep = self._at_kep(i, "SHORT")
                    if kep is not None and self._valid_signal_bar(bar, "SHORT"):
                        self._emit_short(i, "2ES", kep)
                        self.bear_pb_active = False

            # --- F2EL / F2ES ---
            if self.cfg.enable_f2el_f2es and i >= self.cfg.f2e_reversal_bars + 1 and not in_congestion:
                if (trend == "BULL_TREND" and self.cfg.allow_longs
                        and self._trendline_in_play("BULL", i)
                        and not self._new_extreme_block(i, "LONG")
                        and not self._mid_range_block(i, "LONG")):
                    for k in range(1, self.cfg.f2e_reversal_bars + 2):
                        prior = self._bar(i - k)
                        if self._valid_signal_bar(prior, "SHORT"):
                            broke_low = False
                            for m in range(i - k + 1, i + 1):
                                if b.low[m] < prior.low - TICK_SIZE:
                                    broke_low = True; break
                            if not broke_low:
                                kep = self._at_kep(i, "LONG")
                                if kep is not None and self._valid_signal_bar(bar, "LONG"):
                                    self._emit_long(i, "F2EL", kep, notes=f"failed bear at {i-k}")
                                    break

                if (trend == "BEAR_TREND" and self.cfg.allow_shorts
                        and self._trendline_in_play("BEAR", i)
                        and not self._new_extreme_block(i, "SHORT")
                        and not self._mid_range_block(i, "SHORT")):
                    for k in range(1, self.cfg.f2e_reversal_bars + 2):
                        prior = self._bar(i - k)
                        if self._valid_signal_bar(prior, "LONG"):
                            broke_high = False
                            for m in range(i - k + 1, i + 1):
                                if b.high[m] > prior.high + TICK_SIZE:
                                    broke_high = True; break
                            if not broke_high:
                                kep = self._at_kep(i, "SHORT")
                                if kep is not None and self._valid_signal_bar(bar, "SHORT"):
                                    self._emit_short(i, "F2ES", kep, notes=f"failed bull at {i-k}")
                                    break

            # --- Failed Breakout ---  (exempt from mid-range filter, fires AT extreme)
            if self.cfg.enable_failed_breakout and self.active_range is not None and not in_congestion:
                r = self.active_range
                max_pierce = self.cfg.fail_breakout_max_ticks * TICK_SIZE
                lo_excursion = float(b.low[max(0, i - self.cfg.fail_breakout_window):i + 1].min())
                hi_excursion = float(b.high[max(0, i - self.cfg.fail_breakout_window):i + 1].max())

                if self.cfg.fb_require_pullback_confirmation:
                    # ----- Two-stage: arm then confirm with LH/HL -----
                    # Arm a long fade when there was a low excursion below the range, now re-entered
                    if (self.cfg.allow_longs and lo_excursion < r.bottom
                            and r.bottom - lo_excursion <= max_pierce
                            and bar.close > r.bottom):
                        self.fb_armed_long = (i, lo_excursion)
                    if (self.cfg.allow_shorts and hi_excursion > r.top
                            and hi_excursion - r.top <= max_pierce
                            and bar.close < r.top):
                        self.fb_armed_short = (i, hi_excursion)

                    # Confirm long fade: HL structure above excursion_low + valid bull signal.
                    # Stricter: the signal bar must make a HIGHER LOW than prev bar (HL structure).
                    if self.fb_armed_long is not None:
                        armed_at, exc_low = self.fb_armed_long
                        if i - armed_at > self.cfg.fb_pullback_window_bars:
                            self.fb_armed_long = None
                        else:
                            min_off = self.cfg.fb_lh_hl_min_ticks * TICK_SIZE
                            prev = self._bar(i - 1)
                            hl_structure = bar.low > prev.low
                            if (i > armed_at
                                    and bar.low > exc_low + min_off
                                    and hl_structure
                                    and self._valid_signal_bar(bar, "LONG")):
                                self._emit_long(i, "FB_PB_LONG", "RANGE_EDGE",
                                                notes=f"FB pullback HL above {exc_low:.2f}")
                                self.fb_armed_long = None

                    if self.fb_armed_short is not None:
                        armed_at, exc_high = self.fb_armed_short
                        if i - armed_at > self.cfg.fb_pullback_window_bars:
                            self.fb_armed_short = None
                        else:
                            min_off = self.cfg.fb_lh_hl_min_ticks * TICK_SIZE
                            prev = self._bar(i - 1)
                            lh_structure = bar.high < prev.high
                            if (i > armed_at
                                    and bar.high < exc_high - min_off
                                    and lh_structure
                                    and self._valid_signal_bar(bar, "SHORT")):
                                self._emit_short(i, "FB_PB_SHORT", "RANGE_EDGE",
                                                 notes=f"FB pullback LH below {exc_high:.2f}")
                                self.fb_armed_short = None
                else:
                    # ----- Original immediate-fade -----
                    if (self.cfg.allow_longs and lo_excursion < r.bottom
                            and r.bottom - lo_excursion <= max_pierce
                            and bar.close > r.bottom
                            and self._valid_signal_bar(bar, "LONG")):
                        self._emit_long(i, "FB_LONG", "RANGE_EDGE", notes=f"failed break below {r.bottom:.2f}")
                    if (self.cfg.allow_shorts and hi_excursion > r.top
                            and hi_excursion - r.top <= max_pierce
                            and bar.close < r.top
                            and self._valid_signal_bar(bar, "SHORT")):
                        self._emit_short(i, "FB_SHORT", "RANGE_EDGE", notes=f"failed break above {r.top:.2f}")

            # --- HL/LH reversal --- (exempt from new-extreme cooldown — this IS the reversal)
            if self.cfg.enable_hl_lh and not in_congestion:
                sh, sl = self._confirmed_swings_at(i)
                if (self.broken_bear_tl_at is not None
                        and i - self.broken_bear_tl_at < self.cfg.tl_test_window
                        and self.cfg.allow_longs
                        and len(sl) >= 2 and sl[-1] > self.broken_bear_tl_at
                        and b.low[sl[-1]] > b.low[sl[-2]]):
                    if self._valid_signal_bar(bar, "LONG"):
                        self._emit_long(i, "HLLH_LONG", "SR", notes="HL after bear TL break")

                if (self.broken_bull_tl_at is not None
                        and i - self.broken_bull_tl_at < self.cfg.tl_test_window
                        and self.cfg.allow_shorts
                        and len(sh) >= 2 and sh[-1] > self.broken_bull_tl_at
                        and b.high[sh[-1]] < b.high[sh[-2]]):
                    if self._valid_signal_bar(bar, "SHORT"):
                        self._emit_short(i, "HLLH_SHORT", "SR", notes="LH after bull TL break")

        return self.signals
