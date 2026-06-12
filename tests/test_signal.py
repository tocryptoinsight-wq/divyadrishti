import numpy as np
import pytest

from app.engine.signal import (
    signal_logic,
    crossover,
    crossunder,
    get_sl,
    get_tp,
    get_qty,
    adx_slope,
    adx_status,
    adx_color,
    rsi_status,
    rsi_text,
    rsi_color,
)


class TestSignalLogic:
    def test_buy_signal(self):
        result = signal_logic(trend_1h=2, trend_15m=1, adx_15m=25.0, rsi_15m=55.0, volume_24h=1_000_000)
        assert "Buy" in result

    def test_sell_signal(self):
        result = signal_logic(trend_1h=-2, trend_15m=-1, adx_15m=25.0, rsi_15m=45.0, volume_24h=1_000_000)
        assert "Sell" in result

    def test_no_trade_trend_mismatch(self):
        result = signal_logic(trend_1h=2, trend_15m=-1, adx_15m=25.0, rsi_15m=55.0)
        assert result == "NO TRADE"

    def test_no_trade_low_adx(self):
        result = signal_logic(trend_1h=2, trend_15m=1, adx_15m=15.0, rsi_15m=55.0)
        assert result == "NO TRADE"

    def test_no_trade_rsi_contra_buy(self):
        result = signal_logic(trend_1h=2, trend_15m=1, adx_15m=25.0, rsi_15m=45.0)
        assert result == "NO TRADE"

    def test_no_trade_rsi_contra_sell(self):
        result = signal_logic(trend_1h=-2, trend_15m=-1, adx_15m=25.0, rsi_15m=55.0)
        assert result == "NO TRADE"

    def test_no_trade_sideways(self):
        result = signal_logic(trend_1h=0, trend_15m=0, adx_15m=25.0, rsi_15m=55.0)
        assert result == "NO TRADE"

    def test_buy_boundary_adx_20(self):
        result = signal_logic(trend_1h=1, trend_15m=1, adx_15m=20.0, rsi_15m=50.1, volume_24h=1_000_000)
        assert "Buy" in result

    def test_sell_boundary_adx_20(self):
        result = signal_logic(trend_1h=-1, trend_15m=-1, adx_15m=20.0, rsi_15m=49.9, volume_24h=1_000_000)
        assert "Sell" in result


class TestCrossover:
    def test_crossover_detected(self):
        a = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        b = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
        result = crossover(a, b)
        assert result[3] == True
        assert result[0] == False
        assert result[1] == False

    def test_crossover_not_detected(self):
        a = np.array([1.0, 2.0, 3.0])
        b = np.array([10.0, 9.0, 8.0])
        result = crossover(a, b)
        assert not np.any(result)

    def test_crossover_too_short(self):
        a = np.array([1.0])
        b = np.array([2.0])
        result = crossover(a, b)
        assert len(result) == 1
        assert result[0] == False

    def test_crossover_empty(self):
        a = np.array([])
        b = np.array([])
        result = crossover(a, b)
        assert len(result) == 0

    def test_crossover_multiple(self):
        a = np.array([1.0, 3.0, 2.0, 4.0, 3.0])
        b = np.array([2.0, 2.0, 3.0, 3.0, 4.0])
        result = crossover(a, b)
        assert result[1] == True
        assert result[3] == True
        assert result[4] == False

    def test_crossover_exact_equal_no_cross(self):
        a = np.array([2.0, 2.0, 3.0])
        b = np.array([2.0, 2.0, 2.0])
        result = crossover(a, b)
        assert result[2] == True


class TestCrossunder:
    def test_crossunder_detected(self):
        a = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
        b = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = crossunder(a, b)
        assert result[3] == True

    def test_crossunder_not_detected(self):
        a = np.array([10.0, 9.0, 8.0])
        b = np.array([1.0, 2.0, 3.0])
        result = crossunder(a, b)
        assert not np.any(result)

    def test_crossunder_too_short(self):
        a = np.array([1.0])
        b = np.array([2.0])
        result = crossunder(a, b)
        assert result[0] == False

    def test_crossunder_empty(self):
        a = np.array([])
        b = np.array([])
        result = crossunder(a, b)
        assert len(result) == 0


