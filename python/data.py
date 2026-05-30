"""CSV loader and session filtering — pure numpy + stdlib (no pandas).

The `Bars` object holds the entire dataset as numpy arrays plus a parallel list
of timezone-aware datetimes. This is the format consumed by engine.py.
"""
from __future__ import annotations
import csv
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import Optional
import numpy as np
import pytz

from config import Config


@dataclass
class Bars:
    times: list[datetime]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    in_session: np.ndarray  # bool
    session_date: list  # date objects, one per bar
    ema: np.ndarray = field(default_factory=lambda: np.array([]))
    atr: np.ndarray = field(default_factory=lambda: np.array([]))

    def __len__(self) -> int:
        return len(self.close)


def load_csv(path: str) -> Bars:
    times: list[datetime] = []
    o, h, l, c, v = [], [], [], [], []
    with open(path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t_raw = row["Time"]
            # NinjaTrader format: "2026-03-29 15:01:19.308"
            try:
                t = datetime.strptime(t_raw, "%Y-%m-%d %H:%M:%S.%f")
            except ValueError:
                t = datetime.strptime(t_raw, "%Y-%m-%d %H:%M:%S")
            times.append(t)
            o.append(float(row["Open"]))
            h.append(float(row["High"]))
            l.append(float(row["Low"]))
            c.append(float(row["Close"]))
            v.append(float(row["Volume"]))

    return Bars(
        times=times,
        open=np.asarray(o, dtype=np.float64),
        high=np.asarray(h, dtype=np.float64),
        low=np.asarray(l, dtype=np.float64),
        close=np.asarray(c, dtype=np.float64),
        volume=np.asarray(v, dtype=np.float64),
        in_session=np.ones(len(times), dtype=bool),
        session_date=[t.date() for t in times],
    )


def apply_session_filter(bars: Bars, cfg: Config) -> Bars:
    """Mark which bars are in-session per cfg. Open positions are still managed
    every bar; only NEW entries are gated by `in_session`."""
    if cfg.session_filter in ("ALL", "ETH"):
        bars.in_session = np.ones(len(bars), dtype=bool)
        return bars

    tz = pytz.timezone(cfg.session_tz)

    if cfg.session_filter == "RTH":
        start_s, end_s = "09:30", "16:00"
    else:
        start_s, end_s = cfg.session_start, cfg.session_end
    sh, sm = map(int, start_s.split(":"))
    eh, em = map(int, end_s.split(":"))
    start_t, end_t = dtime(sh, sm), dtime(eh, em)

    mask = np.zeros(len(bars), dtype=bool)
    dates = []
    for i, t in enumerate(bars.times):
        # If naive, treat as already in session_tz (NinjaTrader exports usually are)
        local = tz.localize(t) if t.tzinfo is None else t.astimezone(tz)
        local_naive_time = local.time()
        is_weekday = local.weekday() < 5
        in_window = start_t <= local_naive_time < end_t
        mask[i] = bool(is_weekday and in_window)
        dates.append(local.date())

    bars.in_session = mask
    bars.session_date = dates
    return bars
