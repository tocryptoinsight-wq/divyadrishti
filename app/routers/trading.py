import asyncio
import datetime
import logging
import math
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.deps import get_current_user
from app.database import execute, query
from app.data.delta_client import _DELTA_BASE, _delta_auth_get, _delta_auth_post, _delta_order
from app.services.telegram_notifier import send_telegram, send_telegram_retry
from app.services.algo_service import pause_all
from app.services.alert_service import parse_order_rejection, report_broker_success, report_broker_failure
from app.schemas.trading import (
    BalanceRequest,
    CancelOrdersRequest,
    ClosePositionRequest,
    ExecuteTradeRequest,
    OpenOrdersRequest,
    PositionsRequest,
    TradeHistoryRequest,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')

router = APIRouter(tags=["trading"])

_SHARED_CLIENT = httpx.AsyncClient(timeout=15)
_PRODUCT_CACHE = {}
_PRODUCT_CACHE_TTL = 0
_POSITION_CACHE = {}
_POSITION_CACHE_TTL = 0
_TRADE_INITIAL_SL = {}  # symbol -> initial SL for API-sourced trades


async def _load_products():
    global _PRODUCT_CACHE, _PRODUCT_CACHE_TTL
    try:
        resp = await _SHARED_CLIENT.get(f"{_DELTA_BASE}/v2/products")
        if resp.status_code != 200:
            return
        raw = resp.json()
        cache = {}
        if isinstance(raw, dict):
            for p in raw.get("result", []):
                if isinstance(p, dict):
                    sym = p.get("symbol", "")
                    pid = p.get("id")
                    cv = p.get("contract_value", "1")
                    if sym and pid:
                        cv_float = float(cv) if cv else 1.0
                        ct = p.get("contract_type", "")
                        entry = {"id": pid, "contract_value": cv_float, "contract_type": ct}
                        key = sym.upper()
                        if key not in cache:
                            cache[key] = entry
                        if ct == "perpetual_futures":
                            alt = p.get("underlying_asset", {})
                            if isinstance(alt, dict):
                                alt_sym = alt.get("symbol", "")
                                if alt_sym:
                                    for sfx in ("USD", "USDT"):
                                        alias = alt_sym.upper() + sfx
                                        if alias not in cache:
                                            cache[alias] = dict(entry)
        _PRODUCT_CACHE = cache
        _PRODUCT_CACHE_TTL = time.time() + 300
    except Exception as e:
        logger.warning("Failed to load products: %s", e)
        pass


async def _product_info(symbol: str) -> dict | None:
    global _PRODUCT_CACHE, _PRODUCT_CACHE_TTL
    now = time.time()
    if now > _PRODUCT_CACHE_TTL:
        await _load_products()
    return _PRODUCT_CACHE.get(symbol.upper())


@router.get("/api/trade/check-product")
async def check_product(symbol: str = ""):
    try:
        info = await _product_info(symbol.upper()) if symbol else None
        return {
            "symbol": symbol.upper() if symbol else "",
            "product_id": info["id"] if info else None,
            "contract_value": info["contract_value"] if info else None,
            "cache_size": len(_PRODUCT_CACHE),
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


@router.get("/api/trade/check-api")
async def check_api(api_key: str = "", api_secret: str = ""):
    if not api_key or not api_secret:
        return {"valid": False, "error": "Missing credentials"}
    try:
        # /v2/account does not exist on Delta India; use /wallet/balances instead
        r = await _delta_auth_get(api_key, api_secret, "/wallet/balances")
        if r["status"] == 200 and r["body"].get("success") is not False:
            return {"valid": True}
        err = r["body"]
        if isinstance(err, dict):
            err = err.get("error", {})
            if isinstance(err, dict):
                err = err.get("message", str(err))
        return {"valid": False, "error": str(err)}
    except Exception as e:
        return {"valid": False, "error": f"{type(e).__name__}: {e}"}


@router.get("/api/trade/keys")
async def get_api_keys(current_user: dict = Depends(get_current_user)):
    username = current_user["username"]
    rows = query("SELECT id, api_key, api_secret, type FROM user_api_keys WHERE username = ? ORDER BY id", (username,))
    return {"keys": [{"id": r["id"], "api_key": r["api_key"], "api_secret": r["api_secret"], "type": r["type"]} for r in rows]}


@router.post("/api/trade/keys")
async def add_api_key(body: dict, current_user: dict = Depends(get_current_user)):
    username = current_user["username"]
    api_key = body.get("api_key", "").strip()
    api_secret = body.get("api_secret", "").strip()
    key_type = body.get("type", "read")
    if not api_key or not api_secret:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="API key and secret required")
    new_id = execute("INSERT INTO user_api_keys (username, api_key, api_secret, type) VALUES (?, ?, ?, ?)", (username, api_key, api_secret, key_type))
    return {"success": True, "id": new_id}


@router.delete("/api/trade/keys/{key_id}")
async def delete_api_key(key_id: int, current_user: dict = Depends(get_current_user)):
    username = current_user["username"]
    execute("DELETE FROM user_api_keys WHERE id = ? AND username = ?", (key_id, username))
    return {"success": True}


@router.get("/api/trade/contract-details")
async def contract_details(symbol: str = ""):
    if not symbol:
        return {"error": "symbol required"}
    info = await _product_info(symbol.upper())
    if not info:
        return {"error": f"Symbol '{symbol}' not found"}
    return {
        "symbol": symbol.upper(),
        "product_id": info["id"],
        "contract_value": info["contract_value"],
    }


@router.post("/api/trade/balance")
async def get_balance(req: BalanceRequest):
    api_key = req.api_key
    api_secret = req.api_secret
    try:
        r = await _delta_auth_get(api_key, api_secret, "/wallet/balances")
        if r["status"] != 200 or (isinstance(r["body"], dict) and r["body"].get("success") is False):
            return {"success": False, "error": f"API error: {r['body']}"}
        body = r["body"]
        if isinstance(body, dict):
            result = body.get("result", []) if "result" in body else body
        else:
            result = body
        return {"success": True, "balances": result}
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


@router.post("/api/trade/positions")
async def get_positions(req: PositionsRequest):
    global _POSITION_CACHE, _POSITION_CACHE_TTL
    api_key = req.api_key
    api_secret = req.api_secret
    underlying = req.underlying_asset_symbol
    cache_key = f"{api_key}:{underlying or '*'}"
    now = time.time()
    if now < _POSITION_CACHE_TTL and cache_key in _POSITION_CACHE:
        return _POSITION_CACHE[cache_key]

    async def _enrich_cv(positions: list) -> list:
        """Add contract_value field to each position."""
        enriched = []
        for p in positions:
            sym = (p.get("symbol") or "").upper()
            info = await _product_info(sym) if sym else None
            p["contract_value"] = info["contract_value"] if info else 1.0
            enriched.append(p)
        return enriched

    # Delta requires one of: product_id or underlying_asset_symbol
    if underlying:
        r = await _delta_auth_get(api_key, api_secret, "/positions", "?underlying_asset_symbol=" + underlying)
        if r["status"] != 200:
            return {"success": False, "error": f"API error: {r['body']}"}
        body = r["body"]
        if isinstance(body, dict):
            positions = body.get("result", [])
        elif isinstance(body, list):
            positions = body
        else:
            positions = []
        positions = await _enrich_cv(positions)
        result = {"success": True, "positions": positions}
        _POSITION_CACHE[cache_key] = result
        _POSITION_CACHE_TTL = now + 5
        return result
    # No symbol specified: query all common underlying assets concurrently
    common = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "DOT", "LINK", "BNB", "LTC", "TRX", "ATOM", "APT", "ARB", "OP", "MATIC", "NEAR", "FIL", "AAVE", "SUSHI", "UNI", "PEPE", "INJ", "TIA", "SUI", "SEI", "FET", "RNDR", "TAO", "RUNE", "MKR", "ALGO", "EGLD", "FLOW", "AXS", "SAND", "CHZ", "XLM", "VET", "THETA", "GRT", "ICP", "FTM", "CRV"]
    async def _fetch_asset(asset):
        r = await _delta_auth_get(api_key, api_secret, "/positions", "?underlying_asset_symbol=" + asset)
        if r["status"] != 200:
            return []
        body = r["body"]
        if isinstance(body, dict):
            return body.get("result", [])
        if isinstance(body, list):
            return body
        return []
    results = await asyncio.gather(*[_fetch_asset(a) for a in common], return_exceptions=True)
    results = [r if not isinstance(r, Exception) else [] for r in results]
    all_positions = [p for batch in results for p in batch]
    all_positions = await _enrich_cv(all_positions)
    result = {"success": True, "positions": all_positions}
    _POSITION_CACHE[cache_key] = result
    _POSITION_CACHE_TTL = now + 5
    return result


