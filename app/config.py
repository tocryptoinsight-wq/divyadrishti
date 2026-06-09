import json
import logging
import os
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


def _load_desktop_settings() -> dict:
    paths = []
    if getattr(sys, "frozen", False):
        paths.append(Path(sys._MEIPASS) / "desktop" / "settings.json")
    paths.extend([
        Path("desktop/settings.json"),
        Path(os.path.dirname(__file__)).parent / "desktop" / "settings.json",
    ])
    for p in paths:
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception as e:
                logger.warning("Failed to load settings file %s: %s", p, e)
                pass
    return {}


class Settings(BaseSettings):
    app_name: str = "DDScanner"
    app_version: str = "0.1.1"
    debug: bool = True

    delta_api_base: str = "https://api.india.delta.exchange/v2"

    risk_amount: float = 0.70
    max_history: int = 5
    sl_mode: str = "ATR"
    sl_buffer_mult: float = 0.25
    momentum_len: int = 10
    fast_len: int = 20
    mid_len: int = 50
    atr_len: int = 14
    atr_mult: float = 1.5
    rsi_len: int = 14
    max_box_bars: int = 300
    arrow_buffer_atr: float = 0.5
    adx_len: int = 14

    screener_symbols: list[str] = []

    # Auth
    secret_key: str = "change-me-in-production"
    jwt_expire_hours: int = 720  # 30 days

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Live site (for tunnel monitoring)
    live_site_url: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


_desk = _load_desktop_settings()
settings = Settings(**{k: v for k, v in _desk.items() if hasattr(Settings, k)})
