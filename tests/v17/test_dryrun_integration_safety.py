from __future__ import annotations

import ast
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RISK_APP = PROJECT_ROOT / "services" / "risk_engine" / "app.py"
EXECUTION_APP = PROJECT_ROOT / "services" / "execution" / "app.py"


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
    assert isinstance(value, ast.Constant), f"{name} is not a constant"
    return value.value


def _ifexp_names_and_constants(expr: ast.AST) -> tuple[str, str, str]:
    assert isinstance(expr, ast.IfExp), ast.dump(expr)
    assert isinstance(expr.test, ast.Name), ast.dump(expr.test)
    assert isinstance(expr.body, ast.Name | ast.Constant), ast.dump(expr.body)
    assert isinstance(expr.orelse, ast.Name | ast.Constant), ast.dump(expr.orelse)

    def label(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        assert isinstance(node, ast.Constant)
        return str(node.value)

    return expr.test.id, label(expr.body), label(expr.orelse)


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


def _is_create_exchange_adapter_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "create_exchange_adapter"


def _call_names_in_function(fn: ast.FunctionDef) -> list[str]:
    names: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.append(node.func.id)
    return names


def _name_ids(node: ast.AST) -> set[str]:
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def _constant_strings(node: ast.AST) -> set[str]:
    return {child.value for child in ast.walk(node) if isinstance(child, ast.Constant) and isinstance(child.value, str)}


def test_risk_engine_v17_dryrun_streams_are_isolated_and_opt_in() -> None:
    tree = _module_tree(RISK_APP)

    assert _constant_value(tree, "PRODUCTION_TARGET_STREAM") == "alpha.target"
    assert _constant_value(tree, "V17_TARGET_STREAM") == "alpha.target.v17_shadow"
    assert _constant_value(tree, "V17_RISK_APPROVED_STREAM") == "risk.approved.v17_shadow"

    stream_in = _assign_expr(tree, "STREAM_IN")
    stream_out = _assign_expr(tree, "STREAM_OUT")
    assert "V17_DRY_RUN_INTEGRATION" in _name_ids(stream_in)
    assert "V17_TARGET_STREAM" in _name_ids(stream_in)
    assert "PRODUCTION_TARGET_STREAM" in _name_ids(stream_in)
    assert "V17_LIVE_EXECUTION" in _name_ids(stream_in)
    assert "alpha.target" not in _constant_strings(stream_in)
    assert "V17_DRY_RUN_INTEGRATION" in _name_ids(stream_out)
    assert "V17_RISK_APPROVED_STREAM" in _name_ids(stream_out)
    assert "risk.approved" in _constant_strings(stream_out)
    assert "V17_LIVE_EXECUTION" in _name_ids(stream_out)
    assert _has_runtime_error_guard(
        tree,
        "V17 dry-run integration must not consume production alpha.target",
    )


def test_execution_v17_dryrun_streams_require_dry_run_and_do_not_change_default() -> None:
    tree = _module_tree(EXECUTION_APP)

    stream_in = _assign_expr(tree, "STREAM_IN")
    assert "V17_DRY_RUN_INTEGRATION" in _name_ids(stream_in)
    assert "V17_LIVE_EXECUTION" in _name_ids(stream_in)
    assert "risk.approved.v17_shadow" in _constant_strings(stream_in)
    assert "risk.approved.v17_live" in _constant_strings(stream_in)
    assert "risk.approved" in _constant_strings(stream_in)
    dlq_expr = _assign_expr(tree, "DLQ_IN")
    assert isinstance(dlq_expr, ast.JoinedStr), ast.dump(dlq_expr)
    assert any(isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name) and part.value.id == "STREAM_IN" for part in dlq_expr.values)
    assert _has_runtime_error_guard(
        tree,
        "V17 dry-run integration requires DRY_RUN=true",
    )


def test_execution_v17_dryrun_does_not_construct_exchange_adapter() -> None:
    tree = _module_tree(EXECUTION_APP)
    main_fn = _main_function(tree)

    exchange_expr = _assignment_in_function(main_fn, "_exchange")
    assert isinstance(exchange_expr, ast.IfExp), ast.dump(exchange_expr)
    assert isinstance(exchange_expr.test, ast.Name)
    assert exchange_expr.test.id == "V17_LIVE_EXECUTION"
    assert _is_create_exchange_adapter_call(exchange_expr.body)
    assert "V17_DRY_RUN_INTEGRATION" in _name_ids(exchange_expr.orelse)
    assert any(isinstance(node, ast.Constant) and node.value is None for node in ast.walk(exchange_expr.orelse))
    assert _call_names_in_function(main_fn).count("create_exchange_adapter") == 2


def test_dryrun_integration_does_not_introduce_live_submit_or_secret_paths() -> None:
    changed_text = "\n".join([RISK_APP.read_text(), EXECUTION_APP.read_text()])
    forbidden_fragments = [
        "V9_prod",
        "private_key",
        "seed phrase",
        "withdrawal",
        "real_money_execution_allowed = True",
        "STREAM_IN = \"alpha.target.v17_shadow\" if not V17_DRY_RUN_INTEGRATION",
    ]

    for fragment in forbidden_fragments:
        assert fragment not in changed_text
