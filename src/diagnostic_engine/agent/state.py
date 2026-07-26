"""LangGraph diagnostic state schema."""

from __future__ import annotations

from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph.message import add_messages

from diagnostic_engine.analysis.traceback_parser import ParsedTraceback


class FastAPIDiagnosticState(TypedDict, total=False):
    raw_log_entry: str
    session_id: str
    apply_patches: bool
    apply_auto_yes: bool
    parsed_traceback: Optional[ParsedTraceback]
    historical_cases: list[dict[str, Any]]
    dependency_graph: list[dict[str, Any]]
    source_code_snippets: dict[str, str]
    ast_findings: dict[str, Any]
    analyzer_findings: list[dict[str, Any]]
    root_cause_analysis: str
    proposed_patch: dict[str, str]
    reproduction_test_code: str
    test_result: dict[str, Any]
    apply_result: dict[str, Any]
    retry_count: int
    max_retries: int
    messages: Annotated[list[Any], add_messages]
