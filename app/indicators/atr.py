import numpy as np

from app.indicators.rma import rma


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 14) -> np.ndarray:
    result = np.full_like(close, np.nan)
    if len(close) < 2:
        return result
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    result = rma(tr, length)
    return result
