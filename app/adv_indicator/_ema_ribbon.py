import numpy as np
from app.indicators.ema import ema as _ema

from .base import FillDef, Indicator, IndicatorLine, IndicatorMeta, ParamDef


class EMARibbon(Indicator):
    meta = IndicatorMeta(
        id="ema_ribbon",
        name="EMA Ribbon",
        lines=[
            IndicatorLine(id="ema_5", name="EMA 5", color="#08ef10"),
            IndicatorLine(id="ema_8", name="EMA 8", color="#808080"),
            IndicatorLine(id="ema_13", name="EMA 13", color="#808080"),
            IndicatorLine(id="ema_21", name="EMA 21", color="#08ef10"),
            IndicatorLine(id="ema_144h", name="EMA 144 H", color="#ea0000"),
            IndicatorLine(id="ema_144c", name="EMA 144 C", color="#ea0000"),
            IndicatorLine(id="ema_144l", name="EMA 144 L", color="#ea0000"),
        ],
        fills=[
            FillDef(top_line_id="ema_5", bottom_line_id="ema_21", color="#08ef10", opacity=0.10),
            FillDef(top_line_id="ema_144h", bottom_line_id="ema_144l", color="#ea0000", opacity=0.10),
        ],
        params=[
            ParamDef(id="ema_5_len", name="EMA 5 Length", type="int", default=5, min=1, max=500),
            ParamDef(id="ema_5_color", name="EMA 5 Color", type="color", default="#08ef10"),
            ParamDef(id="ema_5_width", name="EMA 5 Width", type="int", default=1, min=1, max=5),
            ParamDef(id="ema_5_transparency", name="EMA 5 Transparency", type="int", default=0, min=0, max=100),
            ParamDef(id="ema_5_enabled", name="EMA 5 Enabled", type="int", default=1, min=0, max=1),
            ParamDef(id="ema_8_len", name="EMA 8 Length", type="int", default=8, min=1, max=500),
            ParamDef(id="ema_8_color", name="EMA 8 Color", type="color", default="#808080"),
            ParamDef(id="ema_8_width", name="EMA 8 Width", type="int", default=1, min=1, max=5),
            ParamDef(id="ema_8_transparency", name="EMA 8 Transparency", type="int", default=0, min=0, max=100),
            ParamDef(id="ema_8_enabled", name="EMA 8 Enabled", type="int", default=1, min=0, max=1),
            ParamDef(id="ema_13_len", name="EMA 13 Length", type="int", default=13, min=1, max=500),
            ParamDef(id="ema_13_color", name="EMA 13 Color", type="color", default="#808080"),
            ParamDef(id="ema_13_width", name="EMA 13 Width", type="int", default=1, min=1, max=5),
            ParamDef(id="ema_13_transparency", name="EMA 13 Transparency", type="int", default=0, min=0, max=100),
            ParamDef(id="ema_13_enabled", name="EMA 13 Enabled", type="int", default=1, min=0, max=1),
            ParamDef(id="ema_21_len", name="EMA 21 Length", type="int", default=21, min=1, max=500),
            ParamDef(id="ema_21_color", name="EMA 21 Color", type="color", default="#08ef10"),
            ParamDef(id="ema_21_width", name="EMA 21 Width", type="int", default=1, min=1, max=5),
            ParamDef(id="ema_21_transparency", name="EMA 21 Transparency", type="int", default=0, min=0, max=100),
            ParamDef(id="ema_21_enabled", name="EMA 21 Enabled", type="int", default=1, min=0, max=1),
            ParamDef(id="ema_144h_len", name="EMA 144 H Length", type="int", default=144, min=1, max=500),
            ParamDef(id="ema_144h_color", name="EMA 144 H Color", type="color", default="#ea0000"),
            ParamDef(id="ema_144h_width", name="EMA 144 H Width", type="int", default=1, min=1, max=5),
            ParamDef(id="ema_144h_transparency", name="EMA 144 H Transparency", type="int", default=0, min=0, max=100),
            ParamDef(id="ema_144h_enabled", name="EMA 144 H Enabled", type="int", default=1, min=0, max=1),
            ParamDef(id="ema_144c_len", name="EMA 144 C Length", type="int", default=144, min=1, max=500),
            ParamDef(id="ema_144c_color", name="EMA 144 C Color", type="color", default="#ea0000"),
            ParamDef(id="ema_144c_width", name="EMA 144 C Width", type="int", default=1, min=1, max=5),
            ParamDef(id="ema_144c_transparency", name="EMA 144 C Transparency", type="int", default=0, min=0, max=100),
            ParamDef(id="ema_144c_enabled", name="EMA 144 C Enabled", type="int", default=1, min=0, max=1),
            ParamDef(id="ema_144l_len", name="EMA 144 L Length", type="int", default=144, min=1, max=500),
            ParamDef(id="ema_144l_color", name="EMA 144 L Color", type="color", default="#ea0000"),
            ParamDef(id="ema_144l_width", name="EMA 144 L Width", type="int", default=1, min=1, max=5),
            ParamDef(id="ema_144l_transparency", name="EMA 144 L Transparency", type="int", default=0, min=0, max=100),
            ParamDef(id="ema_144l_enabled", name="EMA 144 L Enabled", type="int", default=1, min=0, max=1),
            ParamDef(id="fill_5_21_sw", name="Green Fill", type="int", default=0, min=0, max=1),
            ParamDef(id="fill_5_21_clr", name="Green Fill Color", type="color", default="#08ef10"),
            ParamDef(id="fill_5_21_tr", name="Green Fill Transparency", type="int", default=90, min=0, max=100),
            ParamDef(id="fill_144_sw", name="Red Fill", type="int", default=0, min=0, max=1),
            ParamDef(id="fill_144_clr", name="Red Fill Color", type="color", default="#ea0000"),
            ParamDef(id="fill_144_tr", name="Red Fill Transparency", type="int", default=90, min=0, max=100),
        ],
    )

    def compute(self, times: list, close: list, high: list, low: list, open: list = None, params: dict = None) -> dict[str, list]:
        p = params or {}
        l5 = int(p.get("ema_5_len", 5))
        l8 = int(p.get("ema_8_len", 8))
        l13 = int(p.get("ema_13_len", 13))
        l21 = int(p.get("ema_21_len", 21))
        l144h = int(p.get("ema_144h_len", 144))
        l144c = int(p.get("ema_144c_len", 144))
        l144l = int(p.get("ema_144l_len", 144))

        c = np.array(close, dtype=float)
        h = np.array(high, dtype=float)
        l = np.array(low, dtype=float)

        e5 = _ema(c, l5)
        e8 = _ema(c, l8)
        e13 = _ema(c, l13)
        e21 = _ema(c, l21)
        e144h = _ema(h, l144h)
        e144c = _ema(c, l144c)
        e144l = _ema(l, l144l)

        def _to_list(arr):
            return [float(v) if not np.isnan(v) else None for v in arr]

        return {
            "ema_5": _to_list(e5),
            "ema_8": _to_list(e8),
            "ema_13": _to_list(e13),
            "ema_21": _to_list(e21),
            "ema_144h": _to_list(e144h),
            "ema_144c": _to_list(e144c),
            "ema_144l": _to_list(e144l),
        }
