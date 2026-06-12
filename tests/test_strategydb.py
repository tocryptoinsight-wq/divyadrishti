import tempfile
from pathlib import Path

import pytest

from app.StrategyDB import (
    discover_strategies,
    _load_module,
    _get_user_dir,
    load_strategy_source,
    save_user_strategy,
)


class TestDiscoverStrategies:
    def test_discover_includes_pullback_breakout(self):
        strategies = discover_strategies()
        assert "Pullback Breakout" in strategies
        info = strategies["Pullback Breakout"]
        assert info["builtin"] == True
        assert "on_candle" in open(info["path"]).read()

    def test_discover_excludes_base(self):
        strategies = discover_strategies()
        names = list(strategies.keys())
        assert all("base" not in n for n in names)

    def test_discover_returns_dict(self):
        strategies = discover_strategies()
        assert isinstance(strategies, dict)


class TestLoadModule:
    def test_load_pullback_breakout(self):
        mod = _load_module(Path(__file__).parent.parent / "app" / "StrategyDB" / "pullback_breakout.py")
        assert hasattr(mod, "on_candle")
        assert hasattr(mod, "name")
        assert mod.name == "Pullback Breakout"
        assert "atrMult" in mod.params

    def test_load_module_has_on_candle(self):
        mod = _load_module(Path(__file__).parent.parent / "app" / "StrategyDB" / "pullback_breakout.py")
        assert callable(mod.on_candle)


class TestPullbackBreakoutStrategy:
    def _load_strategy(self):
        mod = _load_module(Path(__file__).parent.parent / "app" / "StrategyDB" / "pullback_breakout.py")
        return mod

    def test_buy_signal(self):
        mod = self._load_strategy()
        candle = {
            "close": 105.0,
            "ema8": 103.0,
            "ema20": 101.0,
        }
        state = {"in_trade": False, "base_signal": "Buy \u2191"}
        result = mod.on_candle(candle, state)
        assert result is not None
        assert result["action"] == "enter"
        assert result["side"] == "buy"

    def test_sell_signal(self):
        mod = self._load_strategy()
        candle = {
            "close": 95.0,
            "ema8": 97.0,
            "ema20": 99.0,
        }
        state = {"in_trade": False, "base_signal": "Sell \u2193"}
        result = mod.on_candle(candle, state)
        assert result is not None
        assert result["action"] == "enter"
        assert result["side"] == "sell"

    def test_no_trade_in_trade(self):
        mod = self._load_strategy()
        candle = {
            "close": 105.0,
            "ema8": 103.0,
            "ema20": 101.0,
        }
        state = {"in_trade": True, "base_signal": "Buy \u2191"}
        result = mod.on_candle(candle, state)
        assert result is None

    def test_no_trade_ema_missing(self):
        mod = self._load_strategy()
        candle = {
            "close": 105.0,
            "ema8": None,
            "ema20": 101.0,
        }
        state = {"in_trade": False, "base_signal": "Buy \u2191"}
        result = mod.on_candle(candle, state)
        assert result is None

    def test_no_trade_wrong_base_signal(self):
        mod = self._load_strategy()
        candle = {
            "close": 105.0,
            "ema8": 103.0,
            "ema20": 101.0,
        }
        state = {"in_trade": False, "base_signal": "Sell \u2193"}
        result = mod.on_candle(candle, state)
        assert result is None

    def test_no_trade_ema_condition_fail_buy(self):
        mod = self._load_strategy()
        candle = {
            "close": 100.0,
            "ema8": 103.0,
            "ema20": 101.0,
        }
        state = {"in_trade": False, "base_signal": "Buy \u2191"}
        result = mod.on_candle(candle, state)
        assert result is None

    def test_no_trade_ema_condition_fail_sell(self):
        mod = self._load_strategy()
        candle = {
            "close": 100.0,
            "ema8": 97.0,
            "ema20": 99.0,
        }
        state = {"in_trade": False, "base_signal": "Sell \u2193"}
        result = mod.on_candle(candle, state)
        assert result is None


class TestGetUserDir:
    def test_get_user_dir_returns_path(self):
        user_dir = _get_user_dir()
        assert isinstance(user_dir, Path)
        assert user_dir.exists()
        assert "DDTools" in str(user_dir)

    def test_get_user_dir_cached(self):
        first = _get_user_dir()
        second = _get_user_dir()
        assert first == second


class TestSaveAndLoadStrategy:
    def test_save_and_load(self):
        code = """
name = "TestStrategy"
params = {}
def on_candle(candle, state):
    return None
"""
        path = save_user_strategy("unittest_TestStrategy", code)
        try:
            assert Path(path).exists()
            strategies = discover_strategies()
            assert "TestStrategy" in strategies
            loaded = load_strategy_source("TestStrategy")
            assert loaded is not None
            assert "def on_candle" in loaded
        finally:
            Path(path).unlink(missing_ok=True)

    def test_load_nonexistent(self):
        result = load_strategy_source("nonexistent_strategy_xyz")
        assert result is None
