"""Unit tests for analysis helpers (no Postgres/Neo4j required)."""

from pathlib import Path

from diagnostic_engine.analysis.async_blocking import analyze_async_blocking
from diagnostic_engine.analysis.fastapi_routes import extract_fastapi_topology
from diagnostic_engine.analysis.traceback_parser import (
    parse_all_tracebacks,
    parse_traceback,
    select_primary_traceback,
)
from diagnostic_engine.analyzers.scanner import run_analyzers

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "examples" / "target_app" / "main.py"


def test_parse_traceback_extracts_failing_frame():
    raw = (
        'Traceback (most recent call last):\n'
        '  File "main.py", line 64, in checkout\n'
        "    time.sleep(0.05)\n"
        "KeyboardInterrupt: event loop blocked\n"
    )
    parsed = parse_traceback(raw)
    assert parsed.exception_type == "KeyboardInterrupt"
    assert parsed.failing_frame is not None
    assert parsed.failing_frame.function_name == "checkout"
    assert parsed.failing_frame.line_number == 64


def test_parse_all_tracebacks_from_app_errors_log():
    log = (ROOT / "logs" / "app_errors.log").read_text(encoding="utf-8")
    all_tb = parse_all_tracebacks(log)
    assert len(all_tb) == 2
    types = {tb.exception_type for tb in all_tb}
    assert types == {"KeyboardInterrupt", "IndexError"}
    by_type = {tb.exception_type: tb for tb in all_tb}
    assert by_type["KeyboardInterrupt"].failing_frame.function_name == "checkout"
    assert by_type["IndexError"].failing_frame.function_name == "get_order"


def test_select_primary_prefers_async_blocking_over_indexerror():
    log = (ROOT / "logs" / "app_errors.log").read_text(encoding="utf-8")
    all_tb = parse_all_tracebacks(log)
    primary = select_primary_traceback(all_tb)
    assert primary.exception_type == "KeyboardInterrupt"
    assert primary.failing_frame is not None
    assert primary.failing_frame.function_name == "checkout"
    # Wrapper must not silently pick the last stack (IndexError / get_order)
    wrapped = parse_traceback(log)
    assert wrapped.failing_frame.function_name == "checkout"
    assert wrapped.exception_type == "KeyboardInterrupt"


def test_async_blocking_detects_time_sleep():
    result = analyze_async_blocking(TARGET, "checkout")
    assert result["issues"], "expected blocking call findings"
    assert any("sleep" in i["call"] for i in result["issues"])


def test_extract_fastapi_routes():
    topology = extract_fastapi_topology(TARGET.parent)
    paths = {(r["method"], r["path"]) for r in topology["routes"]}
    assert ("POST", "/orders/checkout") in paths
    assert ("GET", "/health") in paths
    dep_names = {d["name"] for d in topology["dependencies"]}
    assert "get_db" in dep_names
    get_db = next(d for d in topology["dependencies"] if d["name"] == "get_db")
    assert get_db["is_generator"] is True
    assert get_db["has_try_finally"] is False


def test_extract_functions_decorators_params_and_depends():
    topology = extract_fastapi_topology(TARGET.parent)
    functions = {f["name"]: f for f in topology["functions"]}
    assert "checkout" in functions
    assert "get_db" in functions
    checkout = functions["checkout"]
    assert any(d.get("is_route") for d in checkout["decorators"])
    assert "get_db" in checkout["depends_on"] or any(
        "get_db" in d for d in checkout["depends_on"]
    )
    param_names = {p["name"] for p in checkout["params"]}
    assert "order" in param_names
    assert "db" in param_names
    route_dec = next(d for d in checkout["decorators"] if d.get("is_route"))
    assert route_dec["method"] == "POST"
    assert route_dec["path"] == "/orders/checkout"


def test_plugin_analyzers_find_di_and_blocking():
    findings = run_analyzers(
        root=TARGET.parent,
        file_path=TARGET,
        function_name="checkout",
    )
    rule_ids = {f["rule_id"] for f in findings}
    assert "FASTAPI-001" in rule_ids
    assert "FASTAPI-DI-001" in rule_ids
