import asyncio
import logging
import time

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.deps import get_current_user_from_cookie
from app.config import settings
from app.database import execute, query
from app.data.delta_client import delta_client
from app.engine.signal import (
    adx_color,
    adx_slope,
    adx_status,
    compute_analysis,
    crossover,
    crossunder,
    get_qty,
    get_sl,
    get_tp,
    rsi_color,
    rsi_text,
    signal_logic,
)
from app.indicators.adx import dmi
from app.indicators.ema import ema
from app.indicators.rsi import rsi as rsi_func
from app.indicators.trend import trend_color, trend_state, trend_status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["analysis"])

def _check_exit(is_long: bool, close, ema10, ema20) -> bool:
    if len(close) < 3 or len(ema10) < 3 or len(ema20) < 3:
        return False
    c = close[-1]
    e20 = ema20[-1]
    return c < e20 if is_long else c > e20


RESOLUTION_MAP = {
    "5": ("5m", 300),
    "15": ("15m", 900),
    "60": ("1h", 3600),
}


def _to_list(arr):
    if arr is None:
        return []
    if isinstance(arr, np.ndarray):
        return [None if (isinstance(v, float) and np.isnan(v)) else v for v in arr.tolist()]
    return arr


def _safe_last(arr):
    if arr is None:
        return None
    try:
        if len(arr) == 0:
            return None
    except (TypeError, AttributeError):
        return None
    val = arr[-1]
    try:
        if np.isnan(val):
            return None
    except (TypeError, ValueError):
        pass
    return float(val)


def _latest(arr):
    return float(arr[-1]) if len(arr) > 0 else 0.0


def _to_val(val):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    return float(val)


def _build_tf_entry(analysis, adx_smooth_len, adx_slope_back, trend_arr=None):
    trend_v = int(_latest(trend_arr if trend_arr is not None else analysis["trend"]))
    rsi_v = _latest(analysis["rsi"])
    adx_arr = analysis["adx"]
    adx_smooth = ema(adx_arr, adx_smooth_len)
    adx_sl_arr = adx_slope(adx_smooth, adx_slope_back)
    adx_v = _latest(adx_smooth)
    adx_sl_v = _safe_last(adx_sl_arr) or 0

    rsi_ok = _to_val(rsi_v)
    adx_ok = _to_val(adx_v)
    adx_disp = adx_ok if adx_ok is not None else 0

    return {
        "trend": trend_v,
        "trend_text": trend_status(trend_v),
        "trend_color": trend_color(trend_v),
        "rsi": round(rsi_ok, 2) if rsi_ok is not None else None,
        "rsi_text": rsi_text(rsi_ok) if rsi_ok is not None else "-",
        "rsi_color": rsi_color(rsi_ok) if rsi_ok is not None else "#808080",
        "adx": round(adx_ok, 2) if adx_ok is not None else None,
        "adx_text": adx_status(adx_disp, adx_sl_v),
        "adx_color": adx_color(adx_disp),
    }


def _trunc(arr, chart_limit):
    if arr is None:
        return []
    return arr[-chart_limit:] if len(arr) > chart_limit else arr


def _calc_rsi_pro(close_15m):
    """Calculate RSI Pro: RSI(14) + EMA(14) on RSI using 15m close."""
    length = 14
    rsi_vals = rsi_func(np.array(close_15m, dtype=float), length)
    ema_vals = ema(rsi_vals, length)
    return rsi_vals, ema_vals


