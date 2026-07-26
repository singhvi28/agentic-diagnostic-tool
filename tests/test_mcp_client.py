"""MCP client facade + node wiring tests (in-process FastMCP)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from diagnostic_engine.agent.mcp_client import (
    DiagnosticMcpClient,
    get_mcp_client,
    set_mcp_client,
)
from diagnostic_engine.agent.nodes import (
    execute_test_node,
    fetch_source_code_node,
    retrieve_graphrag_context_node,
)
from diagnostic_engine.analysis.traceback_parser import ParsedFrame, ParsedTraceback
from diagnostic_engine.config import Settings, get_settings
from diagnostic_engine.mcp.server import mcp as diagnostic_mcp


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    set_mcp_client(None)
    yield
    set_mcp_client(None)
    get_settings.cache_clear()


@pytest.fixture
def inprocess_settings(monkeypatch):
    monkeypatch.setenv("MCP_TRANSPORT", "inprocess")
    get_settings.cache_clear()
    return Settings(mcp_transport="inprocess")


def test_inprocess_client_lists_and_calls_tools(inprocess_settings):
    with DiagnosticMcpClient.from_settings(inprocess_settings, server=diagnostic_mcp) as client:
        names = client.list_tools()
        assert "run_static_analyzers" in names
        assert "hybrid_root_cause_analysis" in names
        assert "read_code" in names
        assert "run_reproduction_test_tool" in names

        scan = client.call_tool(
            "run_static_analyzers",
            {
                "file_path": "examples/target_app/main.py",
                "function_name": "checkout",
            },
        )
        assert isinstance(scan, dict)
        assert "findings" in scan
        assert any(f.get("rule_id") == "FASTAPI-001" for f in scan["findings"])

        hybrid = client.call_tool(
            "hybrid_root_cause_analysis",
            {
                "traceback_text": (
                    'File "examples/target_app/main.py", line 60, in checkout\n'
                    "KeyError: missing"
                )
            },
        )
        assert isinstance(hybrid, dict)
        assert "similar_bugs" in hybrid
        assert "dependency_graph" in hybrid
        assert "parsed_traceback" in hybrid
        assert "fragile_routes" in hybrid


def test_contextvar_get_mcp_client(inprocess_settings):
    with pytest.raises(RuntimeError, match="MCP client not set"):
        get_mcp_client()
    with DiagnosticMcpClient.from_settings(inprocess_settings, server=diagnostic_mcp):
        assert get_mcp_client() is not None


def test_retrieve_node_calls_hybrid_tool():
    mock = MagicMock()
    mock.call_tool.return_value = {
        "similar_bugs": [{"id": "1"}],
        "dependency_graph": [{"name": "get_db"}],
    }
    set_mcp_client(mock)
    out = retrieve_graphrag_context_node({"raw_log_entry": "boom"})
    mock.call_tool.assert_called_once_with(
        "hybrid_root_cause_analysis",
        {"traceback_text": "boom"},
    )
    assert out["historical_cases"] == [{"id": "1"}]
    assert out["dependency_graph"] == [{"name": "get_db"}]


def test_fetch_node_calls_read_and_analyzers():
    mock = MagicMock()

    def _side_effect(name, arguments=None):
        if name == "read_code":
            return {"file": "examples/target_app/main.py", "content": "1| x"}
        if name == "analyze_ast_for_async_blocking":
            return {"issues": []}
        if name == "run_static_analyzers":
            return {"findings": [{"rule_id": "FASTAPI-DI-001"}], "count": 1}
        raise AssertionError(name)

    mock.call_tool.side_effect = _side_effect
    set_mcp_client(mock)

    parsed = ParsedTraceback(
        exception_type="IndexError",
        exception_message="x",
        failing_frame=ParsedFrame(
            filename="examples/target_app/main.py",
            line_number=79,
            function_name="get_order",
            code_context="return db",
        ),
        full_frames=[],
    )
    out = fetch_source_code_node({"parsed_traceback": parsed})
    called = [c.args[0] for c in mock.call_tool.call_args_list]
    assert "read_code" in called
    assert "analyze_ast_for_async_blocking" in called
    assert "run_static_analyzers" in called
    assert out["analyzer_findings"][0]["rule_id"] == "FASTAPI-DI-001"


def test_execute_node_calls_reproduction_tool():
    mock = MagicMock()
    mock.call_tool.return_value = {"passed": True, "returncode": 0}
    set_mcp_client(mock)
    out = execute_test_node({"reproduction_test_code": "def test_ok():\n    assert True\n"})
    mock.call_tool.assert_called_once()
    assert mock.call_tool.call_args.args[0] == "run_reproduction_test_tool"
    assert out["test_result"]["passed"] is True
