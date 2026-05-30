"""Chart output — pure matplotlib, no pandas.

Render candles + EMA + signal markers + trade lines for one session.
"""
from __future__ import annotations
from datetime import date
from typing import Optional
import numpy as np
import matplotlib.pyplot as plt

from data import Bars
from engine import Signal
from backtest import Trade


SETUP_STYLE = {
    "2EL":        ("^", "lime",       "below"),
    "2ES":        ("v", "red",        "above"),
    "F2EL":       ("^", "yellow",     "below"),
    "F2ES":       ("v", "orange",     "above"),
    "FB_LONG":    ("^", "cyan",       "below"),
    "FB_SHORT":   ("v", "magenta",    "above"),
    "HLLH_LONG":  ("^", "deepskyblue","below"),
    "HLLH_SHORT": ("v", "deeppink",   "above"),
}


def _day_indices(bars: Bars, day: str) -> list[int]:
    target = date.fromisoformat(day)
    return [i for i, d in enumerate(bars.session_date) if d == target]


def plot_day(bars: Bars, signals: list[Signal], trades: list[Trade],
             day: Optional[str] = None, out_path: Optional[str] = None):
    """Plot one session's bars."""
    if day is not None:
        idxs = _day_indices(bars, day)
        if not idxs:
            print(f"No bars for {day}")
            return
    else:
        idxs = list(range(len(bars)))

    idx_map = {orig: local for local, orig in enumerate(idxs)}

    fig, ax = plt.subplots(figsize=(14, 7))

    for orig, local in idx_map.items():
        o = bars.open[orig]; h = bars.high[orig]
        l = bars.low[orig]; c = bars.close[orig]
        color = "#26a69a" if c >= o else "#ef5350"
        ax.plot([local, local], [l, h], color=color, linewidth=0.8)
        body_h = max(abs(c - o), 0.05)
        ax.add_patch(plt.Rectangle((local - 0.3, min(o, c)), 0.6, body_h, color=color))

    # EMA line on selected slice
    if len(bars.ema):
        ema_y = [bars.ema[i] for i in idxs]
        ax.plot(range(len(idxs)), ema_y, color="#42a5f5", linewidth=1.2, label="EMA21")

    for s in signals:
        if s.bar_index not in idx_map: continue
        x = idx_map[s.bar_index]
        marker, color, side = SETUP_STYLE.get(s.setup, ("o", "white", "below"))
        y = s.signal_bar_low - 1.0 if side == "below" else s.signal_bar_high + 1.0
        ax.scatter([x], [y], marker=marker, color=color, s=80, edgecolors="black", linewidths=0.6, zorder=5)
        ax.annotate(s.setup, (x, y), textcoords="offset points",
                    xytext=(0, -12 if side == "below" else 8), ha="center", fontsize=7, color=color)

    for t in trades:
        if t.entry_index in idx_map:
            xe = idx_map[t.entry_index]
            ax.scatter([xe], [t.entry_price], marker="o", color="white",
                       edgecolors="black", s=40, zorder=6)
        if t.exit_index is not None and t.exit_index in idx_map:
            xx = idx_map[t.exit_index]
            c = "lime" if t.pnl_points > 0 else "red"
            ax.scatter([xx], [t.exit_price], marker="x", color=c, s=80, zorder=6)
            if t.entry_index in idx_map:
                ax.plot([idx_map[t.entry_index], xx], [t.entry_price, t.exit_price],
                        color=c, alpha=0.4, linewidth=1)

    ax.set_title(f"/ES — {day if day else 'all bars'}")
    ax.set_xlabel("Bar index"); ax.set_ylabel("Price")
    ax.grid(alpha=0.2); ax.legend(loc="upper left", fontsize=8)
    plt.tight_layout()
    if out_path:
        plt.savefig(out_path, dpi=120)
        print(f"Saved chart to {out_path}")
    else:
        plt.show()


def plot_equity(trades: list[Trade], out_path: Optional[str] = None):
    if not trades:
        print("No trades to plot."); return
    cum = []
    s = 0.0
    times = []
    for t in trades:
        s += t.pnl_dollars
        cum.append(s)
        times.append(t.exit_time)
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(times, cum, color="#26a69a")
    ax.set_title("Equity curve"); ax.set_ylabel("$ P&L")
    ax.grid(alpha=0.3); ax.axhline(0, color="white", linewidth=0.5)
    plt.tight_layout()
    if out_path:
        plt.savefig(out_path, dpi=120)
        print(f"Saved equity curve to {out_path}")
    else:
        plt.show()
