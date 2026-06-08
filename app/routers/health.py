import asyncio
import logging
import sys
import time

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import settings
from app.services.alert_service import check_tunnel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

_START_TIME = time.time()
_OUTBOUND_IP: str | None = None
_OUTBOUND_IP_TS: float = 0
_OUTBOUND_IP_LOCK = asyncio.Lock()


async def _get_outbound_ip() -> str | None:
    global _OUTBOUND_IP, _OUTBOUND_IP_TS
    now = time.time()
    if _OUTBOUND_IP and now - _OUTBOUND_IP_TS < 1800:
        return _OUTBOUND_IP
    async with _OUTBOUND_IP_LOCK:
        if _OUTBOUND_IP and now - _OUTBOUND_IP_TS < 1800:
            return _OUTBOUND_IP
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get("https://api.ipify.org?format=json")
                _OUTBOUND_IP = r.json().get("ip")
                _OUTBOUND_IP_TS = time.time()
        except Exception:
            logger.warning("Failed to fetch outbound IP", exc_info=True)
    return _OUTBOUND_IP


def _get_port() -> int | None:
    try:
        return int(sys.argv[1])  # PM2 passes port as arg, e.g. "python run_server.py 8080"
    except (ValueError, IndexError):
        return None


@router.get("/health")
async def health_check():
    outbound_ip = await _get_outbound_ip()
    resp = JSONResponse({
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "uptime": int(time.time() - _START_TIME),
        "port": _get_port(),
        "outbound_ip": outbound_ip,
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