@router.post("/api/trade/history")
async def get_trade_history(req: TradeHistoryRequest):
    api_key = req.api_key
    api_secret = req.api_secret
    limit = min(req.limit, 50)
    r = await _delta_auth_get(api_key, api_secret, "/fills", f"?page_size={limit * 3}")
    if r["status"] != 200:
        return {"success": False, "error": f"API error: {r['body']}"}
    body = r["body"]
    if isinstance(body, dict):
        fills = body.get("result", [])
    elif isinstance(body, list):
        fills = body
    else:
        fills = []

    # Group fills by product_symbol
    by_product = {}
    for f in fills:
        if not isinstance(f, dict):
            continue
        sym = f.get("product_symbol", "") or ""
        if not sym:
            continue
        by_product.setdefault(sym, []).append(f)

    trades = []
    for sym, sym_fills in by_product.items():
        sym_fills.sort(key=lambda x: x.get("created_at", ""))

        # Running position state across fills for this symbol
        pos_size = 0  # signed position size before current fill
        prev_rpnl = 0.0  # cumulative realized PnL before current fill
        ct = None  # current in-progress trade dict
        last_order_price = None

        for f in sym_fills:
            ts_raw = f.get("created_at", "")
            ts = int(datetime.datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp() * 1000) if ts_raw else 0
            side = f.get("side", "buy")
            size = abs(float(f.get("size", 0)))
            price = float(f.get("price", 0))
            fee = float(f.get("commission", 0)) or 0
            meta = f.get("meta_data", {}) or {}
            new_pos = meta.get("new_position", {}) or {}
            source = meta.get("source", "api") or "api"

            op = meta.get("order_price")
            if op is not None:
                last_order_price = float(op)
            sp = f.get("stop_price") or meta.get("stop_price")
            if sp is not None:
                last_order_price = float(sp)

            # Position size and realized PnL after this fill
            ns_raw = new_pos.get("size")
            new_size = float(ns_raw) if ns_raw is not None else (pos_size + (size if side == "buy" else -size))
            rpnl_raw = new_pos.get("realized_pnl")
            rpnl_available = rpnl_raw is not None
            curr_rpnl = float(rpnl_raw) if rpnl_available else prev_rpnl

            fill_delta = size if side == "buy" else -size
            fill_pnl = curr_rpnl - prev_rpnl

            if pos_size == 0 and new_size != 0:
                # --- Position opened (flat -> long/short) ---
                ct = {
                    "entry_price": price,
                    "entry_total": price * size,
                    "entry_size": size,
                    "entry_fee": fee,
                    "entry_ts": ts,
                    "exit_price": 0.0,
                    "exit_total": 0.0,
                    "exit_size": 0.0,
                    "exit_fee": 0.0,
                    "pnl": 0.0,
                    "side": side,
                    "source": source,
                    "sl": last_order_price,
                }

            elif pos_size != 0 and new_size == 0:
                # --- Position fully closed (long/short -> flat) ---
                if ct is not None:
                    # This fill is the exit — accumulate
                    ct["exit_total"] += price * size
                    ct["exit_size"] += size
                    ct["exit_fee"] += fee
                    if rpnl_available:
                        ct["pnl"] += fill_pnl
                    else:
                        avg_for_pnl = ct["entry_total"] / ct["entry_size"] if ct["entry_size"] > 0 else price
                        if ct["side"] == "buy":
                            ct["pnl"] += (price - avg_for_pnl) * size
                        else:
                            ct["pnl"] += (avg_for_pnl - price) * size

                    avg_entry = ct["entry_total"] / ct["entry_size"] if ct["entry_size"] > 0 else price
                    avg_exit = ct["exit_total"] / ct["exit_size"] if ct["exit_size"] > 0 else 0.0

                    sl_price = ct["sl"]
                    if ct.get("source") == "api" and (not sl_price or (avg_entry > 0 and abs(sl_price - avg_entry) / max(abs(avg_entry), 1e-8) < 1e-8)):
                        sl_price = _TRADE_INITIAL_SL.get(sym, sl_price)
                    rr = None
                    if sl_price and sl_price > 0 and avg_entry > 0:
                        r_dist = abs(avg_entry - sl_price)
                        if r_dist > 0:
                            if ct["side"] == "buy":
                                rr = round((avg_exit - avg_entry) / r_dist, 2)
                            else:
                                rr = round((avg_entry - avg_exit) / r_dist, 2)

                    trades.append({
                        "symbol": sym,
                        "side": ct["side"],
                        "size": ct["entry_size"],
                        "entry_price": round(avg_entry, 2),
                        "exit_price": round(avg_exit, 2),
                        "sl": sl_price,
                        "pnl": round(ct["pnl"], 2),
                        "rr": rr,
                        "fee": round(ct["entry_fee"] + ct["exit_fee"], 4),
                        "source": ct["source"],
                        "entry_ts": ct["entry_ts"],
                        "exit_ts": ts,
                    })
                ct = None

            elif pos_size != 0 and new_size != 0 and (new_size > 0) != (pos_size > 0):
                # --- Direction flip (long -> short or short -> long) ---
                close_size = abs(pos_size)
                open_size = abs(new_size)
                total_fill_size = size  # total contracts in this fill

                if ct is not None:
                    # Close portion: price improvement from old position
                    close_ratio = close_size / total_fill_size if total_fill_size > 0 else 1.0
                    ct["exit_total"] += price * close_size
                    ct["exit_size"] += close_size
                    ct["exit_fee"] += fee * close_ratio
                    if rpnl_available:
                        ct["pnl"] += fill_pnl  # This fill's entire PnL is from closing
                    else:
                        avg_for_pnl = ct["entry_total"] / ct["entry_size"] if ct["entry_size"] > 0 else price
                        if ct["side"] == "buy":
                            ct["pnl"] += (price - avg_for_pnl) * close_size
                        else:
                            ct["pnl"] += (avg_for_pnl - price) * close_size
                    # Discount the open portion from PnL (over-counted by Delta)
                    # Delta reports realized PnL as the total, which includes closing old
                    # and opening new; but opening new shouldn't generate PnL.
                    # We use the full fill_pnl for close portion.

                    avg_entry = ct["entry_total"] / ct["entry_size"] if ct["entry_size"] > 0 else price
                    avg_exit = ct["exit_total"] / ct["exit_size"] if ct["exit_size"] > 0 else 0.0

                    sl_price = ct["sl"]
                    if ct.get("source") == "api" and (not sl_price or (avg_entry > 0 and abs(sl_price - avg_entry) / max(abs(avg_entry), 1e-8) < 1e-8)):
                        sl_price = _TRADE_INITIAL_SL.get(sym, sl_price)
                    rr = None
                    if sl_price and sl_price > 0 and avg_entry > 0:
                        r_dist = abs(avg_entry - sl_price)
                        if r_dist > 0:
                            if ct["side"] == "buy":
                                rr = round((avg_exit - avg_entry) / r_dist, 2)
                            else:
                                rr = round((avg_entry - avg_exit) / r_dist, 2)

                    trades.append({
                        "symbol": sym,
                        "side": ct["side"],
                        "size": ct["entry_size"],
                        "entry_price": round(avg_entry, 2),
                        "exit_price": round(avg_exit, 2),
                        "sl": sl_price,
                        "pnl": round(ct["pnl"], 2),
                        "rr": rr,
                        "fee": round(ct["entry_fee"] + ct["exit_fee"], 4),
                        "source": ct["source"],
                        "entry_ts": ct["entry_ts"],
                        "exit_ts": ts,
                    })

                # Open new trade in opposite direction with remainder
                open_ratio = open_size / total_fill_size if total_fill_size > 0 else 0
                ct = {
                    "entry_price": price,
                    "entry_total": price * open_size,
                    "entry_size": open_size,
                    "entry_fee": fee * open_ratio,
                    "entry_ts": ts,
                    "exit_price": 0.0,
                    "exit_total": 0.0,
                    "exit_size": 0.0,
                    "exit_fee": 0.0,
                    "pnl": 0.0,
                    "side": "buy" if new_size > 0 else "sell",
                    "source": source,
                    "sl": last_order_price,
                }

            pos_size = new_size
            prev_rpnl = curr_rpnl

        # After processing all fills, emit any still-open position
        if ct is not None and ct["entry_size"] > 0:
            avg_entry = ct["entry_total"] / ct["entry_size"]

            trades.append({
                "symbol": sym,
                "side": ct["side"],
                "size": ct["entry_size"],
                "entry_price": round(avg_entry, 2),
                "exit_price": 0.0,
                "sl": ct["sl"],
                "pnl": 0.0,
                "rr": None,
                "fee": round(ct["entry_fee"], 4),
                "source": ct["source"],
                "entry_ts": ct["entry_ts"],
                "exit_ts": 0,
            })

    trades.sort(key=lambda t: t["exit_ts"] or t["entry_ts"], reverse=True)
    return {"success": True, "trades": trades[:limit]}


