import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.manual_trail_service import start_manual_trail, stop_manual_trail, get_manual_trail_status

logger = logging.getLogger(__name__)

router = APIRouter(tags=["manual_trail"])


class StartTrailRequest(BaseModel):
    symbol: str = ""
    api_key: str = ""
    api_secret: str = ""
    trade_setup: Optional[dict] = None


class StopTrailRequest(BaseModel):
    symbol: str = ""


class TrailStatusRequest(BaseModel):
    symbol: str = ""


@router.post("/api/trade/trail/start")
async def api_start_trail(req: StartTrailRequest):
    if not all([req.symbol, req.api_key, req.api_secret]):
        return {"success": False, "error": "Missing required fields"}
    ok = start_manual_trail("unknown", req.symbol, req.api_key, req.api_secret, req.trade_setup or {})
    return {"success": ok}


@router.post("/api/trade/trail/stop")
async def api_stop_trail(req: StopTrailRequest):
    if not req.symbol:
        return {"success": False, "error": "Missing symbol"}
    ok = await stop_manual_trail("unknown", req.symbol)
    return {"success": ok}


@router.post("/api/trade/trail/status")
async def api_trail_status(req: TrailStatusRequest):
    return {"success": True, "trails": get_manual_trail_status(req.symbol)}
