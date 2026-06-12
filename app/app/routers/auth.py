import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.auth.deps import create_token, get_current_user
from app.database import execute, query, query_one, verify_password
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

MAX_SESSIONS = 3


@router.post("/login")
async def login(body: dict, response: Response):
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    device_name = (body.get("device_name") or "").strip() or "Unknown"
    if not username or not password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username and password required")
    row = query_one("SELECT id, username, password, role, is_active, expires_at, failed_attempts FROM users WHERE username = ?", (username,))
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

    if not row["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is deactivated")
    expires_at = row["expires_at"]
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at)
            if exp < datetime.now():
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account has expired")
        except ValueError:
            pass
    if not verify_password(password, row["password"]):
        execute("UPDATE users SET failed_attempts = failed_attempts + 1 WHERE id = ?", (row["id"],))
        failed = (row["failed_attempts"] or 0) + 1
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid username or password. Failed attempts: {failed}")
    if (row["failed_attempts"] or 0) > 0:
        execute("UPDATE users SET failed_attempts = 0 WHERE id = ?", (row["id"],))

    # Check session limit
    logout_session_id = body.get("logout_session_id")
    logout_all_sessions = body.get("logout_all_sessions", False)

    if logout_session_id:
        row_check = query_one("SELECT id FROM sessions WHERE id = ? AND username = ?", (logout_session_id, row["username"]))
        if not row_check:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        execute("DELETE FROM sessions WHERE id = ? AND username = ?", (logout_session_id, row["username"]))
    elif logout_all_sessions:
        execute("DELETE FROM sessions WHERE username = ?", (row["username"],))
    else:
        sess_count = query_one("SELECT COUNT(*) as cnt FROM sessions WHERE username = ?", (row["username"],))
        if sess_count and sess_count["cnt"] >= MAX_SESSIONS:
            rows = query(
                "SELECT id, device_name, created_at, last_active FROM sessions WHERE username = ? ORDER BY created_at ASC",
                (row["username"],),
            )
            return {
                "max_sessions": True,
                "sessions": [
                    {"id": r["id"], "device_name": r["device_name"], "created_at": r["created_at"], "last_active": r["last_active"]}
                    for r in rows
                ],
            }

    sess_token = str(uuid.uuid4())
    execute(
        "INSERT INTO sessions (username, session_token, device_name, last_active) VALUES (?, ?, ?, ?)",
        (row["username"], sess_token, device_name, datetime.utcnow().isoformat()),
    )
    token = create_token(row["username"], row["role"], session_token=sess_token)
    response.set_cookie(
        key="token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=30 * 24 * 3600,
    )
    return {
        "success": True,
        "token": token,
        "username": row["username"],
        "role": row["role"],
    }


@router.post("/logout")
async def logout(current_user: dict = Depends(get_current_user), response: Response = None):
    execute("DELETE FROM sessions WHERE username = ? AND session_token = ?", (current_user.get("username", ""), current_user.get("session_token", "")))
    response.delete_cookie(key="token", path="/")
    return {"success": True}


def _check_session(username: str, session_token: str, response: Response):
    row = query_one("SELECT id FROM sessions WHERE username = ? AND session_token = ?", (username, session_token))
    if not row:
        response.delete_cookie(key="token", path="/")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")


@router.get("/me")
async def me(current_user: dict = Depends(get_current_user), response: Response = None):
    username = current_user["username"]
    role = current_user.get("role", "user")
    if role != "admin":
        _check_session(username, current_user.get("session_token", ""), response)
    execute("UPDATE sessions SET last_active = ? WHERE username = ? AND session_token = ?",
            (datetime.utcnow().isoformat(), username, current_user.get("session_token", "")))
    return {"success": True, "username": username, "role": role}


@router.post("/heartbeat")
async def heartbeat(current_user: dict = Depends(get_current_user), response: Response = None):
    username = current_user["username"]
    role = current_user.get("role", "user")
    if role != "admin":
        _check_session(username, current_user.get("session_token", ""), response)
    execute("UPDATE sessions SET last_active = ? WHERE username = ? AND session_token = ?",
            (datetime.utcnow().isoformat(), username, current_user.get("session_token", "")))
    return {"success": True}


@router.get("/sessions")
async def list_sessions(current_user: dict = Depends(get_current_user)):
    username = current_user["username"]
    rows = query(
        "SELECT id, device_name, created_at, last_active, session_token FROM sessions WHERE username = ? ORDER BY created_at ASC",
        (username,),
    )
    current_token = current_user.get("session_token", "")
    sessions = []
    for r in rows:
        sessions.append({
            "id": r["id"],
            "device_name": r["device_name"],
            "created_at": r["created_at"],
            "last_active": r["last_active"],
            "is_current": r["session_token"] == current_token,
        })
    return {"success": True, "sessions": sessions}


@router.post("/sessions/logout")
async def logout_session(body: dict, current_user: dict = Depends(get_current_user), response: Response = None):
    session_id = body.get("session_id")
    if not session_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="session_id required")
    username = current_user["username"]
    row = query_one("SELECT session_token FROM sessions WHERE id = ? AND username = ?", (session_id, username))
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if row["session_token"] == current_user.get("session_token", ""):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot logout current session. Use logout instead.")
    execute("DELETE FROM sessions WHERE id = ? AND username = ?", (session_id, username))
    return {"success": True}


@router.post("/sessions/logout-other")
async def logout_other_sessions(current_user: dict = Depends(get_current_user)):
    username = current_user["username"]
    current_token = current_user.get("session_token", "")
    execute("DELETE FROM sessions WHERE username = ? AND session_token != ?", (username, current_token))
    return {"success": True}
