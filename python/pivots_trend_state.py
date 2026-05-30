"""
pivots_trend_state.py
=====================
Render /ES 2m RTH with Brooks-style trend-state pivots matching Mouli's
hand-drawn convention:

- During DOWN trend: only HIGHs shown as RED down-arrows (LH/LL)
- During UP trend: only LOWs shown as GREEN up-arrows (HL/HH)
- Trend-extreme pivots (top of UP, bottom of DOWN) shown as BLACK arrows

Usage: python pivots_trend_state.py [YYYY-MM-DD]
"""

from __future__ import annotations

import datetime as dt
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from channels_yahoo_test import (
    fetch_es_2m, filter_session, atr,
    detect_pivots_raw_alternating, detect_raw_fractals,
    label_trend_state, trend_state_at_pivot,
)


def draw_candles(ax, ts, o, h, l, c, ema=None):
    n = len(ts)
    width = 0.65
    for i in range(n):
        color = "#26a69a" if c[i] >= o[i] else "#ef5350"
        ax.plot([i, i], [l[i], h[i]], color="#222", linewidth=0.7, zorder=1)
        lo, hi = min(o[i], c[i]), max(o[i], c[i])
        ax.add_patch(Rectangle((i - width/2, lo), width, max(hi-lo, 0.05),
                               color=color, zorder=2))
    if ema is not None:
        ax.plot(range(n), ema, color="#1e88e5", linewidth=1.2, zorder=3, label="EMA21")
    et_off = -4 * 3600
    ticks, labels = [], []
    for i in range(n):
        et = dt.datetime.fromtimestamp(ts[i] + et_off, tz=dt.timezone.utc).replace(tzinfo=None)
        if et.minute % 30 == 0:
            ticks.append(i)
            labels.append(et.strftime("%H:%M"))
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, fontsize=11)
    ax.tick_params(axis="y", labelsize=11)
    ax.grid(True, alpha=0.25, linestyle=":", linewidth=0.5)
    ax.set_xlim(-1, n)


