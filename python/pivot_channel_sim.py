"""
pivot_channel_sim.py
====================
Faithful port of the es_pa_pivots_simple.pine leg-state + adaptive-channel
logic, run against real /ES 2m RTH data. Purpose: diagnose why no channels
appear by reproducing the exact n_chain / epoch behaviour the Pine status
table reports (n_channel_draws, last_n_chain).

We replicate:
  - multi-strength Williams fractal detection (strength 1/3/5) with Pine's
    confirmation lag (a pivot at bar B confirms at bar B+strength)
  - per-bar processing order: Grade C high, Grade C low, Grade B up/low,
    Grade A up/low
  - slab classification using leg-aware reference
  - compute_leg_state machine (INIT/UP/DOWN, EXTEND/IN_RANGE/TRANSITION)
  - epoch reset on TRANSITION / leg start
  - update_current_channel n_chain counting + draw decision

Outputs a per-bar trace and a summary so we can see exactly where channels
do or don't get drawn.
"""
from __future__ import annotations

import datetime as dt
import sys

import numpy as np

from channels_yahoo_test import fetch_es_2m, filter_session, atr


def pine_pivots(series_h, series_l, strength):
    """Return dict bar_index -> price for confirmed pivot highs and lows at
    a given strength, using Pine ta.pivothigh/pivotlow semantics:
    a bar i is a pivot high if high[i] > high[i-k] and high[i] > high[i+k]
    for k=1..strength (strict both sides). Confirmed at bar i+strength.
    """
    n = len(series_h)
    ph = {}
    pl = {}
    for i in range(strength, n - strength):
        is_h = True
        is_l = True
        for k in range(1, strength + 1):
            if not (series_h[i] > series_h[i - k] and series_h[i] > series_h[i + k]):
                is_h = False
            if not (series_l[i] < series_l[i - k] and series_l[i] < series_l[i + k]):
                is_l = False
        if is_h:
            ph[i] = series_h[i]
        if is_l:
            pl[i] = series_l[i]
    return ph, pl