@router.post("/api/trade/open-orders")
async def get_open_orders(req: OpenOrdersRequest):
    api_key = req.api_key
    api_secret = req.api_secret
    symbol = req.symbol
    qs = f"?symbol={symbol}" if symbol else ""
    r = await _delta_auth_get(api_key, api_secret, "/orders", qs)
    if r["status"] != 200:
        return {"success": False, "error": f"API error: {r['body']}"}
    body = r["body"]
    if isinstance(body, dict):
        orders = body.get("result", [])
    elif isinstance(body, list):
        orders = body
    else:
        orders = []
    return {"success": True, "orders": orders}


@router.post("/api/trade/close-position")
async def close_position(req: ClosePositionRequest):
    if req.read_only:
        return {"success": False, "error": "Currently Read only mode is activated"}
    api_key = req.api_key
    api_secret = req.api_secret
    symbol = req.symbol
    size = req.size
    if not symbol or size == 0:
        return {"success": False, "error": "Symbol and non-zero size required"}
    try:
        # Get product ID for this symbol
        info = await _product_info(symbol.upper())
        if not info:
            return {"success": False, "error": f"Symbol '{symbol}' not found"}
        pid = info["id"]
        side = "sell" if size > 0 else "buy"
        abs_size = abs(size)
        payload = {
            "product_id": pid,
            "size": abs_size,
            "side": side,
            "order_type": "market_order",
            "time_in_force": "gtc",
            "reduce_only": True,
        }
        r = await _delta_order(api_key, api_secret, payload)
        ok = r["status"] == 200 and isinstance(r.get("body"), dict) and r["body"].get("success", False)
        if ok:
            asyncio.ensure_future(send_telegram(
                f"<b>Position Closed</b>\n{symbol}\nSize: {abs_size}\nSide: {side.upper()}"
            ))
        return {"success": ok, "result": r["body"] if ok else r}
    except Exception as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


