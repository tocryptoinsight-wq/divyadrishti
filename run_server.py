import asyncio
import logging
import sys
import uvicorn
from uvicorn.config import Config
from uvicorn.server import Server

from app.config import settings
from restart.helper import _acquire_port, _log_configure
import restart.router

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    _log_configure()

    preferred = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    logger.info("Starting run_server: preferred=%d", preferred)

    port = _acquire_port(preferred)
    logger.info("Starting server on port %d", port)

    config = Config("app.main:app", host="127.0.0.1", port=port, log_level="info", proxy_headers=False)
    server = Server(config)
    restart.router._server = server
    asyncio.run(server.serve())
