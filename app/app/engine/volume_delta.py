import numpy as np


def compute_volume_delta_lower_tf(
    open_1m: np.ndarray, high_1m: np.ndarray, low_1m: np.ndarray,
    close_1m: np.ndarray, volume_1m: np.ndarray,
    time_1m: np.ndarray, parent_start: float, parent_end: float,
):
    mask = (time_1m >= parent_start) & (time_1m < parent_end)
    if not np.any(mask):
        return {"bull": 0.0, "bear": 0.0, "delta": 0.0, "norm": 0.0}

    h = high_1m[mask]
    lo = low_1m[mask]
    c = close_1m[mask]
    v = volume_1m[mask]

    bull_total = 0.0
    bear_total = 0.0
    max_v = 0.0
    poc_price = np.nan

    for i in range(len(c)):
        cr = max(h[i] - lo[i], 1e-10)
        b = v[i] * (c[i] - lo[i]) / cr
        s = v[i] * (h[i] - c[i]) / cr
        bull_total += b
        bear_total += s
        if v[i] > max_v:
            max_v = v[i]
            poc_price = float(c[i])

    delta = bull_total - bear_total
    total = bull_total + bear_total
    norm = delta / total if total > 0 else 0.0
    norm = max(-1.0, min(1.0, norm))

    return {
        "bull": bull_total,
        "bear": bear_total,
        "delta": delta,
        "norm": norm,
        "poc": poc_price,
    }


def compute_volume_delta_batch(
    time_1m: np.ndarray,
    open_1m: np.ndarray, high_1m: np.ndarray,
    low_1m: np.ndarray, close_1m: np.ndarray,
    volume_1m: np.ndarray,
    parent_times: np.ndarray, parent_resolution_sec: int,
):
    n = len(parent_times)
    norms = np.zeros(n)
    pocs = np.full(n, np.nan)

    for i in range(n):
        pt = parent_times[i]
        result = compute_volume_delta_lower_tf(
            open_1m, high_1m, low_1m, close_1m, volume_1m, time_1m,
            pt, pt + parent_resolution_sec,
        )
        norms[i] = result["norm"]
        pocs[i] = result["poc"]

    return {"norm": norms.tolist(), "poc": pocs.tolist()}


def find_poc(bull: np.ndarray, bear: np.ndarray, close_arr: np.ndarray) -> float:
    if len(bull) == 0 or len(bear) == 0:
        return np.nan
    bv_max = np.max(bull) if len(bull) > 0 else 0
    sv_max = np.max(bear) if len(bear) > 0 else 0
    use_arr = bull if bv_max > sv_max else bear
    if len(use_arr) == 0 or len(close_arr) == 0:
        return np.nan
    sorted_idx = np.argsort(use_arr)[::-1]
    if len(sorted_idx) > 0 and sorted_idx[0] < len(close_arr):
        return float(close_arr[sorted_idx[0]])
    return np.nan
