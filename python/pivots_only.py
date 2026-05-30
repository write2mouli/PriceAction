"""
pivots_only.py
==============
Render a high-resolution clean candle chart of /ES 2m RTH for a given date.

- One "clean" image with NO markers - for Mouli to draw their own pivots on top.
- Several "annotated" images, each showing pivots from one strength setting,
  so we can compare and lock down the right sensitivity BEFORE doing channels.

Usage:  python pivots_only.py [YYYY-MM-DD]
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from typing import List

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from channels_yahoo_test import (
    fetch_es_2m, filter_session, atr,
    detect_pivots, detect_pivots_raw_alternating, Pivot
)


def draw_candles(ax, ts, o, h, l, c, ema=None, fontsize=8):
    n = len(ts)
    width = 0.65
    for i in range(n):
        color = "#26a69a" if c[i] >= o[i] else "#ef5350"
        ax.plot([i, i], [l[i], h[i]], color="#222", linewidth=0.7, zorder=1)
        lo, hi = min(o[i], c[i]), max(o[i], c[i])
        ax.add_patch(Rectangle((i - width / 2, lo), width,
                               max(hi - lo, 0.05), color=color, zorder=2))
    if ema is not None:
        ax.plot(range(n), ema, color="#1e88e5", linewidth=1.2, zorder=3, label="EMA21")
    # Time labels every 30 min in ET
    et_offset_sec = -4 * 3600
    ticks, labels = [], []
    for i in range(n):
        et = dt.datetime.fromtimestamp(ts[i] + et_offset_sec,
                                        tz=dt.timezone.utc).replace(tzinfo=None)
        if et.minute % 30 == 0:
            ticks.append(i)
            labels.append(et.strftime("%H:%M"))
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, fontsize=fontsize)
    ax.tick_params(axis="y", labelsize=fontsize)
    ax.grid(True, alpha=0.25, linestyle=":", linewidth=0.5)
    ax.set_xlim(-1, n)


def annotate_pivots(ax, pivots: List[Pivot], strength_label: str):
    for p in pivots:
        if p.kind == "H":
            # Red down arrow above the high
            ax.annotate("", xy=(p.idx, p.price),
                        xytext=(p.idx, p.price + 1.5),
                        arrowprops=dict(arrowstyle="->", color="red", lw=1.8))
            ax.text(p.idx, p.price + 2.2, str(p.idx),
                    ha="center", fontsize=6, color="darkred")
        else:
            # Green up arrow below the low
            ax.annotate("", xy=(p.idx, p.price),
                        xytext=(p.idx, p.price - 1.5),
                        arrowprops=dict(arrowstyle="->", color="green", lw=1.8))
            ax.text(p.idx, p.price - 2.2, str(p.idx),
                    ha="center", fontsize=6, color="darkgreen")


def main():
    target = dt.date(2026, 5, 29)
    if len(sys.argv) > 1:
        target = dt.date.fromisoformat(sys.argv[1])

    out_dir = os.path.join(os.path.dirname(__file__), "out")
    os.makedirs(out_dir, exist_ok=True)

    ts, o, h, l, c = fetch_es_2m()
    ts, o, h, l, c = filter_session(ts, o, h, l, c, target)
    print(f"RTH bars for {target}: {len(ts)}")
    n = len(ts)

    atr_v = atr(h, l, c, period=14)
    ema = np.zeros(n)
    k = 2 / (21 + 1)
    ema[0] = c[0]
    for i in range(1, n):
        ema[i] = c[i] * k + ema[i - 1] * (1 - k)

    # ------------------------------------------------------------------
    # 1) CLEAN high-res image - for user to draw pivots on top
    # ------------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(32, 14), dpi=150)
    draw_candles(ax, ts, o, h, l, c, ema=ema, fontsize=12)
    ax.set_title(
        f"/ES 2m  {target.isoformat()}  RTH  ({n} bars)   "
        f"-- CLEAN, no pivots/channels.  Draw your pivots and send back.",
        fontsize=14,
    )
    ax.legend(loc="upper left", fontsize=10)
    plt.tight_layout()
    clean_path = os.path.join(out_dir, f"pivots_CLEAN_{target.isoformat()}.png")
    plt.savefig(clean_path, dpi=150)
    plt.close(fig)
    print(f"CLEAN -> {clean_path}")

    # ------------------------------------------------------------------
    # 2) RAW alternating fractals at various strengths
    # ------------------------------------------------------------------
    # Mouli's logic: mark every visible swing. No prominence, no confirmation.
    # Just Williams fractal with strength N + alternation enforcement.
    for s in [1, 2, 3]:
        pivots = detect_pivots_raw_alternating(h, l, strength=s)
        fig, ax = plt.subplots(figsize=(32, 14), dpi=150)
        draw_candles(ax, ts, o, h, l, c, ema=ema, fontsize=12)
        annotate_pivots(ax, pivots, f"raw_s{s}")
        n_h = sum(1 for p in pivots if p.kind == "H")
        n_l = sum(1 for p in pivots if p.kind == "L")
        ax.set_title(
            f"/ES 2m  {target.isoformat()}  RTH  ({n} bars)   "
            f"RAW alternating fractals, strength={s} (no prominence, no confirmation)   "
            f"-> {n_h} highs, {n_l} lows. Total {len(pivots)}.",
            fontsize=14,
        )
        ax.legend(loc="upper left", fontsize=10)
        plt.tight_layout()
        path = os.path.join(out_dir, f"pivots_raw_s{s}_{target.isoformat()}.png")
        plt.savefig(path, dpi=150)
        plt.close(fig)
        print(f"raw_s{s:<8d}  {len(pivots):3d} pivots -> {path}")

    # ------------------------------------------------------------------
    # 3) ZigZag detector variants (legacy 3-stage)
    # ------------------------------------------------------------------
    PARAMS = [
        ("zz_s2", 2, 0.0, 0.2),
        ("zz_s3", 3, 0.0, 0.2),
        ("zz_s5", 5, 0.0, 0.2),
    ]
    for label, s, prom, conf in PARAMS:
        pivots = detect_pivots(h, l, atr_v,
                               strength=s,
                               prominence_x_atr=prom,
                               confirmation_x_atr=conf)
        fig, ax = plt.subplots(figsize=(32, 14), dpi=150)
        draw_candles(ax, ts, o, h, l, c, ema=ema, fontsize=12)
        annotate_pivots(ax, pivots, label)
        n_h = sum(1 for p in pivots if p.kind == "H")
        n_l = sum(1 for p in pivots if p.kind == "L")
        ax.set_title(
            f"/ES 2m  {target.isoformat()}  RTH  ({n} bars)   "
            f"ZigZag: strength={s}, prom={prom}xATR, conf={conf}xATR   "
            f"-> {n_h} highs, {n_l} lows. Total {len(pivots)}.",
            fontsize=14,
        )
        ax.legend(loc="upper left", fontsize=10)
        plt.tight_layout()
        path = os.path.join(out_dir, f"pivots_{label}_{target.isoformat()}.png")
        plt.savefig(path, dpi=150)
        plt.close(fig)
        print(f"{label:12s}  {len(pivots):3d} pivots -> {path}")


if __name__ == "__main__":
    main()
