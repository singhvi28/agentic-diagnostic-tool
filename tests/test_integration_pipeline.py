"""Gated integration tests: compose → ingest → hybrid MCP → stub agent → apply."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from diagnostic_engine.agent.apply_patch import apply_patches
from diagnostic_engine.agent.graph import get_agent
from diagnostic_engine.agent.mcp_client import DiagnosticMcpClient
from diagnostic_engine.analysis.fastapi_routes import extract_fastapi_topology
from diagnostic_engine.config import Settings, get_settings
from diagnostic_engine.memory.ingest import ingest
from diagnostic_engine.memory.neo4j_client import Neo4jMemory

ROOT = Path(__file__).resolve().parents[1]
TARGET_ROOT = ROOT / "examples" / "target_app"


@pytest.mark.integration
def test_ingest_seeds_neo4j_and_pgvector(require_graph_stores):
    result = ingest(TARGET_ROOT)
    neo = result["neo4j"]
    assert neo.get("skipped") is False
    assert neo.get("functions", 0) >= 3
    assert neo.get("routes", 0) >= 3
    assert neo.get("error_patterns", 0) >= 1
    assert result["pgvector_seeded"] >= 1


@pytest.mark.integration
def test_hybrid_rca_via_mcp_after_ingest(
    require_graph_stores,
    inprocess_mcp,
    sample_checkout_traceback,
):
    ingest(TARGET_ROOT)
    hybrid = inprocess_mcp.call_tool(
        "hybrid_root_cause_analysis",
        {"traceback_text": sample_checkout_traceback},
    )
    assert isinstance(hybrid, dict)
    graph = hybrid.get("dependency_graph") or []
    fragile = hybrid.get("fragile_routes") or []
    blob = str(graph).lower() + str(fragile).lower()
    assert "checkout" in blob or "get_db" in blob
    assert any(
        (r.get("handler") == "checkout")
        or (r.get("route") == "/orders/checkout")
        for r in fragile
    ) or any(c.get("kind") == "structural" for c in graph if isinstance(c, dict))


@pytest.mark.integration
def test_agent_stub_invoke_over_mcp(
    require_graph_stores,
    inprocess_mcp,
    sample_checkout_traceback,
    monkeypatch,
):
    for key in (
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "CURSOR_API_KEY",
        "openai_api_key",
        "gemini_api_key",
        "cursor_api_key",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("CURSOR_API_KEY", "")
    monkeypatch.setattr(Settings, "has_llm_credentials", lambda self: False)
    get_settings.cache_clear()

    ingest(TARGET_ROOT)
    final = get_agent().invoke(
        {
            "raw_log_entry": sample_checkout_traceback,
            "apply_patches": False,
        }
    )
    assert final.get("session_id")
    findings = final.get("analyzer_findings") or []
    rule_ids = {f.get("rule_id") for f in findings if isinstance(f, dict)}
    assert rule_ids & {"FASTAPI-001", "FASTAPI-DI-001"}
    assert final.get("dependency_graph") or final.get("historical_cases")
    assert (final.get("test_result") or {}).get("passed") is True
    apply_result = final.get("apply_result") or {}
    assert apply_result.get("skipped") is True


@pytest.mark.integration
def test_safe_apply_then_reingest(require_neo4j, tmp_path: Path):
    sandbox = tmp_path / "app"
    sandbox.mkdir()
    src = TARGET_ROOT / "main.py"
    target = sandbox / "main.py"
    shutil.copy2(src, target)
    original = target.read_text(encoding="utf-8")
    assert "Intentionally buggy FastAPI app" in original

    # Minimal unified diff: tweak the module docstring first line
    diff = (
        "--- main.py\n"
        "+++ main.py\n"
        "@@ -1,4 +1,4 @@\n"
        '-"""Intentionally buggy FastAPI app for the diagnostic engine to analyze."""\n'
        '+"""Intentionally buggy FastAPI app for the diagnostic engine (patched)."""\n'
        " \n"
        " from __future__ import annotations\n"
        " \n"
    )

    settings = Settings(
        sandbox_root=sandbox,
        target_app_root=sandbox,
        patch_backup_root=tmp_path / "patches",
        database_url=(
            "postgresql+psycopg://diagnostic:diagnostic@localhost:5433/diagnostic"
        ),
    )
    result = apply_patches(
        "integration-apply-session",
        {"main.py": diff},
        settings=settings,
        require_confirmation=False,
        auto_yes=True,
        reingest=True,
    )
    assert result.get("errors") == []
    assert "main.py" in result.get("applied", [])
    updated = target.read_text(encoding="utf-8")
    assert "patched" in updated
    assert result.get("backups", {}).get("main.py")
    assert Path(result["backups"]["main.py"]).exists()

    neo = Neo4jMemory()
    try:
        assert neo.ping()
        counts = neo.upsert_topology(extract_fastapi_topology(sandbox))
        assert counts.get("functions", 0) >= 1
    finally:
        neo.close()


@pytest.mark.integration
def test_stdio_mcp_list_and_hybrid(require_neo4j, sample_checkout_traceback, monkeypatch):
    if shutil.which("diagnostic-mcp") is None:
        pytest.skip("diagnostic-mcp not on PATH")

    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    get_settings.cache_clear()
    settings = Settings(mcp_transport="stdio", mcp_stdio_command="diagnostic-mcp")
    with DiagnosticMcpClient.from_settings(settings) as client:
        names = client.list_tools()
        assert "hybrid_root_cause_analysis" in names
        hybrid = client.call_tool(
            "hybrid_root_cause_analysis",
            {"traceback_text": sample_checkout_traceback},
        )
    assert isinstance(hybrid, dict)
    assert "parsed_traceback" in hybrid
    assert hybrid["parsed_traceback"].get("exception_type") == "KeyboardInterrupt"
