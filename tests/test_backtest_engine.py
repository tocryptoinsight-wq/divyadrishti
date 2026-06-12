"""Comprehensive tests for the backtest engine.

Tests value flow from inputs through calculations to trade records.
"""
import json
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.routers.strategy import _compute_qty, _compute_sl_price
from app.engine.signal import signal_logic


# ---------------------------------------------------------------------------
# _compute_qty  –  position sizing
# ---------------------------------------------------------------------------
class TestComputeQty:
    """Verify position sizing math."""

    def test_basic_long(self):
        qty = _compute_qty(1000, 0.05, 100, 99, 1)
        # risk_capital = 1000 * 0.05 = 50
        # risk_per_unit = |100-99| * 1 = 1
        # qty = 50 / 1 = 50
        # notional = 50 * 100 * 1 = 5000  ≤  max_notional = 10000  →  not capped
        assert qty == 50.0

    def test_no_leverage_cap(self):
        """Position is always sized to intended risk, leverage cap removed."""
        qty = _compute_qty(1000, 0.5, 100, 99, 1)
        # risk_capital = 500, risk_per_unit = 1 → qty = 500
        # notional would be 50000, but no cap — qty stays at 500
        assert qty == 500.0

    def test_contract_value_scales(self):
        qty = _compute_qty(1000, 0.05, 100, 99, 0.001)
        # risk_per_unit = 1 * 0.001 = 0.001
        # qty = 50 / 0.001 = 50000
        # notional = 50000 * 100 * 0.001 = 5000  ≤ 10000  →  not capped
        assert qty == 50000.0

    def test_cv_zero_falls_back_to_one(self):
        qty = _compute_qty(1000, 0.05, 100, 99, 0)
        assert qty == 50.0   # cv=0 becomes 1

    def test_entry_equals_sl_returns_zero(self):
        assert _compute_qty(1000, 0.05, 100, 100, 1) == 0.0

    def test_nan_capital_returns_zero(self):
        assert _compute_qty(float("nan"), 0.05, 100, 99, 1) == 0.0

    def test_inf_risk_amount_returns_zero(self):
        assert _compute_qty(1000, float("inf"), 100, 99, 1) == 0.0

    def test_sell_side_same_qty(self):
        qty = _compute_qty(1000, 0.05, 100, 101, 1)
        assert qty == 50.0

    def test_sl_loss_equals_intended_risk_when_not_capped(self):
        """SL hit should produce PnL = -capital * risk_amount when not capped."""
        cap, risk = 1000, 0.05
        entry, sl, cv = 100, 99, 1
        qty = _compute_qty(cap, risk, entry, sl, cv)
        pnl = (sl - entry) * qty * cv
        assert abs(pnl + cap * risk) < 0.001

    def test_sl_loss_always_equals_intended_risk(self):
        """Every SL hit produces PnL = -capital * risk_amount (no cap)."""
        cap, risk = 1000, 0.5   # intended risk = 500
        entry, sl, cv = 100, 99, 1
        qty = _compute_qty(cap, risk, entry, sl, cv)
        pnl = (sl - entry) * qty * cv
        assert abs(pnl + cap * risk) < 0.001   # exactly -500

    def test_qty_determined_by_risk_not_leverage(self):
        """Qty is sized to risk_amount, leverage parameter does not cap it."""
        cap, lev, entry, cv = 1000, 10, 100, 1
        qty = _compute_qty(cap, 1.0, entry, 99, cv)
        # risk_capital = 1000 * 1.0 = 1000, risk_per_unit = 1
        # qty = 1000 / 1 = 1000  (not capped to 10000/100)
        assert qty == 1000.0


# ---------------------------------------------------------------------------
# _compute_sl_price
# ---------------------------------------------------------------------------
class TestComputeSlPrice:
    def test_buy(self):
        sl = _compute_sl_price(100, "buy", 2.0, 1.5)
        assert sl == 100 - 2.0 * 1.5  # 97

    def test_sell(self):
        sl = _compute_sl_price(100, "sell", 2.0, 1.5)
        assert sl == 100 + 2.0 * 1.5  # 103


# ---------------------------------------------------------------------------
# signal_logic
# ---------------------------------------------------------------------------
class TestSignalLogic:
    def test_buy_signal(self):
        result = signal_logic(trend_1h=1, trend_15m=1, adx_15m=25, rsi_15m=55, volume_24h=2_000_000)
        assert "Buy" in result

    def test_sell_signal(self):
        result = signal_logic(trend_1h=-1, trend_15m=-1, adx_15m=25, rsi_15m=45, volume_24h=2_000_000)
        assert "Sell" in result

    def test_no_trade_low_adx(self):
        result = signal_logic(trend_1h=1, trend_15m=1, adx_15m=15, rsi_15m=55, volume_24h=2_000_000)
        assert result == "NO TRADE"

    def test_no_trade_rsi_against(self):
        result = signal_logic(trend_1h=1, trend_15m=1, adx_15m=25, rsi_15m=45, volume_24h=2_000_000)
        assert result == "NO TRADE"

    def test_no_trade_low_volume(self):
        result = signal_logic(trend_1h=1, trend_15m=1, adx_15m=25, rsi_15m=55, volume_24h=500_000)
        assert result == "NO TRADE"

    def test_no_trade_flat_trend(self):
        result = signal_logic(trend_1h=0, trend_15m=1, adx_15m=25, rsi_15m=55, volume_24h=2_000_000)
        assert result == "NO TRADE"


