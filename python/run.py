"""CLI entry point. Run a backtest on the sample data.

Usage:
    python run.py                              # default config on bundled data
    python run.py --csv path/to/data.csv
    python run.py --plot-day 2026-04-15        # render chart for one session
    python run.py --target-r 1.5 --stop-mode BEYOND_SIGNAL_BAR
"""
from __future__ import annotations
import argparse
import os
import sys
import json

from config import Config
from data import load_csv, apply_session_filter
from engine import PriceActionEngine
from backtest import run_backtest, summarize, format_summary


HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.normpath(os.path.join(HERE, "..", "data", "ES_2000tick_sample.csv"))


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", default=DEFAULT_CSV)
    p.add_argument("--session", default="RTH", choices=["RTH", "ETH", "CUSTOM", "ALL"])
    p.add_argument("--stop-mode", default=None,
                   choices=["BEYOND_SIGNAL_BAR", "FIXED_POINTS", "FIXED_TICKS", "ATR", "BEYOND_SWING"])
    p.add_argument("--target-mode", default=None,
                   choices=["FIXED_POINTS", "FIXED_TICKS", "R_MULTIPLE", "MEASURED_MOVE", "ATR", "OPPOSITE_KEP", "SCALE_OUT"])
    p.add_argument("--target-r", type=float, default=None)
    p.add_argument("--target-points", type=float, default=None)
    p.add_argument("--stop-points", type=float, default=None)
    p.add_argument("--no-strict-signal", action="store_true")
    p.add_argument("--min-rr", type=float, default=None)
    p.add_argument("--max-concurrent", type=int, default=None)
    p.add_argument("--plot-day", default=None)
    p.add_argument("--plot-equity", action="store_true")
    p.add_argument("--out-dir", default=os.path.join(HERE, "out"))
    p.add_argument("--save-signals", action="store_true")
    p.add_argument("--save-trades", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()

    cfg = Config()
    cfg.session_filter = args.session
    if args.stop_mode: cfg.stop_mode = args.stop_mode
    if args.target_mode: cfg.target_mode = args.target_mode
    if args.target_r is not None: cfg.target_r = args.target_r
    if args.target_points is not None: cfg.target_points = args.target_points
    if args.stop_points is not None: cfg.stop_points = args.stop_points
    if args.no_strict_signal: cfg.strict_signal_bar = False
    if args.min_rr is not None: cfg.min_rr_to_enter = args.min_rr
    if args.max_concurrent is not None: cfg.max_concurrent = args.max_concurrent

    print(f"Loading {args.csv} ...")
    bars = load_csv(args.csv)
    bars = apply_session_filter(bars, cfg)
    print(f"  {len(bars):,} bars, {bars.times[0]} -> {bars.times[-1]}")
    n_in = int(bars.in_session.sum())
    print(f"  in-session bars: {n_in:,}  ({n_in/len(bars)*100:.1f}%)")

    print(f"\nRunning engine (session={cfg.session_filter}, stop={cfg.stop_mode}, target={cfg.target_mode}, target_r={cfg.target_r}) ...")
    eng = PriceActionEngine(bars, cfg)
    signals = eng.run()
    print(f"  {len(signals)} signals emitted.")

    by_setup_count: dict[str, int] = {}
    for s in signals:
        by_setup_count[s.setup] = by_setup_count.get(s.setup, 0) + 1
    if by_setup_count:
        print("  Setup counts:")
        for k, v in sorted(by_setup_count.items()):
            print(f"    {k:>10}: {v}")

    trades = run_backtest(eng.b, signals, cfg)
    print(f"\n{len(trades)} trades executed.\n")
    stats = summarize(trades)
    print(format_summary(stats))

    os.makedirs(args.out_dir, exist_ok=True)
    if args.save_signals:
        import csv
        path = os.path.join(args.out_dir, "signals.csv")
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["bar_index", "time", "setup", "side", "kep", "entry", "stop", "target", "risk"])
            for s in signals:
                w.writerow([s.bar_index, s.time, s.setup, s.side, s.kep,
                            f"{s.entry_trigger:.2f}", f"{s.planned_stop:.2f}",
                            f"{s.planned_target:.2f}", f"{s.initial_risk:.2f}"])
        print(f"\nSignals -> {path}")
    if args.save_trades:
        import csv
        path = os.path.join(args.out_dir, "trades.csv")
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["setup", "side", "kep", "entry_time", "entry", "exit_time",
                        "exit", "exit_reason", "pnl_pts", "pnl_$", "R", "bars_held"])
            for t in trades:
                w.writerow([t.setup, t.side, t.kep, t.entry_time, f"{t.entry_price:.2f}",
                            t.exit_time, f"{(t.exit_price or 0):.2f}", t.exit_reason,
                            f"{t.pnl_points:.2f}", f"{t.pnl_dollars:.2f}",
                            f"{t.r_multiple:.2f}", t.bars_held])
        print(f"Trades -> {path}")

    if args.plot_day:
        from plot import plot_day
        out = os.path.join(args.out_dir, f"day_{args.plot_day}.png")
        plot_day(eng.b, signals, trades, day=args.plot_day, out_path=out)
    if args.plot_equity:
        from plot import plot_equity
        out = os.path.join(args.out_dir, "equity.png")
        plot_equity(trades, out_path=out)


if __name__ == "__main__":
    main()
