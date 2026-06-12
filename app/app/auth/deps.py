import datetime
import logging
import os

from fastapi import Depends, Header, HTTPException, Request, status
from jose import JWTError, jwt

from app.config import settings

logger = logging.getLogger(__name__)

_SECRET_KEY = os.environ.get("SECRET_KEY") or getattr(settings, "secret_key", "change-me-in-production")
_ALGORITHM = "HS256"
_EXPIRE_HOURS = getattr(settings, "jwt_expire_hours", 720)


def _get_secret() -> str:
    val = os.environ.get("SECRET_KEY")
    if val:
        return val
    sk = getattr(settings, "secret_key", None)
    if sk and sk != "change-me":
        return sk
    return _SECRET_KEY


def create_token(username: str, role: str, session_token: str = "") -> str:
    expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=_EXPIRE_HOURS)
    payload = {
        "sub": username,
        "role": role,
        "exp": expire,
        "session_token": session_token,
    }
    return jwt.encode(payload, _get_secret(), algorithm=_ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, _get_secret(), algorithms=[_ALGORITHM])
    except JWTError:
        return None


def _is_local(ip: str) -> bool:
    if ip in ("127.0.0.1", "::1", "localhost", "0.0.0.0"):
        return True
    if ip.startswith("192.168.") or ip.startswith("10."):
        return True
    if ip.startswith("fe80:") or ip.startswith("fc") or ip.startswith("fd"):
        return True
    if ip == "::1" or ip == "0:0:0:0:0:0:0:1":
        return True
    return False


async def get_current_user(authorization: str = Header("")) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid token")
    token = authorization[7:]
    payload = decode_token(token)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return {"username": payload.get("sub", ""), "role": payload.get("role", ""), "session_token": payload.get("session_token", "")}


async def get_current_user_from_cookie(request: Request) -> dict | None:
    token = request.cookies.get("token")
    if not token:
        return None
    payload = decode_token(token)
    if payload is None:
        return None
    return {"username": payload.get("sub", ""), "role": payload.get("role", ""), "session_token": payload.get("session_token", "")}


async def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


async def require_admin_local(request: Request, current_user: dict = Depends(get_current_user)) -> dict:
    if current_user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    client = request.client
    if client and not _is_local(client.host):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access only allowed from local network")
    return current_user
