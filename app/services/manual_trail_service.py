import asyncio
import json
import logging
import math
import time
from typing import Dict, Optional

from app.data.delta_client import _DELTA_BASE, _delta_auth_get, _delta_auth_post
from app.services.telegram_notifier import send_telegram

logger = logging.getLogger(__name__)

_MANUAL_TRAIL_STATE: Dict[tuple, dict] = {}
_MANUAL_TRAIL_TASKS: Dict[tuple, asyncio.Task] = {}
_MANUAL_TRAIL_CREDS: Dict[tuple, dict] = {}

_SHARED_CLIENT = None
_PROD_CACHE: Dict[str, dict] = {}
_PROD_CACHE_TS: float = 0


def _round_to_tick(price: float, tick_size: float) -> float:
    if not tick_size or tick_size <= 0:
        return price
    return round(price / tick_size) * tick_size


async def _fetch_product(symbol: str) -> Optional[dict]:
    global _PROD_CACHE_TS
    now = time.time()
    if now - _PROD_CACHE_TS > 300:
        try:
            from app.data.delta_client import delta_client
            raw = await delta_client.get_products()
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


async def _fetch_ticker(symbol: str) -> Optional[dict]:
    global _SHARED_CLIENT
    if _SHARED_CLIENT is None:
        from app.data.delta_client import delta_client
        _SHARED_CLIENT = delta_client
    try:
        ticker = await _SHARED_CLIENT.get_ticker(symbol)
        raw = ticker.get("result", ticker)
        return raw
    except Exception as e:
        logger.warning("Failed to fetch ticker for %s: %s", symbol, e)
        return None


async def _get_position(api_key: str, api_secret: str, symbol: str) -> Optional[dict]:
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


async def _cancel_manual_orders(api_key: str, api_secret: str, product_id: int):
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


async def _manual_trail_loop(username: str, symbol: str):
    key = (username, symbol)
    state = _MANUAL_TRAIL_STATE.get(key, {})
    creds = _MANUAL_TRAIL_CREDS.get(key, {})

    while state.get("active", False):
        try:
            if not state.get("in_trade"):
                await asyncio.sleep(3)
                continue

            # Check if position was closed externally
            position = await _get_position(creds.get("api_key", ""), creds.get("api_secret", ""), symbol)
            if position is None:
                state["in_trade"] = False
                state["active"] = False
                logger.info("Manual trail %s: position closed, stopping", symbol)
                asyncio.ensure_future(send_telegram(
                    f"<b>Manual Trail Ended</b>\n{symbol}\nPosition closed on exchange"
                ))
                break

            # --- Exit: SL hit (intra-candle) ---
            sl_price = state.get("sl")
            if sl_price is not None and sl_price > 0:
                ticker = await _fetch_ticker(symbol)
                if ticker:
                    live_price = float(ticker.get("mark_price", ticker.get("close", 0)))
                    if live_price > 0:
                        is_long = state.get("trade_side") == "buy"
                        if (is_long and live_price <= sl_price) or (not is_long and live_price >= sl_price):
                            close_side = "sell" if is_long else "buy"
                            pid = state.get("product_id", 0)
                            qty = state.get("entry_qty", 0)
                            if pid and qty:
                                await _cancel_manual_orders(creds.get("api_key", ""), creds.get("api_secret", ""), pid)
                                await _delta_auth_post(creds.get("api_key", ""), creds.get("api_secret", ""), "/orders", {
                                    "product_id": pid, "size": qty, "side": close_side,
                                    "order_type": "market_order", "time_in_force": "gtc", "reduce_only": True,
                                })
                            state["in_trade"] = False
                            state["active"] = False
                            state["hit_2r"] = False
                            asyncio.ensure_future(send_telegram(
                                f"<b>Manual Trail SL Hit</b>\n{symbol}\nSide: {close_side.upper()}\nSL: {sl_price}"
                            ))
                            break

            # --- Track 2R hit for EMA exit eligibility ---
            if state.get("in_trade"):
                r_dist = state.get("r_dist", 0)
                entry_px = state.get("entry_price", 0)
                if not state.get("hit_2r") and r_dist > 0 and entry_px:
                    ticker2 = await _fetch_ticker(symbol)
                    if ticker2:
                        live_px = float(ticker2.get("mark_price", ticker2.get("close", 0)))
                        if live_px > 0:
                            is_long = state.get("trade_side") == "buy"
                            two_r_price = entry_px + 2 * r_dist if is_long else entry_px - 2 * r_dist
                            if (is_long and live_px >= two_r_price) or (not is_long and live_px <= two_r_price):
                                state["hit_2r"] = True

            # Fetch 5m candles for EMA exit check
            from app.data.delta_client import delta_client
            data_5m = await delta_client.get_candles(symbol, "5m", limit=100)
            if len(data_5m["close"]) < 20:
                await asyncio.sleep(3)
                continue

            close_5m = data_5m["close"]
            from app.indicators.ema import ema
            ema8_5m = ema(close_5m, 8)
            ema20_5m = ema(close_5m, 20)
            if len(ema20_5m) > 0:
                state["exit_price"] = float(ema20_5m[-1])

            current_candle_time = int(data_5m["time"][-1]) if len(data_5m["time"]) > 0 else 0
            is_long = state.get("trade_side") == "buy"
            should_exit = False
            if len(close_5m) >= 3 and len(ema8_5m) >= 3 and len(ema20_5m) >= 3 and state.get("hit_2r") and current_candle_time != state.get("last_candle_time"):
                e8 = ema8_5m[-1]
                e20 = ema20_5m[-1]
                c = close_5m[-1]
                if is_long:
                    should_exit = c < e20
                else:
                    should_exit = c > e20

            if should_exit:
                close_side = "sell" if is_long else "buy"
                pid = state.get("product_id", 0)
                qty = state.get("entry_qty", 0)
                if pid and qty:
                    await _cancel_manual_orders(creds.get("api_key", ""), creds.get("api_secret", ""), pid)
                    await _delta_auth_post(creds.get("api_key", ""), creds.get("api_secret", ""), "/orders", {
                        "product_id": pid, "size": qty, "side": close_side,
                        "order_type": "market_order", "time_in_force": "gtc", "reduce_only": True,
                    })
                state["in_trade"] = False
                state["active"] = False
                state["hit_2r"] = False
                asyncio.ensure_future(send_telegram(
                    f"<b>Manual Trail Exit</b>\n{symbol}\nSide: {close_side.upper()}\nReason: EMA8/EMA20 crossover"
                ))
                break

            state["last_candle_time"] = current_candle_time
            await asyncio.sleep(3)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception("Manual trail loop error for %s: %s", symbol, e)
            await asyncio.sleep(5)

    _MANUAL_TRAIL_STATE[key] = state
    if key in _MANUAL_TRAIL_TASKS:
        del _MANUAL_TRAIL_TASKS[key]


