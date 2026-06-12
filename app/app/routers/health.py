import logging
import time

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import settings
from app.services.alert_service import check_tunnel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

_START_TIME = time.time()


@router.get("/health")
async def health_check():
    resp = JSONResponse({
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "uptime": int(time.time() - _START_TIME),
    })
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@router.get("/api/health/broker")
async def broker_health():
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{settings.delta_api_base}/products?limit=1")
        if r.is_success:
            return {"status": "ok", "exchange": "Delta India"}
        return {"status": "degraded", "exchange": "Delta India", "reason": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"status": "down", "exchange": "Delta India", "reason": f"{type(e).__name__}: {e}"}


@router.get("/api/health/tunnel")
async def tunnel_health():
    result = await check_tunnel()
    return result
