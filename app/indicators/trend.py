import numpy as np

from app.indicators.ema import ema


def trend_state(close: np.ndarray, momentum_len: int = 8, mid_len: int = 50) -> np.ndarray:
    ema_fast = ema(close, momentum_len)
    ema_mid = ema(close, mid_len)

    bull_align = ema_fast > ema_mid
    bear_align = ema_fast < ema_mid
    above_ema = close > ema_fast
    below_ema = close < ema_fast

    result = np.zeros(len(close), dtype=int)

    result[bull_align & above_ema] = 2
    result[bull_align & ~above_ema] = 1
    result[bear_align & below_ema] = -2
    result[bear_align & ~below_ema] = -1

    return result


def trend_status(trend: int) -> str:
    if trend == 2:
        return "SB \u219f"
    elif trend == 1:
        return "WB \u219f"
    elif trend == -1:
        return "WB \u21a1"
    elif trend == -2:
        return "SB \u21a1"
    return "Sideways"


def trend_color(trend: int) -> str:
    if trend == 2:
        return "#002d03"
    elif trend == 1:
        return "#138105"
    elif trend == -1:
        return "#e03030"
    elif trend == -2:
        return "#4a0404"
    return "#808080"
