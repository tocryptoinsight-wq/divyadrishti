import numpy as np
from app.indicators.atr import atr as _atr

from .base import FillDef, Indicator, IndicatorLine, IndicatorMeta, ParamDef


class SuperTrend(Indicator):
    meta = IndicatorMeta(
        id="supertrend",
        name="SuperTrend",
        lines=[
            IndicatorLine(id="st_uptrend", name="Up Trend", color="#22c55e"),
            IndicatorLine(id="st_downtrend", name="Down Trend", color="#ef4444"),
            IndicatorLine(id="st_body_middle", name="Body Middle", color="#22c55e"),
        ],
        fills=[
            FillDef(top_line_id="st_body_middle", bottom_line_id="st_uptrend", color="#22c55e", opacity=0.10),
            FillDef(top_line_id="st_body_middle", bottom_line_id="st_downtrend", color="#ef4444", opacity=0.10),
        ],
        params=[
            ParamDef(id="st_atr_period", name="ATR Period", type="int", default=10, min=1, max=500),
            ParamDef(id="st_source", name="Source", type="select", default="hl2",
                     options=["hl2", "ohlc4", "hlc3", "close", "high", "low", "open"]),
            ParamDef(id="st_multiplier", name="ATR Multiplier", type="float", default=3.0, min=0.1, max=100),
            ParamDef(id="st_change_atr", name="Change ATR Calculation Method ?", type="int", default=1, min=0, max=1),
            ParamDef(id="st_highlighting", name="Highlighter On/Off ?", type="int", default=1, min=0, max=1),
            ParamDef(id="st_body_middle_color", name="Body Middle Color", type="color", default="#22c55e"),
            ParamDef(id="st_body_middle_width", name="Body Middle Width", type="int", default=2, min=1, max=5),
            ParamDef(id="st_body_middle_transparency", name="Body Middle Transparency", type="int", default=0, min=0, max=100),
            ParamDef(id="st_body_middle_enabled", name="Body Middle Enabled", type="int", default=0, min=0, max=1),
            ParamDef(id="st_uptrend_color", name="Up Trend Color", type="color", default="#22c55e"),
            ParamDef(id="st_uptrend_width", name="Up Trend Width", type="int", default=2, min=1, max=5),
            ParamDef(id="st_uptrend_transparency", name="Up Trend Transparency", type="int", default=0, min=0, max=100),
            ParamDef(id="st_uptrend_enabled", name="Up Trend Enabled", type="int", default=1, min=0, max=1),
            ParamDef(id="st_downtrend_color", name="Down Trend Color", type="color", default="#ef4444"),
            ParamDef(id="st_downtrend_width", name="Down Trend Width", type="int", default=2, min=1, max=5),
            ParamDef(id="st_downtrend_transparency", name="Down Trend Transparency", type="int", default=0, min=0, max=100),
            ParamDef(id="st_downtrend_enabled", name="Down Trend Enabled", type="int", default=1, min=0, max=1),
        ],
    )

    def compute(self, times: list, close: list, high: list, low: list, open: list = None, params: dict = None) -> dict[str, list]:
        p = params or {}
        atr_len = int(p.get("st_atr_period", 10))
        multiplier = float(p.get("st_multiplier", 3.0))
        source_type = p.get("st_source", "hl2")
        change_atr = int(p.get("st_change_atr", 1))
        showsignals = int(p.get("st_showsignals", 1))

        c = np.array(close, dtype=float)
        h = np.array(high, dtype=float)
        l = np.array(low, dtype=float)
        o = np.array(open, dtype=float) if open is not None else c

        if source_type == "hl2":
            src = (h + l) / 2
        elif source_type == "ohlc4":
            src = (o + h + l + c) / 4
        elif source_type == "hlc3":
            src = (h + l + c) / 3
        elif source_type == "close":
            src = c
        elif source_type == "high":
            src = h
        elif source_type == "low":
            src = l
        elif source_type == "open":
            src = o
        else:
            src = (h + l) / 2

        n = len(c)

        if change_atr:
            atr_vals = _atr(h, l, c, atr_len)
        else:
            prev_close = np.roll(c, 1)
            prev_close[0] = c[0]
            tr = np.maximum(h - l, np.maximum(np.abs(h - prev_close), np.abs(l - prev_close)))
            atr_vals = self._sma(tr, atr_len)

        raw_up = src - multiplier * atr_vals
        raw_dn = src + multiplier * atr_vals

        final_up = raw_up.copy()
        final_dn = raw_dn.copy()
        trend = np.full(n, np.nan, dtype=float)

        for i in range(1, n):
            if np.isnan(atr_vals[i]) or np.isnan(src[i]):
                continue

            prev_up = final_up[i - 1] if not np.isnan(final_up[i - 1]) else raw_up[i]
            if c[i - 1] > prev_up:
                final_up[i] = max(raw_up[i], prev_up)
            else:
                final_up[i] = raw_up[i]

            prev_dn = final_dn[i - 1] if not np.isnan(final_dn[i - 1]) else raw_dn[i]
            if c[i - 1] < prev_dn:
                final_dn[i] = min(raw_dn[i], prev_dn)
            else:
                final_dn[i] = raw_dn[i]

            prev_trend = trend[i - 1] if not np.isnan(trend[i - 1]) else 1
            if prev_trend == -1 and c[i] > prev_dn:
                trend[i] = 1
            elif prev_trend == 1 and c[i] < prev_up:
                trend[i] = -1
            else:
                trend[i] = prev_trend

        for i in range(1, n):
            if np.isnan(trend[i]) and i > 0 and not np.isnan(trend[i - 1]):
                trend[i] = trend[i - 1]

        st_uptrend = np.full(n, np.nan)
        st_downtrend = np.full(n, np.nan)

        for i in range(1, n):
            if trend[i] == 1:
                st_uptrend[i] = final_up[i]
            elif trend[i] == -1:
                st_downtrend[i] = final_dn[i]

        body_middle = np.full(n, np.nan)
        for i in range(1, n):
            body_middle[i] = (o[i] + h[i] + l[i] + c[i]) / 4

        markers = []
        for i in range(1, n):
            if np.isnan(trend[i]) or np.isnan(trend[i - 1]):
                continue
            if trend[i] == 1 and trend[i - 1] != 1:
                markers.append({
                    "time": times[i],
                    "position": "belowBar",
                    "color": "#22c55e",
                    "shape": "circle",
                    "text": "Buy" if showsignals else "",
                    "size": "tiny",
                })
            elif trend[i] == -1 and trend[i - 1] != -1:
                markers.append({
                    "time": times[i],
                    "position": "aboveBar",
                    "color": "#ef4444",
                    "shape": "circle",
                    "text": "Sell" if showsignals else "",
                    "size": "tiny",
                })

        def _to_list(arr):
            return [float(v) if not np.isnan(v) else None for v in arr]

        result = {
            "st_uptrend": _to_list(st_uptrend),
            "st_downtrend": _to_list(st_downtrend),
            "st_body_middle": _to_list(body_middle),
        }
        if markers:
            result["st_markers"] = markers

        return result

    def _sma(self, values: np.ndarray, length: int) -> np.ndarray:
        result = np.full_like(values, np.nan)
        n = len(values)
        for i in range(length - 1, n):
            s = 0.0
            cnt = 0
            for j in range(i - length + 1, i + 1):
                v = values[j]
                if not np.isnan(v):
                    s += v
                    cnt += 1
            if cnt > 0:
                result[i] = s / cnt
        return result
