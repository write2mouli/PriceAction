"""
channels_yahoo_test.py
======================
Pulls /ES 2-min RTH data from Yahoo and renders Andrews-Pitchfork channels
using the same 3-stage pivot detector as `pinescript/es_pa_strategy.pine`.

Goal: tune the channel params against Mouli's hand-drawn channels (visual eye
test) before pushing new defaults to the Pine script.

Environment: numpy + matplotlib + stdlib only (pandas blocked on this Windows
box by AppControl). Yahoo's chart API is hit directly via urllib.

Outputs PNGs to `python/out/channels_<label>.png`, one per parameter set.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

# ----------------------------------------------------------------------------
# Data fetch
# ----------------------------------------------------------------------------

YAHOO_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/ES=F"
    "?interval=2m&range=5d"
)


def fetch_es_2m() -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Returns (timestamps_utc, open, high, low, close) as numpy arrays."""
    req = urllib.request.Request(YAHOO_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    res = data["chart"]["result"][0]
    ts = np.array(res["timestamp"], dtype=np.int64)
    q = res["indicators"]["quote"][0]
    o = np.array(q["open"], dtype=float)
    h = np.array(q["high"], dtype=float)
    l = np.array(q["low"], dtype=float)
    c = np.array(q["close"], dtype=float)
    # Drop bars with any NaN
    ok = ~(np.isnan(o) | np.isnan(h) | np.isnan(l) | np.isnan(c))
    return ts[ok], o[ok], h[ok], l[ok], c[ok]


def filter_session(
    ts: np.ndarray, o, h, l, c, target_date: dt.date,
    start_hm=(9, 30), end_hm=(16, 0)
):
    """Filter to RTH (Eastern) for a single date. Yahoo timestamps are UTC.

    Uses calendar.timegm() to convert UTC-naive datetimes to epoch without
    relying on the system timezone (system may be PT, not ET).
    """
    import calendar
    # In late May the US is on EDT (UTC-4). 09:30 ET = 13:30 UTC.
    start_utc_dt = dt.datetime.combine(target_date, dt.time(*start_hm)) + dt.timedelta(hours=4)
    end_utc_dt   = dt.datetime.combine(target_date, dt.time(*end_hm))   + dt.timedelta(hours=4)
    start_epoch = calendar.timegm(start_utc_dt.timetuple())
    end_epoch   = calendar.timegm(end_utc_dt.timetuple())
    mask = (ts >= start_epoch) & (ts <= end_epoch)
    return ts[mask], o[mask], h[mask], l[mask], c[mask]


# ----------------------------------------------------------------------------
# ATR
# ----------------------------------------------------------------------------


def atr(h, l, c, period=14):
    n = len(h)
    tr = np.zeros(n)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    # Wilder/RMA-ish via simple moving for first period then exponential
    a = np.zeros(n)
    a[:period] = np.mean(tr[:period]) if n >= period else np.mean(tr)
    for i in range(period, n):
        a[i] = (a[i - 1] * (period - 1) + tr[i]) / period
    return a


# ----------------------------------------------------------------------------
# 3-stage pivot detector (mirrors Pine code)
# ----------------------------------------------------------------------------


@dataclass
class Pivot:
    idx: int  # bar index where the pivot bar sits
    price: float
    kind: str  # 'H' or 'L'


def detect_pivots(
    h: np.ndarray,
    l: np.ndarray,
    atr_v: np.ndarray,
    strength: int = 5,
    prominence_x_atr: float = 0.8,
    confirmation_x_atr: float = 1.0,
) -> List[Pivot]:
    """3-stage pivot detection.

    Stage 1: Williams fractal (strength bars left + right)
    Stage 2: prominence vs neighbor extremes >= prominence_x_atr * ATR
    Stage 3: ZigZag confirmation — opposite move >= confirmation_x_atr * ATR

    Returns confirmed pivots in chronological order.
    """
    n = len(h)
    pivots: List[Pivot] = []
    pending: Optional[Pivot] = None
    last_confirmed: Optional[Pivot] = None
    # Scan with strength bars padding on each side
    for i in range(strength, n - strength):
        # Stage 1: fractal
        is_ph = h[i] == np.max(h[i - strength : i + strength + 1])
        is_pl = l[i] == np.min(l[i - strength : i + strength + 1])
        if not (is_ph or is_pl):
            continue
        # Stage 2: prominence vs neighbors (exclude pivot bar itself)
        nbr_h_max = max(
            np.max(h[i - strength : i]),
            np.max(h[i + 1 : i + strength + 1]),
        )
        nbr_l_min = min(
            np.min(l[i - strength : i]),
            np.min(l[i + 1 : i + strength + 1]),
        )
        a = atr_v[i]
        ph_prominent = is_ph and (h[i] - nbr_h_max) >= prominence_x_atr * a
        pl_prominent = is_pl and (nbr_l_min - l[i]) >= prominence_x_atr * a
        if not (ph_prominent or pl_prominent):
            continue
        cand_kind = "H" if ph_prominent else "L"
        cand_price = h[i] if ph_prominent else l[i]
        cand = Pivot(idx=i, price=cand_price, kind=cand_kind)
        # If candidate is same kind as pending, keep the more extreme one
        if pending is not None and pending.kind == cand.kind:
            if (cand.kind == "H" and cand.price > pending.price) or (
                cand.kind == "L" and cand.price < pending.price
            ):
                pending = cand
        else:
            # If pending is opposite kind, we still wait for confirmation; the new
            # candidate just becomes a new pending only after pending is confirmed
            if pending is None:
                pending = cand
            else:
                # pending opposite kind to candidate — candidate becomes new pending
                # but only AFTER pending gets confirmed by enough opposite move
                pass

        # Stage 3: ZigZag confirmation of the pending pivot using subsequent bars
        # Confirm pending when price has moved confirmation_x_atr * ATR opposite
        if pending is not None and i > pending.idx:
            move = (
                pending.price - l[i] if pending.kind == "H" else h[i] - pending.price
            )
            if move >= confirmation_x_atr * atr_v[pending.idx]:
                # Don't confirm two same-kind pivots in a row
                if (
                    last_confirmed is None
                    or last_confirmed.kind != pending.kind
                ):
                    pivots.append(pending)
                    last_confirmed = pending
                    pending = None
                else:
                    # Same kind as last; replace last_confirmed if more extreme
                    if (
                        pending.kind == "H" and pending.price > last_confirmed.price
                    ) or (
                        pending.kind == "L" and pending.price < last_confirmed.price
                    ):
                        pivots[-1] = pending
                        last_confirmed = pending
                    pending = None
    return pivots


# ----------------------------------------------------------------------------
# Andrews Pitchfork drawing
# ----------------------------------------------------------------------------


@dataclass
class Pitchfork:
    p0: Pivot
    p1: Pivot
    p2: Pivot
    slope: float  # price per bar
    median_y0: float  # at p0.idx
    extend_right_idx: int


def build_pitchforks(
    pivots: List[Pivot],
    n_bars: int,
    extend_bars: int = 60,
    retrace_min: float = 0.382,
    retrace_max: float = 1.0,
    max_count: int = 10,
    auto_truncate: bool = False,
) -> List[Pitchfork]:
    """Walk alternating pivot triples and build Andrews pitchforks.

    If auto_truncate is True, each pitchfork's right edge is clipped to the
    P2 of the NEXT pitchfork — so a channel's "active life" ends when the
    next channel begins (Brooks-style).
    """
    out: List[Pitchfork] = []
    if len(pivots) < 3:
        return out
    for k in range(2, len(pivots)):
        p0, p1, p2 = pivots[k - 2], pivots[k - 1], pivots[k]
        # Must alternate H/L/H or L/H/L
        if not (p0.kind != p1.kind and p1.kind != p2.kind):
            continue
        leg1 = abs(p1.price - p0.price)
        leg2 = abs(p2.price - p1.price)
        if leg1 <= 0:
            continue
        ratio = leg2 / leg1
        if ratio < retrace_min or ratio > retrace_max:
            continue
        # Median line goes from P0 through midpoint of P1-P2
        mid_idx = (p1.idx + p2.idx) / 2.0
        mid_p = (p1.price + p2.price) / 2.0
        if mid_idx == p0.idx:
            continue
        slope = (mid_p - p0.price) / (mid_idx - p0.idx)
        right_x = min(n_bars - 1, p2.idx + extend_bars)
        out.append(
            Pitchfork(
                p0=p0,
                p1=p1,
                p2=p2,
                slope=slope,
                median_y0=p0.price,
                extend_right_idx=right_x,
            )
        )
    if auto_truncate and len(out) > 1:
        # Each pitchfork's right edge clips at the next pitchfork's P2 — so a
        # channel ends when the next channel completes.
        for i in range(len(out) - 1):
            nxt_p2 = out[i + 1].p2.idx
            if nxt_p2 > out[i].p2.idx:
                out[i].extend_right_idx = min(out[i].extend_right_idx, nxt_p2)
    # Keep most recent max_count
    return out[-max_count:]


# ----------------------------------------------------------------------------
# Plot
# ----------------------------------------------------------------------------


def plot_session(
    ts, o, h, l, c, pivots: List[Pivot], pitchforks: List[Pitchfork],
    title: str, out_path: str
):
    n = len(ts)
    fig, ax = plt.subplots(figsize=(16, 9), dpi=110)
    # Candles on integer x-axis (bar index)
    width = 0.6
    for i in range(n):
        color = "#26a69a" if c[i] >= o[i] else "#ef5350"
        # wick
        ax.plot([i, i], [l[i], h[i]], color="#444", linewidth=0.8, zorder=1)
        # body
        lo = min(o[i], c[i])
        hi = max(o[i], c[i])
        ax.add_patch(
            Rectangle(
                (i - width / 2, lo),
                width,
                max(hi - lo, 0.1),
                color=color,
                zorder=2,
            )
        )
    # EMA21
    ema = np.zeros(n)
    k = 2 / (21 + 1)
    ema[0] = c[0]
    for i in range(1, n):
        ema[i] = c[i] * k + ema[i - 1] * (1 - k)
    ax.plot(range(n), ema, color="#1e88e5", linewidth=1.2, zorder=3, label="EMA21")

    # Pivot dots
    for p in pivots:
        ax.scatter(
            p.idx,
            p.price,
            s=40,
            color="red" if p.kind == "H" else "green",
            zorder=4,
            marker="v" if p.kind == "H" else "^",
        )

    # Pitchforks
    for pf in pitchforks:
        x_left = pf.p0.idx
        x_right = pf.extend_right_idx
        # Median (gray dashed)
        y_left_m = pf.median_y0
        y_right_m = pf.median_y0 + pf.slope * (x_right - x_left)
        ax.plot(
            [x_left, x_right],
            [y_left_m, y_right_m],
            color="gray",
            linestyle="--",
            linewidth=0.9,
            zorder=3,
        )
        # Upper tine through p1, parallel
        y_left_u = pf.p1.price + pf.slope * (x_left - pf.p1.idx)
        y_right_u = pf.p1.price + pf.slope * (x_right - pf.p1.idx)
        ax.plot(
            [x_left, x_right],
            [y_left_u, y_right_u],
            color="black",
            linewidth=0.9,
            zorder=3,
        )
        # Lower tine through p2, parallel
        y_left_l = pf.p2.price + pf.slope * (x_left - pf.p2.idx)
        y_right_l = pf.p2.price + pf.slope * (x_right - pf.p2.idx)
        ax.plot(
            [x_left, x_right],
            [y_left_l, y_right_l],
            color="black",
            linewidth=0.9,
            zorder=3,
        )

    # X tick labels = ET time every ~30 min
    et_offset_sec = -4 * 3600
    et_labels = []
    et_ticks = []
    for i in range(n):
        et = dt.datetime.fromtimestamp(ts[i] + et_offset_sec, tz=dt.timezone.utc).replace(tzinfo=None)
        if et.minute % 30 == 0:
            et_ticks.append(i)
            et_labels.append(et.strftime("%H:%M"))
    ax.set_xticks(et_ticks)
    ax.set_xticklabels(et_labels, rotation=0, fontsize=8)
    ax.set_title(title, fontsize=11)
    ax.set_xlim(-1, n)
    ax.grid(True, alpha=0.25, linestyle=":", linewidth=0.5)
    ax.legend(loc="upper left", fontsize=8)
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close(fig)


# ----------------------------------------------------------------------------
# Main: run multiple parameter sets
# ----------------------------------------------------------------------------


def plot_grid(ts, o, h, l, c, results, out_path):
    """Render N variants in a 2x3 grid for side-by-side comparison."""
    n = len(ts)
    rows = (len(results) + 2) // 3
    fig, axes = plt.subplots(rows, 3, figsize=(22, 6 * rows), dpi=110)
    axes = axes.flatten()
    for ax_i, (label, pivots, pfs) in enumerate(results):
        ax = axes[ax_i]
        width = 0.6
        for i in range(n):
            color = "#26a69a" if c[i] >= o[i] else "#ef5350"
            ax.plot([i, i], [l[i], h[i]], color="#444", linewidth=0.5, zorder=1)
            lo = min(o[i], c[i]); hi = max(o[i], c[i])
            ax.add_patch(Rectangle((i - width/2, lo), width, max(hi-lo, 0.1), color=color, zorder=2))
        for p in pivots:
            ax.scatter(p.idx, p.price, s=20,
                       color="red" if p.kind == "H" else "green",
                       zorder=4, marker="v" if p.kind == "H" else "^")
        for pf in pfs:
            x_left = pf.p0.idx; x_right = pf.extend_right_idx
            y_left_m = pf.median_y0
            y_right_m = pf.median_y0 + pf.slope * (x_right - x_left)
            ax.plot([x_left, x_right], [y_left_m, y_right_m], color="gray", linestyle="--", linewidth=0.7, zorder=3)
            y_left_u = pf.p1.price + pf.slope * (x_left - pf.p1.idx)
            y_right_u = pf.p1.price + pf.slope * (x_right - pf.p1.idx)
            ax.plot([x_left, x_right], [y_left_u, y_right_u], color="black", linewidth=0.7, zorder=3)
            y_left_l = pf.p2.price + pf.slope * (x_left - pf.p2.idx)
            y_right_l = pf.p2.price + pf.slope * (x_right - pf.p2.idx)
            ax.plot([x_left, x_right], [y_left_l, y_right_l], color="black", linewidth=0.7, zorder=3)
        ax.set_title(f"{label}  ({len(pivots)} piv, {len(pfs)} pf)", fontsize=10)
        ax.set_xlim(-1, n)
        ax.grid(True, alpha=0.2, linestyle=":", linewidth=0.4)
        ax.tick_params(labelsize=7)
    for ax_i in range(len(results), len(axes)):
        axes[ax_i].axis("off")
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close(fig)


PARAM_SETS = [
    # Fifth pass: very tight extension; trailing edge of each pitchfork stops
    # at or shortly after the last bar in its triple. Matches Mouli's red
    # drawing better (pitchforks don't project forward forever).
    # label, strengths, prom_xATR, conf_xATR, retrace_min, retrace_max, extend, max_count_per_scale, auto_truncate
    ("Y_zero_ext",         (2, 3, 5), 0.05, 0.3, 0.0, 1.5,  0, 25, True),
    ("Z_ext5",             (2, 3, 5), 0.05, 0.3, 0.0, 1.5,  5, 25, True),
    ("AA_ext8",            (2, 3, 5), 0.05, 0.3, 0.0, 1.5,  8, 25, True),
    ("AB_ext5_tight",      (2, 3, 5), 0.05, 0.5, 0.1, 1.2,  5, 18, True),
    ("AC_dense_2_3_4_5",   (2, 3, 4, 5), 0.05, 0.3, 0.0, 1.5, 5, 15, True),
    ("AD_S_for_compare",   (2, 3, 5), 0.05, 0.3, 0.0, 1.5, 20, 18, True),
]


def main():
    target = dt.date(2026, 5, 29)
    if len(sys.argv) > 1:
        target = dt.date.fromisoformat(sys.argv[1])
    out_dir = os.path.join(os.path.dirname(__file__), "out")
    os.makedirs(out_dir, exist_ok=True)

    print(f"Fetching /ES 2m from Yahoo ...")
    ts, o, h, l, c = fetch_es_2m()
    print(f"  total bars: {len(ts)}")
    ts, o, h, l, c = filter_session(ts, o, h, l, c, target)
    print(f"  RTH bars for {target}: {len(ts)}")
    if len(ts) < 30:
        print("Not enough data for the requested session.")
        return

    atr_v = atr(h, l, c, period=14)
    avg_atr = float(np.mean(atr_v[-50:]))
    print(f"  avg ATR(14) last 50 bars: {avg_atr:.2f}")

    summary_lines = []
    grid_results: List[Tuple[str, List[Pivot], List[Pitchfork]]] = []
    for ps in PARAM_SETS:
        # accept 8-tuple (legacy) or 9-tuple (with auto_truncate)
        if len(ps) == 8:
            label, strengths, prom, conf, rmin, rmax, ext, maxc = ps
            auto_trunc = False
        else:
            label, strengths, prom, conf, rmin, rmax, ext, maxc, auto_trunc = ps
        if isinstance(strengths, int):
            strengths = (strengths,)
        all_pivots: List[Pivot] = []
        all_pfs: List[Pitchfork] = []
        for s in strengths:
            piv_s = detect_pivots(h, l, atr_v,
                                  strength=s,
                                  prominence_x_atr=prom,
                                  confirmation_x_atr=conf)
            # Build pitchforks WITHOUT per-strength truncation; we'll
            # cross-truncate across ALL scales after the loop.
            pfs_s = build_pitchforks(piv_s, n_bars=len(ts),
                                    extend_bars=ext,
                                    retrace_min=rmin,
                                    retrace_max=rmax,
                                    max_count=maxc,
                                    auto_truncate=False)
            all_pivots.extend(piv_s)
            all_pfs.extend(pfs_s)
        # Cross-strength truncation: each pitchfork's right edge clips at the
        # NEXT pitchfork's p2 (chronologically), regardless of which scale.
        if auto_trunc and len(all_pfs) > 1:
            all_pfs.sort(key=lambda pf: pf.p2.idx)
            for i in range(len(all_pfs) - 1):
                # Find next pitchfork whose p2 is strictly later
                for j in range(i + 1, len(all_pfs)):
                    if all_pfs[j].p2.idx > all_pfs[i].p2.idx:
                        all_pfs[i].extend_right_idx = min(
                            all_pfs[i].extend_right_idx,
                            all_pfs[j].p2.idx
                        )
                        break
        title = (f"strengths={strengths}  prom={prom}xATR  conf={conf}xATR  "
                 f"retrace=[{rmin},{rmax}]  ext={ext}  trunc={auto_trunc}  "
                 f"-> {len(all_pivots)} pivots, {len(all_pfs)} pitchforks  [{label}]")
        path = os.path.join(out_dir, f"channels_{label}.png")
        plot_session(ts, o, h, l, c, all_pivots, all_pfs, title, path)
        line = f"  {label:30s}  pivots={len(all_pivots):3d}  pitchforks={len(all_pfs):3d}  -> {path}"
        print(line)
        summary_lines.append(line)
        grid_results.append((label, all_pivots, all_pfs))

    # Comparison grid
    grid_path = os.path.join(out_dir, f"channels_grid_{target.isoformat()}.png")
    plot_grid(ts, o, h, l, c, grid_results, grid_path)
    print("\nGrid render:", grid_path)

    with open(os.path.join(out_dir, "channels_summary.txt"), "w") as f:
        f.write(f"Date: {target}\n")
        f.write(f"Bars: {len(ts)}\n")
        f.write(f"Avg ATR(14) last 50: {avg_atr:.2f}\n\n")
        for ln in summary_lines:
            f.write(ln + "\n")
    print("\nDone. PNGs in", out_dir)


if __name__ == "__main__":
    main()
