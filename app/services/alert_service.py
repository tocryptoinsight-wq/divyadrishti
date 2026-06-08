import asyncio
import logging
import time

import httpx

from app.config import settings
from app.services.telegram_notifier import send_telegram

logger = logging.getLogger(__name__)

# ── Alert retry queue ──────────────────────────────────────────
_alert_queue: list[dict] = []
_ALERT_MAX_RETRIES = 4
_ALERT_BASE_DELAY = 15


async def _alert_retry_loop():
    while True:
        await asyncio.sleep(15)
        if not _alert_queue:
            continue
        now = time.time()
        still_pending = []
        for entry in _alert_queue:
            if now < entry["next_retry"]:
                still_pending.append(entry)
                continue
            ok = await send_telegram(entry["message"])
            if ok:
                logger.info("Retried alert delivered: %.60s", entry["message"])
                continue
            entry["attempts"] += 1
            if entry["attempts"] >= _ALERT_MAX_RETRIES:
                logger.warning("Alert dropped after %d attempts: %.60s", _ALERT_MAX_RETRIES, entry["message"])
                continue
            entry["next_retry"] = now + _ALERT_BASE_DELAY * (2 ** (entry["attempts"] - 1))
            still_pending.append(entry)
        _alert_queue[:] = still_pending


def queue_alert(message: str):
    _alert_queue.append({
        "message": message,
        "attempts": 0,
        "next_retry": time.time() + 5,
    })
    logger.info("Alert queued: %.60s", message)


async def send_or_queue(message: str) -> bool:
    ok = await send_telegram(message)
    if not ok:
        queue_alert(message)
    return ok


# ── Broker disconnect monitor ──────────────────────────────────
_broker_failures: dict[str, int] = {}  # key -> consecutive failures
_broker_alerted: set[str] = set()


def _broker_key(api_key: str) -> str:
    return api_key[:12] if api_key else "unknown"


def report_broker_success(api_key: str):
    key = _broker_key(api_key)
    _broker_failures.pop(key, None)
    _broker_alerted.discard(key)


async def report_broker_failure(api_key: str, reason: str, api_path: str = ""):
    key = _broker_key(api_key)
    count = _broker_failures.get(key, 0) + 1
    _broker_failures[key] = count
    if count >= 3 and key not in _broker_alerted:
        _broker_alerted.add(key)
        msg = (
            f"<b>Broker Disconnect</b>\n"
            f"API key: {key}...\n"
            f"Consecutive failures: {count}\n"
            f"Endpoint: {api_path}\n"
            f"Reason: {reason}"
        )
        await send_or_queue(msg)
    elif count < 3:
        logger.info("Broker failure #%d for %s: %s", count, key, reason)


# ── Tunnel monitor ─────────────────────────────────────────────
_tunnel_down_alerted = False
_tunnel_healthy_since: float = 0.0


async def check_tunnel() -> dict:
    global _tunnel_down_alerted
    live_url = settings.live_site_url
    if not live_url:
        return {"status": "unknown", "reason": "LIVE_SITE_URL not configured"}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(live_url.rstrip("/") + "/health")
        if r.is_success:
            _tunnel_down_alerted = False
            return {"status": "ok"}
        reason = f"HTTP {r.status_code}"
        _tunnel_down_alerted = True
        return {"status": "down", "reason": reason}
    except httpx.ConnectError as e:
        reason = f"Connection refused: {e}"
        if not _tunnel_down_alerted:
            await send_or_queue(f"<b>Tunnel Down</b>\n{live_url}\n{reason}")
        _tunnel_down_alerted = True
        return {"status": "down", "reason": reason}
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        if not _tunnel_down_alerted:
            await send_or_queue(f"<b>Tunnel Down</b>\n{live_url}\n{reason}")
        _tunnel_down_alerted = True
        return {"status": "down", "reason": reason}


async def _tunnel_monitor_loop():
    while True:
        await asyncio.sleep(120)
        try:
            await check_tunnel()
        except Exception as e:
            logger.warning("Tunnel monitor error: %s", e)


# ── Order rejection reason parsing ─────────────────────────────


def parse_order_rejection(resp_body) -> str:
    if isinstance(resp_body, dict):
        err = resp_body.get("error", resp_body)
        if isinstance(err, dict):
            msg = err.get("message", str(err))
            code = err.get("code", "")
            if code:
                return f"[{code}] {msg}"
            return msg
        if isinstance(err, str):
            return err
        return str(resp_body)
    if isinstance(resp_body, str):
        return resp_body
    return str(resp_body)


# ── Background starter ─────────────────────────────────────────


async def start_monitors(app):
    asyncio.ensure_future(_alert_retry_loop())
    asyncio.ensure_future(_tunnel_monitor_loop())
    logger.info("Alert retry and tunnel monitors started")
