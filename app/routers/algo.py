import logging

from fastapi import APIRouter, Depends

from app.auth.deps import get_current_user
from app.data.delta_client import delta_client
from app.schemas.algo import (
    AlgoPauseRequest,
    AlgoPricesRequest,
    AlgoStartRequest,
)
from app.services import algo_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/algo", tags=["algo"])


@router.get("/status")
async def algo_status(current_user: dict = Depends(get_current_user)):
    return {"success": True, "status": algo_service.get_status(username=current_user.get("username", ""))}


@router.post("/start")
async def algo_start(req: AlgoStartRequest, current_user: dict = Depends(get_current_user)):
    if req.read_only:
        return {"success": False, "error": "Currently Read only mode is activated"}
    ts = req.trade_setup
    if isinstance(ts, str):
        import json
        try:
            ts = json.loads(ts)
        except Exception as e:
            logger.warning("Failed to parse trade setup JSON: %s", e)
            ts = {}
    ok = algo_service.start_algo(req.symbol, req.api_key, req.api_secret, ts, username=current_user.get("username", "unknown"), trail=req.trail)
    return {"success": ok, "symbol": req.symbol}


@router.post("/pause")
async def algo_pause(req: AlgoPauseRequest, current_user: dict = Depends(get_current_user)):
    ok = await algo_service.pause_algo(current_user.get("username", ""), req.symbol)
    return {"success": ok}


_price_cache: dict[str, float] = {}

@router.post("/prices")
async def algo_prices(req: AlgoPricesRequest, current_user: dict = Depends(get_current_user)):
    prices = {}
    for sym in req.symbols:
        try:
            ticker = await delta_client.get_ticker(sym)
            raw = ticker.get("result", ticker)
            price = float(raw.get("mark_price", raw.get("close", 0)))
            _price_cache[sym] = price
            prices[sym] = price
        except Exception as e:
            logger.warning("Failed to fetch price for %s: %s", sym, e)
            prices[sym] = _price_cache.get(sym)
    return {"success": True, "prices": prices}
