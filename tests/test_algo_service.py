import numpy as np
import pytest

from app.services.algo_service import _compute_state


class TestComputeState:
    def test_buy_crossover(self):
        close = np.array([100.0, 101.0, 102.0])
        ema8 = np.array([99.0, 100.0, 103.0])
        ema50 = np.array([101.0, 101.0, 101.0])
        result = _compute_state(True, close, ema8, ema50)
        assert result == "Crossover"

    def test_buy_ready(self):
        close = np.array([100.0, 101.0, 103.0])
        ema8 = np.array([98.0, 99.0, 101.0])
        ema50 = np.array([97.0, 98.0, 99.0])
        result = _compute_state(True, close, ema8, ema50)
        assert result == "Ready"

    def test_buy_pullback(self):
        close = np.array([100.0, 101.0, 99.0])
        ema8 = np.array([98.0, 99.0, 100.0])
        ema50 = np.array([97.0, 98.0, 99.0])
        result = _compute_state(True, close, ema8, ema50)
        assert result == "Pullback"

    def test_buy_scanning(self):
        close = np.array([100.0, 101.0, 102.0])
        ema8 = np.array([98.0, 99.0, 100.0])
        ema50 = np.array([99.0, 100.0, 101.0])
        result = _compute_state(True, close, ema8, ema50)
        assert result == "Scanning"

    def test_sell_crossunder(self):
        close = np.array([100.0, 99.0, 98.0])
        ema8 = np.array([101.0, 100.0, 97.0])
        ema50 = np.array([99.0, 99.0, 99.0])
        result = _compute_state(False, close, ema8, ema50)
        assert result == "Crossunder"

    def test_sell_ready(self):
        close = np.array([100.0, 98.0, 96.0])
        ema8 = np.array([102.0, 101.0, 99.0])
        ema50 = np.array([103.0, 102.0, 101.0])
        result = _compute_state(False, close, ema8, ema50)
        assert result == "Ready"

    def test_sell_pullback(self):
        close = np.array([100.0, 98.0, 100.0])
        ema8 = np.array([102.0, 101.0, 100.0])
        ema50 = np.array([103.0, 102.0, 101.0])
        result = _compute_state(False, close, ema8, ema50)
        assert result == "Pullback"

    def test_sell_scanning(self):
        close = np.array([100.0, 99.0, 98.0])
        ema8 = np.array([102.0, 101.0, 100.0])
        ema50 = np.array([101.0, 100.0, 99.0])
        result = _compute_state(False, close, ema8, ema50)
        assert result == "Scanning"

    def test_too_short_arrays(self):
        close = np.array([1.0])
        ema8 = np.array([2.0])
        ema50 = np.array([3.0])
        result = _compute_state(True, close, ema8, ema50)
        assert result == "-"

    def test_empty_arrays(self):
        result = _compute_state(True, np.array([]), np.array([]), np.array([]))
        assert result == "-"

    def test_buy_ready_no_crossover_boundary(self):
        close = np.array([100.0, 102.0, 104.0])
        ema8 = np.array([99.0, 100.5, 102.0])
        ema50 = np.array([100.0, 100.4, 101.0])  # e8_p=100.5 > e50_p=100.4, not crossover
        result = _compute_state(True, close, ema8, ema50)
        assert result == "Ready"