class TestGetSL:
    def test_sl_atr_long(self):
        sl = get_sl(dir_val=1, sl_mode="ATR", close=100.0, low=95.0,
                    high=105.0, atr_val=2.0, atr_mult=1.5, buffer=0.25)
        expected_sl = 100.0 - (2.0 * 1.5) - (2.0 * 0.25)
        assert sl == pytest.approx(expected_sl)

    def test_sl_atr_short(self):
        sl = get_sl(dir_val=-1, sl_mode="ATR", close=100.0, low=95.0,
                    high=105.0, atr_val=2.0, atr_mult=1.5, buffer=0.25)
        expected_sl = 100.0 + (2.0 * 1.5) + (2.0 * 0.25)
        assert sl == pytest.approx(expected_sl)

    def test_sl_low_long(self):
        sl = get_sl(dir_val=1, sl_mode="LOW", close=100.0, low=95.0,
                    high=105.0, atr_val=2.0, atr_mult=1.5, buffer=0.25)
        expected_sl = 95.0 - (2.0 * 0.25)
        assert sl == pytest.approx(expected_sl)

    def test_sl_high_short(self):
        sl = get_sl(dir_val=-1, sl_mode="HIGH", close=100.0, low=95.0,
                    high=105.0, atr_val=2.0, atr_mult=1.5, buffer=0.25)
        expected_sl = 105.0 + (2.0 * 0.25)
        assert sl == pytest.approx(expected_sl)

    def test_sl_zero_buffer(self):
        sl = get_sl(dir_val=1, sl_mode="ATR", close=100.0, low=95.0,
                    high=105.0, atr_val=2.0, atr_mult=1.5, buffer=0.0)
        expected_sl = 100.0 - (2.0 * 1.5)
        assert sl == pytest.approx(expected_sl)


class TestGetTP:
    def test_tp_long(self):
        tp = get_tp(entry=100.0, sl=97.0, dir_val=1)
        assert tp == pytest.approx(106.0)

    def test_tp_short(self):
        tp = get_tp(entry=100.0, sl=103.0, dir_val=-1)
        assert tp == pytest.approx(94.0)

    def test_tp_zero_risk(self):
        tp = get_tp(entry=100.0, sl=100.0, dir_val=1)
        assert tp == pytest.approx(100.0)


class TestGetQty:
    def test_basic_qty(self):
        qty = get_qty(risk_amount=100.0, entry=100.0, sl=98.0)
        assert qty == pytest.approx(50.0)

    def test_qty_zero_dist(self):
        qty = get_qty(risk_amount=100.0, entry=100.0, sl=100.0)
        assert qty == 0.0

    def test_qty_rounding(self):
        qty = get_qty(risk_amount=75.0, entry=100.0, sl=97.33)
        assert qty == pytest.approx(28.09, abs=0.01)

    def test_qty_large_risk(self):
        qty = get_qty(risk_amount=1000.0, entry=50.0, sl=49.0)
        assert qty == pytest.approx(1000.0)


class TestAdxSlope:
    def test_basic_slope(self):
        arr = np.array([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
        result = adx_slope(arr, back=3)
        assert np.all(np.isnan(result[:3]))
        assert result[3] == pytest.approx(3.0)
        assert result[4] == pytest.approx(3.0)
        assert result[5] == pytest.approx(3.0)

    def test_slope_too_short(self):
        arr = np.array([1.0, 2.0])
        result = adx_slope(arr, back=3)
        assert np.all(np.isnan(result))

    def test_slope_negative(self):
        arr = np.array([15.0, 14.0, 13.0, 12.0, 11.0, 10.0])
        result = adx_slope(arr, back=2)
        assert result[2] == pytest.approx(-2.0)
        assert result[3] == pytest.approx(-2.0)

    def test_slope_empty(self):
        arr = np.array([])
        result = adx_slope(arr, back=3)
        assert len(result) == 0


class TestDisplayHelpers:
    def test_adx_status_weak(self):
        assert "W" in adx_status(15.0, 0.1)
        assert "↔" in adx_status(15.0, 0.0)

    def test_adx_status_medium(self):
        assert "M" in adx_status(22.0, 0.1)

    def test_adx_status_strong(self):
        assert "S" in adx_status(30.0, 0.1)

    def test_adx_status_slope_up(self):
        assert "\u219f" in adx_status(20.0, 0.5)

    def test_adx_status_slope_down(self):
        assert "\u21a1" in adx_status(20.0, -0.5)

    def test_adx_status_slope_flat(self):
        assert "↔" in adx_status(20.0, 0.2)

    def test_adx_status_nan(self):
        result = adx_status(None, 0.0)
        assert "na" in result
        assert "W" in result

    def test_adx_color_values(self):
        assert adx_color(15.0) == "#600707"
        assert adx_color(22.0) == "#524f01"
        assert adx_color(30.0) == "#104a12"
        assert adx_color(24.9) == "#524f01"
        assert adx_color(25.0) == "#104a12"

    def test_rsi_status_values(self):
        assert "↑↑" in rsi_status(65.0)
        assert "↑↓" in rsi_status(55.0)
        assert "↓↑" in rsi_status(45.0)
        assert "↓↓" in rsi_status(35.0)

    def test_rsi_text(self):
        text = rsi_text(62.0)
        assert "62" in text

    def test_rsi_text_rounding(self):
        text = rsi_text(62.7)
        assert "63" in text

    def test_rsi_color_values(self):
        assert rsi_color(65.0) == "#002d03"
        assert rsi_color(50.0) == "#524f01"
        assert rsi_color(35.0) == "#4a0404"
        assert rsi_color(40.0) == "#4a0404"
        assert rsi_color(45.0) == "#524f01"