def _compute_historical_boxes(data_5m, analysis_5m, data_15m, analysis_15m, data_1h, analysis_1h, volume_24h):
    """
    Generate historical position boxes matching exact TradingView Pine Script logic.

    Flow: Signal → Pullback wait → Pullback mem → Cross/Reclaim trigger → Entry
    """
    close_5m = data_5m["close"]
    open_5m = data_5m["open"]
    high_5m = data_5m["high"]
    low_5m = data_5m["low"]
    times_5m = data_5m["time"]
    ema10_5m = analysis_5m["ema10"]
    ema20_5m = analysis_5m["ema20"]
    atr_5m = analysis_5m["atr"]

    trend_1h = analysis_1h["trend"]
    times_1h = data_1h["time"]
    trend_15m = analysis_15m["trend"]
    times_15m = data_15m["time"]
    adx_15m_raw = analysis_15m["adx"]
    rsi_15m = analysis_15m["rsi"]

    adx_arr = np.array(adx_15m_raw, dtype=float) if not isinstance(adx_15m_raw, np.ndarray) else adx_15m_raw.astype(float)
    adx_smooth_15m = ema(adx_arr, 5)

    def _map_idx(times, target):
        for j in range(len(times)):
            if times[j] > target:
                return max(0, j - 1)
        return len(times) - 1

    boxes = []
    n = len(close_5m)
    if n < 60:
        return boxes

    # State machine (mirrors Pine Script trade state)
    pullback_ready_long = False
    pullback_ready_short = False
    prev_sig = "NO TRADE"

    i = 50
    while i < n:
        t5 = times_5m[i]
        j15 = _map_idx(times_15m, t5)
        j1h = _map_idx(times_1h, t5)

        r_1h = int(trend_1h[j1h]) if 0 <= j1h < len(trend_1h) else 0
        r_15m = int(trend_15m[j15]) if 0 <= j15 < len(trend_15m) else 0
        adx_v = float(adx_smooth_15m[j15]) if 0 <= j15 < len(adx_smooth_15m) and not np.isnan(adx_smooth_15m[j15]) else 0
        rsi_v = float(rsi_15m[j15]) if 0 <= j15 < len(rsi_15m) and not np.isnan(rsi_15m[j15]) else 50

        sig_val = signal_logic(r_1h, r_15m, adx_v, rsi_v, volume_24h)
        is_buy = sig_val == "Allowed (Buy)"
        is_sell = sig_val == "Allowed (Sell)"

        # Detect signal transitions (mirrors Pine Script newBuySignal / newSellSignal / buyLost / sellLost)
        new_buy = is_buy and prev_sig != "Allowed (Buy)"
        new_sell = is_sell and prev_sig != "Allowed (Sell)"
        sig_lost = (prev_sig == "Allowed (Buy)" and not is_buy) or (prev_sig == "Allowed (Sell)" and not is_sell)
        prev_sig = sig_val

        # --- Setup mode: looking for entries ---

        # Reset pullback on new signal or signal lost (mirrors Pine Script)
        if new_buy or new_sell or sig_lost:
            pullback_ready_long = False
            pullback_ready_short = False

        # Pullback observation (mirrors Pine Script: isBuySignal && !pullbackReady && close < ema10)
        if is_buy and not pullback_ready_long and close_5m[i] < ema10_5m[i]:
            pullback_ready_long = True
        if is_sell and not pullback_ready_short and close_5m[i] > ema10_5m[i]:
            pullback_ready_short = True

        # --- Entry triggers (mirrors Pine Script buyEntryTrigger / sellEntryTrigger) ---
        buy_entry = False
        sell_entry = False

        if is_buy and i >= 1:
            # Cross trigger: crossover(ema10, ema20) && close > ema10 && close > open
            buy_cross = (ema10_5m[i] > ema20_5m[i] and ema10_5m[i - 1] <= ema20_5m[i - 1]
                         and close_5m[i] > ema10_5m[i] and close_5m[i] > open_5m[i])
            # Reclaim trigger: pullbackReady && close > ema10 && close[1] < ema10[1] && ema10 > ema20
            buy_reclaim = (pullback_ready_long and close_5m[i] > ema10_5m[i]
                           and close_5m[i - 1] < ema10_5m[i - 1]
                           and ema10_5m[i] > ema20_5m[i])
            buy_entry = buy_cross or buy_reclaim

        if is_sell and i >= 1:
            # Cross trigger: crossunder(ema10, ema20) && close < ema10 && close < open
            sell_cross = (ema10_5m[i] < ema20_5m[i] and ema10_5m[i - 1] >= ema20_5m[i - 1]
                          and close_5m[i] < ema10_5m[i] and close_5m[i] < open_5m[i])
            # Reclaim trigger: pullbackReady && close < ema10 && close[1] > ema10[1] && ema10 < ema20
            sell_reclaim = (pullback_ready_short and close_5m[i] < ema10_5m[i]
                            and close_5m[i - 1] > ema10_5m[i - 1]
                            and ema10_5m[i] < ema20_5m[i])
            sell_entry = sell_cross or sell_reclaim

        if buy_entry or sell_entry:
            entry_price = float(close_5m[i])
            atr_val = atr_5m[i]
            if atr_val is not None and not np.isnan(atr_val) and atr_val > 0:
                atr_val_f = float(atr_val)
                sl_price = entry_price - atr_val_f * 1.6 if buy_entry else entry_price + atr_val_f * 1.6
                entry_time = int(times_5m[i + 1]) if i + 1 < n else int(times_5m[i])

                exited = False
                exit_time = None
                exit_price = None
                exit_reason = None
                sl_dist = atr_val_f * 1.6
                two_r_price = entry_price + 2 * sl_dist if buy_entry else entry_price - 2 * sl_dist
                hit_2r = False

                for k in range(i + 1, n):
                    if buy_entry and low_5m[k] <= sl_price:
                        exit_time = int(times_5m[k])
                        exit_price = sl_price
                        exit_reason = "sl"
                        exited = True
                        i = k + 1
                        break
                    elif sell_entry and high_5m[k] >= sl_price:
                        exit_time = int(times_5m[k])
                        exit_price = sl_price
                        exit_reason = "sl"
                        exited = True
                        i = k + 1
                        break

                    # Track 2R hit for EMA exit eligibility
                    if not hit_2r:
                        if buy_entry and high_5m[k] >= two_r_price:
                            hit_2r = True
                        elif sell_entry and low_5m[k] <= two_r_price:
                            hit_2r = True

                    if hit_2r and k >= i + 2:
                        if _check_exit(buy_entry, close_5m[:k + 1], ema10_5m[:k + 1], ema20_5m[:k + 1]):
                            exit_time = int(times_5m[k + 1]) if k + 1 < n else int(times_5m[k])
                            exit_price = float(close_5m[k])
                            exit_reason = "ema"
                            exited = True
                            i = k + 1
                            break

                if not exited:
                    i = n

                boxes.append({
                    "time": entry_time,
                    "entry": entry_price,
                    "sl": sl_price,
                    "tp": None,
                    "isLong": bool(buy_entry),
                    "source": "scan",
                    "autoExpand": True,
                    "exited": exited,
                    "exitTime": exit_time,
                    "exitPrice": exit_price,
                    "exitReason": exit_reason,
                })

                # Reset state after trade (mirrors Pine Script justExited + pullbackReady reset)
                pullback_ready_long = False
                pullback_ready_short = False
                continue

        i += 1

    return boxes


