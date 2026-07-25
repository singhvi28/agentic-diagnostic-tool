"""LangGraph nodes for the FastAPI diagnostic loop."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from diagnostic_engine.agent.apply_patch import apply_patches
from diagnostic_engine.agent.state import FastAPIDiagnosticState
from diagnostic_engine.analysis.asgi_runner import run_reproduction_test
from diagnostic_engine.analysis.code_reader import read_source_file
from diagnostic_engine.analysis.traceback_parser import parse_traceback
from diagnostic_engine.analyzers.scanner import run_analyzers
from diagnostic_engine.config import get_settings
from diagnostic_engine.db import repository as repo
from diagnostic_engine.memory.neo4j_client import Neo4jMemory
from diagnostic_engine.memory.pgvector_client import PgVectorMemory


def _safe_event(session_id: str | None, node: str, payload: dict[str, Any]) -> None:
    if not session_id:
        return
    try:
        repo.append_event(session_id, node, payload)
    except Exception:
        pass


def parse_log_node(state: FastAPIDiagnosticState) -> dict[str, Any]:
    settings = get_settings()
    raw = state.get("raw_log_entry") or ""
    if not raw and settings.error_log_path.exists():
        raw = settings.error_log_path.read_text(encoding="utf-8")
    parsed = parse_traceback(raw)

    session_id = state.get("session_id")
    try:
        sid = repo.create_session(
            raw,
            exception_type=parsed.exception_type,
            failing_function=(
                parsed.failing_frame.function_name if parsed.failing_frame else None
            ),
        )
        session_id = str(sid)
    except Exception:
        session_id = session_id or ""

    out = {
        "raw_log_entry": raw,
        "parsed_traceback": parsed,
        "session_id": session_id,
        "retry_count": 0,
        "max_retries": settings.max_retries,
    }
    _safe_event(session_id, "parse_log", {
        "exception_type": parsed.exception_type,
        "failing_function": parsed.failing_frame.function_name if parsed.failing_frame else None,
    })
    return out


def retrieve_graphrag_context_node(state: FastAPIDiagnosticState) -> dict[str, Any]:
    settings = get_settings()
    raw = state.get("raw_log_entry", "")
    historical: list[dict[str, Any]] = []
    graph: list[dict[str, Any]] = []

    pg = PgVectorMemory(settings)
    try:
        if pg.ping():
            historical = pg.query_similar(raw, top_k=3)
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

    out = {"historical_cases": historical, "dependency_graph": graph}
    _safe_event(state.get("session_id"), "retrieve_context", {
        "historical_count": len(historical),
        "graph_count": len(graph),
    })
    return out


def fetch_source_code_node(state: FastAPIDiagnosticState) -> dict[str, Any]:
    settings = get_settings()
    parsed = state.get("parsed_traceback")
    snippets: dict[str, str] = {}
    findings: dict[str, Any] = {}
    analyzer_findings: list[dict[str, Any]] = []
    target_file: str | None = None
    target_fn: str | None = None

    if parsed and parsed.failing_frame:
        frame = parsed.failing_frame
        target_fn = frame.function_name
        try:
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
            target_file = result["file"]
            from diagnostic_engine.analysis.async_blocking import analyze_async_blocking

            findings = analyze_async_blocking(Path(result["file"]), frame.function_name)
        except Exception as exc:  # noqa: BLE001
            snippets["error"] = str(exc)

    analyzer_findings = run_analyzers(
        root=settings.target_app_root,
        file_path=target_file,
        function_name=target_fn,
    )

    out = {
        "source_code_snippets": snippets,
        "ast_findings": findings,
        "analyzer_findings": analyzer_findings,
    }
    _safe_event(state.get("session_id"), "fetch_source", {
        "finding_count": len(analyzer_findings),
        "snippet_files": list(snippets.keys()),
    })
    return out


def diagnose_and_patch_node(state: FastAPIDiagnosticState) -> dict[str, Any]:
    settings = get_settings()
    retry = state.get("retry_count", 0) + 1

    system = (
        "You diagnose FastAPI bugs (async blocking, Pydantic 500s, "
        "Depends yield without finally, lifespan leaks). Return valid JSON only."
    )
    prompt = (
        f"Traceback:\n{state.get('raw_log_entry')}\n\n"
        f"Graph Context:\n{json.dumps(state.get('dependency_graph'), default=str)}\n\n"
        f"Historical cases:\n{json.dumps(state.get('historical_cases'), default=str)}\n\n"
        f"Snippets:\n{json.dumps(state.get('source_code_snippets'), default=str)}\n\n"
        f"Analyzer findings:\n{json.dumps(state.get('analyzer_findings'), default=str)}\n\n"
        f"Previous Test Result:\n{json.dumps(state.get('test_result'), default=str)}\n\n"
        "Respond as JSON with keys: root_cause_analysis (str), "
        "proposed_patch (object mapping filename -> full new file content), "
        "reproduction_test_code (pytest using httpx.ASGITransport against examples.target_app.main:app)."
    )

    if not settings.has_llm_credentials():
        out = {
            "root_cause_analysis": (
                f"No API key for llm_provider={settings.llm_provider!r} — stub RCA. "
                f"Analyzer findings: {json.dumps(state.get('analyzer_findings') or [], default=str)[:2000]}"
            ),
            "proposed_patch": {
                "note": (
                    "Set OPENAI_API_KEY, GEMINI_API_KEY, or CURSOR_API_KEY "
                    f"(and LLM_PROVIDER={settings.llm_provider}) to enable LLM patch generation."
                ),
            },
            "reproduction_test_code": "def test_stub():\n    assert True\n",
            "retry_count": retry,
        }
    else:
        from diagnostic_engine.llm import MissingCredentialsError, get_llm_client

        try:
            client = get_llm_client(settings)
            response = client.complete(system=system, user=prompt)
            content = response.content
        except MissingCredentialsError as exc:
            content = str(exc)
        except Exception as exc:  # noqa: BLE001
            content = f"LLM provider error ({settings.llm_provider}): {exc}"

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(content[start : end + 1])
                except json.JSONDecodeError:
                    data = {
                        "root_cause_analysis": content,
                        "proposed_patch": {},
                        "reproduction_test_code": "def test_stub():\n    assert False\n",
                    }
            else:
                data = {
                    "root_cause_analysis": content,
                    "proposed_patch": {},
                    "reproduction_test_code": "def test_stub():\n    assert False\n",
                }
        out = {
            "root_cause_analysis": data.get("root_cause_analysis", content),
            "proposed_patch": data.get("proposed_patch", {}),
            "reproduction_test_code": data.get("reproduction_test_code", ""),
            "retry_count": retry,
        }

    session_id = state.get("session_id")
    if session_id:
        try:
            repo.add_patch(
                session_id,
                attempt=retry,
                files=out.get("proposed_patch") or {},
                test_code=out.get("reproduction_test_code") or "",
                test_passed=None,
                applied=False,
            )
        except Exception:
            pass
    _safe_event(session_id, "diagnose_and_patch", {"attempt": retry})
    return out


def execute_test_node(state: FastAPIDiagnosticState) -> dict[str, Any]:
    code = state.get("reproduction_test_code") or "def test_stub():\n    assert True\n"
    result = run_reproduction_test(code)
    session_id = state.get("session_id")
    if session_id:
        try:
            repo.update_latest_patch_test_result(session_id, bool(result.get("passed")))
        except Exception:
            pass
    _safe_event(session_id, "execute_test", {"passed": result.get("passed")})
    return {"test_result": result}


def apply_patch_node(state: FastAPIDiagnosticState) -> dict[str, Any]:
    """Apply patches only when apply_patches flag is set and tests passed."""
    if not state.get("apply_patches"):
        return {"apply_result": {"skipped": True, "reason": "apply_patches flag not set"}}
    if not state.get("test_result", {}).get("passed"):
        return {"apply_result": {"skipped": True, "reason": "tests did not pass"}}
    session_id = state.get("session_id") or ""
    proposed = state.get("proposed_patch") or {}
    result = apply_patches(session_id, proposed)
    _safe_event(session_id, "apply_patch", result)
    return {"apply_result": result}


def finalize_session_node(state: FastAPIDiagnosticState) -> dict[str, Any]:
    session_id = state.get("session_id")
    if not session_id:
        return {}
    if state.get("test_result", {}).get("passed"):
        status = "passed"
    elif state.get("retry_count", 0) >= state.get("max_retries", 3):
        status = "max_retries"
    else:
        status = "failed"
    try:
        repo.finish_session(
            session_id,
            status,
            root_cause=state.get("root_cause_analysis"),
            retry_count=state.get("retry_count"),
        )
    except Exception:
        pass
    _safe_event(session_id, "finalize", {"status": status})
    return {}


def evaluate_test_router(state: FastAPIDiagnosticState) -> str:
    if state.get("test_result", {}).get("passed"):
        return "passed"
    if state.get("retry_count", 0) >= state.get("max_retries", 3):
        return "max_retries_reached"
    return "retry"
