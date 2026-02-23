import importlib
import os
import sys


def load_module():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    mod_name = "services.execution.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def test_round_size_truncates():
    mod = load_module()
    assert mod.round_size(1.23456, 3) == 1.234
    assert mod.round_size(0.0009, 3) == 0.0


def test_format_price_integer_kept():
    mod = load_module()
    assert mod.format_price(123456.0, 2) == 123456.0


def test_format_price_sig_figs_and_decimals():
    mod = load_module()
    # 1234.56 -> 5 sig figs -> 1234.5
    assert mod.format_price(1234.56, 0) == 1234.5
    # decimals capped by MAX_DECIMALS - szDecimals
    assert mod.format_price(0.123456, 5) == 0.1  # max_decimals=1 -> truncates to 1 decimal


def test_min_notional_check():
    mod = load_module()
    assert mod.is_min_notional_ok(0.001, 10000.0, 10.0) is True
    assert mod.is_min_notional_ok(0.0001, 10000.0, 10.0) is False


def test_effective_slices_for_notional():
    mod = load_module()
    # total notional=25, min=10, safety=1 => up to 2 slices allowed
    assert mod.effective_slices_for_notional(0.25, 100.0, 3, min_notional=10.0, safety_ratio=1.0) == 2
    # total notional below minimum => skip symbol
    assert mod.effective_slices_for_notional(0.05, 100.0, 3, min_notional=10.0, safety_ratio=1.0) == 0
