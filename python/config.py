"""Configuration for the price action backtest.

All parameter defaults match docs/strategy-spec.md §10. Override per-run by
instantiating Config(...) with kwargs.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal

# /ES contract specs
TICK_SIZE = 0.25
POINT_VALUE = 50.0
TICKS_PER_POINT = 4


StopMode = Literal["BEYOND_SIGNAL_BAR", "FIXED_POINTS", "FIXED_TICKS", "ATR", "BEYOND_SWING"]
TargetMode = Literal["FIXED_POINTS", "FIXED_TICKS", "R_MULTIPLE", "MEASURED_MOVE", "ATR", "OPPOSITE_KEP", "SCALE_OUT"]
TrailMode = Literal["NONE", "EMA", "SWING", "CHANDELIER"]
SessionFilter = Literal["RTH", "ETH", "CUSTOM", "ALL"]


@dataclass
class Config:
    # ---- Detection (§10.1) ----
    ema_length: int = 21
    atr_length: int = 14
    swing_strength: int = 3

    ema_proximity_ticks: int = 4
    min_close_strength: float = 0.60
    min_body_fraction: float = 0.40
    max_signal_range_atr: float = 2.5
    doji_fraction: float = 0.10
    strict_signal_bar: bool = True

    min_pullback_ticks: int = 8
    max_pullback_bars: int = 15
    entry_expiry_bars: int = 2

    tl_pierce_ticks: int = 6
    tl_break_ticks: int = 4
    tl_violation_ticks: int = 4
    tl_test_window: int = 20

    sr_proximity_ticks: int = 4
    sr_cluster_ticks: int = 4

    range_lookback: int = 30
    range_band_ticks: int = 6
    range_break_ticks: int = 6
    range_break_bars: int = 3

    fail_breakout_max_ticks: int = 20
    fail_breakout_window: int = 5
    f2e_reversal_bars: int = 2

    trend_bars_above_ema: int = 14
    extreme_fail_ticks: int = 4

    # ---- Regime-specific gates (§13.5, §7.5, §6.4) ----
    # Pullback depth filter (§13.5.3) — "deeper correction = higher probability"
    min_pullback_depth_frac: float = 0.30  # fraction of last impulse leg

    # New-extreme cooldown (§13.5.4) — "after new high, buying on hold"
    new_extreme_min_ticks: int = 4
    new_extreme_cooldown_bars: int = 5

    # Middle-of-range filter (§13.5.5) — "buy low, sell high"
    mid_range_low_frac: float = 0.40    # block shorts when below this band
    mid_range_high_frac: float = 0.60   # block longs when above this band

    # Congestion filter (§7.5) — block ALL entries while sideways/tight
    enable_congestion_filter: bool = True
    congestion_lookback: int = 10
    congestion_max_range_ticks: int = 16
    congestion_max_drift_ticks: int = 8

    # Short-term counter-trend (PB) trendlines (§6.4)
    enable_pb_tl: bool = True
    pb_tl_min_pivots: int = 2

    # Failed Breakout — Pullback Confirmation variant (§9.3.1)
    fb_require_pullback_confirmation: bool = True
    fb_pullback_window_bars: int = 8
    fb_lh_hl_min_ticks: int = 4

    # Prior-trend bias inside a TR (§13.5.7)
    prior_trend_memory_bars: int = 60
    prior_trend_min_bars: int = 20
    tr_disable_against_prior_trend: bool = False

    # ---- Position management (§10.2) ----
    contracts: int = 1
    allow_longs: bool = True
    allow_shorts: bool = True
    max_concurrent: int = 1
    session_filter: SessionFilter = "RTH"
    session_start: str = "09:30"
    session_end: str = "16:00"
    session_tz: str = "America/New_York"

    # ---- Stop placement (§10.3) ----
    stop_mode: StopMode = "BEYOND_SIGNAL_BAR"
    stop_points: float = 4.0
    stop_ticks: int = 16
    stop_atr_mult: float = 1.5

    # ---- Target / exit (§10.4) ----
    target_mode: TargetMode = "R_MULTIPLE"
    target_points: float = 2.0
    target_ticks: int = 8
    target_r: float = 2.0
    target_atr_mult: float = 3.0
    t1_r: float = 1.0  # for SCALE_OUT

    trail_mode: TrailMode = "NONE"
    trail_atr_mult: float = 2.0

    # ---- Risk filters (§10.5) ----
    max_daily_loss_r: float = 3.0
    max_daily_trades: int = 6
    cooldown_bars_after_loss: int = 5
    min_rr_to_enter: float = 1.0

    # ---- Execution model (§11) ----
    slippage_ticks: int = 1
    commission_per_side: float = 1.25

    # ---- Setup toggles ----
    enable_2el_2es: bool = True
    enable_f2el_f2es: bool = True
    enable_failed_breakout: bool = True
    enable_hl_lh: bool = True

    # ---- Derived ----
    def ticks_to_points(self, ticks: int) -> float:
        return ticks * TICK_SIZE

    def points_to_ticks(self, points: float) -> int:
        return int(round(points / TICK_SIZE))