def start_manual_trail(username: str, symbol: str, api_key: str, api_secret: str, trade_setup: dict) -> bool:
    key = (username, symbol)
    if key in _MANUAL_TRAIL_TASKS and not _MANUAL_TRAIL_TASKS[key].done():
        _MANUAL_TRAIL_TASKS[key].cancel()
    _MANUAL_TRAIL_CREDS[key] = {"api_key": api_key, "api_secret": api_secret, "username": username}
    ts = trade_setup or {}
    _MANUAL_TRAIL_STATE[key] = {
        "active": True,
        "in_trade": True,
        "trade_side": ts.get("side"),
        "entry_price": ts.get("entry_price", 0),
        "sl": ts.get("sl", 0),
        "tp": None,
        "entry_qty": ts.get("entry_qty", 0),
        "product_id": ts.get("product_id", 0),
        "r_dist": ts.get("sl_dist", 0),
        "margin": ts.get("margin", 0),
        "entry_at": time.time(),
        "hit_2r": False,
        "last_candle_time": 0,
    }
    _MANUAL_TRAIL_TASKS[key] = asyncio.create_task(_manual_trail_loop(username, symbol))
    asyncio.ensure_future(send_telegram(
        f"<b>Manual Trail Started</b>\n{symbol}\nMonitoring EMA8/EMA20 for exit"
    ))
    return True


async def stop_manual_trail(username: str, symbol: str) -> bool:
    key = (username, symbol)
    if key in _MANUAL_TRAIL_TASKS and not _MANUAL_TRAIL_TASKS[key].done():
        _MANUAL_TRAIL_TASKS[key].cancel()
    state = _MANUAL_TRAIL_STATE.get(key, {})
    creds = _MANUAL_TRAIL_CREDS.get(key, {})

    if state.get("in_trade") and creds.get("api_key"):
        side = state.get("trade_side", "buy")
        entry_px = state.get("entry_price", 0)
        sl_px = state.get("sl", 0)
        pid = state.get("product_id", 0)
        if entry_px and sl_px and pid:
            tp_2r = entry_px + 2 * abs(entry_px - sl_px) if side == "buy" else entry_px - 2 * abs(entry_px - sl_px)
            info = await _fetch_product(symbol)
            tick = info.get("tick_size", 0.01) if info else 0.01
            tp_2r = _round_to_tick(tp_2r, tick)
            await _cancel_manual_orders(creds["api_key"], creds["api_secret"], pid)
            bracket_payload = {
                "product_id": pid,
                "bracket_stop_trigger_method": "last_traded_price",
                "take_profit_order": {"order_type": "limit_order", "stop_price": tp_2r, "limit_price": tp_2r},
            }
            await _delta_auth_post(creds["api_key"], creds["api_secret"], "/orders/bracket", bracket_payload)

    state["active"] = False
    state["in_trade"] = False
    asyncio.ensure_future(send_telegram(
        f"<b>Manual Trail Stopped</b>\n{symbol}"
    ))
    return True


async def stop_all_manual_trails():
    for key in list(_MANUAL_TRAIL_STATE.keys()):
        username, symbol = key
        await stop_manual_trail(username, symbol)


def get_manual_trail_status(symbol: str = "") -> dict:
    result = {}
    for (uname, sym), state in _MANUAL_TRAIL_STATE.items():
        if symbol and sym != symbol:
            continue
        result[sym] = {
            "active": state.get("active", False),
            "in_trade": state.get("in_trade", False),
            "trade_side": state.get("trade_side"),
            "entry_price": state.get("entry_price"),
            "exit_price": state.get("exit_price"),
            "sl": state.get("sl"),
            "hit_2r": state.get("hit_2r", False),
            "entry_at": state.get("entry_at"),
        }
    return result