@router.get("/analysis/{symbol}")
async def get_analysis(
    symbol: str,
    resolution: str = Query("5", description="Main chart resolution: 5, 15, or 60"),
    chart_limit: int = Query(2000, ge=10, le=2000, description="Candles to return"),
):
    main_res, main_sec = RESOLUTION_MAP.get(resolution, ("5m", 300))

    data_main = await delta_client.get_candles(symbol, main_res, limit=500)
    if len(data_main["close"]) < 100:
        return {"error": "Not enough data"}

    data_5m = data_main if main_res == "5m" else await delta_client.get_candles(symbol, "5m", limit=500)
    data_15m, data_1h = await asyncio.gather(
        delta_client.get_candles(symbol, "15m", limit=500),
        delta_client.get_candles(symbol, "1h", limit=500),
    )

    analysis_main = compute_analysis(
        data_main["close"], data_main["high"], data_main["low"],
        data_main["volume"], settings,
    )
    analysis_5m = compute_analysis(
        data_5m["close"], data_5m["high"], data_5m["low"],
        data_5m["volume"], settings,
    )
    analysis_15m = compute_analysis(
        data_15m["close"], data_15m["high"], data_15m["low"],
        data_15m["volume"], settings,
    )
    analysis_1h = compute_analysis(
        data_1h["close"], data_1h["high"], data_1h["low"],
        data_1h["volume"], settings,
    )

    # Direction from EMA10 > EMA20 for signal
    _e10_1h = analysis_1h["ema10"]
    _e20_1h = analysis_1h["ema20"]
    _e10_15m = analysis_15m["ema10"]
    _e20_15m = analysis_15m["ema20"]
    r_1h = 1 if _e10_1h[-1] > _e20_1h[-1] else -1 if _e10_1h[-1] < _e20_1h[-1] else 0
    r_15m = 1 if _e10_15m[-1] > _e20_15m[-1] else -1 if _e10_15m[-1] < _e20_15m[-1] else 0

    rsi_15m_val = _latest(analysis_15m["rsi"])

    adx_15m_arr = analysis_15m["adx"]
    adx_smooth_15m = ema(adx_15m_arr, 5)
    adx_val_15m = _latest(adx_smooth_15m)

    close_arr = data_main["close"]
    last_close = float(close_arr[-1])

    # Fetch ticker for 24h volume and last_price
    try:
        ticker = await delta_client.get_ticker(symbol)
        raw = ticker.get("result", ticker)
        volume_24h = float(raw.get("turnover_usd", raw.get("volume", 0)) or 0)
        last_price = float(raw.get("mark_price", raw.get("close", raw.get("last", last_close))))
    except Exception as e:
        logger.warning("Failed to fetch ticker for %s: %s", symbol, e)
        volume_24h = 0
        last_price = last_close

    trade_signal = signal_logic(r_1h, r_15m, adx_val_15m if adx_val_15m is not None else 0, rsi_15m_val if rsi_15m_val is not None else 50, volume_24h)

    is_buy = trade_signal == "Allowed (Buy)"
    is_sell = trade_signal == "Allowed (Sell)"

    # 5m state — new state machine: price position relative to EMAs
    state = "-"
    if is_buy or is_sell:
        close_5m = data_5m["close"]
        low_5m = data_5m["low"]
        high_5m = data_5m["high"]
        ema10_5m = analysis_5m["ema10"]
        ema20_5m = analysis_5m["ema20"]
        ema50_5m = analysis_5m["ema50"]
        if len(close_5m) >= 1:
            c = close_5m[-1]; l = low_5m[-1]; h = high_5m[-1]
            e10 = ema10_5m[-1]; e20 = ema20_5m[-1]; e50 = ema50_5m[-1]
            if is_buy and e10 > e20:
                if c > e10:                                          state = "Active (Buy)"
                elif (c < e10 or l < e10) and c > e20:               state = "Shallow Pullback (Buy)"
                elif c < e20 and c > e50:                            state = "Normal Pullback (Buy)"
                elif c < e50:                                        state = "Deep Pullback (Buy)"
            elif not is_buy and e10 < e20:
                if c < e10:                                          state = "Active (Sell)"
                elif (c > e10 or h > e10) and c < e20:               state = "Shallow Pullback (Sell)"
                elif c > e20 and c < e50:                            state = "Normal Pullback (Sell)"
                elif c > e50:                                        state = "Deep Pullback (Sell)"

    mtf_trend_1h = trend_state(data_1h["close"], settings.momentum_len, settings.fast_len)
    mtf_trend_15m = trend_state(data_15m["close"], settings.momentum_len, settings.fast_len)
    mtf_trend_5m = trend_state(data_5m["close"], settings.momentum_len, settings.fast_len)
    mtf = {
        "1h": _build_tf_entry(analysis_1h, 5, 3, mtf_trend_1h),
        "15m": _build_tf_entry(analysis_15m, 5, 3, mtf_trend_15m),
        "5m": _build_tf_entry(analysis_5m, 3, 2, mtf_trend_5m),
    }

    # Build historical per-candle MTF arrays aligned to main chart timeline
    times_main = data_main["time"]
    times_15m = data_15m["time"]
    times_1h = data_1h["time"]

    mtf_5m_trend = _to_list(analysis_5m["trend"])
    mtf_5m_rsi = _to_list(analysis_5m["rsi"])
    mtf_5m_adx = _to_list(ema(np.array(analysis_5m["adx"], dtype=float), 3))

    mtf_15m_trend = _to_list(analysis_15m["trend"])
    mtf_15m_rsi = _to_list(analysis_15m["rsi"])
    mtf_15m_adx = _to_list(ema(np.array(analysis_15m["adx"], dtype=float), 5))

    mtf_1h_trend = _to_list(analysis_1h["trend"])
    mtf_1h_rsi = _to_list(analysis_1h["rsi"])
    mtf_1h_adx = _to_list(ema(np.array(analysis_1h["adx"], dtype=float), 5))

    idx_15m = np.clip(np.searchsorted(times_15m, times_main, side='right') - 1, 0, len(times_15m) - 1)
    idx_1h = np.clip(np.searchsorted(times_1h, times_main, side='right') - 1, 0, len(times_1h) - 1)

    mtf_values = {
        "5m": {
            "trend": _trunc(mtf_5m_trend, chart_limit),
            "rsi": _trunc(mtf_5m_rsi, chart_limit),
            "adx": _trunc(mtf_5m_adx, chart_limit),
        },
        "15m": {
            "trend": _trunc([mtf_15m_trend[j] for j in idx_15m], chart_limit),
            "rsi": _trunc([mtf_15m_rsi[j] for j in idx_15m], chart_limit),
            "adx": _trunc([mtf_15m_adx[j] for j in idx_15m], chart_limit),
        },
        "1h": {
            "trend": _trunc([mtf_1h_trend[j] for j in idx_1h], chart_limit),
            "rsi": _trunc([mtf_1h_rsi[j] for j in idx_1h], chart_limit),
            "adx": _trunc([mtf_1h_adx[j] for j in idx_1h], chart_limit),
        },
    }

    high_arr = data_main["high"]
    low_arr = data_main["low"]
    open_arr = data_main["open"]
    ema10 = analysis_main["ema10"]
    ema20 = analysis_main["ema20"]

    buy_entry = is_buy and state == "Active (Buy)"
    sell_entry = is_sell and state == "Active (Sell)"

    atr_val = _latest(analysis_main["atr"]) or 0.001
    last_low = float(low_arr[-1])
    last_high = float(high_arr[-1])

    sl_long = get_sl(
        1, settings.sl_mode, last_close, last_low, last_high,
        atr_val, settings.atr_mult, settings.sl_buffer_mult,
    )
    sl_short = get_sl(
        -1, settings.sl_mode, last_close, last_low, last_high,
        atr_val, settings.atr_mult, settings.sl_buffer_mult,
    )

    tp_long = get_tp(last_close, sl_long, 1)
    tp_short = get_tp(last_close, sl_short, -1)

    qty_long = get_qty(settings.risk_amount, last_close, sl_long)
    qty_short = get_qty(settings.risk_amount, last_close, sl_short)

    # RSI Pro (15m timeframe)
    rsi_pro_vals, rsi_pro_ema = _calc_rsi_pro(data_15m["close"])
    rsi_pro_times = _to_list(data_15m["time"])

    # Historical boxes matching exact algo logic
    try:
        boxes = _compute_historical_boxes(data_5m, analysis_5m, data_15m, analysis_15m, data_1h, analysis_1h, volume_24h)
    except Exception as e:
        logger.warning("Failed to compute historical boxes for %s: %s", symbol, e)
        boxes = []

    return {
        "symbol": symbol,
        "last_close": last_close,
        "last_price": last_price,
        "atr_val": round(atr_val, 6),
        "signal": trade_signal,
        "state": state,
        "is_buy": is_buy,
        "is_sell": is_sell,
        "buy_entry": bool(buy_entry),
        "sell_entry": bool(sell_entry),
        "mtf": mtf,
        "mtf_values": mtf_values,
        "trade_setup": {
            "sl_long": round(sl_long, 6),
            "tp_long": round(tp_long, 6),
            "qty_long": round(qty_long, 4),
            "sl_short": round(sl_short, 6),
            "tp_short": round(tp_short, 6),
            "qty_short": round(qty_short, 4),
        },
        "ema": {
            "ema10": _trunc(_to_list(ema10), chart_limit),
            "ema20": _trunc(_to_list(ema20), chart_limit),
            "ema50": _trunc(_to_list(analysis_main["ema50"]), chart_limit),
            "atr": _trunc(_to_list(analysis_main["atr"]), chart_limit),
        },
        "indicator_values": {
            "adx": _trunc(_to_list(analysis_main["adx"]), chart_limit),
            "rsi": _trunc(_to_list(analysis_main["rsi"]), chart_limit),
        },
        "candles": {
            "time": _trunc(_to_list(data_main["time"]), chart_limit),
            "open": _trunc(_to_list(open_arr), chart_limit),
            "high": _trunc(_to_list(high_arr), chart_limit),
            "low": _trunc(_to_list(low_arr), chart_limit),
            "close": _trunc(_to_list(close_arr), chart_limit),
            "volume": _trunc(_to_list(data_main["volume"]), chart_limit),
        },
        "rsi_pro": {
            "time": _trunc(_to_list(rsi_pro_times), 200),
            "value": _trunc(_to_list(rsi_pro_vals), 200),
            "ema": _trunc(_to_list(rsi_pro_ema), 200),
        },
        "boxes": boxes,
    }