def main():
    target = dt.date(2026, 5, 29)
    if len(sys.argv) > 1:
        target = dt.date.fromisoformat(sys.argv[1])

    ts, o, h, l, c = fetch_es_2m()
    ts, o, h, l, c = filter_session(ts, o, h, l, c, target)
    n = len(ts)
    print(f"RTH bars for {target}: {n}")
    atr_v = atr(h, l, c, 14)

    sc, sb, sa = 1, 3, 5
    leg_break_atr = 0.75      # raised from 0.5 so minor wiggles don't spawn micro-legs
    if len(sys.argv) > 2:
        leg_break_atr = float(sys.argv[2])
    channel_min_pivots = 2
    small_leg_bars = 15       # a leg spanning <= this many bars is "small"
    broad_min_run = 3         # >= this many consecutive small legs -> one broad channel

    ph_c, pl_c = pine_pivots(h, l, sc)
    ph_b, pl_b = pine_pivots(h, l, sb)
    ph_a, pl_a = pine_pivots(h, l, sa)

    # parallel arrays
    piv_bars, piv_prices, piv_kinds, piv_grades, piv_slabs, piv_leg = [], [], [], [], [], []

    last_h_price = None
    last_l_price = None
    leg_dir = 0
    last_hl_price = None
    last_lh_price = None
    leg_extreme_h_idx = -1
    leg_extreme_l_idx = -1
    leg_extreme_h_price = None
    leg_extreme_l_price = None
    leg_start_pivot_idx = -1
    channel_epoch_start_idx = -1

    n_channel_draws = 0
    last_n_chain = 0
    n_legs = 0
    # Track epochs: each epoch is (start_idx, direction). On transition we
    # "finalize" the epoch's channel if it reached >=2 chain pivots.
    epochs = []  # list of dicts: {start, dir, max_n_chain}
    finalized_channels = 0

    trace = []

    def compute_leg_state(price, kind, slab, pivot_idx, thr):
        nonlocal_state = {}
        out_state = "INIT"
        new_dir = leg_dir
        new_last_hl = last_hl_price
        new_last_lh = last_lh_price
        new_eh_idx = leg_extreme_h_idx
        new_el_idx = leg_extreme_l_idx
        new_eh_price = leg_extreme_h_price
        new_el_price = leg_extreme_l_price
        legs_inc = 0
        retro = -1
        if leg_dir == 0:
            if kind == -1 and slab == "HL":
                new_dir = 1; new_last_hl = price; new_eh_price = -1e10
                new_el_price = price; new_el_idx = pivot_idx
                out_state = "EXTEND_UP"; legs_inc = 1
            elif kind == -1 and slab == "LL":
                new_dir = 2; new_el_price = price; new_el_idx = pivot_idx
                out_state = "EXTEND_DN"; legs_inc = 1
            elif kind == 1 and slab == "HH":
                new_dir = 1; new_eh_price = price; new_eh_idx = pivot_idx
                out_state = "EXTEND_UP"; legs_inc = 1
            elif kind == 1 and slab == "LH":
                new_dir = 2; new_last_lh = price; new_eh_price = price
                new_eh_idx = pivot_idx; out_state = "EXTEND_DN"; legs_inc = 1
        elif leg_dir == 1:
            if kind == -1:
                if slab == "HL":
                    new_last_hl = price; out_state = "EXTEND_UP"
                    if leg_extreme_l_price is None or price < leg_extreme_l_price:
                        new_el_price = price; new_el_idx = pivot_idx
                else:
                    if last_hl_price is None or price >= last_hl_price - thr:
                        out_state = "IN_RANGE"
                    else:
                        out_state = "TRANSITION"; retro = leg_extreme_h_idx
                        new_dir = 2; new_last_hl = None; new_last_lh = None
                        new_eh_idx = -1; new_eh_price = None
                        new_el_idx = pivot_idx; new_el_price = price; legs_inc = 1
            else:
                if slab == "HH":
                    out_state = "EXTEND_UP"
                    if leg_extreme_h_price is None or price > leg_extreme_h_price:
                        new_eh_price = price; new_eh_idx = pivot_idx
                else:
                    out_state = "IN_RANGE"
        elif leg_dir == 2:
            if kind == 1:
                if slab == "LH":
                    new_last_lh = price; out_state = "EXTEND_DN"
                    if leg_extreme_h_price is None or price > leg_extreme_h_price:
                        new_eh_price = price; new_eh_idx = pivot_idx
                else:
                    if last_lh_price is None or price <= last_lh_price + thr:
                        out_state = "IN_RANGE"
                    else:
                        out_state = "TRANSITION"; retro = leg_extreme_l_idx
                        new_dir = 1; new_last_hl = None; new_last_lh = None
                        new_el_idx = -1; new_el_price = None
                        new_eh_idx = pivot_idx; new_eh_price = price; legs_inc = 1
            else:
                if slab == "LL":
                    out_state = "EXTEND_DN"
                    if leg_extreme_l_price is None or price < leg_extreme_l_price:
                        new_el_price = price; new_el_idx = pivot_idx
                else:
                    out_state = "IN_RANGE"
        return (out_state, new_dir, new_last_hl, new_last_lh, new_eh_idx,
                new_el_idx, new_eh_price, new_el_price, legs_inc, retro)

    def update_current_channel(leg_direction, leg_start_idx, cur_bar):
        target_slab = "HL" if leg_direction == 1 else "LH"
        target_kind = -1 if leg_direction == 1 else 1
        first_chain = -1; second_chain = -1; n_chain = 0
        if leg_start_idx < 0:
            return 0, -1, -1, False
        for i in range(leg_start_idx, len(piv_bars)):
            if piv_kinds[i] == target_kind and piv_slabs[i] == target_slab:
                if first_chain < 0:
                    first_chain = i
                elif second_chain < 0:
                    second_chain = i
                n_chain += 1
        drew = n_chain >= channel_min_pivots and second_chain >= 0
        return n_chain, first_chain, second_chain, drew

    def find_prev_significant_price(pivot_idx, kind, min_grade):
        result = None
        for i in range(0, pivot_idx):
            if piv_kinds[i] == kind and piv_grades[i] >= min_grade:
                result = piv_prices[i]
        return result

    def upgrade(bx, kind, new_grade):
        found = -1
        for i in range(len(piv_bars)):
            if piv_bars[i] == bx and piv_kinds[i] == kind:
                found = i
        if found < 0:
            return
        if new_grade > piv_grades[found]:
            piv_grades[found] = new_grade
            bp = piv_prices[found]
            ref = find_prev_significant_price(found, kind, new_grade)
            if ref is not None:
                if kind == 1:
                    piv_slabs[found] = "HH" if bp > ref else "LH"
                else:
                    piv_slabs[found] = "LL" if bp < ref else "HL"

    for b in range(n):
        thr = 0.0 if np.isnan(atr_v[b]) else atr_v[b] * leg_break_atr

        # Grade C HIGH confirmed at this bar: pivot bar = b - sc
        def process(kind, price):
            nonlocal last_h_price, last_l_price, leg_dir, last_hl_price, last_lh_price
            nonlocal leg_extreme_h_idx, leg_extreme_l_idx, leg_extreme_h_price, leg_extreme_l_price
            nonlocal leg_start_pivot_idx, channel_epoch_start_idx, n_legs
            nonlocal n_channel_draws, last_n_chain, finalized_channels
            if kind == 1:
                ref_h = last_lh_price if (leg_dir == 2 and last_lh_price is not None) else last_h_price
                slab = "?" if ref_h is None else ("HH" if price > ref_h else "LH")
                last_h_price = price
            else:
                ref_l = last_hl_price if (leg_dir == 1 and last_hl_price is not None) else last_l_price
                slab = "?" if ref_l is None else ("LL" if price < ref_l else "HL")
                last_l_price = price
            piv_bars.append(b - sc); piv_prices.append(price); piv_kinds.append(kind)
            piv_grades.append(1); piv_slabs.append(slab)
            new_idx = len(piv_bars) - 1
            (state, nd, nhl, nlh, nehi, neli, nehp, nelp, linc, retro) = \
                compute_leg_state(price, kind, slab, new_idx, thr)
            leg_dir = nd; last_hl_price = nhl; last_lh_price = nlh
            leg_extreme_h_idx = nehi; leg_extreme_l_idx = neli
            leg_extreme_h_price = nehp; leg_extreme_l_price = nelp
            n_legs += linc
            piv_leg.append(state)
            if retro >= 0 or linc == 1:
                # On finalize: if the epoch that just ended reached >=2 chain
                # pivots, it would have a persistent (finalized) channel.
                if epochs:
                    if epochs[-1]["max_n_chain"] >= channel_min_pivots:
                        finalized_channels += 1
                # Backtrack the epoch to the CHoCH-initiating pivot (the prior
                # leg's BLACK extreme = retro) on a transition; else new_idx.
                if epochs:
                    epochs[-1]["end_bar"] = b
                    epochs[-1]["end_idx"] = new_idx
                start = retro if retro >= 0 else new_idx
                epochs.append({"start": start, "dir": leg_dir, "max_n_chain": 0,
                               "first_chain": -1, "end_bar": n - 1, "end_idx": None})
                leg_start_pivot_idx = new_idx; channel_epoch_start_idx = start
            if leg_dir != 0:
                nch, fc, sc2, drew = update_current_channel(leg_dir, channel_epoch_start_idx, b)
                last_n_chain = nch
                if epochs:
                    epochs[-1]["max_n_chain"] = max(epochs[-1]["max_n_chain"], nch)
                    if fc >= 0 and epochs[-1]["first_chain"] < 0:
                        epochs[-1]["first_chain"] = fc
                if drew:
                    n_channel_draws += 1
            return slab, state

        bar_h = b - sc
        if bar_h in ph_c:
            process(1, ph_c[bar_h])
        if bar_h in pl_c:
            process(-1, pl_c[bar_h])
        # Grade B upgrades
        bar_b = b - sb
        if bar_b in ph_b:
            upgrade(bar_b, 1, 2)
        if bar_b in pl_b:
            upgrade(bar_b, -1, 2)
        # Grade A upgrades
        bar_a = b - sa
        if bar_a in ph_a:
            upgrade(bar_a, 1, 3)
        if bar_a in pl_a:
            upgrade(bar_a, -1, 3)

    # Summary
    print(f"\nTotal pivots: {len(piv_bars)}")
    print(f"n_legs: {n_legs}")
    print(f"n_channel_draws: {n_channel_draws}")
    print(f"last_n_chain (final epoch): {last_n_chain}")
    print(f"final leg_dir: {leg_dir}  epoch_start_idx: {channel_epoch_start_idx}")
    # Count the still-live final epoch if it reached >=2 chain pivots
    live_final = 1 if (epochs and epochs[-1]["max_n_chain"] >= channel_min_pivots) else 0
    print(f"PERSISTENT channels expected on chart: {finalized_channels + live_final} "
          f"(finalized={finalized_channels} + live={live_final})")
    print("epochs (start_idx, dir, max_n_chain):")
    for e in epochs:
        d = {0: "INIT", 1: "UP", 2: "DOWN"}[e["dir"]]
        mark = " <- channel" if e["max_n_chain"] >= channel_min_pivots else ""
        fc = e["first_chain"]
        fc_bar = piv_bars[fc] if fc >= 0 else -1
        st_bar = piv_bars[e['start']] if e['start'] >= 0 else -1
        print(f"   epoch_start_idx={e['start']:3d}(bar{st_bar:3d})  dir={d:4s}  "
              f"max_n_chain={e['max_n_chain']}  first_chain_idx={fc:3d}(bar{fc_bar:3d}){mark}")

    # Show epoch boundaries and how many chain pivots each epoch reached
    print("\n--- Epoch analysis ---")
    # Re-derive epochs by walking leg states
    from collections import Counter
    slab_counts = Counter(piv_slabs)
    print("slab counts:", dict(slab_counts))
    state_counts = Counter(piv_leg)
    print("leg-state counts:", dict(state_counts))

    # For each pivot, print compact line
    print("\nidx bar kind grade slab     legstate")
    for i in range(len(piv_bars)):
        k = "H" if piv_kinds[i] == 1 else "L"
        print(f"{i:3d} {piv_bars[i]:3d}  {k}    {piv_grades[i]}    {piv_slabs[i]:4s}  {piv_leg[i]}")

    # ------------------------------------------------------------------
    # Render the 3-line channels (support/resistance + center) using the
    # construction Mouli described:
    #   UP : chain (support) line from initial LL through HLs (slope locked
    #        LL->first HL); parallel (resistance) through first HIGH pivot;
    #        center = midline.
    #   DOWN: chain (resistance) from initial HH through LHs; parallel
    #         (support) through first LOW pivot; center = midline.
    # ------------------------------------------------------------------
    def compute_channel(start_idx, end_idx, direction):
        if end_idx is None:
            end_idx = len(piv_bars)
        chain_kind = -1 if direction == 1 else 1
        chain_slab = "HL" if direction == 1 else "LH"
        opp_kind = 1 if direction == 1 else -1
        anchor_idx = -1
        anchor_price = 1e10 if direction == 1 else -1e10
        first_opp = -1
        # Collect ALL chain-side pivots (lows for UP / highs for DOWN) so the
        # support/resistance slope is a least-squares fit that rides 2-3+
        # pivots -- a two-point (anchor->first) slope is too steep/noisy.
        cxs = []; cys = []
        for i in range(start_idx, min(end_idx + 1, len(piv_bars))):
            k = piv_kinds[i]; p = piv_prices[i]
            if k == chain_kind:
                cxs.append(piv_bars[i]); cys.append(p)
                if (direction == 1 and p < anchor_price) or (direction == 2 and p > anchor_price):
                    anchor_price = p; anchor_idx = i
            if k == opp_kind and first_opp < 0:
                first_opp = i
        nch = len(cxs)
        if anchor_idx < 0 or first_opp < 0 or nch < 2:
            return None
        # Least-squares slope over the chain-side pivots.
        mx = sum(cxs) / nch; my = sum(cys) / nch
        num = sum((cxs[j] - mx) * (cys[j] - my) for j in range(nch))
        den = sum((cxs[j] - mx) ** 2 for j in range(nch))
        if den == 0:
            return None
        slope = num / den
        # Anchor the chain line visually at the initial extreme (LL/HH) so it
        # starts where Mouli starts, but ride the regression slope.
        x1 = piv_bars[anchor_idx]; y1 = piv_prices[anchor_idx]
        xo = piv_bars[first_opp]; yo = piv_prices[first_opp]
        return {"x1": x1, "y1": y1, "slope": slope, "xo": xo, "yo": yo, "dir": direction}

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    import os

    fig, ax = plt.subplots(figsize=(32, 14), dpi=130)
    for i in range(n):
        col = "#26a69a" if c[i] >= o[i] else "#ef5350"
        ax.plot([i, i], [l[i], h[i]], color="#333", linewidth=0.7, zorder=1)
        lo, hi = min(o[i], c[i]), max(o[i], c[i])
        ax.add_patch(Rectangle((i - 0.3, lo), 0.6, max(hi - lo, 0.05), color=col, zorder=2))
    for i in range(len(piv_bars)):
        bx = piv_bars[i]; bp = piv_prices[i]
        if piv_kinds[i] == 1:
            ax.text(bx, bp + 1.0, piv_slabs[i], ha="center", fontsize=6, color="#b00")
        else:
            ax.text(bx, bp - 1.0, piv_slabs[i], ha="center", fontsize=6, color="#080")

    # ------------------------------------------------------------------
    # Broad parallel band over a cluster of pivots [start_idx, end_idx].
    # One regression slope over ALL pivots; top line offset through the
    # highest high, bottom through the lowest low, center between.
    # ------------------------------------------------------------------
    def compute_broad(start_idx, end_idx):
        xs = []; ys = []
        hi_i = -1; lo_i = -1
        hi_p = -1e18; lo_p = 1e18
        for i in range(start_idx, min(end_idx + 1, len(piv_bars))):
            xs.append(piv_bars[i]); ys.append(piv_prices[i])
            if piv_prices[i] > hi_p:
                hi_p = piv_prices[i]; hi_i = i
            if piv_prices[i] < lo_p:
                lo_p = piv_prices[i]; lo_i = i
        if len(xs) < 3 or hi_i < 0 or lo_i < 0:
            return None
        mx = sum(xs) / len(xs); my = sum(ys) / len(ys)
        num = sum((xs[j] - mx) * (ys[j] - my) for j in range(len(xs)))
        den = sum((xs[j] - mx) ** 2 for j in range(len(xs)))
        slope = num / den if den else 0.0
        xa = piv_bars[start_idx]; xb = piv_bars[min(end_idx, len(piv_bars) - 1)]
        xhi = piv_bars[hi_i]; xlo = piv_bars[lo_i]
        # intercepts so the lines pass through the extreme pivots
        top_b = hi_p - slope * xhi
        bot_b = lo_p - slope * xlo
        return {"xa": xa, "xb": xb, "slope": slope, "top_b": top_b, "bot_b": bot_b}

    # Build the per-epoch candidate channels. Span is taken from the epoch's
    # pivot range (accurate), not the drifting end_bar.
    cands = []
    for e in epochs:
        ch = compute_channel(e["start"], e.get("end_idx"), e["dir"])
        if ch is None:
            continue
        s_idx = e["start"]
        e_idx = e["end_idx"] if e["end_idx"] is not None else len(piv_bars) - 1
        ch["s_idx"] = s_idx; ch["e_idx"] = e_idx
        ch["xa"] = piv_bars[s_idx]; ch["xb"] = piv_bars[min(e_idx, len(piv_bars) - 1)]
        ch["right_x"] = ch["xb"]
        ch["span"] = ch["xb"] - ch["xa"]
        ch["small"] = ch["span"] <= small_leg_bars
        cands.append(ch)

    # Group consecutive SMALL legs into runs; a run of >= broad_min_run becomes
    # one broad channel and the individual slim channels are suppressed.
    broads = []
    keep = [True] * len(cands)
    i = 0
    while i < len(cands):
        if cands[i]["small"]:
            j = i
            while j < len(cands) and cands[j]["small"]:
                j += 1
            run = list(range(i, j))
            if len(run) >= broad_min_run:
                bch = compute_broad(cands[run[0]]["s_idx"], cands[run[-1]]["e_idx"])
                if bch is not None:
                    broads.append(bch)
                    for k in run:
                        keep[k] = False
            i = j
        else:
            i += 1

    for idx, ch in enumerate(cands):
        d = {1: "UP", 2: "DOWN"}[ch["dir"]]
        print(f"  cand {idx}: dir={d:4s} xa={ch['xa']:3d} xb={ch['xb']:3d} span={ch['span']:3d} small={ch['small']} keep={keep[idx]}")
    print(f"  broad channels: {len(broads)}")

    n_drawn = 0
    for idx, ch in enumerate(cands):
        if not keep[idx]:
            continue
        n_drawn += 1
        x1, y1, slope = ch["x1"], ch["y1"], ch["slope"]
        xo, yo = ch["xo"], ch["yo"]
        right_x = ch["right_x"]
        col = "green" if ch["dir"] == 1 else "red"
        # chain line from anchor
        xs = [x1, right_x]
        chain_y = [y1, y1 + slope * (right_x - x1)]
        # opposite parallel through first opp
        opp_y = [yo + slope * (x1 - xo), yo + slope * (right_x - xo)]
        # center
        cen_y = [(chain_y[0] + opp_y[0]) / 2, (chain_y[1] + opp_y[1]) / 2]
        ax.plot(xs, chain_y, color=col, linewidth=1.5, zorder=5)
        ax.plot(xs, opp_y, color=col, linewidth=1.5, zorder=5)
        ax.plot(xs, cen_y, color=col, linewidth=1.0, linestyle=":", alpha=0.7, zorder=5)

    # Broad trading-range channels in a distinct color (purple).
    for bch in broads:
        xa, xb, slope = bch["xa"], bch["xb"], bch["slope"]
        xs = [xa, xb]
        top_y = [bch["top_b"] + slope * xa, bch["top_b"] + slope * xb]
        bot_y = [bch["bot_b"] + slope * xa, bch["bot_b"] + slope * xb]
        cen_y = [(top_y[0] + bot_y[0]) / 2, (top_y[1] + bot_y[1]) / 2]
        ax.plot(xs, top_y, color="purple", linewidth=2.0, zorder=6)
        ax.plot(xs, bot_y, color="purple", linewidth=2.0, zorder=6)
        ax.plot(xs, cen_y, color="purple", linewidth=1.0, linestyle=":", alpha=0.7, zorder=6)

    ax.set_title(f"/ES 2m {target} -- 3-line channels (support/resistance + center). {n_drawn} channels", fontsize=13)
    ax.set_xlim(-1, n)
    ax.grid(True, alpha=0.2, linestyle=":", linewidth=0.4)
    out_dir = os.path.join(os.path.dirname(__file__), "out")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"channel_3line_{target.isoformat()}.png")
    plt.tight_layout(); plt.savefig(out_path, dpi=130); plt.close(fig)
    print(f"\n3-line channel render -> {out_path}  ({n_drawn} channels)")


if __name__ == "__main__":
    main()
