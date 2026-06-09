import asyncio
import logging
import math
import time
from typing import Dict, Optional

import httpx
import numpy as np

from app.config import settings
from app.database import execute as db_execute
from app.services.telegram_notifier import send_telegram, send_telegram_retry
from app.services.alert_service import parse_order_rejection, report_broker_success, report_broker_failure
from app.data.delta_client import (
    _DELTA_BASE,
    _delta_auth_get,
    _delta_auth_post,
    _delta_order,
    delta_client,
)
from app.engine.signal import signal_logic
from app.indicators.adx import dmi
from app.indicators.atr import atr as atr_func
from app.indicators.ema import ema
from app.indicators.rsi import rsi as rsi_func
from app.indicators.trend import trend_state

logger = logging.getLogger(__name__)

import json as _json

_ALGO_STATE: Dict[tuple[str, str], dict] = {}
_ALGO_TASKS: Dict[tuple[str, str], asyncio.Task] = {}
_TRADE_CREDENTIALS: Dict[tuple[str, str], dict] = {}
_TRADE_SETUP: Dict[tuple[str, str], dict] = {}  # trade setup params per (username, symbol)

_SHARED_CLIENT = httpx.AsyncClient(timeout=15)


def _compute_state(is_buy: bool, close, ema10, ema20, open_arr=None) -> str:
    if len(close) < 3 or len(ema10) < 3 or len(ema20) < 3:
        return "-"
    e10 = ema10[-1]
    e20 = ema20[-1]
    e10_p = ema10[-2]
    e20_p = ema20[-2]
    c = close[-1]
    if is_buy:
        if e10 > e20 and e10_p <= e20_p and c > e10 and (open_arr is None or c > open_arr[-1]):
            return "Ready"
        if e10 > e20 and c > e10:
            return "Active"
        if c < e10:
            return "Pullback"
        return "-"
    else:
        if e10 < e20 and e10_p >= e20_p and c < e10 and (open_arr is None or c < open_arr[-1]):
            return "Ready"
        if e10 < e20 and c < e10:
            return "Active"
        if c > e10:
            return "Pullback"
        return "-"


def _check_exit(is_long: bool, close, ema10, ema20) -> bool:
    if len(close) < 3 or len(ema10) < 3 or len(ema20) < 3:
        return False
    e10 = ema10[-1]
    e20 = ema20[-1]
    c = close[-1]
    if is_long:
        return c < e20
    return c > e20


def _round_to_tick(price: float, tick_size: float) -> float:
    if not tick_size or tick_size <= 0:
        return price
    return round(price / tick_size) * tick_size


async def _place_trade(symbol: str, side: str, qty: float, sl: float, tp: float = None, api_key: str = "", api_secret: str = "") -> dict:
    info = await _fetch_product(symbol)
    if not info:
        return {"success": False, "error": "Product not found"}

    pid = info["id"]
    cv = info["contract_value"]
    entry_qty = max(1, math.floor(qty / cv if cv > 0 else qty))

    entry_payload = {
        "product_id": pid, "size": entry_qty, "side": side,
        "order_type": "market_order", "time_in_force": "gtc",
    }

    tick = info.get("tick_size", 0.01)
    sl_rounded = _round_to_tick(sl, tick)
    tp_rounded = None if tp is None else _round_to_tick(tp, tick)

    bracket_payload = {
        "product_id": pid,
        "stop_loss_order": {"order_type": "market_order", "stop_price": sl_rounded},
        "bracket_stop_trigger_method": "last_traded_price",
    }
    if tp_rounded is not None:
        bracket_payload["take_profit_order"] = {"order_type": "limit_order", "stop_price": tp_rounded, "limit_price": tp_rounded}

    r = await _delta_order(api_key, api_secret, entry_payload)
    body_ok = isinstance(r.get("body"), dict)
    if not (r["status"] == 200 and body_ok and r["body"].get("success", False)):
        err = parse_order_rejection(r.get("body", ""))
        await report_broker_failure(api_key, err, "/orders")
        asyncio.ensure_future(send_telegram_retry(
            f"<b>Algo Order Rejected</b>\n{symbol}\n"
            f"Type: Entry\nSide: {side.upper()}\nQty: {entry_qty}\n"
            f"Reason: {err}"
        ))
        return {"success": False, "error": f"Entry failed: {err}"}

    report_broker_success(api_key)

    r2 = await _delta_auth_post(api_key, api_secret, "/orders/bracket", bracket_payload)
    bracket_ok = r2["status"] in (200, 201) and isinstance(r2.get("body"), dict)
    if not bracket_ok:
        bracket_err = parse_order_rejection(r2.get("body", ""))
        logger.warning("Bracket order failed for %s: %s", symbol, bracket_err)
        asyncio.ensure_future(send_telegram_retry(
            f"<b>Algo Order Rejected</b>\n{symbol}\n"
            f"Type: Bracket (SL/TP)\nReason: {bracket_err}"
        ))
        # Rollback: close the naked entry position
        close_side = "sell" if side == "buy" else "buy"
        await _delta_order(api_key, api_secret, {
            "product_id": pid, "size": entry_qty, "side": close_side,
            "order_type": "market_order", "time_in_force": "gtc", "reduce_only": True,
        })
        asyncio.ensure_future(send_telegram_retry(
            f"<b>Algo Rollback Executed</b>\n{symbol}\n"
            f"Bracket (SL/TP) placement failed — naked position closed."
        ))
        return {"success": False, "error": f"Bracket order failed: {bracket_err}"}

    asyncio.ensure_future(send_telegram_retry(
        f"<b>Algo Trade Executed</b>\n{symbol}\n"
        f"Side: {side.upper()}\nQty: {entry_qty}\n"
        f"SL: {sl:.2f}\nTP: {tp:.2f}"
    ))
    return {"success": True, "entry": r, "bracket": r2, "qty": entry_qty, "product_id": pid}