_screener_cache: dict[str, dict] = {}
_screener_cache_ts: dict[str, float] = {}
SCREENER_TTL = 15
_SCREENER_SEM = asyncio.Semaphore(4)

_valid_symbols_cache: list[str] = []
_valid_symbols_cache_ts: float = 0
_TICKER_CACHE_TTL = 60


async def _refresh_valid_symbols():
    global _valid_symbols_cache, _valid_symbols_cache_ts
    try:
        tickers_raw = await delta_client.get_all_tickers("perpetual_futures")
        if isinstance(tickers_raw, dict):
            ticker_list = tickers_raw.get("result", [])
        elif isinstance(tickers_raw, list):
            ticker_list = tickers_raw
        else:
            ticker_list = []
        if not isinstance(ticker_list, list):
            ticker_list = []
        symbols = []
        for t in ticker_list:
            if not isinstance(t, dict):
                continue
            sym = t.get("symbol", "")
            if sym:
                symbols.append(sym)
        if symbols:
            _valid_symbols_cache = symbols
            _valid_symbols_cache_ts = time.time()
    except Exception as e:
        logger.warning("Failed to refresh valid symbols: %s", e)


async def _validate_symbol(sym: str) -> tuple[bool, str]:
    """Returns (is_valid, resolved_symbol) for a symbol against Delta Exchange products."""
    if not _valid_symbols_cache or time.time() - _valid_symbols_cache_ts > _TICKER_CACHE_TTL:
        await _refresh_valid_symbols()
    if _valid_symbols_cache:
        if sym in _valid_symbols_cache:
            return True, sym
        for suffix in ['USD', 'USDT', 'USD.P']:
            candidate = sym + suffix
            if candidate in _valid_symbols_cache:
                return True, candidate
        for suffix in ['USD', 'USDT', 'USD.P']:
            if sym.endswith(suffix):
                base = sym[:-len(suffix)]
                if base in _valid_symbols_cache:
                    return True, base
        return False, sym
    try:
        data = await delta_client.get_candles(sym, "5m", limit=2)
        if len(data.get("close", [])) < 2:
            return False, sym
        return True, sym
    except Exception as e:
        logger.warning("Symbol validation failed for %s: %s", sym, e)
        return False, sym


