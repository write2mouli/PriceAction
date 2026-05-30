"""Execution simulator + metrics — pure numpy.

Takes the Signal stream from engine.py and walks bars forward to fill entry stops,
manage stops/targets/trails per §11 of the spec, producing a trade ledger
and performance stats per §12.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import math
import numpy as np

from config import Config, TICK_SIZE, POINT_VALUE
from engine import Signal
from data import Bars


@dataclass
class Trade:
    setup: str
    side: str
    kep: str
    signal_time: datetime
    entry_time: datetime
    entry_index: int
    entry_price: float
    initial_stop: float
    initial_target: float
    initial_risk_points: float

    exit_time: Optional[datetime] = None
    exit_index: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""

    pnl_points: float = 0.0
    pnl_dollars: float = 0.0
    r_multiple: float = 0.0
    bars_held: int = 0


def run_backtest(bars: Bars, signals: list[Signal], cfg: Config) -> list[Trade]:
    n = len(bars)
    open_trade: Optional[Trade] = None
    pending: dict[int, Signal] = {}
    trades: list[Trade] = []

    daily_loss_r = 0.0
    daily_trade_count = 0
    last_session_date = None
    cooldown_until_bar = -1

    by_bar: dict[int, list[Signal]] = {}
    for s in signals:
        by_bar.setdefault(s.bar_index, []).append(s)

    for i in range(n):
        bar_open = bars.open[i]; bar_high = bars.high[i]
        bar_low = bars.low[i]; bar_close = bars.close[i]
        bar_time = bars.times[i]
        in_session = bool(bars.in_session[i])
        session_date = bars.session_date[i]

        if session_date != last_session_date:
            daily_loss_r = 0.0
            daily_trade_count = 0
            last_session_date = session_date

        # ---- 1. Manage open position ----
        if open_trade is not None:
            t = open_trade
            t.bars_held = i - t.entry_index

            new_stop = _trail_stop(bars, i, t, cfg)
            if cfg.trail_mode != "NONE":
                if t.side == "LONG":
                    t.initial_stop = max(t.initial_stop, new_stop)
                else:
                    t.initial_stop = min(t.initial_stop, new_stop)

            if t.side == "LONG":
                stop_hit = bar_low <= t.initial_stop
                target_hit = bar_high >= t.initial_target
            else:
                stop_hit = bar_high >= t.initial_stop
                target_hit = bar_low <= t.initial_target

            if stop_hit and target_hit:
                if t.side == "LONG":
                    if bar_open >= t.initial_target:
                        target_first = True
                    elif bar_open <= t.initial_stop:
                        target_first = False
                    else:
                        target_first = False
                else:
                    if bar_open <= t.initial_target:
                        target_first = True
                    elif bar_open >= t.initial_stop:
                        target_first = False
                    else:
                        target_first = False
                price = t.initial_target if target_first else t.initial_stop
                reason = "target" if target_first else "stop"
                _close_trade(t, i, bar_time, price, reason, cfg)
                trades.append(t); open_trade = None
                if t.r_multiple < 0:
                    daily_loss_r += abs(t.r_multiple)
                    cooldown_until_bar = i + cfg.cooldown_bars_after_loss
            elif stop_hit:
                _close_trade(t, i, bar_time, t.initial_stop, "stop", cfg)
                trades.append(t); open_trade = None
                if t.r_multiple < 0:
                    daily_loss_r += abs(t.r_multiple)
                    cooldown_until_bar = i + cfg.cooldown_bars_after_loss
            elif target_hit:
                _close_trade(t, i, bar_time, t.initial_target, "target", cfg)
                trades.append(t); open_trade = None

        # ---- 2. Fill pending entries ----
        if open_trade is None:
            new_pending: dict[int, Signal] = {}
            for expire_bar, s in pending.items():
                if i > expire_bar: continue
                if not in_session:
                    new_pending[expire_bar] = s; continue
                if daily_trade_count >= cfg.max_daily_trades: continue
                if daily_loss_r >= cfg.max_daily_loss_r: continue
                if i < cooldown_until_bar:
                    new_pending[expire_bar] = s; continue

                filled = False
                slip = cfg.slippage_ticks * TICK_SIZE
                if s.side == "LONG":
                    if bar_high >= s.entry_trigger:
                        fill = max(bar_open, s.entry_trigger) + slip
                        filled = True
                else:
                    if bar_low <= s.entry_trigger:
                        fill = min(bar_open, s.entry_trigger) - slip
                        filled = True
                if filled:
                    risk_pts = abs(fill - s.planned_stop)
                    open_trade = Trade(
                        setup=s.setup, side=s.side, kep=s.kep,
                        signal_time=s.time, entry_time=bar_time, entry_index=i,
                        entry_price=fill, initial_stop=s.planned_stop,
                        initial_target=s.planned_target, initial_risk_points=risk_pts,
                    )
                    daily_trade_count += 1
                    break
                else:
                    new_pending[expire_bar] = s
            pending = new_pending

        # ---- 3. Queue new signals ----
        for s in by_bar.get(i, []):
            if open_trade is not None and cfg.max_concurrent <= 1: continue
            expire = i + cfg.entry_expiry_bars
            pending[expire] = s

    if open_trade is not None:
        last_i = n - 1
        _close_trade(open_trade, last_i, bars.times[last_i], float(bars.close[last_i]), "eod", cfg)
        trades.append(open_trade)

    return trades


def _close_trade(t: Trade, i: int, time: datetime, price: float, reason: str, cfg: Config):
    t.exit_index = i
    t.exit_time = time
    t.exit_price = price
    t.exit_reason = reason
    if t.side == "LONG":
        t.pnl_points = price - t.entry_price
    else:
        t.pnl_points = t.entry_price - price
    t.pnl_dollars = t.pnl_points * POINT_VALUE * cfg.contracts - 2 * cfg.commission_per_side * cfg.contracts
    t.r_multiple = t.pnl_points / t.initial_risk_points if t.initial_risk_points > 0 else 0.0
    t.bars_held = i - t.entry_index


def _trail_stop(bars: Bars, i: int, t: Trade, cfg: Config) -> float:
    if cfg.trail_mode == "NONE":
        return t.initial_stop
    if cfg.trail_mode == "EMA":
        emav = bars.ema[i]
        if math.isnan(emav): return t.initial_stop
        return float(emav) - TICK_SIZE if t.side == "LONG" else float(emav) + TICK_SIZE
    if cfg.trail_mode == "SWING":
        lo, hi = bars.low[t.entry_index:i + 1], bars.high[t.entry_index:i + 1]
        if t.side == "LONG":
            return float(lo.min()) - TICK_SIZE
        return float(hi.max()) + TICK_SIZE
    if cfg.trail_mode == "CHANDELIER":
        atrv = bars.atr[i]
        if math.isnan(atrv): return t.initial_stop
        off = cfg.trail_atr_mult * float(atrv)
        if t.side == "LONG":
            return float(bars.high[t.entry_index:i + 1].max()) - off
        return float(bars.low[t.entry_index:i + 1].min()) + off
    return t.initial_stop


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {"trades": 0}
    n = len(trades)
    wins = [t for t in trades if t.pnl_points > 0]
    losses = [t for t in trades if t.pnl_points <= 0]
    gross_win = sum(t.pnl_points for t in wins)
    gross_loss = -sum(t.pnl_points for t in losses)
    pnl_dollars = sum(t.pnl_dollars for t in trades)
    avg_w = (gross_win / len(wins)) if wins else 0.0
    avg_l = (gross_loss / len(losses)) if losses else 0.0
    win_rate = len(wins) / n
    expectancy_pts = sum(t.pnl_points for t in trades) / n
    expectancy_r = sum(t.r_multiple for t in trades) / n
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    equity = np.cumsum([t.pnl_dollars for t in trades])
    peak = np.maximum.accumulate(equity)
    dd = peak - equity
    max_dd = float(dd.max())

    by_setup: dict[str, dict] = {}
    for t in trades:
        d = by_setup.setdefault(t.setup, {"n": 0, "wins": 0, "pts": 0.0, "r": 0.0})
        d["n"] += 1
        d["wins"] += 1 if t.pnl_points > 0 else 0
        d["pts"] += t.pnl_points
        d["r"] += t.r_multiple

    return {
        "trades": n, "wins": len(wins), "losses": len(losses),
        "win_rate": win_rate, "avg_winner_pts": avg_w, "avg_loser_pts": avg_l,
        "expectancy_pts": expectancy_pts, "expectancy_r": expectancy_r,
        "profit_factor": pf,
        "gross_pnl_points": sum(t.pnl_points for t in trades),
        "gross_pnl_dollars": pnl_dollars,
        "max_drawdown_dollars": max_dd,
        "by_setup": by_setup,
    }


def format_summary(s: dict) -> str:
    if s.get("trades", 0) == 0:
        return "No trades."
    lines = [
        f"Total trades:        {s['trades']}",
        f"Wins / losses:       {s['wins']} / {s['losses']}  ({s['win_rate']*100:.1f}%)",
        f"Avg winner:          {s['avg_winner_pts']:.2f} pts",
        f"Avg loser:           {s['avg_loser_pts']:.2f} pts",
        f"Expectancy:          {s['expectancy_pts']:.2f} pts/trade  ({s['expectancy_r']:.2f} R)",
        f"Profit factor:       {s['profit_factor']:.2f}",
        f"Gross P&L:           {s['gross_pnl_points']:.2f} pts  / ${s['gross_pnl_dollars']:.2f}",
        f"Max drawdown:        ${s['max_drawdown_dollars']:.2f}",
        "",
        "By setup:",
    ]
    for setup, d in sorted(s["by_setup"].items()):
        wr = d["wins"] / d["n"] * 100 if d["n"] else 0
        lines.append(f"  {setup:>10}  n={d['n']:>4}  win%={wr:5.1f}  pts={d['pts']:+7.2f}  R={d['r']:+6.2f}")
    return "\n".join(lines)
