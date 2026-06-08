import numpy as np


def rma(values: np.ndarray, length: int) -> np.ndarray:
    result = np.full_like(values, np.nan)
    n = len(values)
    if n < length or length < 1:
        return result
    alpha = 1.0 / length
    first_valid = length - 1
    sma_sum = 0.0
    sma_count = 0
    for i in range(length):
        v = values[i]
        if not np.isnan(v):
            sma_sum += v
            sma_count += 1
    result[first_valid] = sma_sum / sma_count if sma_count > 0 else 0.0
    for i in range(length, n):
        v = values[i]
        if np.isnan(v):
            result[i] = result[i - 1]
        else:
            result[i] = alpha * v + (1.0 - alpha) * result[i - 1]
    return result
