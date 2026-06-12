import numpy as np
import pytest

from app.indicators.rsi import rsi
from app.indicators.ema import ema
from app.indicators.rma import rma
from app.indicators.atr import atr
from app.indicators.adx import dmi
from app.indicators.trend import trend_state, trend_status, trend_color


class TestRMA:
    def test_basic_rma(self):
        close = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        result = rma(close, 5)
        assert len(result) == 10
        assert np.isnan(result[3])
        assert not np.isnan(result[4])
        assert result[4] == pytest.approx(3.0)

    def test_rma_too_short(self):
        close = np.array([1.0, 2.0])
        result = rma(close, 5)
        assert np.all(np.isnan(result))

    def test_rma_with_nan_values(self):
        close = np.array([1.0, np.nan, 3.0, 4.0, 5.0, 6.0])
        result = rma(close, 3)
        assert np.isnan(result[1])
        assert not np.isnan(result[2])
        assert result[4] > 0

    def test_rma_zero_length(self):
        close = np.array([1.0, 2.0, 3.0])
        result = rma(close, 0)
        assert np.all(np.isnan(result))

    def test_rma_constant_values(self):
        close = np.full(20, 5.0)
        result = rma(close, 5)
        assert not np.isnan(result[4])
        assert np.allclose(result[4:], 5.0)


class TestEMA:
    def test_basic_ema(self):
        close = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = ema(close, 3)
        assert len(result) == 5
        assert result[0] == 1.0
        assert not np.isnan(result[-1])

    def test_ema_too_short(self):
        close = np.array([1.0])
        result = ema(close, 5)
        assert result[0] == 1.0

    def test_ema_empty(self):
        close = np.array([])
        result = ema(close, 5)
        assert len(result) == 0

    def test_ema_zero_length(self):
        close = np.array([1.0, 2.0, 3.0])
        result = ema(close, 0)
        assert np.all(np.isnan(result))

    def test_ema_with_nan(self):
        close = np.array([np.nan, 2.0, 3.0, np.nan, 5.0])
        result = ema(close, 3)
        assert np.isnan(result[0])
        assert not np.isnan(result[1])
        assert result[2] > 0
        assert result[3] == result[2]

    def test_ema_leading_nans(self):
        close = np.array([np.nan, np.nan, 1.0, 2.0, 3.0])
        result = ema(close, 2)
        assert np.isnan(result[0])
        assert np.isnan(result[1])
        assert result[2] == 1.0

    def test_ema_all_nan(self):
        close = np.full(5, np.nan)
        result = ema(close, 3)
        assert np.all(np.isnan(result))

    def test_ema_rising_values(self):
        close = np.arange(1.0, 101.0)
        result = ema(close, 10)
        assert result[-1] > result[0]
        assert np.all(np.diff(result[10:]) > 0)


class TestRSI:
    def test_basic_rsi(self):
        close = np.arange(1.0, 31.0, dtype=float)
        result = rsi(close, 14)
        assert len(result) == 30
        assert np.all(np.isnan(result[:13]))
        assert not np.isnan(result[13])
        assert result[-1] == pytest.approx(99.01, abs=0.01)

    def test_rsi_too_short(self):
        close = np.array([1.0, 2.0, 3.0])
        result = rsi(close, 14)
        assert np.all(np.isnan(result))

    def test_rsi_all_down(self):
        close = np.arange(100.0, 40.0, -1.0, dtype=float)
        result = rsi(close, 14)
        assert not np.isnan(result[14])
        assert result[-1] == pytest.approx(0.0, abs=0.01)

    def test_rsi_constant(self):
        close = np.full(30, 50.0)
        result = rsi(close, 14)
        assert np.isnan(result[14])
        assert np.isnan(result[-1])

    def test_rsi_edge_all_gains(self):
        close = np.array([1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0, 34.0, 55.0, 89.0,
                          100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 160.0, 170.0,
                          180.0, 190.0], dtype=float)
        result = rsi(close, 14)
        assert not np.isnan(result[-1])
        assert result[-1] > 99.0

    def test_rsi_oscillating(self):
        x = np.sin(np.linspace(0, 4 * np.pi, 100)) * 10 + 50
        result = rsi(x, 14)
        assert not np.isnan(result[14])
        assert np.all((result[14:] >= 0) & (result[14:] <= 100))


