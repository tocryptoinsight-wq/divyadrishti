import numpy as np
from app.indicators.ema import ema as _ema

from .base import Indicator, IndicatorLine, IndicatorMeta, ParamDef


class TripleEMA(Indicator):
    meta = IndicatorMeta(
        id="triple_ema",
        name="Multi EMA",
        lines=[
            IndicatorLine(id="ema_10", name="EMA 10", color="#00cc00"),
            IndicatorLine(id="ema_20", name="EMA 20", color="yellow"),
            IndicatorLine(id="ema_50", name="EMA 50", color="red"),
            IndicatorLine(id="ema_100", name="EMA 100", color="#ff8800"),
        ],
        params=[
            ParamDef(id="ema_10_len", name="EMA 10 Length", type="int", default=10, min=1, max=500),
            ParamDef(id="ema_10_color", name="EMA 10 Color", type="color", default="#00cc00"),
            ParamDef(id="ema_10_width", name="EMA 10 Width", type="int", default=1, min=1, max=5),
            ParamDef(id="ema_10_transparency", name="EMA 10 Transparency", type="int", default=0, min=0, max=100),
            ParamDef(id="ema_10_enabled", name="EMA 10 Enabled", type="int", default=1, min=0, max=1),
            ParamDef(id="ema_20_len", name="EMA 20 Length", type="int", default=20, min=1, max=500),
            ParamDef(id="ema_20_color", name="EMA 20 Color", type="color", default="#ffff00"),
            ParamDef(id="ema_20_width", name="EMA 20 Width", type="int", default=1, min=1, max=5),
            ParamDef(id="ema_20_transparency", name="EMA 20 Transparency", type="int", default=0, min=0, max=100),
            ParamDef(id="ema_20_enabled", name="EMA 20 Enabled", type="int", default=1, min=0, max=1),
            ParamDef(id="ema_50_len", name="EMA 50 Length", type="int", default=50, min=1, max=500),
            ParamDef(id="ema_50_color", name="EMA 50 Color", type="color", default="#ff0000"),
            ParamDef(id="ema_50_width", name="EMA 50 Width", type="int", default=1, min=1, max=5),
            ParamDef(id="ema_50_transparency", name="EMA 50 Transparency", type="int", default=0, min=0, max=100),
            ParamDef(id="ema_50_enabled", name="EMA 50 Enabled", type="int", default=1, min=0, max=1),
            ParamDef(id="ema_100_len", name="EMA 100 Length", type="int", default=100, min=1, max=500),
            ParamDef(id="ema_100_color", name="EMA 100 Color", type="color", default="#ff8800"),
            ParamDef(id="ema_100_width", name="EMA 100 Width", type="int", default=1, min=1, max=5),
            ParamDef(id="ema_100_transparency", name="EMA 100 Transparency", type="int", default=0, min=0, max=100),
            ParamDef(id="ema_100_enabled", name="EMA 100 Enabled", type="int", default=1, min=0, max=1),
        ],
    )

    def compute(self, times: list, close: list, high: list, low: list, open: list = None, params: dict = None) -> dict[str, list]:
        p = params or {}
        l10 = int(p.get("ema_10_len", 10))
        l20 = int(p.get("ema_20_len", 20))
        l50 = int(p.get("ema_50_len", 50))
        l100 = int(p.get("ema_100_len", 100))

        c = np.array(close, dtype=float)
        e10 = _ema(c, l10)
        e20 = _ema(c, l20)
        e50 = _ema(c, l50)
        e100 = _ema(c, l100)

        def _to_list(arr):
            return [float(v) if not np.isnan(v) else None for v in arr]

        return {
            "ema_10": _to_list(e10),
            "ema_20": _to_list(e20),
            "ema_50": _to_list(e50),
            "ema_100": _to_list(e100),
        }
