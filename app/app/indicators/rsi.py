import numpy as np

from app.indicators.rma import rma


def rsi(close: np.ndarray, length: int = 14) -> np.ndarray:
    result = np.full_like(close, np.nan)
    if len(close) < length + 1:
        return result
    change = np.diff(close)
    u = np.where(change > 0, change, 0.0)
    d = np.where(change < 0, -change, 0.0)
    u = np.concatenate([[np.nan], u])
    d = np.concatenate([[np.nan], d])
    avg_u = rma(u, length)
    avg_d = rma(d, length)
    rs = np.divide(avg_u, avg_d, out=np.full_like(avg_u, np.nan), where=avg_d != 0)
    rs = np.where((avg_d == 0) & (avg_u > 0), 100.0, rs)
    result = 100.0 - 100.0 / (1.0 + rs)
    return result
