import numpy as np
import pytest

from app.engine.volume_delta import (
    compute_volume_delta_lower_tf,
    compute_volume_delta_batch,
    find_poc,
)


class TestComputeVolumeDeltaLowerTf:
    def test_basic_delta(self):
        time_1m = np.array([100.0, 101.0, 102.0, 103.0])
        open_1m = np.array([100.0, 101.0, 102.0, 103.0])
        high_1m = np.array([101.0, 102.0, 103.0, 104.0])
        low_1m = np.array([99.0, 100.0, 101.0, 102.0])
        close_1m = np.array([100.5, 101.5, 102.5, 103.5])
        volume_1m = np.array([100.0, 200.0, 150.0, 250.0])

        result = compute_volume_delta_lower_tf(
            open_1m, high_1m, low_1m, close_1m, volume_1m,
            time_1m, 100.0, 104.0,
        )
        assert result["bull"] > 0
        assert result["bear"] > 0
        assert isinstance(result["delta"], float)
        assert -1.0 <= result["norm"] <= 1.0

    def test_no_candles_in_range(self):
        time_1m = np.array([100.0, 101.0])
        result = compute_volume_delta_lower_tf(
            np.array([]), np.array([]), np.array([]),
            np.array([]), np.array([]),
            time_1m, 200.0, 300.0,
        )
        assert result["bull"] == 0.0
        assert result["bear"] == 0.0
        assert result["delta"] == 0.0
        assert result["norm"] == 0.0

    def test_all_bullish_candles(self):
        time_1m = np.array([0.0, 60.0, 120.0])
        open_1m = np.array([100.0, 101.0, 100.0])
        high_1m = np.array([102.0, 103.0, 104.0])
        low_1m = np.array([99.0, 100.5, 99.5])
        close_1m = np.array([101.5, 102.5, 103.5])
        volume_1m = np.array([100.0, 100.0, 100.0])

        result = compute_volume_delta_lower_tf(
            open_1m, high_1m, low_1m, close_1m, volume_1m,
            time_1m, 0.0, 180.0,
        )
        assert result["bull"] > result["bear"]
        assert result["norm"] > 0

    def test_all_bearish_candles(self):
        time_1m = np.array([0.0, 60.0, 120.0])
        open_1m = np.array([103.0, 102.0, 101.0])
        high_1m = np.array([103.5, 102.5, 101.5])
        low_1m = np.array([100.0, 99.0, 98.0])
        close_1m = np.array([100.5, 99.5, 98.5])
        volume_1m = np.array([100.0, 100.0, 100.0])

        result = compute_volume_delta_lower_tf(
            open_1m, high_1m, low_1m, close_1m, volume_1m,
            time_1m, 0.0, 180.0,
        )
        assert result["bear"] > result["bull"]
        assert result["norm"] < 0

    def test_poc_detection(self):
        time_1m = np.array([0.0, 60.0, 120.0])
        open_1m = np.array([100.0, 101.0, 102.0])
        high_1m = np.array([101.0, 102.0, 103.0])
        low_1m = np.array([99.0, 100.0, 101.0])
        close_1m = np.array([100.5, 101.5, 102.5])
        volume_1m = np.array([50.0, 300.0, 100.0])

        result = compute_volume_delta_lower_tf(
            open_1m, high_1m, low_1m, close_1m, volume_1m,
            time_1m, 0.0, 180.0,
        )
        assert result["poc"] == pytest.approx(101.5)


class TestComputeVolumeDeltaBatch:
    def test_basic_batch(self):
        time_1m = np.array([0.0, 60.0, 120.0, 180.0, 240.0])
        open_1m = np.array([100.0, 101.0, 102.0, 103.0, 104.0])
        high_1m = np.array([101.0, 102.0, 103.0, 104.0, 105.0])
        low_1m = np.array([99.0, 100.0, 101.0, 102.0, 103.0])
        close_1m = np.array([100.5, 101.5, 102.5, 103.5, 104.5])
        volume_1m = np.array([100.0, 100.0, 100.0, 100.0, 100.0])
        parent_times = np.array([0.0, 120.0])

        result = compute_volume_delta_batch(
            time_1m, open_1m, high_1m, low_1m, close_1m, volume_1m,
            parent_times, 120,
        )
        assert len(result["norm"]) == 2
        assert len(result["poc"]) == 2
        assert isinstance(result["norm"][0], float)
        assert isinstance(result["poc"][0], float)

    def test_batch_empty_parents(self):
        time_1m = np.array([0.0, 60.0])
        parent_times = np.array([])
        result = compute_volume_delta_batch(
            time_1m, np.array([]), np.array([]), np.array([]),
            np.array([]), np.array([]), parent_times, 60,
        )
        assert len(result["norm"]) == 0
        assert len(result["poc"]) == 0

    def test_batch_single_parent(self):
        time_1m = np.array([0.0, 60.0])
        open_1m = np.array([100.0, 101.0])
        high_1m = np.array([102.0, 103.0])
        low_1m = np.array([99.0, 100.0])
        close_1m = np.array([101.0, 101.5])
        volume_1m = np.array([200.0, 200.0])
        parent_times = np.array([0.0])

        result = compute_volume_delta_batch(
            time_1m, open_1m, high_1m, low_1m, close_1m, volume_1m,
            parent_times, 120,
        )
        assert len(result["norm"]) == 1
        assert isinstance(result["norm"][0], float)


class TestFindPoc:
    def test_find_poc_bull_higher(self):
        bull = np.array([10.0, 50.0, 30.0])
        bear = np.array([20.0, 10.0, 25.0])
        close_arr = np.array([100.0, 101.0, 102.0])
        result = find_poc(bull, bear, close_arr)
        assert result == pytest.approx(101.0)

    def test_find_poc_bear_higher(self):
        bull = np.array([10.0, 20.0, 15.0])
        bear = np.array([30.0, 50.0, 25.0])
        close_arr = np.array([100.0, 101.0, 102.0])
        result = find_poc(bull, bear, close_arr)
        assert result == pytest.approx(101.0)

    def test_find_poc_empty_bull(self):
        result = find_poc(np.array([]), np.array([1.0]), np.array([100.0]))
        assert np.isnan(result)

    def test_find_poc_empty_both(self):
        result = find_poc(np.array([]), np.array([]), np.array([]))
        assert np.isnan(result)

    def test_find_poc_single_element(self):
        result = find_poc(np.array([5.0]), np.array([3.0]), np.array([100.0]))
        assert result == pytest.approx(100.0)

    def test_find_poc_tie_bull_wins(self):
        bull = np.array([10.0, 10.0])
        bear = np.array([10.0, 10.0])
        close_arr = np.array([100.0, 101.0])
        result = find_poc(bull, bear, close_arr)
        assert not np.isnan(result)
