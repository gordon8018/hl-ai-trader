import importlib
import os
import sys


def load_module():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("UNIVERSE", "BTC,ETH")
    mod_name = "services.execution.app"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    return importlib.import_module(mod_name)


def test_parse_ctl_command():
    mod = load_module()
    cmd, reason = mod.parse_ctl_command({"data": {"cmd": "halt", "reason": "manual"}})
    assert cmd == "HALT"
    assert reason == "manual"

    cmd2, _ = mod.parse_ctl_command({"cmd": "reduce_only"})
    assert cmd2 == "REDUCE_ONLY"

    cmd3, _ = mod.parse_ctl_command({"data": {"cmd": "invalid"}})
    assert cmd3 is None


def test_mode_from_command_and_merge():
    mod = load_module()
    assert mod.mode_from_command("HALT") == "HALT"
    assert mod.mode_from_command("REDUCE_ONLY") == "REDUCE_ONLY"
    assert mod.mode_from_command("RESUME") == "NORMAL"

    assert mod.merge_modes("NORMAL", "HALT") == "HALT"
    assert mod.merge_modes("NORMAL", "REDUCE_ONLY") == "REDUCE_ONLY"
    assert mod.merge_modes("REDUCE_ONLY", "NORMAL") == "REDUCE_ONLY"
    assert mod.merge_modes("HALT", "NORMAL") == "HALT"