def _user_symbols(username: str) -> list[str]:
    rows = query("SELECT symbol FROM user_screener_symbols WHERE username = ? ORDER BY symbol", (username,))
    return [r["symbol"] for r in rows]


@router.get("/screener/validate-symbol")
async def validate_screener_symbol(symbol: str = Query(...)):
    valid, resolved = await _validate_symbol(symbol.upper())
    return {"valid": valid, "symbol": resolved}


@router.get("/screener/symbols")
async def get_screener_symbols(current_user: dict | None = Depends(get_current_user_from_cookie)):
    if not current_user:
        return {"symbols": []}
    symbols = _user_symbols(current_user["username"])
    return {"symbols": symbols}


@router.post("/screener/symbols")
async def update_screener_symbols(body: dict, current_user: dict | None = Depends(get_current_user_from_cookie)):
    if not current_user:
        return {"symbols": []}
    username = current_user["username"]
    symbols = body.get("symbols", [])
    if not isinstance(symbols, list):
        return {"symbols": []}
    resolved = []
    invalid = []
    for sym in symbols:
        s = sym.strip().upper()
        if not s:
            continue
        valid, resolved_sym = await _validate_symbol(s)
        if valid:
            resolved.append(resolved_sym)
        else:
            invalid.append(s)
    if invalid:
        raise HTTPException(status_code=400, detail={"invalid_symbols": invalid, "symbols": symbols})
    execute("DELETE FROM user_screener_symbols WHERE username = ?", (username,))
    for sym in resolved:
        execute("INSERT INTO user_screener_symbols (username, symbol) VALUES (?, ?)", (username, sym))
    _screener_cache.pop(username, None)
    _screener_cache_ts.pop(username, None)
    return {"symbols": resolved}


