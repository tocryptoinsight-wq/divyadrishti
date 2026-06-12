import hashlib
import hmac
import json
import logging
import time

import httpx
import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

_DELTA_BASE = settings.delta_api_base.rstrip('/').rstrip('/v2')


class DeltaClient:
    def __init__(self, base_url: str = settings.delta_api_base):
        self.base_url = base_url
        self._client = httpx.AsyncClient(timeout=15.0)

    async def get_products(self):
        resp = await self._client.get(f"{self.base_url}/products")
        resp.raise_for_status()
        return resp.json()

    async def get_candles(
        self,
        symbol: str,
        resolution: str = "1h",
        start: int | None = None,
        end: int | None = None,
        limit: int = 500,
    ):
        ts_end = end or int(time.time())
        ts_start = start or ts_end - limit * self._resolution_seconds(resolution)

        params = {
            "symbol": symbol,
            "resolution": resolution,
            "start": ts_start,
            "end": ts_end,
        }
        resp = await self._client.get(f"{self.base_url}/history/candles", params=params)
        resp.raise_for_status()
        data = resp.json()
        return self._parse_candles(data.get("result", []))

    async def get_ticker(self, symbol: str):
        resp = await self._client.get(f"{self.base_url}/tickers/{symbol}")
        resp.raise_for_status()
        return resp.json()

    async def get_all_tickers(self, contract_types: str = "perpetual_futures"):
        resp = await self._client.get(
            f"{self.base_url}/tickers",
            params={"contract_types": contract_types},
        )
        resp.raise_for_status()
        return resp.json()

    def _resolution_seconds(self, resolution: str) -> int:
        units = {
            "1m": 60, "3m": 180, "5m": 300, "15m": 900,
            "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400,
            "6h": 21600, "1d": 86400, "7d": 604800, "30d": 2592000,
        }
        return units.get(resolution, 3600)

    def _parse_candles(self, raw: list) -> dict:
        if not raw:
            return {"time": np.array([]), "open": np.array([]), "high": np.array([]),
                    "low": np.array([]), "close": np.array([]), "volume": np.array([])}

        raw_sorted = sorted(raw, key=lambda x: x.get("time", 0))
        return {
            "time": np.array([c.get("time", 0) for c in raw_sorted]),
            "open": np.array([float(c.get("open", 0)) for c in raw_sorted], dtype=float),
            "high": np.array([float(c.get("high", 0)) for c in raw_sorted], dtype=float),
            "low": np.array([float(c.get("low", 0)) for c in raw_sorted], dtype=float),
            "close": np.array([float(c.get("close", 0)) for c in raw_sorted], dtype=float),
            "volume": np.array([float(c.get("volume", 0)) for c in raw_sorted], dtype=float),
        }

    async def close(self):
        await self._client.aclose()


# ---------- shared auth helpers for trading.py / algo_service.py ----------
_AUTH_CLIENT = httpx.AsyncClient(timeout=15)

def _sign(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()

async def _delta_auth_get(api_key: str, api_secret: str, path: str, query_string: str = "") -> dict:
    path_v2 = "/v2" + path
    ts = str(int(time.time()))
    sig_data = "GET" + ts + path_v2 + query_string
    sig = _sign(api_secret, sig_data)
    headers = {"api-key": api_key, "signature": sig, "timestamp": ts, "User-Agent": "python-rest-client"}
    url = f"{_DELTA_BASE}{path_v2}{query_string}"
    resp = await _AUTH_CLIENT.get(url, headers=headers)
    try:
        return {"status": resp.status_code, "body": resp.json()}
    except Exception:
        return {"status": resp.status_code, "body": resp.text}

async def _delta_auth_post(api_key: str, api_secret: str, path: str, payload: dict) -> dict:
    path_v2 = "/v2" + path
    body = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    ts = str(int(time.time()))
    sig_data = "POST" + ts + path_v2 + "" + body
    sig = _sign(api_secret, sig_data)
    headers = {"api-key": api_key, "signature": sig, "timestamp": ts, "User-Agent": "python-rest-client", "Content-Type": "application/json"}
    resp = await _AUTH_CLIENT.post(f"{_DELTA_BASE}{path_v2}", headers=headers, content=body)
    try:
        return {"status": resp.status_code, "body": resp.json()}
    except Exception:
        return {"status": resp.status_code, "body": resp.text}

async def _delta_order(api_key: str, api_secret: str, payload: dict) -> dict:
    return await _delta_auth_post(api_key, api_secret, "/orders", payload)

async def _close_auth_client():
    await _AUTH_CLIENT.aclose()

delta_client = DeltaClient()