async def _cancel_orders(api_key: str, api_secret: str, product_id: int):
    """Cancel SL/TP bracket orders for a product_id (used on pause/trail)."""
    r = await _delta_auth_get(api_key, api_secret, "/orders", f"?product_id={product_id}")
    if r["status"] != 200:
        return
    orders = r.get("body", {})
    if isinstance(orders, dict):
        orders = orders.get("result", [])
    if not isinstance(orders, list):
        return
    for o in orders:
        if isinstance(o, dict) and o.get("id"):
            sto = (o.get("stop_order_type") or "").lower()
            ot = (o.get("order_type") or "").lower()
            is_relevant = (
                sto in ("stop_loss_order", "stop_market", "take_profit_limit", "take_profit_market")
                or "stop_loss" in sto
                or "take_profit" in sto
            )
            if is_relevant:
                await _delta_auth_post(api_key, api_secret, "/orders/cancel", {"id": o["id"]})


_PROD_CACHE: Dict[str, dict] = {}
_PROD_CACHE_TS: float = 0

async def _fetch_product(symbol: str) -> Optional[dict]:
    global _PROD_CACHE_TS
    now = time.time()
    if now - _PROD_CACHE_TS > 300:
        try:
            resp = await _SHARED_CLIENT.get(f"{_DELTA_BASE}/v2/products")
            resp.raise_for_status()
            raw = resp.json()
            new_cache = {}
            for p in raw.get("result", []):
                if isinstance(p, dict) and p.get("symbol"):
                    cv = float(p.get("contract_value", "1")) if p.get("contract_value") else 1.0
                    key = p["symbol"].upper()
                    new_cache[key] = {"id": p["id"], "contract_value": cv, "contract_type": p.get("contract_type", ""), "tick_size": float(p.get("tick_size", 0.01)) if p.get("tick_size") else 0.01}
            _PROD_CACHE.clear()
            _PROD_CACHE.update(new_cache)
            _PROD_CACHE_TS = now
        except Exception as e:
            logger.warning("Failed to cache product: %s", e)
    return _PROD_CACHE.get(symbol.upper())


async def _get_position(api_key: str, api_secret: str, symbol: str) -> Optional[dict]:
    """Get current position for a symbol from Delta."""
    # Extract underlying asset (e.g. BTCUSD -> BTC, SOLUSD -> SOL)
    underlying = symbol.upper()
    for sfx in ["USD.P", "USDT", "USD"]:
        if underlying.endswith(sfx):
            underlying = underlying[:-len(sfx)]
            break
    r = await _delta_auth_get(api_key, api_secret, "/positions", "?underlying_asset_symbol=" + underlying)
    if r["status"] != 200:
        return None
    body = r.get("body", {})
    if isinstance(body, dict):
        positions = body.get("result", [])
    elif isinstance(body, list):
        positions = body
    else:
        return None
    for p in positions:
        if isinstance(p, dict) and p.get("symbol", "").upper() == symbol.upper():
            size = float(p.get("size", 0))
            if size != 0:
                return p
    return None


