from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "v17_shadow.json"
RISK_APP = PROJECT_ROOT / "services" / "risk_engine" / "app.py"
EXECUTION_APP = PROJECT_ROOT / "services" / "execution" / "app.py"
PORTFOLIO_STATE_APP = PROJECT_ROOT / "services" / "portfolio_state" / "app.py"


def _module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def _assign_expr(tree: ast.Module, name: str) -> ast.AST:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
    raise AssertionError(f"missing assignment for {name}")


def _constant_value(tree: ast.Module, name: str) -> object:
    value = _assign_expr(tree, name)
    assert isinstance(value, ast.Constant), f"{name} is not a constant: {ast.dump(value)}"
    return value.value


def _main_function(tree: ast.Module) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    raise AssertionError("missing main function")


def _assignment_in_function(fn: ast.FunctionDef, name: str) -> ast.AST:
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
    raise AssertionError(f"missing function assignment for {name}")


def _has_runtime_error_guard(tree: ast.Module, message: str) -> bool:
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Raise):
                continue
            call = stmt.exc
            if not isinstance(call, ast.Call):
                continue
            if not isinstance(call.func, ast.Name) or call.func.id != "RuntimeError":
                continue
            if call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value == message:
                return True
    return False


def _name_ids(node: ast.AST) -> set[str]:
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def _constant_strings(node: ast.AST) -> set[str]:
    return {child.value for child in ast.walk(node) if isinstance(child, ast.Constant) and isinstance(child.value, str)}


def _portfolio_state_stream_reports(extra_env: dict[str, str]) -> str:
    env = os.environ.copy()
    env.update(
        {
            "REDIS_URL": "redis://127.0.0.1:6381/0",
            "HL_ACCOUNT_ADDRESS": "0x0000000000000000000000000000000000000000",
            "DRY_RUN": "true",
        }
    )
    env.pop("V17_LIVE_EXECUTION", None)
    env.update(extra_env)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import services.portfolio_state.app as mod; print(mod.STREAM_REPORTS)",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_v17_live_mode_is_configured_but_disabled_by_default() -> None:
    config = json.loads(CONFIG_PATH.read_text())

    assert config["mode"] == "paper_shadow"
    assert config["dry_run"] is True
    assert config["real_money_execution_allowed"] is False
    assert config["live"]["enabled"] is False
    assert config["live"]["requires_manual_confirmation"] is True
    assert config["live"]["stream_mode"] == "v17_isolated"
    assert config["streams"]["live_approved"] == "risk.approved.v17_live"
    assert config["streams"]["live_reports"] == "exec.reports.v17_live"


def test_risk_engine_has_explicit_v17_live_stream_mode() -> None:
    tree = _module_tree(RISK_APP)

    assert _constant_value(tree, "V17_LIVE_TARGET_STREAM") == "alpha.target.v17_shadow"
    assert _constant_value(tree, "V17_LIVE_RISK_APPROVED_STREAM") == "risk.approved.v17_live"

    stream_in = _assign_expr(tree, "STREAM_IN")
    stream_out = _assign_expr(tree, "STREAM_OUT")
    assert "V17_LIVE_EXECUTION" in _name_ids(stream_in)
    assert "V17_LIVE_EXECUTION" in _name_ids(stream_out)
    assert "V17_LIVE_TARGET_STREAM" in _name_ids(stream_in)
    assert "V17_LIVE_RISK_APPROVED_STREAM" in _name_ids(stream_out)
    assert "alpha.target" not in _constant_strings(stream_in)
    assert _has_runtime_error_guard(
        tree,
        "V17 live execution requires explicit real-money approval",
    )


def test_execution_v17_live_stream_is_isolated_and_requires_manual_approval() -> None:
    tree = _module_tree(EXECUTION_APP)

    stream_in = _assign_expr(tree, "STREAM_IN")
    dlq_in = _assign_expr(tree, "DLQ_IN")
    assert "V17_LIVE_EXECUTION" in _name_ids(stream_in)
    assert "risk.approved.v17_live" in _constant_strings(stream_in)
    assert "risk.approved.v17_shadow" in _constant_strings(stream_in)
    assert "risk.approved" in _constant_strings(stream_in)
    assert "STREAM_IN" in _name_ids(dlq_in)

    assert _has_runtime_error_guard(
        tree,
        "V17 live execution requires explicit real-money approval",
    )
    assert _has_runtime_error_guard(
        tree,
        "V17 live execution must use DRY_RUN=false",
    )


def test_execution_v17_live_reports_are_isolated_from_default_reports() -> None:
    tree = _module_tree(EXECUTION_APP)

    stream_reports = _assign_expr(tree, "STREAM_REPORTS")
    assert "V17_LIVE_EXECUTION" in _name_ids(stream_reports)
    assert "exec.reports.v17_live" in _constant_strings(stream_reports)
    assert "exec.reports" in _constant_strings(stream_reports)


def test_portfolio_state_v17_live_reports_are_isolated_from_default_reports() -> None:
    tree = _module_tree(PORTFOLIO_STATE_APP)
    stream_reports = _assign_expr(tree, "STREAM_REPORTS")
    assert "V17_LIVE_EXECUTION" in _name_ids(stream_reports)
    assert "exec.reports.v17_live" in _constant_strings(stream_reports)
    assert "exec.reports" in _constant_strings(stream_reports)


def test_portfolio_state_runtime_reports_default_to_exec_reports() -> None:
    assert _portfolio_state_stream_reports({}) == "exec.reports"


def test_portfolio_state_runtime_reports_use_v17_live_stream_when_enabled() -> None:
    assert _portfolio_state_stream_reports({"V17_LIVE_EXECUTION": "true", "DRY_RUN": "false"}) == "exec.reports.v17_live"


def test_execution_v17_live_constructs_exchange_adapter_only_after_live_gate() -> None:
    tree = _module_tree(EXECUTION_APP)
    main_fn = _main_function(tree)

    exchange_expr = _assignment_in_function(main_fn, "_exchange")
    assert "V17_DRY_RUN_INTEGRATION" in _name_ids(exchange_expr)
    assert "V17_LIVE_EXECUTION" in _name_ids(exchange_expr)
    assert "create_exchange_adapter" in {node.func.id for node in ast.walk(exchange_expr) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
