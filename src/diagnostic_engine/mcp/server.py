"""FastMCP diagnostic server: tools + resources for the LangGraph agent."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from diagnostic_engine.analysis.async_blocking import analyze_async_blocking
from diagnostic_engine.analysis.code_reader import read_source_file
from diagnostic_engine.analysis.fastapi_routes import extract_fastapi_topology
from diagnostic_engine.analysis.traceback_parser import parse_traceback
from diagnostic_engine.config import get_settings
from diagnostic_engine.memory.neo4j_client import Neo4jMemory
from diagnostic_engine.memory.qdrant_client import QdrantMemory

mcp = FastMCP("FastAPI-Diagnostic-Engine")


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
def hybrid_root_cause_analysis(traceback_text: str) -> dict[str, Any]:
    """Combine Qdrant vector search and Neo4j graph traversal for RCA context."""
    settings = get_settings()
    parsed = parse_traceback(traceback_text)

    similar_logs: list[dict[str, Any]] = []
    graph_context: list[dict[str, Any]] = []

    qdrant = QdrantMemory(settings)
    try:
        if qdrant.ping():
            similar_logs = qdrant.query_similar(traceback_text, top_k=3)
    except Exception as exc:  # noqa: BLE001 — soft-fail when store is down
        similar_logs = [{"error": f"Qdrant unavailable: {exc}"}]

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
def run_reproduction_test(test_code: str, timeout_seconds: int = 30) -> dict[str, Any]:
    """Write a pytest snippet to a temp file and execute it under the sandbox cwd."""
    import subprocess
    import sys

    settings = get_settings()
    sandbox = settings.sandbox_root.resolve()
    with tempfile.TemporaryDirectory(prefix="diag_test_") as tmp:
        test_path = Path(tmp) / "test_reproduction.py"
        test_path.write_text(test_code, encoding="utf-8")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", str(test_path), "-q", "--tb=short"],
                cwd=str(sandbox),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            return {
                "passed": result.returncode == 0,
                "returncode": result.returncode,
                "stdout": result.stdout[-4000:],
                "stderr": result.stderr[-2000:],
            }
        except subprocess.TimeoutExpired:
            return {"passed": False, "error": f"Timed out after {timeout_seconds}s"}
        except FileNotFoundError:
            return {"passed": False, "error": "pytest not installed in this environment"}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