async def _get_account_balance(api_key: str, api_secret: str) -> float:
    """Get available balance from Delta (used for pre-entry check)."""
    try:
        r = await _delta_auth_get(api_key, api_secret, "/users/balances")
        if r["status"] == 200:
            body = r.get("body", {})
            if isinstance(body, dict):
                balances = body.get("result", [])
                for b in balances:
                    if isinstance(b, dict) and b.get("asset_symbol") == "USDT":
                        return float(b.get("available_balance", 0))
            elif isinstance(body, list):
                for b in body:
                    if isinstance(b, dict) and b.get("asset_symbol") == "USDT":
                        return float(b.get("available_balance", 0))
    except Exception as e:
        logger.warning("Failed to fetch balance for %s", e)
    return 0.0


async def _get_open_orders(api_key: str, api_secret: str, symbol: str) -> list:
    """Get all open orders for a symbol."""
    r = await _delta_auth_get(api_key, api_secret, "/orders", "?symbol=" + symbol)
    if r["status"] != 200:
        return []
    body = r.get("body", {})
    if isinstance(body, dict):
        return body.get("result", [])
    if isinstance(body, list):
        return body
    return []




async def _algo_loop(username: str, symbol: str):
    key = (username, symbol)
    state = _ALGO_STATE.get(key, {})
    creds = _TRADE_CREDENTIALS.get(key, {})
    last_candle_time = 0
    _cached_atr = None

    while state.get("active", False):
        try:
            data_1h = await delta_client.get_candles(symbol, "1h", limit=100)
            data_15m = await delta_client.get_candles(symbol, "15m", limit=100)
            data_5m = await delta_client.get_candles(symbol, "5m", limit=100)

            if len(data_5m["close"]) < 50:
                await asyncio.sleep(5)
                continue

            close_5m = data_5m["close"]
            # Fetch live ticker for real-time mark_price and 24h volume
            try:
                ticker = await delta_client.get_ticker(symbol)
                raw = ticker.get("result", ticker)
                live_price = float(raw.get("mark_price", raw.get("close", close_5m[-1])))
                volume_24h = float(raw.get("turnover_usd", raw.get("volume", 0)) or 0)
            except Exception as e:
                logger.warning("Failed to fetch live price for %s: %s", symbol, e)
                live_price = float(close_5m[-1])
                volume_24h = 0
            state["mark_price"] = live_price
            trend_1h_arr = trend_state(data_1h["close"], settings.momentum_len, settings.mid_len)
            trend_15m_arr = trend_state(data_15m["close"], settings.momentum_len, settings.mid_len)
            rsi_15m_arr = rsi_func(data_15m["close"], settings.rsi_len)
            _, _, adx_15m_arr = dmi(data_15m["high"], data_15m["low"], data_15m["close"], settings.adx_len)
            adx_15m_smooth = ema(adx_15m_arr, 5)

            r_1h_v = int(trend_1h_arr[-1]) if len(trend_1h_arr) > 0 else 0
            r_15m_v = int(trend_15m_arr[-1]) if len(trend_15m_arr) > 0 else 0
            adx_v = float(adx_15m_smooth[-1]) if len(adx_15m_smooth) > 0 and not np.isnan(adx_15m_smooth[-1]) else 0
            rsi_v = float(rsi_15m_arr[-1]) if len(rsi_15m_arr) > 0 and not np.isnan(rsi_15m_arr[-1]) else 50

            sig_val = signal_logic(r_1h_v, r_15m_v, adx_v, rsi_v, volume_24h)
            is_buy = sig_val == "Allowed (Buy)"
            is_sell = sig_val == "Allowed (Sell)"
            current_state = "-"

            if is_buy or is_sell:
                ema10_5m = ema(close_5m, settings.momentum_len)
                ema20_5m = ema(close_5m, settings.fast_len)
                current_state = _compute_state(is_buy, close_5m, ema10_5m, ema20_5m, data_5m.get("open"))

            if not is_buy and not is_sell:
                await asyncio.sleep(5)
                continue

            current_candle_time = int(data_5m["time"][-1]) if len(data_5m["time"]) > 0 else 0

            # --- Track Pullback observation: entry only after Pullback → Ready sequence ---
            if not state.get("in_trade"):
                if current_state == "Pullback":
                    state["seen_pullback"] = True

            # --- Cross-source sync: check if position was closed externally ---
            if state.get("in_trade"):
                try:
                    position = await _get_position(creds.get("api_key", ""), creds.get("api_secret", ""), symbol)
                    api_ok = True
                    report_broker_success(creds.get("api_key", ""))
                except Exception as e:
                    logger.warning("Failed to get position for %s: %s", symbol, e)
                    await report_broker_failure(creds.get("api_key", ""), str(e), "/positions")
                    api_ok = False
                    position = None
                if api_ok and position is None:
                    state["in_trade"] = False
                    state["trade_side"] = None
                    state["entry_price"] = None
                    state["sl"] = None
                    state["tp"] = None
                    state["trail_step"] = 0
                    state["seen_pullback"] = False
                    state["hit_2r"] = False

            # --- Entry: Ready or Reclaim at new candle close (only if Pullback was observed) ---
            reclaim_entry = (state.get("seen_pullback") and current_state == "Active"
                             and ((is_buy and len(close_5m) >= 2 and close_5m[-2] < ema8_5m[-2])
                                  or (is_sell and len(close_5m) >= 2 and close_5m[-2] > ema8_5m[-2])))
            if not state.get("in_trade") and state.get("seen_pullback") and (current_state == "Ready" or reclaim_entry):
                if current_candle_time != last_candle_time:
                    entry_side = "buy" if is_buy else "sell"
                    last_close = float(close_5m[-1])

                    # === Cross-symbol validation before entry ===
                    existing_pos = await _get_position(creds.get("api_key", ""), creds.get("api_secret", ""), symbol)
                    if existing_pos:
                        logger.info("Algo entry skipped for %s: position already open on Delta", symbol)
                        continue

                    # --- Use same SL/quantity logic as manual trade setup ---
                    ts = _TRADE_SETUP.get(key, {})
                    sl_type = ts.get("slType", "ATR")
                    atr_mult = float(ts.get("atrMult", 1.6))
                    trade_capital = ts.get("tradeCapital")
                    risk_amount = ts.get("riskAmount")
                    if trade_capital is None or risk_amount is None:
                        logger.info("Algo entry skipped for %s: tradeCapital or riskAmount not set in Trade Setup", symbol)
                        asyncio.ensure_future(send_telegram(
                            f"<b>Algo Entry Skipped</b>\n{symbol}\n"
                            f"Trade Capital or Risk Amount not set in Trade Setup.\n"
                            f"Please save Trade Setup before running Algo."
                        ))
                        continue
                    trade_capital = float(trade_capital)
                    risk_amount = float(risk_amount)

                    if sl_type == "ATR":
                        if _cached_atr is None:
                            atr_vals = atr_func(np.array(data_5m["high"], dtype=float),
                                                np.array(data_5m["low"], dtype=float),
                                                np.array(data_5m["close"], dtype=float), 14)
                            _cached_atr = float(atr_vals[-1]) if len(atr_vals) > 0 and not np.isnan(atr_vals[-1]) else float(np.std(close_5m[-20:]))
                        atr_val = _cached_atr
                        sl_price = last_close - atr_val * atr_mult if is_buy else last_close + atr_val * atr_mult
                    else:
                        last_low = float(data_5m["low"][-1]) if len(data_5m["low"]) > 0 else last_close * 0.99
                        last_high = float(data_5m["high"][-1]) if len(data_5m["high"]) > 0 else last_close * 1.01
                        sl_price = last_low if is_buy else last_high

                    sl_dist = abs(last_close - sl_price)
                    tp_price = last_close + sl_dist * 2 if is_buy else last_close - sl_dist * 2

                    capital = trade_capital
                    risk = risk_amount

                    qty = (risk / sl_dist) if sl_dist > 0 else 1

                    # === Balance & risk validation before entry ===
                    avail_balance = await _get_account_balance(creds.get("api_key", ""), creds.get("api_secret", ""))
                    if avail_balance > 0 and capital > avail_balance:
                        logger.info("Algo entry skipped for %s: capital %.2f > balance %.2f", symbol, capital, avail_balance)
                        asyncio.ensure_future(send_telegram(
                            f"<b>Algo Entry Skipped</b>\n{symbol}\n"
                            f"Capital (${capital:.2f}) exceeds available balance (${avail_balance:.2f}).\n"
                            f"Please reduce Trade Capital in Setup."
                        ))
                        continue
                    if risk > capital:
                        logger.info("Algo entry skipped for %s: risk %.2f > capital %.2f", symbol, risk, capital)
                        asyncio.ensure_future(send_telegram(
                            f"<b>Algo Entry Skipped</b>\n{symbol}\n"
                            f"Risk (${risk:.2f}) exceeds capital (${capital:.2f}).\n"
                            f"Please reduce Risk Amount in Setup."
                        ))
                        continue

                    result = await _place_trade(
                        symbol, entry_side, qty, sl_price,
                        api_key=creds.get("api_key", ""), api_secret=creds.get("api_secret", ""),
                    )
                    if result.get("success"):
                        state["in_trade"] = True
                        state["trade_side"] = entry_side
                        state["entry_price"] = last_close
                        state["sl"] = sl_price
                        state["tp"] = None
                        state["trail_step"] = 0
                        state["r_dist"] = sl_dist
                        state["entry_qty"] = result.get("qty", 0)
                        state["product_id"] = result.get("product_id")
                        state["hit_2r"] = False
                        state["seen_pullback"] = False
                        _persist_algo_state(username, symbol)
                        asyncio.ensure_future(send_telegram(
                            f"<b>Algo Entry</b>\n{symbol}\nSide: {entry_side.upper()}\nPrice: {last_close:.2f}\nSL: {sl_price:.2f}"
                        ))

            # --- Exit: SL hit (intra-candle) ---
            if state.get("in_trade"):
                sl_price = state.get("sl")
                if sl_price is not None:
                    is_long = state.get("trade_side") == "buy"
                    if (is_long and live_price <= sl_price) or (not is_long and live_price >= sl_price):
                        close_side = "sell" if is_long else "buy"
                        pid = state.get("product_id", 0)
                        qty = state.get("entry_qty", 0)
                        if pid and qty:
                            await _cancel_orders(creds.get("api_key", ""), creds.get("api_secret", ""), pid)
                            await _delta_order(creds.get("api_key", ""), creds.get("api_secret", ""), {
                                "product_id": pid, "size": qty, "side": close_side,
                                "order_type": "market_order", "time_in_force": "gtc", "reduce_only": True,
                            })
                        state["in_trade"] = False
                        state["trade_side"] = None
                        state["entry_price"] = None
                        state["sl"] = None
                        state["tp"] = None
                        state["entry_qty"] = 0
                        state["product_id"] = 0
                        state["seen_pullback"] = False
                        state["hit_2r"] = False
                        _persist_algo_state(username, symbol)
                        asyncio.ensure_future(send_telegram(
                            f"<b>Algo SL Hit</b>\n{symbol}\nSide: {close_side.upper()}\nSL: {sl_price}"
                        ))

            # --- Track 2R hit for EMA exit eligibility ---
            if state.get("in_trade"):
                r_dist = state.get("r_dist", 0)
                entry_px = state.get("entry_price", 0)
                if not state.get("hit_2r") and r_dist > 0 and entry_px:
                    is_long = state.get("trade_side") == "buy"
                    two_r_price = entry_px + 2 * r_dist if is_long else entry_px - 2 * r_dist
                    if (is_long and live_price >= two_r_price) or (not is_long and live_price <= two_r_price):
                        state["hit_2r"] = True

            # --- Exit: close past EMA20 (only if 2R was hit) ---
            if state.get("in_trade") and state.get("hit_2r"):
                close_5m_exit = data_5m["close"]
                ema10_exit = ema(close_5m_exit, settings.momentum_len)
                ema20_exit = ema(close_5m_exit, settings.fast_len)
                if len(ema20_exit) > 0:
                    state["exit_price"] = float(ema20_exit[-1])
                is_long = state.get("trade_side") == "buy"
                if current_candle_time != last_candle_time and _check_exit(is_long, close_5m_exit, ema10_exit, ema20_exit):
                    close_side = "sell" if is_long else "buy"
                    pid = state.get("product_id", 0)
                    qty = state.get("entry_qty", 0)
                    if pid and qty:
                        await _cancel_orders(creds.get("api_key", ""), creds.get("api_secret", ""), pid)
                        await _delta_order(creds.get("api_key", ""), creds.get("api_secret", ""), {
                            "product_id": pid, "size": qty, "side": close_side,
                            "order_type": "market_order", "time_in_force": "gtc", "reduce_only": True,
                        })
                    state["in_trade"] = False
                    state["trade_side"] = None
                    state["entry_price"] = None
                    state["sl"] = None
                    state["tp"] = None
                    state["entry_qty"] = 0
                    state["product_id"] = 0
                    state["seen_pullback"] = False
                    state["hit_2r"] = False
                    _persist_algo_state(username, symbol)
                    asyncio.ensure_future(send_telegram(
                        f"<b>Algo Exit</b>\n{symbol}\nSide: {close_side.upper()}\nReason: EMA8/EMA20 crossover"
                    ))


            last_candle_time = current_candle_time
            _cached_atr = None
            await asyncio.sleep(3)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception("Algo loop error for %s: %s", symbol, e)
            await report_broker_failure(creds.get("api_key", ""), str(e), "algo_loop")
            await asyncio.sleep(5)


