import numpy as np

from app.indicators.rma import rma


def _true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    return np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))


def dmi(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 14):
    n = len(close)
    if n == 0:
        return np.array([]), np.array([]), np.array([])
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)

    high_change = np.diff(high, prepend=high[0])
    low_change = np.diff(low, prepend=low[0])
    high_change[0] = 0.0
    low_change[0] = 0.0

    up = high_change
    down = -low_change

    for i in range(n):
        if up[i] > down[i] and up[i] > 0:
            plus_dm[i] = up[i]
        if down[i] > up[i] and down[i] > 0:
            minus_dm[i] = down[i]

    tr = _true_range(high, low, close)
    atr_val = rma(tr, length)

    rma_plus = rma(plus_dm, length)
    rma_minus = rma(minus_dm, length)

    mask = atr_val != 0
    plus_di = np.where(mask, 100.0 * rma_plus / atr_val, np.nan)
    minus_di = np.where(mask, 100.0 * rma_minus / atr_val, np.nan)

    di_sum = plus_di + minus_di
    di_diff = np.abs(plus_di - minus_di)
    dx_mask = di_sum != 0
    dx = np.where(dx_mask, 100.0 * di_diff / di_sum, np.nan)

    adx_val = rma(dx, length)

    return plus_di, minus_di, adx_val
