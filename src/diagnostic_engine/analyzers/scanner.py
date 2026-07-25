"""Orchestrate deterministic FastAPI diagnostic analyzers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from diagnostic_engine.analyzers.async_blocking import AsyncBlockingAnalyzer
from diagnostic_engine.analyzers.base import DiagnosticFinding, findings_to_dicts
from diagnostic_engine.analyzers.di_rollback import DIRollbackAnalyzer
from diagnostic_engine.analyzers.pydantic_validation import PydanticValidationAnalyzer


def run_analyzers(
    *,
    root: str | Path,
    file_path: str | Path | None = None,
    function_name: str | None = None,
) -> list[dict[str, Any]]:
    findings: list[DiagnosticFinding] = []
    findings.extend(DIRollbackAnalyzer().safe_analyze(root))
    if file_path and function_name:
        findings.extend(AsyncBlockingAnalyzer().safe_analyze(file_path, function_name))
        findings.extend(PydanticValidationAnalyzer().safe_analyze(file_path, function_name))
    elif file_path:
        findings.extend(PydanticValidationAnalyzer().safe_analyze(file_path, None))
    return findings_to_dicts(findings)