def get_status(username: str = "") -> dict:
    running = {}
    for (uname, sym), state in _ALGO_STATE.items():
        if username and uname != username:
            continue
        running[sym] = {
            "active": state.get("active", False),
            "in_trade": state.get("in_trade", False),
            "trade_side": state.get("trade_side"),
            "entry_price": state.get("entry_price"),
            "entry_qty": state.get("entry_qty", 0),
            "trail_step": state.get("trail_step", 0),
            "r_dist": state.get("r_dist", 0),
            "margin": state.get("margin", 0),
            "entry_at": state.get("entry_at", 0),
            "sl": state.get("sl"),
            "tp": state.get("tp"),
            "exit_price": state.get("exit_price"),
            "mark_price": state.get("mark_price", 0),
            "last_exit": state.get("last_exit"),
        }
        if running[sym].get("last_exit"):
            del state["last_exit"]
    return running


def start_algo(symbol: str, api_key: str = "", api_secret: str = "", trade_setup: dict = None, username: str = "unknown", trail: bool = False) -> bool:
    key = (username, symbol)
    if key in _ALGO_TASKS and not _ALGO_TASKS[key].done():
        _ALGO_TASKS[key].cancel()
    _TRADE_CREDENTIALS[key] = {"api_key": api_key, "api_secret": api_secret, "username": username}
    _TRADE_SETUP[key] = trade_setup or {}
    _ALGO_STATE[key] = {
        "active": True, "in_trade": False, "trade_side": None,
        "entry_price": None, "sl": None, "tp": None, "trail_step": 0,
        "r_dist": 0, "entry_qty": 0, "product_id": 0,
        "seen_pullback": False,
        "hit_2r": False,
    }
    if trail:
        ts = trade_setup or {}
        state = _ALGO_STATE[key]
        state["in_trade"] = True
        state["seen_pullback"] = True
        state["trade_side"] = ts.get("side")
        state["entry_price"] = ts.get("entry_price")
        state["sl"] = ts.get("sl")
        state["tp"] = ts.get("tp")
        state["entry_qty"] = ts.get("entry_qty", 0)
        state["product_id"] = ts.get("product_id", 0)
        state["r_dist"] = ts.get("sl_dist", 0)
        state["margin"] = ts.get("margin", 0)
        state["entry_at"] = time.time()
    _persist_algo_state(username, symbol)
    _ALGO_TASKS[key] = asyncio.create_task(_algo_loop(username, symbol))
    asyncio.ensure_future(send_telegram(
        f"<b>Algo Started</b>\n{symbol}\nMode: Real"
    ))
    return True


