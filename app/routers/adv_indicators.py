import json
import logging
import math

import numpy as np
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.auth.deps import get_current_user_from_cookie
from app.data.delta_client import delta_client
from app.adv_indicator.registry import list_indicators, compute

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/adv_indicators", tags=["adv_indicators"])


@router.get("/list")
async def get_indicator_list():
    metas = list_indicators()
    return [
        {
            "id": m.id,
            "name": m.name,
            "lines": [{"id": l.id, "name": l.name, "color": l.color} for l in m.lines],
            "fills": [{"top_line_id": f.top_line_id, "bottom_line_id": f.bottom_line_id, "color": f.color, "opacity": f.opacity} for f in m.fills],
            "params": [{"id": p.id, "name": p.name, "type": p.type, "default": p.default, "options": p.options, "min": p.min, "max": p.max} for p in m.params],
        }
        for m in metas
    ]


def _to_list(arr):
    if arr is None:
        return []
    if isinstance(arr, np.ndarray):
        return [None if (isinstance(v, float) and math.isnan(v)) else v for v in arr.tolist()]
    if isinstance(arr, (list, tuple)):
        return [None if (isinstance(v, float) and math.isnan(v)) else v for v in arr]
    return arr


@router.get("/compute/{symbol}")
async def compute_indicator(
    symbol: str,
    resolution: str = Query("5"),
    indicator: str = Query(...),
    params: str = Query(None),
):
    res_map = {"5": "5m", "15": "15m", "60": "1h"}
    res = res_map.get(resolution, "5m")
    data = await delta_client.get_candles(symbol, res, limit=500)
    times = _to_list(data["time"])
    close = _to_list(data["close"])
    high = _to_list(data["high"])
    low = _to_list(data["low"])
    open_prices = _to_list(data["open"])
    if len(close) < 10:
        return JSONResponse(content={"error": "Not enough data"}, status_code=400)
    parsed_params = None
    if params:
        try:
            parsed_params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            return JSONResponse(content={"error": "Invalid params JSON"}, status_code=400)
    try:
        result = compute(indicator, times, close, high, low, open=open_prices, params=parsed_params)
    except ValueError as e:
        return JSONResponse(content={"error": str(e)}, status_code=400)
    return {
        "symbol": symbol,
        "resolution": resolution,
        "indicator": indicator,
        "params": parsed_params,
        "times": times,
        "lines": {k: _to_list(v) for k, v in result.items()},
    }