class TestATR:
    def test_basic_atr(self):
        high = np.arange(1.0, 101.0)
        low = np.arange(0.5, 100.5)
        close = np.arange(0.8, 100.8)
        result = atr(high, low, close, 14)
        assert len(result) == 100
        assert np.all(np.isnan(result[:13]))
        assert result[13] > 0

    def test_atr_too_short(self):
        high = np.array([1.0])
        low = np.array([0.5])
        close = np.array([0.8])
        result = atr(high, low, close, 14)
        assert len(result) == 1
        assert np.isnan(result[0])

    def test_atr_constant_range(self):
        high = np.full(50, 110.0)
        low = np.full(50, 90.0)
        close = np.full(50, 100.0)
        result = atr(high, low, close, 14)
        assert not np.isnan(result[13])
        assert result[13] == pytest.approx(20.0, abs=0.1)

    def test_atr_zero_length(self):
        high = np.array([1.0, 2.0])
        low = np.array([0.5, 1.5])
        close = np.array([0.8, 1.8])
        result = atr(high, low, close, 0)
        assert np.all(np.isnan(result))


class TestADX:
    def test_basic_dmi(self):
        n = 60
        high = np.linspace(100.0, 159.0, n)
        low = np.linspace(99.5, 158.5, n)
        close = np.linspace(99.8, 158.8, n)
        plus_di, minus_di, adx_val = dmi(high, low, close, 14)
        assert len(plus_di) == n
        assert len(minus_di) == n
        assert len(adx_val) == n
        assert np.isnan(adx_val[0])
        assert not np.isnan(adx_val[13])

    def test_dmi_too_short(self):
        high = np.array([1.0, 2.0])
        low = np.array([0.5, 1.5])
        close = np.array([0.8, 1.8])
        plus_di, minus_di, adx_val = dmi(high, low, close, 14)
        assert np.all(np.isnan(adx_val))

    def test_dmi_strong_trend(self):
        n = 60
        high = np.linspace(100.0, 159.0, n)
        low = np.linspace(99.0, 158.0, n)
        close = np.linspace(99.5, 158.5, n)
        plus_di, minus_di, adx_val = dmi(high, low, close, 14)
        assert plus_di[-1] > minus_di[-1]

    def test_dmi_strong_downtrend(self):
        n = 60
        high = np.linspace(159.0, 100.0, n)
        low = np.linspace(158.0, 99.0, n)
        close = np.linspace(158.5, 99.5, n)
        plus_di, minus_di, adx_val = dmi(high, low, close, 14)
        assert minus_di[-1] > plus_di[-1]

    def test_dmi_adx_values_in_range(self):
        n = 60
        high = np.linspace(100.0, 159.0, n)
        low = np.linspace(99.5, 158.5, n)
        close = np.linspace(99.8, 158.8, n)
        _, _, adx_val = dmi(high, low, close, 14)
        valid = adx_val[~np.isnan(adx_val)]
        assert np.all((valid >= 0) & (valid <= 100))


class TestTrend:
    def test_trend_state_strong_bull(self):
        close = np.array([10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0,
                          18.0, 19.0, 20.0, 21.0, 22.0, 23.0, 24.0, 25.0])
        result = trend_state(close, momentum_len=8, mid_len=12)
        assert result.dtype == np.int_
        assert result[-1] == 2

    def test_trend_state_strong_bear(self):
        close = np.array([25.0, 24.0, 23.0, 22.0, 21.0, 20.0, 19.0, 18.0,
                          17.0, 16.0, 15.0, 14.0, 13.0, 12.0, 11.0, 10.0])
        result = trend_state(close, momentum_len=8, mid_len=12)
        assert result[-1] == -2

    def test_trend_state_sideways(self):
        close = np.full(30, 50.0)
        result = trend_state(close, momentum_len=8, mid_len=12)
        assert np.all(result[11:] == 0)

    def test_trend_state_length(self):
        close = np.array([1.0, 2.0, 3.0])
        result = trend_state(close, momentum_len=8, mid_len=12)
        assert len(result) == 3

    def test_trend_status_strings(self):
        assert "SB" in trend_status(2)
        assert "WB" in trend_status(1)
        assert "Sideways" in trend_status(0)
        assert "WB" in trend_status(-1)
        assert "SB" in trend_status(-2)
        assert "Sideways" in trend_status(99)

    def test_trend_color_values(self):
        assert trend_color(2) == "#002d03"
        assert trend_color(1) == "#138105"
        assert trend_color(0) == "#808080"
        assert trend_color(-1) == "#e03030"
        assert trend_color(-2) == "#4a0404"
        assert trend_color(99) == "#808080"
