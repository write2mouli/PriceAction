"""
channel_options.py
==================
Prototype DIFFERENT channel-construction methods against the real /ES 2m chart
and render them side by side, so we can pick the one that matches Mouli's
hand-drawn (dark-blue) channels before porting to Pine.

Mouli's steer (May 28):
  - The two/three big channels are SLOPE phases of one uptrend (spike -> channel),
    not trend reversals.
  - "Channels should develop regressively as time progresses" -> regression-based
    channels that evolve, not jumpy discrete gear-change segments.
  - For an uptrend the LOWER line is support; pivots should sit on it.

Environment: numpy + matplotlib + stdlib only (pandas blocked). Reuses the Yahoo
fetcher + ATR from channels_yahoo_test.py.

Usage:
  python channel_options.py [YYYY-MM-DD]    (default 2026-05-28)

Outputs:
  python/out/chanopt_<method>.png   one per method
  python/out/chanopt_grid_<date>.png   all methods in a grid
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from channels_yahoo_test import fetch_es_2m, atr  # reuse data + ATR


# ----------------------------------------------------------------------------
# Session filter (WIDE: include extended hours -- the morning spike is premarket)
# ----------------------------------------------------------------------------
def filter_session_wide(ts, o, h, l, c, target_date: dt.date,
                        start_hm=(4, 0), end_hm=(20, 0)):
    import calendar
    # late-May US = EDT (UTC-4). ET hour + 4 = UTC hour.
    s = dt.datetime.combine(target_date, dt.time(*start_hm)) + dt.timedelta(hours=4)
    e = dt.datetime.combine(target_date, dt.time(*end_hm)) + dt.timedelta(hours=4)
    s_ep = calendar.timegm(s.timetuple())
    e_ep = calendar.timegm(e.timetuple())
    m = (ts >= s_ep) & (ts <= e_ep)
    return ts[m], o[m], h[m], l[m], c[m]


# ----------------------------------------------------------------------------
# Graded fractal pivots (mirror Pine: strength 1=C, 3=B, 5=A)
# ----------------------------------------------------------------------------
@dataclass
class Piv:
    idx: int
    price: float
    kind: int     # +1 high, -1 low
    grade: int    # 1=C, 2=B, 3=A


def _is_fractal(arr, i, s, want_high):
    n = len(arr)
    if i - s < 0 or i + s >= n:
        return False
    v = arr[i]
    for k in range(1, s + 1):
        if want_high:
            if not (v > arr[i - k] and v >= arr[i + k]):
                return False
        else:
            if not (v < arr[i - k] and v <= arr[i + k]):
                return False
    return True


def detect_graded_pivots(h, l, s_c=1, s_b=3, s_a=5) -> List[Piv]:
    """Every strength-s_c fractal is a pivot; grade = widest strength it survives."""
    n = len(h)
    out: List[Piv] = []
    for i in range(n):
        for want_high in (True, False):
            arr = h if want_high else l
            if not _is_fractal(arr, i, s_c, want_high):
                continue
            grade = 1
            if _is_fractal(arr, i, s_b, want_high):
                grade = 2
            if _is_fractal(arr, i, s_a, want_high):
                grade = 3
            out.append(Piv(i, arr[i], +1 if want_high else -1, grade))
    out.sort(key=lambda p: (p.idx, -p.kind))
    return out


# ----------------------------------------------------------------------------
# Leg state machine (faithful port of Pine compute_leg_state, HL/LH chain)
# Processes a chronological list of pivots; returns a leg id per pivot + dirs.
# ----------------------------------------------------------------------------
@dataclass
class Leg:
    direction: int            # 1 up, 2 down
    start_piv: int            # index into the pivot list
    end_piv: int              # inclusive
    piv_idxs: List[int] = field(default_factory=list)


def run_leg_machine(pivs: List[Piv], atr_v, break_atr: float,
                    min_grade: int) -> List[Leg]:
    """Returns legs over the subset of pivots with grade >= min_grade.
    Slab is classified vs the previous same-kind pivot in that subset."""
    sub = [(k, p) for k, p in enumerate(pivs) if p.grade >= min_grade]
    legs: List[Leg] = []
    leg_dir = 0
    last_hl = last_lh = None
    cur_start = -1            # index within `sub`
    prev_h = prev_l = None    # previous same-kind price (for slab)

    def slab_of(p):
        nonlocal prev_h, prev_l
        if p.kind == 1:
            s = "?" if prev_h is None else ("HH" if p.price > prev_h else "LH")
            prev_h = p.price
            return s
        else:
            s = "?" if prev_l is None else ("LL" if p.price < prev_l else "HL")
            prev_l = p.price
            return s

    cur_piv_idxs: List[int] = []

    def close_leg(end_si):
        if cur_start >= 0 and leg_dir != 0:
            legs.append(Leg(leg_dir, cur_start, end_si, list(cur_piv_idxs)))

    for si, (orig_k, p) in enumerate(sub):
        thr = (atr_v[p.idx] * break_atr) if atr_v is not None else 0.0
        s = slab_of(p)
        transition = False
        if leg_dir == 0:
            if p.kind == -1 and s == "HL":
                leg_dir = 1; last_hl = p.price; cur_start = si; cur_piv_idxs = [si]
            elif p.kind == -1 and s == "LL":
                leg_dir = 2; cur_start = si; cur_piv_idxs = [si]
            elif p.kind == 1 and s == "HH":
                leg_dir = 1; cur_start = si; cur_piv_idxs = [si]
            elif p.kind == 1 and s == "LH":
                leg_dir = 2; last_lh = p.price; cur_start = si; cur_piv_idxs = [si]
            else:
                continue
        elif leg_dir == 1:
            if p.kind == -1:
                if s == "HL":
                    last_hl = p.price; cur_piv_idxs.append(si)
                else:  # LL
                    if last_hl is None or p.price >= last_hl - thr:
                        cur_piv_idxs.append(si)          # IN_RANGE
                    else:
                        transition = True                # hard break -> DOWN
            else:  # high
                cur_piv_idxs.append(si)                  # HH extend / LH in-range
            if transition:
                close_leg(si - 1)
                leg_dir = 2; last_hl = None; last_lh = None
                cur_start = si; cur_piv_idxs = [si]
        else:  # leg_dir == 2
            if p.kind == 1:
                if s == "LH":
                    last_lh = p.price; cur_piv_idxs.append(si)
                else:  # HH
                    if last_lh is None or p.price <= last_lh + thr:
                        cur_piv_idxs.append(si)
                    else:
                        transition = True                # hard break -> UP
            else:
                cur_piv_idxs.append(si)
            if transition:
                close_leg(si - 1)
                leg_dir = 1; last_hl = None; last_lh = None
                cur_start = si; cur_piv_idxs = [si]
    close_leg(len(sub) - 1)
    # remap sub-local pivot indices back to original pivot list indices
    for lg in legs:
        lg.piv_idxs = [sub[si][0] for si in lg.piv_idxs]
        lg.start_piv = sub[lg.start_piv][0]
        lg.end_piv = sub[lg.end_piv][0]
    return legs


# ----------------------------------------------------------------------------
# Channel construction methods. Each returns a list of ChannelSeg.
# A ChannelSeg is drawn as support/resist parallels: y = slope*x + b.
# ----------------------------------------------------------------------------
@dataclass
class ChannelSeg:
    x0: int
    x1: int
    slope: float
    sup_b: float      # support intercept (lower line for UP)
    res_b: float      # resist intercept  (upper line for UP)
    direction: int


def _regress(xs, ys):
    """Least-squares slope+intercept. Returns (slope, intercept)."""
    n = len(xs)
    if n < 2:
        return None, None
    sx = sum(xs); sy = sum(ys)
    sxx = sum(x * x for x in xs); sxy = sum(x * y for x, y in zip(xs, ys))
    den = n * sxx - sx * sx
    if den == 0:
        return None, None
    m = (n * sxy - sx * sy) / den
    b = (sy - m * sx) / n
    return m, b


def _envelope(slope, leg_pivs: List[Piv]):
    """Given a slope, return (min_resid, max_resid) over the pivots."""
    rs = [p.price - slope * p.idx for p in leg_pivs]
    return min(rs), max(rs)


def method_regression_per_leg(legs, pivs, h, l, min_grade) -> List[ChannelSeg]:
    """Slope = regression over CHAIN-side pivots (lows for UP); band = +/- max
    residual over all leg pivots. One channel per major leg."""
    segs = []
    for lg in legs:
        lp = [pivs[i] for i in lg.piv_idxs]
        chain_kind = -1 if lg.direction == 1 else 1
        cp = [p for p in lp if p.kind == chain_kind]
        if len(cp) < 2:
            continue
        m, _ = _regress([p.idx for p in cp], [p.price for p in cp])
        if m is None:
            continue
        rmin, rmax = _envelope(m, lp)
        x0 = min(p.idx for p in lp); x1 = max(p.idx for p in lp)
        segs.append(ChannelSeg(x0, x1, m, rmin, rmax, lg.direction))
    return segs


def method_regression_centered(legs, pivs, h, l, min_grade) -> List[ChannelSeg]:
    """Slope = regression over ALL leg pivots (both kinds); band = +/- max
    residual. Center rides the mean, like a TV regression channel."""
    segs = []
    for lg in legs:
        lp = [pivs[i] for i in lg.piv_idxs]
        if len(lp) < 3:
            continue
        m, _ = _regress([p.idx for p in lp], [p.price for p in lp])
        if m is None:
            continue
        rmin, rmax = _envelope(m, lp)
        x0 = min(p.idx for p in lp); x1 = max(p.idx for p in lp)
        segs.append(ChannelSeg(x0, x1, m, rmin, rmax, lg.direction))
    return segs


def method_rolling_regression(legs, pivs, h, l, min_grade,
                              win_bars=45, step=6) -> List[ChannelSeg]:
    """Continuous: regression of CLOSE-ish (use mid of H/L) over a rolling
    window of `win_bars`, redrawn every `step` bars. Ignores legs entirely --
    a 'develops as time progresses' regression channel. Band = +/- max dev of
    high/low from the regression line in the window."""
    n = len(h)
    mid = (h + l) / 2.0
    segs = []
    for end in range(win_bars, n, step):
        a = end - win_bars
        xs = list(range(a, end))
        m, b = _regress(xs, list(mid[a:end]))
        if m is None:
            continue
        # band from highs above / lows below the line
        res_hi = max(h[i] - (m * i + b) for i in xs)
        res_lo = min(l[i] - (m * i + b) for i in xs)
        # direction by slope sign
        d = 1 if m >= 0 else 2
        segs.append(ChannelSeg(a, end - 1, m, b + res_lo, b + res_hi, d))
    return segs


def method_hull_support(legs, pivs, h, l, min_grade) -> List[ChannelSeg]:
    """Lower hull support (UP) / upper hull (DOWN): anchor at first chain pivot,
    slope = min slope to a later chain pivot (UP). Band envelopes all pivots so
    the support TOUCHES 2+ lows."""
    segs = []
    for lg in legs:
        lp = [pivs[i] for i in lg.piv_idxs]
        chain_kind = -1 if lg.direction == 1 else 1
        cp = [p for p in lp if p.kind == chain_kind]
        if len(cp) < 2:
            continue
        a = cp[0]
        slope = None
        for p in cp[1:]:
            if p.idx == a.idx:
                continue
            s = (p.price - a.price) / (p.idx - a.idx)
            if slope is None:
                slope = s
            elif lg.direction == 1:
                slope = min(slope, s)
            else:
                slope = max(slope, s)
        if slope is None:
            continue
        rmin, rmax = _envelope(slope, lp)
        x0 = min(p.idx for p in lp); x1 = max(p.idx for p in lp)
        segs.append(ChannelSeg(x0, x1, slope, rmin, rmax, lg.direction))
    return segs


def _emit_reg_seg(segs, seg_chain: List[Piv], leg_pivs: List[Piv], direction):
    if len(seg_chain) < 2:
        return
    m, _ = _regress([p.idx for p in seg_chain], [p.price for p in seg_chain])
    if m is None:
        return
    x0 = seg_chain[0].idx
    x1 = seg_chain[-1].idx
    inside = [p for p in leg_pivs if x0 <= p.idx <= x1]
    if not inside:
        inside = seg_chain
    rmin, rmax = _envelope(m, inside)
    segs.append(ChannelSeg(x0, x1, m, rmin, rmax, direction))


def _band_robust(slope, pivs: List[Piv], direction, top_pct):
    """Asymmetric band: the TREND side (support for UP / resistance for DOWN)
    touches the extreme pivot exactly, but the OPPOSITE side is a trimmed
    percentile so a lone overshoot pokes OUT instead of widening the channel
    ('center line can be top line', 'overshoot anomaly')."""
    rs = np.array([p.price - slope * p.idx for p in pivs])
    if direction == 1:                       # UP
        return rs.min(), float(np.percentile(rs, top_pct))
    return float(np.percentile(rs, 100 - top_pct)), rs.max()  # DOWN


def _emit_robust(segs, seg_chain, leg_pivs, direction, top_pct, c, n,
                 atr_v=None, margin_atr=0.25):
    """Robust band + extend the channel to the right until a bar CLOSES beyond
    the trend-side line by a CLEAN-break margin (UP: close < support - margin /
    DOWN: close > resistance + margin). The margin stops normal bounces from
    snapping the channel (esp. downtrends). If price never breaks, extend to the
    session end."""
    if len(seg_chain) < 2:
        return
    m, _ = _regress([p.idx for p in seg_chain], [p.price for p in seg_chain])
    if m is None:
        return
    x0 = seg_chain[0].idx
    x1 = seg_chain[-1].idx
    inside = [p for p in leg_pivs if x0 <= p.idx <= x1] or seg_chain
    sup_b, res_b = _band_robust(m, inside, direction, top_pct)
    x1e = n - 1
    for b in range(x1 + 1, n):
        mgn = (atr_v[b] * margin_atr) if atr_v is not None else 0.0
        if direction == 1 and c[b] < m * b + sup_b - mgn:
            x1e = b; break
        if direction == 2 and c[b] > m * b + res_b + mgn:
            x1e = b; break
    segs.append(ChannelSeg(x0, x1e, m, sup_b, res_b, direction))


def method_piecewise_robust(legs, pivs, h, l, c, min_grade, atr_v,
                            tol_atr=1.2, top_pct=85, margin_atr=0.25) -> List[ChannelSeg]:
    """method F + robust trimmed band + extend-until-support-break."""
    n = len(h)
    segs = []
    for lg in legs:
        chain_kind = -1 if lg.direction == 1 else 1
        lp = [pivs[i] for i in lg.piv_idxs]
        cp = [p for p in lp if p.kind == chain_kind]
        if len(cp) < 2:
            continue
        seg_start = 0
        i = 2
        while i <= len(cp):
            xs = [p.idx for p in cp[seg_start:i]]
            ys = [p.price for p in cp[seg_start:i]]
            m, b = _regress(xs, ys)
            tol = atr_v[cp[i - 1].idx] * tol_atr if atr_v is not None else 1e9
            maxres = 0.0 if m is None else max(abs(y - (m * x + b))
                                               for x, y in zip(xs, ys))
            if m is not None and maxres > tol and (i - 1) - seg_start >= 2:
                _emit_robust(segs, cp[seg_start:i - 1], lp, lg.direction,
                             top_pct, c, n, atr_v, margin_atr)
                seg_start = i - 2
                i = seg_start + 2
            else:
                i += 1
        _emit_robust(segs, cp[seg_start:], lp, lg.direction, top_pct, c, n,
                     atr_v, margin_atr)
    # Clean handoff: a channel stops where the next one starts (or earlier, at
    # its own support break). Prevents many channels all running to session end.
    segs.sort(key=lambda s: s.x0)
    for i in range(len(segs) - 1):
        if segs[i + 1].x0 > segs[i].x0:
            segs[i].x1 = min(segs[i].x1, segs[i + 1].x0)
    return segs


def method_piecewise_regression(legs, pivs, h, l, min_grade,
                                atr_v, tol_atr=1.2) -> List[ChannelSeg]:
    """Develops regressively: within a leg, greedily extend a regression line
    over the chain-side pivots while it still FITS (max residual <= tol_atr*ATR).
    When a new pivot would break the fit (a real slope change, e.g. spike ->
    channel), close the segment and start a new one at the prior pivot. Yields a
    FEW regression channels per leg -- the middle ground between one-per-leg
    (too broad) and the gear-change (too many)."""
    segs = []
    for lg in legs:
        chain_kind = -1 if lg.direction == 1 else 1
        lp = [pivs[i] for i in lg.piv_idxs]
        cp = [p for p in lp if p.kind == chain_kind]
        if len(cp) < 2:
            continue
        seg_start = 0
        i = 2
        while i <= len(cp):
            xs = [p.idx for p in cp[seg_start:i]]
            ys = [p.price for p in cp[seg_start:i]]
            m, b = _regress(xs, ys)
            tol = atr_v[cp[i - 1].idx] * tol_atr if atr_v is not None else 1e9
            maxres = 0.0 if m is None else max(abs(y - (m * x + b))
                                               for x, y in zip(xs, ys))
            if m is not None and maxres > tol and (i - 1) - seg_start >= 2:
                _emit_reg_seg(segs, cp[seg_start:i - 1], lp, lg.direction)
                seg_start = i - 2          # overlap one pivot for continuity
                i = seg_start + 2
            else:
                i += 1
        _emit_reg_seg(segs, cp[seg_start:], lp, lg.direction)
    return segs


def method_spike_and_channel(legs, pivs, h, l, min_grade,
                             atr_v=None, gear_atr=0.75) -> List[ChannelSeg]:
    """Current Pine approach: lock slope from first 2 chain pivots, finalize +
    restart when a chain pivot deviates > gear_atr*ATR from the locked support.
    Shown to confirm the 'too many segments' behavior."""
    segs = []
    for lg in legs:
        chain_kind = -1 if lg.direction == 1 else 1
        cps = [pivs[i] for i in lg.piv_idxs if pivs[i].kind == chain_kind]
        allp = [pivs[i] for i in lg.piv_idxs]
        if len(cps) < 2:
            continue
        seg_start = 0          # index into cps
        locked = None
        i = 1
        while i < len(cps):
            a = cps[seg_start]
            if locked is None:
                locked = (cps[seg_start + 1].price - a.price) / \
                         (cps[seg_start + 1].idx - a.idx)
            test = cps[i]
            tol = (atr_v[test.idx] * gear_atr) if atr_v is not None else 0.0
            dev = test.price - (a.price + locked * (test.idx - a.idx))
            if abs(dev) > tol and i - 1 > seg_start:
                # finalize current segment [seg_start .. i-1]
                seg_pivs = cps[seg_start:i]
                x0 = seg_pivs[0].idx; x1 = seg_pivs[-1].idx
                rmin, rmax = _envelope(locked, [p for p in allp
                                                if x0 <= p.idx <= x1])
                segs.append(ChannelSeg(x0, x1, locked, rmin, rmax, lg.direction))
                seg_start = i - 1
                locked = None
            i += 1
        # tail segment
        a = cps[seg_start]
        if locked is None and len(cps) - seg_start >= 2:
            locked = (cps[seg_start + 1].price - a.price) / \
                     (cps[seg_start + 1].idx - a.idx)
        if locked is not None:
            seg_pivs = cps[seg_start:]
            x0 = seg_pivs[0].idx; x1 = seg_pivs[-1].idx
            rmin, rmax = _envelope(locked, [p for p in allp if x0 <= p.idx <= x1])
            segs.append(ChannelSeg(x0, x1, locked, rmin, rmax, lg.direction))
    return segs


# ----------------------------------------------------------------------------
# Render
# ----------------------------------------------------------------------------
def _draw_candles(ax, o, h, l, c):
    n = len(o)
    w = 0.6
    for i in range(n):
        col = "#26a69a" if c[i] >= o[i] else "#ef5350"
        ax.plot([i, i], [l[i], h[i]], color="#888", linewidth=0.5, zorder=1)
        lo = min(o[i], c[i]); hi = max(o[i], c[i])
        ax.add_patch(Rectangle((i - w / 2, lo), w, max(hi - lo, 0.1),
                               color=col, zorder=2))


def _draw_segs(ax, segs: List[ChannelSeg]):
    for s in segs:
        col = "#0aa" if s.direction == 1 else "#e69500"
        for b, lw in ((s.sup_b, 1.6), (s.res_b, 1.6)):
            y0 = s.slope * s.x0 + b
            y1 = s.slope * s.x1 + b
            ax.plot([s.x0, s.x1], [y0, y1], color=col, linewidth=lw, zorder=4)
        cb = (s.sup_b + s.res_b) / 2.0
        ax.plot([s.x0, s.x1], [s.slope * s.x0 + cb, s.slope * s.x1 + cb],
                color=col, linewidth=0.7, linestyle=":", zorder=4)


def render_grid(ts, o, h, l, c, pivs, results, out_path):
    n = len(ts)
    rows = (len(results) + 1) // 2
    fig, axes = plt.subplots(rows, 2, figsize=(24, 7 * rows), dpi=100)
    axes = np.array(axes).flatten()
    for ai, (label, segs) in enumerate(results):
        ax = axes[ai]
        _draw_candles(ax, o, h, l, c)
        for p in pivs:
            if p.grade >= 2:
                ax.scatter(p.idx, p.price, s=18 if p.grade == 3 else 9,
                           color="black" if p.grade == 3 else "#777",
                           marker="v" if p.kind == 1 else "^", zorder=3)
        _draw_segs(ax, segs)
        ax.set_title(f"{label}  ({len(segs)} channels)", fontsize=11)
        ax.set_xlim(-1, n)
        ax.grid(True, alpha=0.2, linestyle=":", linewidth=0.4)
    for ai in range(len(results), len(axes)):
        axes[ai].axis("off")
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close(fig)


def render_single(ts, o, h, l, c, pivs, label, segs, out_path):
    n = len(ts)
    fig, ax = plt.subplots(figsize=(20, 10), dpi=110)
    _draw_candles(ax, o, h, l, c)
    for p in pivs:
        if p.grade >= 2:
            ax.scatter(p.idx, p.price, s=24 if p.grade == 3 else 10,
                       color="black" if p.grade == 3 else "#777",
                       marker="v" if p.kind == 1 else "^", zorder=3)
    _draw_segs(ax, segs)
    # ET time ticks every 30 min
    et_ticks, et_labels = [], []
    for i in range(n):
        et = dt.datetime.fromtimestamp(ts[i] - 4 * 3600, tz=dt.timezone.utc).replace(tzinfo=None)
        if et.minute % 30 == 0:
            et_ticks.append(i); et_labels.append(et.strftime("%H:%M"))
    ax.set_xticks(et_ticks); ax.set_xticklabels(et_labels, fontsize=8)
    ax.set_title(f"{label}  ({len(segs)} channels)", fontsize=12)
    ax.set_xlim(-1, n)
    ax.grid(True, alpha=0.2, linestyle=":", linewidth=0.4)
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close(fig)


def main():
    target = dt.date(2026, 5, 28)
    if len(sys.argv) > 1:
        target = dt.date.fromisoformat(sys.argv[1])
    out_dir = os.path.join(os.path.dirname(__file__), "out")
    os.makedirs(out_dir, exist_ok=True)

    print("Fetching /ES 2m from Yahoo ...")
    ts, o, h, l, c = fetch_es_2m()
    print(f"  total bars: {len(ts)}")
    ts, o, h, l, c = filter_session_wide(ts, o, h, l, c, target)
    print(f"  session bars for {target} (04:00-20:00 ET): {len(ts)}")
    if len(ts) < 40:
        print("Not enough data."); return

    atr_v = atr(h, l, c, 14)
    pivs = detect_graded_pivots(h, l, 1, 3, 5)
    print(f"  pivots: {len(pivs)}  (A={sum(p.grade==3 for p in pivs)}, "
          f"B={sum(p.grade==2 for p in pivs)}, C={sum(p.grade==1 for p in pivs)})")

    # MAJOR legs (Grade A) with a generous break threshold so the day is a few
    # big legs, not many.
    legs = run_leg_machine(pivs, atr_v, break_atr=1.5, min_grade=3)
    print(f"  major legs (gradeA, break=1.5ATR): {len(legs)}")
    for i, lg in enumerate(legs):
        d = "UP" if lg.direction == 1 else "DOWN"
        print(f"    leg {i}: {d}  pivs[{lg.start_piv}..{lg.end_piv}]  "
              f"bars[{pivs[lg.start_piv].idx}..{pivs[lg.end_piv].idx}]")

    methods = [
        ("A_regression_per_leg (slope=reg over lows)",
         method_regression_per_leg(legs, pivs, h, l, 3)),
        ("B_regression_centered (slope=reg over all)",
         method_regression_centered(legs, pivs, h, l, 3)),
        ("C_rolling_regression (win45, no legs)",
         method_rolling_regression(legs, pivs, h, l, 3, win_bars=45, step=6)),
        ("D_hull_support (touches lows)",
         method_hull_support(legs, pivs, h, l, 3)),
        ("E_spike_and_channel (current Pine)",
         method_spike_and_channel(legs, pivs, h, l, 3, atr_v, 0.75)),
        ("F_piecewise_regression tol1.2 (spike->grind split)",
         method_piecewise_regression(legs, pivs, h, l, 3, atr_v, 1.2)),
        ("H_robust top85 + extend-until-support-break",
         method_piecewise_robust(legs, pivs, h, l, c, 3, atr_v, 1.2, 85)),
    ]

    for label, segs in methods:
        safe = label.split(" ")[0]
        render_single(ts, o, h, l, c, pivs, label, segs,
                      os.path.join(out_dir, f"chanopt_{safe}.png"))
        print(f"  {label:48s} -> {len(segs)} channels")

    render_grid(ts, o, h, l, c, pivs, methods,
                os.path.join(out_dir, f"chanopt_grid_{target.isoformat()}.png"))
    print("\nGrid:", os.path.join(out_dir, f"chanopt_grid_{target.isoformat()}.png"))


if __name__ == "__main__":
    main()
