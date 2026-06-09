import asyncio
import logging
import os
import signal
import subprocess
import sys

from fastapi import APIRouter, Depends

from app.auth.deps import require_admin_local

logger = logging.getLogger(__name__)

router = APIRouter(tags=["restart"])

_server = None


def _pm2_name(port: str) -> str | None:
    return "divyadrishti-live" if port == "8080" else "divyadrishti-dev" if port == "8000" else None


@router.post("/restart")
async def restart_server(_admin: dict = Depends(require_admin_local)):
    async def _delayed_restart():
        await asyncio.sleep(0.3)

        port = str(_server.config.port) if _server and _server.config else "8000"

        if _server and _server.servers:
            for srv in _server.servers:
                srv.close()
            await asyncio.sleep(0.5)

        pm2 = _pm2_name(port)
        if pm2:
            logger.info("PM2 restarting %s on port %s", pm2, port)
            subprocess.Popen(
                ["pm2", "restart", pm2],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await asyncio.sleep(2.0)
        else:
            logger.info("No PM2 name for port %s — only killing self", port)
            await asyncio.sleep(0.5)

        logger.info("Terminating process (PID %d)", os.getpid())
        os.kill(os.getpid(), signal.SIGTERM if sys.platform != "win32" else signal.SIGINT)
        sys.exit(0)

    async def _restart_wrapper():
        try:
            await _delayed_restart()
        except Exception as e:
            logger.error("Restart failed: %s", e, exc_info=True)
            sys.exit(1)

    asyncio.create_task(_restart_wrapper())
    return {"status": "restarting"}
