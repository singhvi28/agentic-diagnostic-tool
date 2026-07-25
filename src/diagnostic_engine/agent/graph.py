"""Compile the LangGraph diagnostic state machine."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from diagnostic_engine.agent.nodes import (
    apply_patch_node,
    diagnose_and_patch_node,
    evaluate_test_router,
    execute_test_node,
    fetch_source_code_node,
    finalize_session_node,
    parse_log_node,
    retrieve_graphrag_context_node,
)
from diagnostic_engine.agent.state import FastAPIDiagnosticState


def build_diagnostic_agent():
    builder = StateGraph(FastAPIDiagnosticState)
    builder.add_node("parse_log", parse_log_node)
    builder.add_node("retrieve_context", retrieve_graphrag_context_node)
    builder.add_node("fetch_source", fetch_source_code_node)
    builder.add_node("diagnose_and_patch", diagnose_and_patch_node)
    builder.add_node("execute_test", execute_test_node)
    builder.add_node("apply_patch", apply_patch_node)
    builder.add_node("finalize", finalize_session_node)

    builder.set_entry_point("parse_log")
    builder.add_edge("parse_log", "retrieve_context")
    builder.add_edge("retrieve_context", "fetch_source")
    builder.add_edge("fetch_source", "diagnose_and_patch")
    builder.add_edge("diagnose_and_patch", "execute_test")
    builder.add_conditional_edges(
        "execute_test",
        evaluate_test_router,
        {
            "passed": "apply_patch",
            "retry": "diagnose_and_patch",
            "max_retries_reached": "finalize",
        },
    )
    builder.add_edge("apply_patch", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile()


diagnostic_agent = None


def get_agent():
    global diagnostic_agent
    if diagnostic_agent is None:
        diagnostic_agent = build_diagnostic_agent()
    return diagnostic_agent
