"""FASTAPI-001: sync blocking I/O inside async handlers."""

from __future__ import annotations

from pathlib import Path

from diagnostic_engine.analysis.async_blocking import analyze_async_blocking
from diagnostic_engine.analyzers.base import BaseAnalyzer, DiagnosticFinding


class AsyncBlockingAnalyzer(BaseAnalyzer):
    name = "async_blocking"

    def analyze(self, file_path: str | Path, function_name: str) -> list[DiagnosticFinding]:
        result = analyze_async_blocking(file_path, function_name)
        findings: list[DiagnosticFinding] = []
        for issue in result.get("issues", []):
            findings.append(
                DiagnosticFinding(
                    rule_id="FASTAPI-001",
                    severity="HIGH",
                    summary=issue["message"],
                    location=f"{result['file']}:{issue['line']}",
                    recommendation="Use awaitable APIs (asyncio.sleep, httpx.AsyncClient) or run_in_executor.",
                    details={"blocking_call": issue["call"], "line": issue["line"]},
                    analyzer=self.name,
                )
            )
        for note in result.get("notes", []):
            findings.append(
                DiagnosticFinding(
                    rule_id="FASTAPI-001-NOTE",
                    severity="INFO",
                    summary=note,
                    location=str(result.get("file", "")),
                    recommendation="Verify the handler is registered as intended.",
                    analyzer=self.name,
                )
            )
        return findings