@router.post("/api/trade/cancel-orders")
async def cancel_all_orders(req: CancelOrdersRequest):
    if req.read_only:
        return {"success": False, "error": "Currently Read only mode is activated"}
    api_key = req.api_key
    api_secret = req.api_secret
    if not api_key or not api_secret:
        return {"success": False, "error": "Missing credentials"}
    symbol = req.symbol
    qs = f"?symbol={symbol}" if symbol else ""
    r = await _delta_auth_get(api_key, api_secret, "/orders", qs)
    if r["status"] != 200:
        return {"success": False, "error": f"API error: {r['body']}"}
    body = r["body"]
    orders = body.get("result", []) if isinstance(body, dict) else (body if isinstance(body, list) else [])
    cancelled = []
    for o in orders:
        if isinstance(o, dict) and o.get("id"):
            cr = await _delta_auth_post(api_key, api_secret, "/orders/cancel", {"id": o["id"]})
            cancelled.append({"id": o["id"], "ok": cr["status"] == 200})
    return {"success": True, "cancelled": len(cancelled)}


@router.post("/api/trade/execute")
async def execute_trade(req: ExecuteTradeRequest):
    if req.read_only:
        return {"success": False, "error": "Currently Read only mode is activated"}
    try:
        api_key = req.api_key
        api_secret = req.api_secret
        symbol = req.symbol
        side = req.side
        sl_in = req.sl
        tp_in = req.tp
        qty = req.qty
        entry_planned = req.entry
        sl_dist = req.sl_dist
        dry_run = req.dry_run

        if not all([api_key, api_secret, qty]):
            return {"success": False, "error": "Missing required fields"}

        raw_qty = float(qty)
        if math.isnan(raw_qty) or raw_qty <= 0:
            return {"success": False, "error": "Invalid quantity"}

        info = await _product_info(symbol)
        if not info:
            return {"success": False, "error": f"Symbol '{symbol}' not found on Delta"}

        if info.get("contract_type") != "perpetual_futures":
            return {"success": False, "error": f"Only perpetual futures supported, got '{info.get('contract_type')}'"}

        pid = info["id"]
        contract_value = info["contract_value"]

        entry_qty = raw_qty / contract_value if contract_value > 0 else raw_qty
        entry_qty = math.floor(entry_qty)
        entry_qty = max(entry_qty, 1)

        sl_side = "sell" if side == "buy" else "buy"

        entry_payload = {
            "product_id": pid,
            "size": entry_qty,
            "side": side,
            "order_type": "market_order",
            "time_in_force": "gtc",
        }

        # dry-run: return payloads without API calls
        if dry_run:
            payloads = [{"label": "Entry", "payload": entry_payload}]
            if sl_in is not None or tp_in is not None:
                bracket_payload = {
                    "product_id": pid,
                    "bracket_stop_trigger_method": "last_traded_price",
                }
                if sl_in is not None:
                    bracket_payload["stop_loss_order"] = {
                        "order_type": "market_order",
                        "stop_price": sl_in,
                    }
                if tp_in is not None:
                    bracket_payload["take_profit_order"] = {
                        "order_type": "limit_order",
                        "stop_price": tp_in,
                        "limit_price": tp_in,
                    }
                payloads.append({"label": "Bracket SL/TP", "payload": bracket_payload})
            return {
                "success": True,
                "dry_run": True,
                "payloads": payloads,
                "sent_qty": entry_qty,
                "product_id": pid,
                "contract_value": contract_value,
            }

        results = []

        # Step 1: Place entry order only (no SL/TP upfront)
        r = await _delta_order(api_key, api_secret, entry_payload)
        r["label"] = "Entry"
        results.append(r)
        logger.info(f"Entry response for {symbol}: status={r['status']}, body={r.get('body')}")

        body_ok = isinstance(r["body"], dict)
        entry_ok = r["status"] == 200 and body_ok and r["body"].get("success", False)

        if not entry_ok:
            err_detail = parse_order_rejection(r["body"])
            await report_broker_failure(api_key, err_detail, "/orders")
            asyncio.ensure_future(send_telegram_retry(
                f"<b>Order Rejected</b>\n{symbol}\n"
                f"Side: {side.upper()}\nQty: {entry_qty}\n"
                f"Reason: {err_detail}"
            ))
            return {
                "success": False,
                "error": f"Entry failed: {err_detail}",
                "results": results,
                "sent_qty": entry_qty,
                "product_id": pid,
                "contract_value": contract_value,
            }

        report_broker_success(api_key)

        # Step 2: Place bracket order with SL/TP
        bracket_ok = True
        if sl_in is not None or tp_in is not None:
            bracket_payload = {
                "product_id": pid,
                "bracket_stop_trigger_method": "last_traded_price",
            }
            if sl_in is not None:
                bracket_payload["stop_loss_order"] = {
                    "order_type": "market_order",
                    "stop_price": sl_in,
                }
            if tp_in is not None:
                bracket_payload["take_profit_order"] = {
                    "order_type": "limit_order",
                    "stop_price": tp_in,
                    "limit_price": tp_in,
                }
            logger.info(f"Bracket payload for {symbol}: {bracket_payload}")
            r_bracket = await _delta_auth_post(api_key, api_secret, "/orders/bracket", bracket_payload)
            r_bracket["label"] = "Bracket (SL/TP)"
            results.append(r_bracket)
            logger.warning(f"Bracket response for {symbol}: status={r_bracket['status']}, body={r_bracket.get('body')}")
            bracket_ok = r_bracket["status"] in (200, 201) and isinstance(r_bracket.get("body"), dict)
            logger.warning(f"bracket_ok={bracket_ok} for {symbol}")
            if not bracket_ok:
                rejection = parse_order_rejection(r_bracket.get("body", ""))
                logger.error(f"Bracket FAILED for {symbol}: rejection='{rejection}', full response: status={r_bracket['status']}, body={r_bracket.get('body')}")
                asyncio.ensure_future(send_telegram_retry(
                    f"<b>Order Rejected</b>\n{symbol}\n"
                    f"Type: Bracket (SL/TP)\nQty: {entry_qty}\n"
                    f"Reason: {rejection}"
                ))
                # Rollback: close position to avoid naked trade
                close_side = "sell" if side == "buy" else "buy"
                await _delta_order(api_key, api_secret, {
                    "product_id": pid, "size": entry_qty, "side": close_side,
                    "order_type": "market_order", "time_in_force": "gtc", "reduce_only": True,
                })
                asyncio.ensure_future(send_telegram_retry(
                    f"<b>Rollback Executed</b>\n{symbol}\n"
                    f"Bracket (SL/TP) placement failed — position closed to prevent naked trade."
                ))
                return {
                    "success": False,
                    "error": f"Bracket order failed: {rejection}",
                    "results": results,
                    "sent_qty": entry_qty,
                    "product_id": pid,
                    "contract_value": contract_value,
                }

        # Get actual entry price from order response (for reporting)
        order_result = r["body"].get("result", {}) if isinstance(r["body"], dict) else {}
        actual_entry = None
        if isinstance(order_result, dict):
            actual_entry = (
                _parse_float(order_result.get("average_filled_price"))
                or _parse_float(order_result.get("price"))
                or _parse_float(order_result.get("filled_price"))
            )

        if bracket_ok and sl_in is not None:
            _TRADE_INITIAL_SL[symbol] = sl_in

        logger.info(f"Trade execution result for {symbol}: bracket_ok={bracket_ok}, entry_ok={entry_ok}, actual_entry={actual_entry}")

        ret = {
            "success": True,
            "results": results,
            "sent_qty": entry_qty,
            "actual_entry": actual_entry or 0,
            "product_id": pid,
        }
        asyncio.ensure_future(send_telegram(
            f"<b>Manual Trade Executed</b>\n{symbol}\nSide: {side.upper()}\nQty: {entry_qty}\nEntry: {actual_entry or 0:.2f}"
        ))
        return ret
    except Exception as e:
        return {"success": False, "error": f"Server error: {type(e).__name__}: {e}"}