def main():
    target = dt.date(2026, 5, 29)
    if len(sys.argv) > 1:
        target = dt.date.fromisoformat(sys.argv[1])
    out_dir = os.path.join(os.path.dirname(__file__), "out")
    os.makedirs(out_dir, exist_ok=True)

    ts, o, h, l, c = fetch_es_2m()
    ts, o, h, l, c = filter_session(ts, o, h, l, c, target)
    n = len(ts)
    print(f"RTH bars for {target}: {n}")

    ema = np.zeros(n)
    k = 2 / (21 + 1)
    ema[0] = c[0]
    for i in range(1, n):
        ema[i] = c[i] * k + ema[i - 1] * (1 - k)

    # Two-tier detection:
    # - Alternated (kept-extreme) sequence drives TREND-STATE DETECTION
    # - Raw fractal sequence drives DISPLAY (every visible swing marked)
    alt_pivots = detect_pivots_raw_alternating(h, l, strength=2)
    atr_v = atr(h, l, c, 14)
    # min_break_atr = 0.5 means reversal requires the new extreme to break
    # the prior structural level by at least 0.5*ATR (~1.5 pts on /ES 2m).
    alt_labels = label_trend_state(alt_pivots, atr_v=atr_v, min_break_atr=0.5)
    # Use strength=1 for raw display (catches every minor swing peak/trough)
    # while strength=2 alternation drives the trend-state state machine
    # (more stable, fewer false reversals).
    raw_pivots = detect_raw_fractals(h, l, strength=1)
    print(f"  alternated pivots (for trend detection): {len(alt_pivots)}")
    print(f"  raw fractals (for display):              {len(raw_pivots)}")
    print(f"  BLACK: {sum(1 for x in alt_labels if x == 'BLACK')}")

    # Build a per-bar trend state map by walking the alternated labels
    # State changes only at BLACK pivots. Between BLACKs, state is constant.
    bar_state = ["UNK"] * n
    state = "UNK"
    next_alt_i = 0
    # Pre-build a list of (idx, state_after) transitions from alt sequence
    transitions = []  # list of (bar_idx, new_state)
    cur_state = "UNK"
    for p, lab in zip(alt_pivots, alt_labels):
        # Before this pivot, state is cur_state
        if lab == "BLACK":
            new_state = "DOWN" if p.kind == "H" else "UP"
            transitions.append((p.idx, new_state))
            cur_state = new_state
        elif cur_state == "UNK":
            if lab in ("HH", "HL"):
                cur_state = "UP"
                transitions.append((p.idx, "UP"))
            elif lab in ("LL", "LH"):
                cur_state = "DOWN"
                transitions.append((p.idx, "DOWN"))
    # Fill bar_state by walking transitions
    tr_i = 0
    cur = "UNK"
    for b in range(n):
        while tr_i < len(transitions) and transitions[tr_i][0] <= b:
            cur = transitions[tr_i][1]
            tr_i += 1
        bar_state[b] = cur

    # Build set of BLACK bar indices for fast lookup
    black_bars = {p.idx for p, lab in zip(alt_pivots, alt_labels) if lab == "BLACK"}

    # ---- Mouli-convention display: use RAW fractals + per-bar trend state ----
    fig, ax = plt.subplots(figsize=(32, 14), dpi=150)
    draw_candles(ax, ts, o, h, l, c, ema=ema)

    n_shown_black = n_shown_green = n_shown_red = 0
    for p in raw_pivots:
        st = bar_state[p.idx]
        is_black = p.idx in black_bars
        if is_black:
            if p.kind == "H":
                ax.annotate("", xy=(p.idx, p.price), xytext=(p.idx, p.price + 2.0),
                            arrowprops=dict(arrowstyle="->", color="black", lw=2.5))
            else:
                ax.annotate("", xy=(p.idx, p.price), xytext=(p.idx, p.price - 2.0),
                            arrowprops=dict(arrowstyle="->", color="black", lw=2.5))
            n_shown_black += 1
        elif st == "UP" and p.kind == "L":
            ax.annotate("", xy=(p.idx, p.price), xytext=(p.idx, p.price - 1.5),
                        arrowprops=dict(arrowstyle="->", color="green", lw=1.6))
            n_shown_green += 1
        elif st == "DOWN" and p.kind == "H":
            ax.annotate("", xy=(p.idx, p.price), xytext=(p.idx, p.price + 1.5),
                        arrowprops=dict(arrowstyle="->", color="red", lw=1.6))
            n_shown_red += 1
        # else: hide (wrong-kind in current trend)

    total = n_shown_black + n_shown_green + n_shown_red
    ax.set_title(
        f"/ES 2m {target.isoformat()} RTH ({n} bars) | RAW s=2 + trend-state labelling | "
        f"BLACK={n_shown_black}  GREEN(HL/HH in UP)={n_shown_green}  "
        f"RED(LH/LL in DOWN)={n_shown_red}  TOTAL shown={total}",
        fontsize=13,
    )
    ax.legend(loc="upper left", fontsize=11)
    plt.tight_layout()
    out_path = os.path.join(out_dir, f"pivots_trend_state_{target.isoformat()}.png")
    plt.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"\nMouli-convention render -> {out_path}")

    # ---- Side-by-side: algo on top, clean on bottom for manual overlay ----
    fig, axes = plt.subplots(2, 1, figsize=(32, 26), dpi=150)
    # Top: algo output (re-draw)
    ax = axes[0]
    draw_candles(ax, ts, o, h, l, c, ema=ema)
    for p in raw_pivots:
        st = bar_state[p.idx]
        is_black = p.idx in black_bars
        if is_black:
            if p.kind == "H":
                ax.annotate("", xy=(p.idx, p.price), xytext=(p.idx, p.price + 2.0),
                            arrowprops=dict(arrowstyle="->", color="black", lw=2.5))
            else:
                ax.annotate("", xy=(p.idx, p.price), xytext=(p.idx, p.price - 2.0),
                            arrowprops=dict(arrowstyle="->", color="black", lw=2.5))
        elif st == "UP" and p.kind == "L":
            ax.annotate("", xy=(p.idx, p.price), xytext=(p.idx, p.price - 1.5),
                        arrowprops=dict(arrowstyle="->", color="green", lw=1.6))
        elif st == "DOWN" and p.kind == "H":
            ax.annotate("", xy=(p.idx, p.price), xytext=(p.idx, p.price + 1.5),
                        arrowprops=dict(arrowstyle="->", color="red", lw=1.6))
    ax.set_title(f"TOP: Algo output  ({n_shown_black} BLACK, {n_shown_green} GREEN, {n_shown_red} RED)",
                 fontsize=13)
    # Bottom: clean candles for manual overlay
    ax = axes[1]
    draw_candles(ax, ts, o, h, l, c, ema=ema)
    ax.set_title("BOTTOM: Clean -- compare to your hand-annotated chart", fontsize=13)
    plt.tight_layout()
    sbs_path = os.path.join(out_dir, f"pivots_sidebyside_{target.isoformat()}.png")
    plt.savefig(sbs_path, dpi=150)
    plt.close(fig)
    print(f"Side-by-side -> {sbs_path}")

    # ---- Debug: all ALTERNATED pivots with structural labels ----
    fig, ax = plt.subplots(figsize=(32, 14), dpi=150)
    draw_candles(ax, ts, o, h, l, c, ema=ema)
    for p, lab in zip(alt_pivots, alt_labels):
        if p.kind == "H":
            color = "black" if lab == "BLACK" else ("red" if lab == "LH" else "orange")
            ax.annotate("", xy=(p.idx, p.price), xytext=(p.idx, p.price + 1.5),
                        arrowprops=dict(arrowstyle="->", color=color, lw=1.6))
            ax.text(p.idx, p.price + 2.5, lab, ha="center", fontsize=7, color=color)
        else:
            color = "black" if lab == "BLACK" else ("green" if lab == "HL" else "blue")
            ax.annotate("", xy=(p.idx, p.price), xytext=(p.idx, p.price - 1.5),
                        arrowprops=dict(arrowstyle="->", color=color, lw=1.6))
            ax.text(p.idx, p.price - 2.5, lab, ha="center", fontsize=7, color=color)
    ax.set_title(
        f"/ES 2m {target.isoformat()} DEBUG -- all alternating pivots with structural labels  "
        f"(LH=red HHs=orange  HL=green LLs=blue  BLACK=trend switch)",
        fontsize=13,
    )
    plt.tight_layout()
    dbg_path = os.path.join(out_dir, f"pivots_trend_debug_{target.isoformat()}.png")
    plt.savefig(dbg_path, dpi=150)
    plt.close(fig)
    print(f"Debug labels -> {dbg_path}")


if __name__ == "__main__":
    main()
