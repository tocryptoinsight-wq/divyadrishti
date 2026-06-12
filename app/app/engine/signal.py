import numpy as np

from app.indicators.adx import dmi
from app.indicators.atr import atr
from app.indicators.ema import ema
from app.indicators.rsi import rsi
from app.indicators.trend import trend_state


def compute_analysis(close, high, low, volume, settings):
    if len(close) == 0:
        return {k: np.array([np.nan]) for k in ["ema10","ema20","ema50","rsi","adx","atr","trend"]}
    ema10 = ema(close, settings.momentum_len)
    ema20 = ema(close, settings.fast_len)
    ema50 = ema(close, settings.mid_len)
    rsi_val = rsi(close, settings.rsi_len)
    _, _, adx_val = dmi(high, low, close, settings.adx_len)
    atr_val = atr(high, low, close, settings.atr_len)
    trend = trend_state(close, settings.momentum_len, settings.fast_len)

    return {
        "ema10": ema10,
        "ema20": ema20,
        "ema50": ema50,
        "rsi": rsi_val,
        "adx": adx_val,
        "atr": atr_val,
        "trend": trend,
    }


def signal_logic(
    trend_1h: int,
    trend_15m: int,
    adx_15m: float,
    rsi_15m: float,
    volume_24h: float = 0,
) -> str:
    if trend_1h >= 1 and trend_15m >= 1 and adx_15m > 20 and rsi_15m > 50 and volume_24h > 1_000_000:
        return "Allowed (Buy)"
    if trend_1h <= -1 and trend_15m <= -1 and adx_15m > 20 and rsi_15m < 50 and volume_24h > 1_000_000:
        return "Allowed (Sell)"
    return "NO TRADE"


def crossover(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    result = np.full(len(a), False)
    if len(a) < 2:
        return result
    result[1:] = (a[:-1] <= b[:-1]) & (a[1:] > b[1:])
    return result


def crossunder(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    result = np.full(len(a), False)
    if len(a) < 2:
        return result
    result[1:] = (a[:-1] >= b[:-1]) & (a[1:] < b[1:])
    return result


def get_sl(
    dir_val: int, sl_mode: str, close: float, low: float,
    high: float, atr_val: float, atr_mult: float, buffer: float,
) -> float:
    sl = 0.0
    if dir_val == 1:
        sl = close - (atr_val * atr_mult) if sl_mode == "ATR" else low
        sl = sl - (atr_val * buffer)
    elif dir_val == -1:
        sl = close + (atr_val * atr_mult) if sl_mode == "ATR" else high
        sl = sl + (atr_val * buffer)
    return sl


def get_tp(entry: float, sl: float, dir_val: int) -> float:
    risk = abs(entry - sl)
    return entry + (risk * 2) if dir_val == 1 else entry - (risk * 2)


def get_qty(risk_amount: float, entry: float, sl: float) -> float:
    dist = abs(entry - sl)
    return round((risk_amount / dist) * 100) / 100 if dist != 0 else 0.0


def adx_slope(adx_smooth: np.ndarray, back: int = 3) -> np.ndarray:
    result = np.full(len(adx_smooth), np.nan)
    if len(adx_smooth) > back:
        result[back:] = adx_smooth[back:] - adx_smooth[:-back]
    return result


def adx_status(val: float, slope: float) -> str:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "W (na) ↔"
    strength = "W" if val < 20 else "M" if val < 25 else "S"
    thresh = 0.3
    arrow = "\u219f" if slope > thresh else "\u21a1" if slope < -thresh else "\u2194"
    return f"{strength} ({round(val)}) {arrow}"


def adx_color(val: float) -> str:
    if val < 20:
        return "#600707"
    elif val < 25:
        return "#524f01"
    return "#104a12"


def rsi_status(val: float) -> str:
    if val >= 60:
        return "\u2191\u2191"
    elif val > 50:
        return "\u2191\u2193"
    elif val >= 40:
        return "\u2193\u2191"
    return "\u2193\u2193"


def rsi_text(val: float) -> str:
    return f"{rsi_status(val)} ({round(val)})"


def rsi_color(val: float) -> str:
    if val >= 60:
        return "#002d03"
    elif val <= 40:
        return "#4a0404"
    return "#524f01"