@router.post("/api/trade/emergency-kill")
async def emergency_kill(req: CancelOrdersRequest):
    if req.read_only:
        return {"success": False, "error": "Currently Read only mode is activated"}
    api_key = req.api_key
    api_secret = req.api_secret
    if not api_key or not api_secret:
        return {"success": False, "error": "Missing credentials"}
    errors = []
    closed = 0
    cancelled = 0

    await pause_all()

    r = await _delta_auth_get(api_key, api_secret, "/positions")
    if r["status"] == 200:
        body = r["body"]
        positions = body.get("result", []) if isinstance(body, dict) else (body if isinstance(body, list) else [])
        for p in positions:
            pid = p.get("product_id")
            size = float(p.get("size", 0))
            if pid and size != 0:
                side = "sell" if size > 0 else "buy"
                payload = {
                    "product_id": pid,
                    "size": abs(size),
                    "side": side,
                    "order_type": "market_order",
                    "time_in_force": "gtc",
                    "reduce_only": True,
                }
                cr = await _delta_order(api_key, api_secret, payload)
                if cr["status"] == 200:
                    closed += 1
                else:
                    errors.append(f"close {p.get('symbol', pid)}: {cr.get('body', cr)}")

    r2 = await _delta_auth_get(api_key, api_secret, "/orders")
    if r2["status"] == 200:
        body = r2["body"]
        orders = body.get("result", []) if isinstance(body, dict) else (body if isinstance(body, list) else [])
        for o in orders:
            oid = o.get("id")
            if oid:
                cr = await _delta_auth_post(api_key, api_secret, "/orders/cancel", {"id": oid})
                if cr["status"] == 200:
                    cancelled += 1

    asyncio.ensure_future(send_telegram(
        f"<b>EMERGENCY KILL ACTIVATED</b>\n"
        f"Positions closed: {closed}\n"
        f"Orders cancelled: {cancelled}"
    ))

    return {
        "success": True,
        "positions_closed": closed,
        "orders_cancelled": cancelled,
        "errors": errors,
    }


def _order_failed(resp: dict) -> str | None:
    if resp["status"] == 200 and isinstance(resp["body"], dict) and resp["body"].get("success", False):
        return None
    err = resp.get("body", "")
    if isinstance(err, dict):
        err = err.get("error", {})
        if isinstance(err, dict):
            err = err.get("message", str(err))
    return str(err)


def _parse_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