async def pause_algo(username: str, symbol: str) -> bool:
    key = (username, symbol)
    if key in _ALGO_TASKS and not _ALGO_TASKS[key].done():
        _ALGO_TASKS[key].cancel()
    state = _ALGO_STATE.get(key, {})
    creds = _TRADE_CREDENTIALS.get(key, {})

    # If in trade, set TP 2R for exit
    if state.get("in_trade") and creds.get("api_key"):
        side = state.get("trade_side", "buy")
        entry_px = state.get("entry_price", 0)
        sl_px = state.get("sl", 0)
        pid = state.get("product_id", 0)
        qty = state.get("entry_qty", 0)
        if entry_px and sl_px and pid and qty:
            tp_2r = entry_px + 2 * abs(entry_px - sl_px) if side == "buy" else entry_px - 2 * abs(entry_px - sl_px)
            info = await _fetch_product(symbol)
            tick = info.get("tick_size", 0.01) if info else 0.01
            tp_2r = _round_to_tick(tp_2r, tick)
            await _cancel_orders(creds["api_key"], creds["api_secret"], pid)
            bracket_payload = {
                "product_id": pid,
                "bracket_stop_trigger_method": "last_traded_price",
                "take_profit_order": {"order_type": "limit_order", "stop_price": tp_2r, "limit_price": tp_2r},
            }
            await _delta_auth_post(creds["api_key"], creds["api_secret"], "/orders/bracket", bracket_payload)

    # Preserve trade info when pausing (don't discard entry_price, sl, etc.)
    _ALGO_STATE[key] = {"active": False}
    _ALGO_STATE[key].update({k: state.get(k) for k in ("in_trade", "trade_side", "entry_price", "sl", "tp", "exit_price", "entry_qty", "product_id", "trail_step", "r_dist", "margin", "entry_at") if state.get(k) is not None or k == "in_trade"})
    _persist_algo_state(username, symbol)
    asyncio.ensure_future(send_telegram(
        f"<b>Algo Paused</b>\n{symbol}\nIn trade: {state.get('in_trade', False)}"
    ))
    return True


