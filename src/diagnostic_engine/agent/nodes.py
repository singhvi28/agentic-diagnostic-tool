"""LangGraph nodes for the FastAPI diagnostic loop."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from diagnostic_engine.analysis.async_blocking import analyze_async_blocking
from diagnostic_engine.analysis.code_reader import read_source_file
from diagnostic_engine.analysis.traceback_parser import parse_traceback
from diagnostic_engine.agent.state import FastAPIDiagnosticState
from diagnostic_engine.config import get_settings
from diagnostic_engine.memory.neo4j_client import Neo4jMemory
from diagnostic_engine.memory.qdrant_client import QdrantMemory
from diagnostic_engine.mcp.server import run_reproduction_test


def parse_log_node(state: FastAPIDiagnosticState) -> dict[str, Any]:
    settings = get_settings()
    raw = state.get("raw_log_entry") or ""
    if not raw and settings.error_log_path.exists():
        raw = settings.error_log_path.read_text(encoding="utf-8")
    parsed = parse_traceback(raw)
    return {
        "raw_log_entry": raw,
        "parsed_traceback": parsed,
        "retry_count": 0,
        "max_retries": settings.max_retries,
    }


def retrieve_graphrag_context_node(state: FastAPIDiagnosticState) -> dict[str, Any]:
    settings = get_settings()
    raw = state.get("raw_log_entry", "")
    historical: list[dict[str, Any]] = []
    graph: list[dict[str, Any]] = []

    qdrant = QdrantMemory(settings)
    try:
        if qdrant.ping():
            historical = qdrant.query_similar(raw, top_k=3)
    except Exception as exc:  # noqa: BLE001
        historical = [{"error": str(exc)}]

    parsed = state.get("parsed_traceback")
    seed = parsed.failing_frame.function_name if parsed and parsed.failing_frame else None
    neo = Neo4jMemory(settings)
    try:
        if seed and neo.ping():
            graph = neo.traverse_from_function(seed, hops=3)
    except Exception as exc:  # noqa: BLE001
        graph = [{"error": str(exc)}]
    finally:
        neo.close()

    return {"historical_cases": historical, "dependency_graph": graph}


def fetch_source_code_node(state: FastAPIDiagnosticState) -> dict[str, Any]:
    settings = get_settings()
    parsed = state.get("parsed_traceback")
    snippets: dict[str, str] = {}
    findings: dict[str, Any] = {}

    if parsed and parsed.failing_frame:
        frame = parsed.failing_frame
        try:
            # Prefer sandbox-relative path if frame path is absolute under sandbox
            file_path = frame.filename
            sandbox = settings.sandbox_root.resolve()
            abs_frame = Path(file_path)
            if abs_frame.is_absolute() and abs_frame.resolve().is_relative_to(sandbox):
                file_path = str(abs_frame.resolve().relative_to(sandbox))
            elif not abs_frame.is_absolute():
                file_path = frame.filename

            start = max(1, frame.line_number - 15)
            end = frame.line_number + 15
            result = read_source_file(file_path, settings.sandbox_root, start, end)
            snippets[result["file"]] = result["content"]
            findings = analyze_async_blocking(
                Path(result["file"]),
                frame.function_name,
            )
        except Exception as exc:  # noqa: BLE001
            snippets["error"] = str(exc)

    return {"source_code_snippets": snippets, "ast_findings": findings}


def diagnose_and_patch_node(state: FastAPIDiagnosticState) -> dict[str, Any]:
    settings = get_settings()
    if not settings.openai_api_key:
        # Offline stub so the graph can be exercised without an API key
        retry = state.get("retry_count", 0) + 1
        return {
            "root_cause_analysis": (
                "OPENAI_API_KEY not set — stub RCA. "
                f"AST findings: {json.dumps(state.get('ast_findings') or {}, default=str)}"
            ),
            "proposed_patch": {
                "note": "Set OPENAI_API_KEY to enable LLM patch generation.",
            },
            "reproduction_test_code": (
                "def test_stub():\n"
                "    assert True\n"
            ),
            "retry_count": retry,
        }

    llm = ChatOpenAI(model=settings.llm_model, temperature=0, api_key=settings.openai_api_key)
    prompt = (
        f"Traceback:\n{state.get('raw_log_entry')}\n\n"
        f"Graph Context:\n{json.dumps(state.get('dependency_graph'), default=str)}\n\n"
        f"Historical cases:\n{json.dumps(state.get('historical_cases'), default=str)}\n\n"
        f"Snippets:\n{json.dumps(state.get('source_code_snippets'), default=str)}\n\n"
        f"AST findings:\n{json.dumps(state.get('ast_findings'), default=str)}\n\n"
        f"Previous Test Result:\n{json.dumps(state.get('test_result'), default=str)}\n\n"
        "Respond as JSON with keys: root_cause_analysis (str), "
        "proposed_patch (object mapping filename -> new content or unified diff), "
        "reproduction_test_code (pytest using httpx.AsyncClient against the ASGI app)."
    )
    response = llm.invoke(
        [
            SystemMessage(
                content=(
                    "You diagnose FastAPI bugs (async blocking, Pydantic 500s, "
                    "Depends yield without finally, lifespan leaks). Return valid JSON only."
                )
            ),
            HumanMessage(content=prompt),
        ]
    )
    content = response.content if isinstance(response.content, str) else str(response.content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # Try to extract fenced JSON
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(content[start : end + 1])
        else:
            data = {
                "root_cause_analysis": content,
                "proposed_patch": {},
                "reproduction_test_code": "def test_stub():\n    assert False\n",
            }

    return {
        "root_cause_analysis": data.get("root_cause_analysis", content),
        "proposed_patch": data.get("proposed_patch", {}),
        "reproduction_test_code": data.get("reproduction_test_code", ""),
        "retry_count": state.get("retry_count", 0) + 1,
    }


def execute_test_node(state: FastAPIDiagnosticState) -> dict[str, Any]:
    code = state.get("reproduction_test_code") or "def test_stub():\n    assert True\n"
    result = run_reproduction_test(code)
    return {"test_result": result}


def evaluate_test_router(state: FastAPIDiagnosticState) -> str:
    if state.get("test_result", {}).get("passed"):
        return "passed"
    if state.get("retry_count", 0) >= state.get("max_retries", 3):
        return "max_retries_reached"
    return "retry"
