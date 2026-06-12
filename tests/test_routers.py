from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestHealth:
    async def test_health(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["app"] == "DDScanner"
        assert "uptime" in data

    async def test_root(self, client):
        resp = await client.get("/")
        assert resp.status_code == 307

    async def test_restart(self, admin_client):
        resp = await admin_client.post("/restart")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "restarting"


class TestAlgo:
    async def test_algo_status(self, client):
        resp = await client.get("/api/algo/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] == True
        assert isinstance(data["status"], dict)

    async def test_algo_start_and_pause(self, admin_client):
        resp = await admin_client.post("/api/algo/start", json={
            "symbol": "TEST_SYM", "api_key": "k", "api_secret": "s",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] == True
        assert data["symbol"] == "TEST_SYM"
        resp2 = await admin_client.post("/api/algo/pause", json={"symbol": "TEST_SYM"})
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["success"] == True

    async def test_algo_start_with_symbol(self, admin_client):
        with patch("app.services.algo_service.start_algo", return_value=True):
            resp = await admin_client.post("/api/algo/start", json={
                "symbol": "BTCUSDT", "api_key": "k", "api_secret": "s",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] == True

    async def test_algo_pause_valid(self, client):
        with patch("app.services.algo_service.pause_algo", new=AsyncMock(return_value=True)):
            resp = await client.post("/api/algo/pause", json={"symbol": "BTCUSDT"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] == True


@pytest.fixture(autouse=True)
def _protect_screener_state():
    """Save/restore screener module state and prevent file persistence."""
    import app.routers.analysis as analysis_mod
    orig_symbols = list(analysis_mod._screener_symbols)
    with patch.object(analysis_mod, "_persist_screener_symbols", return_value=None):
        yield
    analysis_mod._screener_symbols[:] = orig_symbols


class TestAnalysis:
    async def test_screener_symbols_empty(self, client):
        resp = await client.get("/api/screener/symbols")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["symbols"], list)

    async def test_update_screener_symbols(self, client):
        with patch("app.routers.analysis._persist_screener_symbols"):
            resp = await client.post("/api/screener/symbols", json={"symbols": ["BTCUSDT"]})
            assert resp.status_code == 200
            data = resp.json()
            assert "BTCUSDT" in data["symbols"]

    async def test_update_screener_symbols_empty(self, client):
        resp = await client.post("/api/screener/symbols", json={"symbols": []})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["symbols"], list)

    async def test_ticker_lightweight(self, client):
        mock_candles = {
            "time": np.array([1000]),
            "open": np.array([100.0]),
            "high": np.array([101.0]),
            "low": np.array([99.0]),
            "close": np.array([100.5]),
            "volume": np.array([1000.0]),
        }
        mock_ticker = {"result": {"mark_price": "101.50", "close": "100.50"}}

        with patch("app.routers.analysis.delta_client.get_ticker", new=AsyncMock(return_value=mock_ticker)), \
             patch("app.routers.analysis.delta_client.get_candles", new=AsyncMock(return_value=mock_candles)):
            resp = await client.get("/api/ticker/BTCUSDT")
            assert resp.status_code == 200
            data = resp.json()
            assert data["last_price"] == 101.50
            assert data["last_close"] == 100.5

    async def test_ticker_fallback_on_error(self, client):
        with patch("app.routers.analysis.delta_client.get_ticker", new=AsyncMock(side_effect=Exception("API error"))), \
             patch("app.routers.analysis.delta_client.get_candles", new=AsyncMock(side_effect=Exception("API error"))):
            resp = await client.get("/api/ticker/BTCUSDT")
            assert resp.status_code == 200
            data = resp.json()
            assert data["last_price"] == 0
            assert data["last_close"] == 0

    async def test_validate_symbol_invalid(self, client):
        with patch("app.routers.analysis.delta_client.get_candles", new=AsyncMock(return_value={"close": []})):
            resp = await client.get("/api/screener/validate-symbol?symbol=INVALIDXYZ")
            assert resp.status_code == 200
            data = resp.json()
            assert data["valid"] == False

    async def test_validate_symbol_valid(self, client):
        mock_candles = {
            "time": np.array([1000, 2000]),
            "open": np.array([100.0, 101.0]),
            "high": np.array([101.0, 102.0]),
            "low": np.array([99.0, 100.0]),
            "close": np.array([100.5, 101.5]),
            "volume": np.array([1000.0, 1100.0]),
        }
        with patch("app.routers.analysis.delta_client.get_candles", new=AsyncMock(return_value=mock_candles)):
            resp = await client.get("/api/screener/validate-symbol?symbol=BTCUSDT")
            assert resp.status_code == 200
            data = resp.json()
            assert data["valid"] == True


class TestAlgoPrices:
    async def test_prices_empty(self, client):
        resp = await client.post("/api/algo/prices", json={"symbols": []})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] == True
        assert data["prices"] == {}

    async def test_prices_with_symbols(self, client):
        mock_ticker = {"result": {"mark_price": "50000.0"}}
        with patch("app.routers.algo.delta_client.get_ticker", new=AsyncMock(return_value=mock_ticker)):
            resp = await client.post("/api/algo/prices", json={"symbols": ["BTCUSDT"]})
            assert resp.status_code == 200
            data = resp.json()
            assert data["prices"]["BTCUSDT"] == 50000.0

    async def test_prices_fallback_on_error(self, client):
        with patch("app.routers.algo.delta_client.get_ticker", new=AsyncMock(side_effect=Exception("API error"))), \
             patch("app.routers.algo._price_cache", new={}):
            resp = await client.post("/api/algo/prices", json={"symbols": ["BTCUSDT"]})
            assert resp.status_code == 200
            data = resp.json()
            assert data["prices"]["BTCUSDT"] is None


class TestStrategy:
    async def test_list_strategies(self, client):
        resp = await client.post("/api/strategy/list")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] == True
        assert isinstance(data["strategies"], list)
        names = [s["name"] for s in data["strategies"]]
        assert "Pullback Breakout" in names

    async def test_load_strategy(self, admin_client):
        resp = await admin_client.post("/api/strategy/load", json={"name": "Pullback Breakout"})
        assert resp.status_code == 200
        data = resp.json()
        assert "code" in data
        assert "def on_candle" in data["code"]

    async def test_load_nonexistent(self, admin_client):
        resp = await admin_client.post("/api/strategy/load", json={"name": "nonexistent_xyz"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] == False

    async def test_get_template(self, admin_client):
        resp = await admin_client.post("/api/strategy/template")
        assert resp.status_code == 200
        data = resp.json()
        assert "code" in data
        assert "def on_candle" in data["code"]

    async def test_save_strategy(self, admin_client):
        code = """
name = "Test Strat"
params = {}
def on_candle(candle, state):
    return None
"""
        resp = await admin_client.post("/api/strategy/save", json={"name": "test_strat", "code": code})
        assert resp.status_code == 200
        data = resp.json()
        assert "file" in data
        assert data["success"] == True
        # Clean up persisted file
        import os
        fpath = data.get("file", "")
        if fpath and os.path.exists(fpath):
            os.unlink(fpath)

    async def test_save_strategy_invalid_name(self, admin_client):
        resp = await admin_client.post("/api/strategy/save", json={"name": "", "code": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] == False

    async def test_run_backtest_no_symbol(self, client):
        resp = await client.post("/api/strategy/run", json={
            "symbol": "",
            "strategy": "Pullback Breakout",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] == False

    async def test_run_backtest_no_strategy_name(self, client):
        resp = await client.post("/api/strategy/run", json={
            "symbol": "BTCUSDT",
            "strategy": "",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] == False


class TestTrading:
    async def test_check_product_empty_symbol(self, client):
        resp = await client.get("/api/trade/check-product")
        assert resp.status_code == 200
        data = resp.json()
        assert "symbol" in data

    async def test_check_api_missing_credentials(self, client):
        resp = await client.get("/api/trade/check-api")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] == False

    async def test_check_api_success(self, client):
        with patch("app.routers.trading._delta_auth_get", new=AsyncMock(return_value={"status": 200, "body": {"result": []}})):
            resp = await client.get(
                "/api/trade/check-api",
                params={"api_key": "good", "api_secret": "good"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["valid"] == True

    async def test_check_api_failure(self, client):
        with patch("app.routers.trading._delta_auth_get", new=AsyncMock(return_value={"status": 401, "body": {"error": "unauthorized"}})):
            resp = await client.get(
                "/api/trade/check-api",
                params={"api_key": "bad", "api_secret": "bad"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["valid"] == False

    async def test_contract_details_no_symbol(self, client):
        resp = await client.get("/api/trade/contract-details")
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