async def pause_all():
    for key in list(_ALGO_STATE.keys()):
        username, symbol = key
        await pause_algo(username, symbol)


def _persist_algo_state(username: str, symbol: str):
    """Save algo state to DB so it survives restarts."""
    key = (username, symbol)
    state = _ALGO_STATE.get(key)
    if state is None:
        return
    state_copy = {k: v for k, v in state.items() if not k.startswith("_")}
    creds = _TRADE_CREDENTIALS.get(key, {})
    setup = _TRADE_SETUP.get(key, {})
    try:
        db_execute(
            "INSERT OR REPLACE INTO algo_state (username, symbol, state_json, credentials_json, setup_json, updated_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (username, symbol, _json.dumps(state_copy), _json.dumps(creds), _json.dumps(setup)),
        )
    except Exception as e:
        logger.warning("Failed to persist algo state for %s/%s: %s", username, symbol, e)


def _restore_algo_state():
    """Restore all algo states from DB and restart loops for in_trade states."""
    from app.database import _DB_PATH, query
    try:
        if _DB_PATH:
            import sqlite3
            conn = sqlite3.connect(str(_DB_PATH))
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT username, symbol, state_json, credentials_json, setup_json FROM algo_state").fetchall()
            conn.close()
        else:
            rows = query("SELECT username, symbol, state_json, credentials_json, setup_json FROM algo_state")
        loop = asyncio.get_event_loop()
        for row in rows:
            uname = row["username"]
            sym = row["symbol"]
            key = (uname, sym)
            state = _json.loads(row["state_json"])
            creds = _json.loads(row["credentials_json"]) if row["credentials_json"] else {}
            setup = _json.loads(row["setup_json"]) if row["setup_json"] else {}
            state["active"] = False
            _ALGO_STATE[key] = state
            _TRADE_CREDENTIALS[key] = creds
            _TRADE_SETUP[key] = setup
            logger.info("Restored algo state for %s/%s (in_trade=%s)", uname, sym, state.get("in_trade"))
            if state.get("in_trade"):
                logger.info("Auto-restarting algo loop for %s/%s", uname, sym)
                state["active"] = True
                _ALGO_TASKS[key] = loop.create_task(_algo_loop(uname, sym))
    except Exception as e:
        logger.warning("Failed to restore algo state: %s", e)
