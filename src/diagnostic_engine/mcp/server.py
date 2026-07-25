"""FastMCP diagnostic server: tools + resources for the LangGraph agent."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from diagnostic_engine.analysis.asgi_runner import run_reproduction_test
from diagnostic_engine.analysis.async_blocking import analyze_async_blocking
from diagnostic_engine.analysis.code_reader import read_source_file
from diagnostic_engine.analysis.fastapi_routes import extract_fastapi_topology
from diagnostic_engine.analysis.traceback_parser import parse_traceback
from diagnostic_engine.analyzers.scanner import run_analyzers
from diagnostic_engine.config import get_settings
from diagnostic_engine.db import repository as repo
from diagnostic_engine.db.session import get_engine, reset_engine
from diagnostic_engine.memory.neo4j_client import Neo4jMemory
from diagnostic_engine.memory.pgvector_client import PgVectorMemory


@asynccontextmanager
async def diagnostic_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    settings = get_settings()
    engine = get_engine(settings)
    neo = Neo4jMemory(settings)
    try:
        yield {"engine": engine, "neo4j": neo, "settings": settings}
    finally:
        neo.close()
        reset_engine()


mcp = FastMCP(
    "FastAPI-Diagnostic-Engine",
    lifespan=diagnostic_lifespan,
    instructions="Root-cause analysis tools for FastAPI applications.",
)


@mcp.resource("logs://runtime/errors")
def get_recent_error_logs() -> str:
    """Expose recent exception logs for the LLM to inspect."""
    settings = get_settings()
    log_file = settings.error_log_path
    if not log_file.exists():
        return "No logs."
    lines = log_file.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[-50:])


@mcp.resource("routes://app/endpoints")
def get_route_map() -> str:
    """Expose statically discovered FastAPI routes from the target app."""
    settings = get_settings()
    topology = extract_fastapi_topology(settings.target_app_root)
    return json.dumps(topology, indent=2)


@mcp.resource("sessions://recent")
def get_recent_sessions() -> str:
    """List recent diagnostic sessions."""
    try:
        return json.dumps(repo.list_recent_sessions(20), indent=2, default=str)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)})


@mcp.resource("sessions://{session_id}")
def get_session_detail(session_id: str) -> str:
    """Return session events and patch history."""
    try:
        data = repo.get_session(session_id)
        if data is None:
            return json.dumps({"error": "session not found", "session_id": session_id})
        return json.dumps(data, indent=2, default=str)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc), "session_id": session_id})


@mcp.tool()
def read_code(
    file_path: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> dict[str, Any]:
    """Read a sandboxed slice of source code from the target FastAPI app."""
    settings = get_settings()
    return read_source_file(
        file_path,
        sandbox_root=settings.sandbox_root,
        start_line=start_line,
        end_line=end_line,
    )


@mcp.tool()
def analyze_ast_for_async_blocking(file_path: str, function_name: str) -> dict[str, Any]:
    """Parse an endpoint AST to detect sync blocking I/O inside async handlers."""
    settings = get_settings()
    path = Path(file_path)
    if not path.is_absolute():
        path = settings.sandbox_root / path
    return analyze_async_blocking(path, function_name)


@mcp.tool()
def run_static_analyzers(file_path: str | None = None, function_name: str | None = None) -> dict[str, Any]:
    """Run deterministic FastAPI analyzers (async blocking, DI rollback, validation)."""
    settings = get_settings()
    findings = run_analyzers(
        root=settings.target_app_root,
        file_path=file_path,
        function_name=function_name,
    )
    return {"findings": findings, "count": len(findings)}


@mcp.tool()
def search_similar_bugs(traceback_text: str, top_k: int = 3) -> dict[str, Any]:
    """Semantic search over past logs/tracebacks in Postgres pgvector."""
    settings = get_settings()
    pg = PgVectorMemory(settings)
    try:
        if not pg.ping():
            return {"results": [], "error": "Postgres unavailable"}
        return {"results": pg.query_similar(traceback_text, top_k=top_k)}
    except Exception as exc:  # noqa: BLE001
        return {"results": [], "error": str(exc)}


@mcp.tool()
def hybrid_root_cause_analysis(traceback_text: str) -> dict[str, Any]:
    """Combine pgvector similarity search and Neo4j graph traversal for RCA context."""
    settings = get_settings()
    parsed = parse_traceback(traceback_text)

    similar_logs: list[dict[str, Any]] = []
    graph_context: list[dict[str, Any]] = []

    pg = PgVectorMemory(settings)
    try:
        if pg.ping():
            similar_logs = pg.query_similar(traceback_text, top_k=3)
    except Exception as exc:  # noqa: BLE001
        similar_logs = [{"error": f"Postgres/pgvector unavailable: {exc}"}]

    seed_function = None
    if parsed.failing_frame:
        seed_function = parsed.failing_frame.function_name
    elif similar_logs and isinstance(similar_logs[0].get("metadata"), dict):
        seed_function = similar_logs[0]["metadata"].get("function_name")

    neo = Neo4jMemory(settings)
    try:
        if seed_function and neo.ping():
            graph_context = neo.traverse_from_function(seed_function, hops=3)
    except Exception as exc:  # noqa: BLE001
        graph_context = [{"error": f"Neo4j unavailable: {exc}"}]
    finally:
        neo.close()

    return {
        "parsed_traceback": {
            "exception_type": parsed.exception_type,
            "exception_message": parsed.exception_message,
            "failing_frame": (
                {
                    "filename": parsed.failing_frame.filename,
                    "line_number": parsed.failing_frame.line_number,
                    "function_name": parsed.failing_frame.function_name,
                    "code_context": parsed.failing_frame.code_context,
                }
                if parsed.failing_frame
                else None
            ),
        },
        "similar_bugs": similar_logs,
        "dependency_graph": graph_context,
    }


@mcp.tool()
def run_reproduction_test_tool(test_code: str, timeout_seconds: int = 30) -> dict[str, Any]:
    """Execute a generated pytest snippet against the project sandbox."""
    return run_reproduction_test(test_code, timeout_seconds=timeout_seconds)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
