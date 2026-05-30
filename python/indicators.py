"""Numerical primitives: EMA, ATR, swing pivot detection. Pure numpy."""
from __future__ import annotations
import numpy as np


def ema(values: np.ndarray, length: int) -> np.ndarray:
    """Standard EMA matching TradingView/NinjaTrader (alpha = 2/(N+1)).

    Returns array of same length. First `length-1` values are NaN to match
    `min_periods=length` semantics.
    """
    n = len(values)
    out = np.full(n, np.nan, dtype=np.float64)
    if n == 0:
        return out
    alpha = 2.0 / (length + 1.0)
    # Seed with SMA of first `length` values per common TV/NT convention
    if n < length:
        return out
    seed = values[:length].mean()
    out[length - 1] = seed
    prev = seed
    for i in range(length, n):
        prev = alpha * values[i] + (1 - alpha) * prev
        out[i] = prev
    return out


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 14) -> np.ndarray:
    """Wilder's ATR (RMA of true range)."""
    n = len(close)
    out = np.full(n, np.nan, dtype=np.float64)
    if n == 0:
        return out
    tr = np.empty(n, dtype=np.float64)
    tr[0] = high[0] - low[0]
    prev_close = close[:-1]
    tr[1:] = np.maximum.reduce([
        high[1:] - low[1:],
        np.abs(high[1:] - prev_close),
        np.abs(low[1:] - prev_close),
    ])
    if n < length:
        return out
    seed = tr[:length].mean()
    out[length - 1] = seed
    prev = seed
    alpha = 1.0 / length
    for i in range(length, n):
        prev = alpha * tr[i] + (1 - alpha) * prev
        out[i] = prev
    return out


def swing_pivots(high: np.ndarray, low: np.ndarray, strength: int = 3):
    """Return (swing_high_indices, swing_low_indices).

    A swing high at i requires high[i] > high[i±k] for k = 1..strength.
    Returns sorted ascending lists of integer bar indices.
    """
    n = len(high)
    sh_idx: list[int] = []
    sl_idx: list[int] = []
    for i in range(strength, n - strength):
        h = high[i]
        is_sh = True
        for k in range(1, strength + 1):
            if not (h > high[i - k] and h > high[i + k]):
                is_sh = False; break
        if is_sh:
            sh_idx.append(i)

        lo = low[i]
        is_sl = True
        for k in range(1, strength + 1):
            if not (lo < low[i - k] and lo < low[i + k]):
                is_sl = False; break
        if is_sl:
            sl_idx.append(i)
    return sh_idx, sl_idx