# ---------------------------------------------------------------------------
# Helper to generate synthetic Delta-API candle data
# ---------------------------------------------------------------------------
def _make_candles(count: int, start_ts: int, step_s: int,
                  price_open: float = 100.0, slope: float = 0.0,
                  amp: float = 0.5) -> list:
    """Create synthetic candle dicts like Delta API returns."""
    candles = []
    for i in range(count):
        t = start_ts + i * step_s
        o = price_open + slope * i + amp * np.sin(i * 0.3)
        c = price_open + slope * (i + 1) + amp * np.sin((i + 1) * 0.3)
        h = max(o, c) + amp * 0.5
        l_ = min(o, c) - amp * 0.5
        candles.append({
            "time": t,
            "open": f"{o:.2f}",
            "high": f"{h:.2f}",
            "low": f"{l_:.2f}",
            "close": f"{c:.2f}",
            "volume": "2000.0",
        })
    return candles


def _make_product(symbol: str, cv: str = "0.001", vol: str = "50000000") -> dict:
    return {"result": [{"symbol": symbol, "contract_value": cv,
                        "turnover_usd_24h": vol}]}


# ---------------------------------------------------------------------------
# Full backtest integration test
# ---------------------------------------------------------------------------
class TestBacktestRun:
    """End-to-end test with mocked Delta API.

    Synthetic 5m / 15m / 1h candles create conditions that trigger
    at least one trade so we can verify every field in the output.
    """

    SYMBOL = "TESTBTC"
    START_TS = 1_700_000_000  # arbitrary
    N_5M = 400                # ~ 33 h  of 5m data
    STEP_5M = 300

    @pytest.fixture
    async def client(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    def _gen_mocks(self):
        """Return (candles_5m, candles_15m, candles_1h, products)."""
        # 5m – modest uptrend so indicators warm up reasonably
        c5 = _make_candles(self.N_5M, self.START_TS, self.STEP_5M,
                           price_open=100, slope=0.008, amp=0.4)
        # 15m – every 3rd 5m candle
        step_15m = self.STEP_5M * 3
        n_15m = self.N_5M // 3 + 1
        c15 = _make_candles(n_15m, self.START_TS, step_15m,
                            price_open=100, slope=0.024, amp=0.8)
        # 1h – every 12th 5m candle
        step_1h = self.STEP_5M * 12
        n_1h = self.N_5M // 12 + 1
        c1h = _make_candles(n_1h, self.START_TS, step_1h,
                            price_open=100, slope=0.096, amp=1.5)
        prod = _make_product(self.SYMBOL, "0.001", "50000000")
        return c5, c15, c1h, prod

    async def test_run_backtest_returns_trades(self, client):
        c5, c15, c1h, prod = self._gen_mocks()

        # We need to return different data depending on the path passed
        # to _delta_get.  The strategy module calls:
        #   _delta_get(f"/history/candles?symbol={sym}&resolution=5m&...")
        #   _delta_get(f"/history/candles?symbol={sym}&resolution=15m&...")
        #   _delta_get(f"/history/candles?symbol={sym}&resolution=1h&...")
        #   _delta_get("/products")
        async def _mock_delta_get(path: str) -> dict:
            if "resolution=5m" in path:
                return {"result": c5}
            if "resolution=15m" in path:
                return {"result": c15}
            if "resolution=1h" in path:
                return {"result": c1h}
            if "/products" in path:
                return prod
            return {"result": []}

        with patch("app.routers.strategy._delta_get",
                   new=AsyncMock(side_effect=_mock_delta_get)):
            payload = {
                "symbol": self.SYMBOL,
                "strategy": "Pullback Breakout",
                "capital": 2000,
                "risk_amount": 0.05,
                "fee_rate": 0.0005,
                "leverage": 10,
                "start_date": "2023-11-15",
                "end_date": "2023-11-16",
            }
            resp = await client.post("/api/strategy/run", json=payload)
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True, data.get("error", "unknown error")

        # ---- verify report ----
        r = data["report"]
        assert r["trades"] >= 0  # may be 0 if signal conditions not met
        assert isinstance(r["win_rate"], (int, float))
        assert isinstance(r["avg_rr"], (int, float))
        assert isinstance(r["total_pnl"], (int, float))
        assert isinstance(r["max_dd"], (int, float))
        assert isinstance(r["equity_curve"], list)
        assert len(r["equity_curve"]) == r["trades"]

        # ---- verify each trade ----
        for t in data["trades"]:
            # all required keys present
            assert "entry_date" in t
            assert "exit_date" in t
            assert "direction" in t
            assert "qty" in t
            assert "entry_price" in t
            assert "sl" in t
            assert "exit_price" in t
            assert "leverage" in t
            assert "pnl" in t
            assert "rr" in t

            # types
            assert isinstance(t["direction"], str)
            assert t["direction"] in ("Buy", "Sell")
            assert isinstance(t["qty"], (int, float))
            assert isinstance(t["entry_price"], (int, float))
            assert isinstance(t["sl"], (int, float))
            assert isinstance(t["exit_price"], (int, float))
            assert isinstance(t["leverage"], (int, float))
            assert isinstance(t["pnl"], (int, float))
            assert isinstance(t["rr"], (int, float))

            # basic sanity
            assert t["qty"] > 0
            assert t["entry_price"] > 0
            assert t["sl"] > 0
            assert t["exit_price"] > 0
            assert t["leverage"] > 0
            assert isinstance(t["entry_date"], int)
            assert isinstance(t["exit_date"], int)
            # entry date ≤ exit date
            assert t["entry_date"] <= t["exit_date"]

            # RR sign matches PnL sign
            if t["pnl"] > 0:
                assert t["rr"] > 0, f"positive PnL but RR={t['rr']}"
            elif t["pnl"] < 0:
                assert t["rr"] < 0, f"negative PnL but RR={t['rr']}"
            else:
                assert t["rr"] == 0

            # direction string consistency
            assert t["direction"] in ("Buy", "Sell")

    async def test_backtest_value_flow(self, client):
        """Verify input values reach calculations correctly.

        Use wide SL to avoid SL hits, making it easier to verify
        the computed trade values.
        """
        # Use a tiny range with a moderate uptrend so the strategy
        # likely triggers at least one trade.
        c5 = _make_candles(self.N_5M, self.START_TS, self.STEP_5M,
                           price_open=100, slope=0.01, amp=0.3)
        c15 = _make_candles(self.N_5M // 3 + 1, self.START_TS,
                            self.STEP_5M * 3,
                            price_open=100, slope=0.03, amp=0.6)
        c1h = _make_candles(self.N_5M // 12 + 1, self.START_TS,
                            self.STEP_5M * 12,
                            price_open=100, slope=0.12, amp=1.2)
        prod = _make_product(self.SYMBOL, "0.001", "50000000")

        async def _mock_delta_get(path: str) -> dict:
            if "resolution=5m" in path:
                return {"result": c5}
            if "resolution=15m" in path:
                return {"result": c15}
            if "resolution=1h" in path:
                return {"result": c1h}
            if "/products" in path:
                return prod
            return {"result": []}

        capital = 2000.0
        risk_amount = 0.05  # 5 %
        leverage = 10.0

        with patch("app.routers.strategy._delta_get",
                   new=AsyncMock(side_effect=_mock_delta_get)):
            resp = await client.post("/api/strategy/run", json={
                "symbol": self.SYMBOL,
                "strategy": "Pullback Breakout",
                "capital": capital,
                "risk_amount": risk_amount,
                "fee_rate": 0.0005,
                "leverage": leverage,
                "start_date": "2023-11-15",
                "end_date": "2023-11-16",
            })
            data = resp.json()
            assert data["success"] is True, data.get("error", "")

        for t in data.get("trades", []):
            # Contract value from mock = 0.001
            cv = 0.001
            entry = t["entry_price"]
            sl = t["sl"]
            qty = t["qty"]
            t_lev = t["leverage"]

            # Actual leverage stored in trade should be
            # (qty * entry * cv) / capital  (rounded to 2 decimal places)
            expected_lev = round((qty * entry * cv) / capital, 2)
            assert abs(t_lev - expected_lev) < 0.02, \
                f"leverage mismatch: trade={t_lev} expected={expected_lev}"

            # SL distance
            sl_dist = abs(entry - sl)

            # Entry risk stored in engine state would be: sl_dist * qty * cv
            entry_risk = sl_dist * qty * cv

            # Trade PnL / entry_risk should equal rr
            # (only for trades closed by SL hit or manual exit; end-of-data
            #  close may have different pnl)
            pnl = t["pnl"]
            rr = t["rr"]
            if entry_risk > 0:
                # Check that RR is consistent (may not be exactly
                # pnl/entry_risk for end-of-data forced closes)
                expected_rr = round(pnl / entry_risk, 2)
                # Allow minor deviation for rounding
                assert abs(rr - expected_rr) < 0.02, \
                    f"RR mismatch: trade={rr} expected={expected_rr} " \
                    f"pnl={pnl} entry_risk={entry_risk}"

    async def test_sl_hit_produces_rr_minus_one(self, client):
        """Trades closed by SL hit should have RR ≈ -1.0."""
        # Create very tight range so SL is hit quickly.
        # Need enough candles for valid indicators.
        np.random.seed(42)
        n = 400
        ts = self.START_TS
        step = self.STEP_5M
        c5 = []
        price = 100.0
        for i in range(n):
            t = ts + i * step
            # Random walk with small moves so ATR is small
            price += np.random.randn() * 0.05
            o = price - 0.02
            c = price + 0.02
            c5.append({
                "time": t,
                "open": f"{o:.2f}",
                "high": f"{max(o, c) + 0.05:.2f}",
                "low": f"{min(o, c) - 0.05:.2f}",
                "close": f"{c:.2f}",
                "volume": "5000.0",
            })

        c15 = _make_candles(n // 3 + 1, ts, step * 3, price_open=100, amp=1.0)
        c1h = _make_candles(n // 12 + 1, ts, step * 12, price_open=100, amp=2.0)
        prod = _make_product(self.SYMBOL, "0.001", "50000000")

        async def _mock_delta_get(path: str) -> dict:
            if "resolution=5m" in path:
                return {"result": c5}
            if "resolution=15m" in path:
                return {"result": c15}
            if "resolution=1h" in path:
                return {"result": c1h}
            if "/products" in path:
                return prod
            return {"result": []}

        with patch("app.routers.strategy._delta_get",
                   new=AsyncMock(side_effect=_mock_delta_get)):
            resp = await client.post("/api/strategy/run", json={
                "symbol": self.SYMBOL,
                "strategy": "Pullback Breakout",
                "capital": 2000,
                "risk_amount": 0.05,
                "fee_rate": 0.0005,
                "leverage": 10,
                "start_date": "2023-11-15",
                "end_date": "2023-11-16",
            })
            data = resp.json()
            assert data["success"] is True, data.get("error", "")

        # If any trades exist, check SL-hit trades have RR ≈ -1.0
        for t in data.get("trades", []):
            entry, sl, exit_px = t["entry_price"], t["sl"], t["exit_price"]
            dir_ = t["direction"]
            # Detect SL-hit: exit price matches sl exactly (or within 1 tick)
            sl_hit = abs(exit_px - sl) < 0.01
            if sl_hit:
                assert abs(t["rr"] - (-1.0)) < 0.02, \
                    f"SL-hit trade RR={t['rr']} should be -1.0 " \
                    f"entry={entry} sl={sl} exit={exit_px} pnl={t['pnl']}"

    async def test_input_values_reach_backend(self, client):
        """Verify capital, risk_amount, leverage are received and used."""
        c5 = _make_candles(300, self.START_TS, self.STEP_5M,
                           price_open=100, slope=0.005, amp=0.3)
        c15 = _make_candles(100, self.START_TS, self.STEP_5M * 3,
                            price_open=100, slope=0.015, amp=0.6)
        c1h = _make_candles(25, self.START_TS, self.STEP_5M * 12,
                            price_open=100, slope=0.06, amp=1.2)
        prod = _make_product(self.SYMBOL, "0.001", "50000000")

        async def _mock_delta_get(path: str) -> dict:
            if "resolution=5m" in path:
                return {"result": c5}
            if "resolution=15m" in path:
                return {"result": c15}
            if "resolution=1h" in path:
                return {"result": c1h}
            if "/products" in path:
                return prod
            return {"result": []}

        with patch("app.routers.strategy._delta_get",
                   new=AsyncMock(side_effect=_mock_delta_get)):
            resp = await client.post("/api/strategy/run", json={
                "symbol": self.SYMBOL,
                "strategy": "Pullback Breakout",
                "capital": 5000,
                "risk_amount": 0.02,
                "fee_rate": 0.001,
                "leverage": 25,
                "start_date": "2023-11-15",
                "end_date": "2023-11-16",
            })
            data = resp.json()
            assert data["success"] is True, data.get("error", "")

        # Spot-check trades for correct capital/risk scaling
        intended_risk = 5000 * 0.02  # 100
        for t in data.get("trades", []):
            entry, sl, qty = t["entry_price"], t["sl"], t["qty"]
            cv = 0.001
            # Entry risk = SL distance * qty * cv
            entry_risk = abs(entry - sl) * qty * cv
            # With no leverage cap, entry_risk should equal intended risk
            assert abs(entry_risk - intended_risk) < intended_risk * 0.02 + 1, \
                f"entry_risk={entry_risk} expected ~{intended_risk}"
