import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def send_telegram(message: str) -> bool:
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            )
            return r.is_success
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)
        return False


def send_telegram_sync(message: str) -> bool:
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        return False
    try:
        with httpx.Client(timeout=10) as client:
            r = client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            )
            return r.is_success
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)
        return False


async def send_telegram_retry(message: str) -> bool:
    from app.services.alert_service import queue_alert
    ok = await send_telegram(message)
    if not ok:
        queue_alert(message)
    return ok
