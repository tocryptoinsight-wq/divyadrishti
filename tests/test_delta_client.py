import numpy as np
import pytest

from app.data.delta_client import DeltaClient


class TestResolutionSeconds:
    def setup_method(self):
        self.client = DeltaClient()

    def test_1m(self):
        assert self.client._resolution_seconds("1m") == 60

    def test_5m(self):
        assert self.client._resolution_seconds("5m") == 300

    def test_15m(self):
        assert self.client._resolution_seconds("15m") == 900

    def test_1h(self):
        assert self.client._resolution_seconds("1h") == 3600

    def test_1d(self):
        assert self.client._resolution_seconds("1d") == 86400

    def test_unknown_resolution(self):
        assert self.client._resolution_seconds("unknown") == 3600


class TestParseCandles:
    def setup_method(self):
        self.client = DeltaClient()

    def test_empty_list(self):
        result = self.client._parse_candles([])
        assert len(result["time"]) == 0
        assert len(result["open"]) == 0
        assert len(result["close"]) == 0

    def test_single_candle(self):
        raw = [{"time": 1000, "open": "100.0", "high": "101.0", "low": "99.0", "close": "100.5", "volume": "1000"}]
        result = self.client._parse_candles(raw)
        assert result["time"][0] == 1000
        assert result["open"][0] == 100.0
        assert result["high"][0] == 101.0
        assert result["low"][0] == 99.0
        assert result["close"][0] == 100.5
        assert result["volume"][0] == 1000.0

    def test_multiple_candles_unsorted(self):
        raw = [
            {"time": 3000, "open": "102.0", "high": "103.0", "low": "101.0", "close": "102.5", "volume": "1500"},
            {"time": 1000, "open": "100.0", "high": "101.0", "low": "99.0", "close": "100.5", "volume": "1000"},
            {"time": 2000, "open": "101.0", "high": "102.0", "low": "100.0", "close": "101.5", "volume": "1200"},
        ]
        result = self.client._parse_candles(raw)
        assert np.all(result["time"] == np.array([1000, 2000, 3000]))
        assert np.all(result["close"] == np.array([100.5, 101.5, 102.5]))

    def test_dtype_float64(self):
        raw = [{"time": 1000, "open": "100.0", "high": "101.0", "low": "99.0", "close": "100.5", "volume": "1000"}]
        result = self.client._parse_candles(raw)
        assert result["open"].dtype == np.float64
        assert result["high"].dtype == np.float64
        assert result["low"].dtype == np.float64
        assert result["close"].dtype == np.float64
        assert result["volume"].dtype == np.float64

    def test_missing_fields(self):
        raw = [{"time": 1000, "open": "100.0", "high": "101.0"}]  # missing low, close, volume
        result = self.client._parse_candles(raw)
        assert result["low"][0] == 0.0
        assert result["close"][0] == 0.0
        assert result["volume"][0] == 0.0