@router.get("/screener/sync-top")
async def sync_screener_top30(current_user: dict | None = Depends(get_current_user_from_cookie)):
    if not current_user:
        return {"symbols": []}
    username = current_user["username"]
    try:
        tickers_raw = await delta_client.get_all_tickers()
        ticker_list = tickers_raw.get("result", []) if isinstance(tickers_raw, dict) else []
    except Exception as e:
        logger.warning("Failed to fetch tickers for sync-top30: %s", e)
        raise HTTPException(status_code=502, detail="Delta Exchange API unavailable — try again")
    if not isinstance(ticker_list, list):
        ticker_list = []
    sorted_by_vol = sorted(
        [t for t in ticker_list if isinstance(t, dict)],
        key=lambda t: float(t.get("turnover_usd", 0) or 0),
        reverse=True,
    )
    top30_raw = [t.get("symbol", "") for t in sorted_by_vol[:30] if t.get("symbol")]
    top30_resolved = []
    for sym in top30_raw:
        try:
            valid, resolved = await _validate_symbol(sym)
            if valid:
                top30_resolved.append(resolved)
        except Exception:
            pass
    current = set(_user_symbols(username))
    top30_set = set(top30_resolved)
    keep = current & top30_set
    to_remove = current - top30_set
    remaining_slots = max(0, 30 - len(current))
    top30_new = [s for s in top30_resolved if s not in current][:remaining_slots]
    final = sorted(top30_set | set(top30_new))
    for sym in to_remove:
        execute("DELETE FROM user_screener_symbols WHERE username = ? AND symbol = ?", (username, sym))
    for sym in final:
        if sym not in current:
            execute("INSERT INTO user_screener_symbols (username, symbol) VALUES (?, ?)", (username, sym))
    _screener_cache.pop(username, None)
    _screener_cache_ts.pop(username, None)
    return {"symbols": final}


def _screener_symbols_for(username: str | None) -> list[str]:
    if username:
        return _user_symbols(username)
    return []


