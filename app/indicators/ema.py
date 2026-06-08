import numpy as np


def ema(values: np.ndarray, length: int) -> np.ndarray:
    result = np.full_like(values, np.nan)
    n = len(values)
    if n < 1 or length < 1:
        return result
    alpha = 2.0 / (length + 1)
    start = 0
    while start < n and np.isnan(values[start]):
        start += 1
    if start >= n:
        return result
    result[start] = values[start]
    for i in range(start + 1, n):
        v = values[i]
        if np.isnan(v):
            result[i] = result[i - 1]
        else:
            result[i] = alpha * v + (1.0 - alpha) * result[i - 1]
    return result
