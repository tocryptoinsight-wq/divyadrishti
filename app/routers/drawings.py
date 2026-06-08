import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.deps import get_current_user
from app.database import execute, query_one

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/drawings", tags=["drawings"])


@router.get("/{symbol}")
async def get_drawings(symbol: str, current_user: dict = Depends(get_current_user)):
    username = current_user["username"]
    row = query_one(
        "SELECT drawings_json FROM user_drawings WHERE username = ? AND symbol = ?",
        (username, symbol),
    )
    if not row:
        return {}
    try:
        return json.loads(row["drawings_json"])
    except json.JSONDecodeError:
        return {}


@router.post("/{symbol}")
async def save_drawings(symbol: str, body: dict, current_user: dict = Depends(get_current_user)):
    username = current_user["username"]
    drawings_json = json.dumps(body)
    execute(
        """INSERT INTO user_drawings (username, symbol, drawings_json, updated_at)
           VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(username, symbol) DO UPDATE
           SET drawings_json = excluded.drawings_json, updated_at = datetime('now')""",
        (username, symbol, drawings_json),
    )
    return {"success": True}