@router.get("/screener")
async def get_screener(
    live: bool = Query(False, description="Fetch live data ([-1]) instead of candle-close ([-2])"),
    current_user: dict | None = Depends(get_current_user_from_cookie),
):
    username = current_user["username"] if current_user else None
    now = time.time()
    cache_key = username or "__anonymous__"
    if not live and now - _screener_cache_ts.get(cache_key, 0) < SCREENER_TTL and cache_key in _screener_cache:
        return _screener_cache[cache_key]

    symbols = _screener_symbols_for(username)
    if not symbols:
        result = {"results": []}
        _screener_cache[cache_key] = result
        _screener_cache_ts[cache_key] = time.time()
        return result

    # Build volume, price, and change maps BEFORE processing symbols
    vol_map = {}
    price_map = {}
    change_map = {}
    try:
        tickers_raw = await delta_client.get_all_tickers()
        if isinstance(tickers_raw, dict):
            ticker_list = tickers_raw.get("result", [])
        elif isinstance(tickers_raw, list):
            ticker_list = tickers_raw
        else:
            ticker_list = []
        if not isinstance(ticker_list, list):
            ticker_list = []
        for t in ticker_list:
            if not isinstance(t, dict):
                continue
            sym = t.get("symbol", "")
            vol = t.get("turnover_usd", 0) or t.get("volume", 0) or 0
            if vol:
                vol_map[sym] = float(vol)
            raw_t = t.get("result", t)
            price = raw_t.get("mark_price", raw_t.get("close", raw_t.get("last", None)))
            if price is not None:
                price_map[sym] = float(price)
            chg = raw_t.get("ltp_change_24h", None)
            if chg is not None:
                change_map[sym] = float(chg)
    except Exception as e:
        logger.warning("Failed to fetch tickers: %s", e)
        pass

    # Fallback: individual ticker lookup for symbols still missing data
    missing = [sym for sym in symbols if sym not in vol_map or vol_map.get(sym, 0) == 0]
    if missing:
        async def _fetch_vol(sym):
            try:
                t = await delta_client.get_ticker(sym)
                if isinstance(t, dict):
                    raw_t = t.get("result", t)
                    vol = raw_t.get("turnover_usd", 0) or raw_t.get("volume", 0) or 0
                    if not vol:
                        nested = t.get("result", {})
                        if isinstance(nested, dict):
                            vol = nested.get("turnover_usd", 0) or nested.get("volume", 0) or 0
                    result = {"sym": sym, "vol": float(vol) if vol else 0}
                    price = raw_t.get("mark_price", raw_t.get("close", raw_t.get("last", None)))
                    if price is not None:
                        result["price"] = float(price)
                    chg = raw_t.get("ltp_change_24h", None)
                    if chg is not None:
                        result["change"] = float(chg)
                    return result
            except Exception as e:
                logger.warning("Failed to fetch volume ticker: %s", e)
                pass
            return {"sym": sym, "vol": 0}
        tick_results = await asyncio.gather(*[_fetch_vol(sym) for sym in missing])
        for r in tick_results:
            if r["vol"]:
                vol_map[r["sym"]] = r["vol"]
            if r.get("price") is not None:
                price_map[r["sym"]] = r["price"]
            if r.get("change") is not None:
                change_map[r["sym"]] = r["change"]

    async def _process_symbol(sym):
        volume = vol_map.get(sym, 0)
        last_price = price_map.get(sym)
        price_change_24h = change_map.get(sym)
        try:
            data_1h = await delta_client.get_candles(sym, "1h", limit=500)
            data_15m = await delta_client.get_candles(sym, "15m", limit=500)
            data_5m = await delta_client.get_candles(sym, "5m", limit=500)
            if len(data_1h["close"]) < 50 or len(data_15m["close"]) < 50 or len(data_5m["close"]) < 50:
                return {"symbol": sym, "signal": "NO TRADE", "state": "-", "volume_24h": volume, "last_price": last_price, "price_change_24h": price_change_24h}

            ema10_1h_arr = ema(data_1h["close"], settings.momentum_len)
            ema20_1h_arr = ema(data_1h["close"], settings.fast_len)
            ema10_15m_arr = ema(data_15m["close"], settings.momentum_len)
            ema20_15m_arr = ema(data_15m["close"], settings.fast_len)
            rsi_15m_arr = rsi_func(data_15m["close"], settings.rsi_len)
            _, _, adx_15m_arr = dmi(
                data_15m["high"], data_15m["low"],
                data_15m["close"], settings.adx_len,
            )
            adx_15m_smooth = ema(adx_15m_arr, 5)

            r_1h_v = 1 if ema10_1h_arr[-1] > ema20_1h_arr[-1] else -1 if ema10_1h_arr[-1] < ema20_1h_arr[-1] else 0
            r_15m_v = 1 if ema10_15m_arr[-1] > ema20_15m_arr[-1] else -1 if ema10_15m_arr[-1] < ema20_15m_arr[-1] else 0
            adx_v = float(adx_15m_smooth[-1]) if not np.isnan(adx_15m_smooth[-1]) else 0
            rsi_v = float(rsi_15m_arr[-1]) if not np.isnan(rsi_15m_arr[-1]) else 50

            base_signal = signal_logic(r_1h_v, r_15m_v, adx_v, rsi_v, volume)

            sig_val = base_signal
            state = "-"
            if sig_val in ("Allowed (Buy)", "Allowed (Sell)"):
                close_5m = data_5m["close"]
                low_5m = data_5m["low"]
                high_5m = data_5m["high"]
                ema10_5m = ema(close_5m, settings.momentum_len)
                ema20_5m = ema(close_5m, settings.fast_len)
                ema50_5m = ema(close_5m, settings.mid_len)
                if len(close_5m) >= 1:
                    is_buy = sig_val == "Allowed (Buy)"
                    c = close_5m[-1]; l = low_5m[-1]; h = high_5m[-1]
                    e10 = ema10_5m[-1]; e20 = ema20_5m[-1]; e50 = ema50_5m[-1]

                    if is_buy and e10 > e20:
                        if c > e10:                                          state = "Active (Buy)"
                        elif (c < e10 or l < e10) and c > e20:               state = "Shallow Pullback (Buy)"
                        elif c < e20 and c > e50:                            state = "Normal Pullback (Buy)"
                        elif c < e50:                                        state = "Deep Pullback (Buy)"
                    elif not is_buy and e10 < e20:
                        if c < e10:                                          state = "Active (Sell)"
                        elif (c > e10 or h > e10) and c < e20:               state = "Shallow Pullback (Sell)"
                        elif c > e20 and c < e50:                            state = "Normal Pullback (Sell)"
                        elif c > e50:                                        state = "Deep Pullback (Sell)"
                return {"symbol": sym, "signal": sig_val, "state": state, "volume_24h": volume, "last_price": last_price, "price_change_24h": price_change_24h}
            return {"symbol": sym, "signal": sig_val, "state": state, "volume_24h": volume, "last_price": last_price, "price_change_24h": price_change_24h}
        except Exception as e:
            return {"symbol": sym, "signal": "NO TRADE", "error": str(e), "state": "-", "volume_24h": volume, "last_price": last_price, "price_change_24h": price_change_24h}

    async def _screener_task(sym):
        async with _SCREENER_SEM:
            return await _process_symbol(sym)
    results = await asyncio.gather(*[_screener_task(sym) for sym in symbols])
    results = list(results)

    result = {"results": results}
    if not live:
        _screener_cache[cache_key] = result
        _screener_cache_ts[cache_key] = time.time()
    return result


