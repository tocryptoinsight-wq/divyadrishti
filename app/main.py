import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse

from app.auth.deps import decode_token
from app.config import settings
from app.database import close_db, init_db
from app.routers import adv_indicators, analysis, auth, admin, drawings, health, manual_trail, trading
from restart.router import router as restart_router


def _static_dir() -> str:
    if getattr(sys, "frozen", False):
        return str(Path(sys._MEIPASS) / "app" / "static")
    return str(Path(__file__).parent / "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: init DB
    init_db()
    from app.services.alert_service import start_monitors
    await start_monitors(app)
    yield
    from app.data.delta_client import _close_auth_client, delta_client
    await delta_client.close()
    await _close_auth_client()
    await trading._SHARED_CLIENT.aclose()
    close_db()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(restart_router)
app.include_router(analysis.router)
app.include_router(trading.router)
app.include_router(manual_trail.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(adv_indicators.router)
app.include_router(drawings.router)

_static_dir_path = Path(_static_dir())


@app.get("/{full_path:path}")
async def serve_static(request: Request, full_path: str):
    is_index = not full_path or full_path == "index.html"
    if is_index:
        token = request.cookies.get("token")
        if not token or not decode_token(token):
            return RedirectResponse(url="/login.html")
    file_path = _static_dir_path / (full_path or "index.html")
    if not file_path.exists():
        file_path = _static_dir_path / "index.html"
    resp = FileResponse(str(file_path))
    if not full_path or full_path.endswith(".html"):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp
