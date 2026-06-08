import asyncio
import logging
import os
import uvicorn
from uvicorn.config import Config
from uvicorn.server import Server

from app.config import settings
from restart.helper import _log_configure
import restart.router

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    _log_configure()

    port = int(os.environ.get("PORT", 8080))
    logger.info("Starting server on port %d", port)

    config = Config("app.main:app", host="0.0.0.0", port=port, log_level="info", proxy_headers=True)
    server = Server(config)
    restart.router._server = server
    asyncio.run(server.serve())