@router.get("/screener/prices")
async def get_screener_prices(current_user: dict | None = Depends(get_current_user_from_cookie)):
    """Lightweight endpoint returning latest price/change/volume for user's screener symbols only."""
    if not current_user:
        return {}
    symbols = _user_symbols(current_user["username"])
    if not symbols:
        return {}
    prices = {}
    try:
        tickers_raw = await delta_client.get_all_tickers()
        ticker_list = tickers_raw.get("result", []) if isinstance(tickers_raw, dict) else (tickers_raw if isinstance(tickers_raw, list) else [])
        seen = set()
        for t in ticker_list:
            if not isinstance(t, dict):
                continue
            sym = t.get("symbol", "")
            if sym not in symbols or sym in seen:
                continue
            seen.add(sym)
            raw_t = t.get("result", t)
            price = raw_t.get("mark_price", raw_t.get("close", raw_t.get("last", None)))
            chg = raw_t.get("ltp_change_24h", None)
            vol = t.get("turnover_usd", 0) or t.get("volume", 0) or 0
            prices[sym] = {
                "last_price": float(price) if price is not None else None,
                "price_change_24h": float(chg) if chg is not None else None,
                "volume_24h": float(vol) if vol else 0,
            }
    except Exception as e:
        logger.warning("Failed to fetch screener prices: %s", e)
    missing = [s for s in symbols if s not in prices]
    if missing:
        async def _fetch_one(sym):
            try:
                t = await delta_client.get_ticker(sym)
                if isinstance(t, dict):
                    raw_t = t.get("result", t)
                    price = raw_t.get("mark_price", raw_t.get("close", raw_t.get("last", None)))
                    chg = raw_t.get("ltp_change_24h", None)
                    return sym, {"last_price": float(price) if price is not None else None, "price_change_24h": float(chg) if chg is not None else None}
            except Exception:
                pass
            return sym, {}
        results = await asyncio.gather(*[_fetch_one(s) for s in missing])
        for sym, data in results:
            if data:
                prices[sym] = data
    return prices


@router.get("/analysis/{symbol}/candles")
async def get_candles(
    symbol: str,
    resolution: str = Query("5m"),
    limit: int = Query(200, le=2000),
):
    data = await delta_client.get_candles(symbol, resolution, limit=limit)
    return {
        "time": _to_list(data["time"]),
        "open": _to_list(data["open"]),
        "high": _to_list(data["high"]),
        "low": _to_list(data["low"]),
        "close": _to_list(data["close"]),
        "volume": _to_list(data["volume"]),
    }


@router.get("/ticker/{symbol}")
async def get_ticker_price(symbol: str):
    """Lightweight endpoint returning only last_price and last_close for chart polling."""
    try:
        ticker = await delta_client.get_ticker(symbol)
        raw = ticker.get("result", ticker)
        last_price = float(raw.get("mark_price", raw.get("close", raw.get("last", 0))))
    except Exception as e:
        logger.warning("Failed to fetch ticker price: %s", e)
        last_price = 0
    try:
        data = await delta_client.get_candles(symbol, "5m", limit=1)
        close_arr = data.get("close", [])
        last_close = float(close_arr[-1]) if len(close_arr) > 0 else 0
    except Exception as e:
        logger.warning("Failed to fetch candle close: %s", e)
        last_close = 0
    return {"last_price": last_price, "last_close": last_close}
