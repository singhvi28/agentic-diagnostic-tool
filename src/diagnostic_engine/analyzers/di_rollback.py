"""FASTAPI-DI-001: yield-based Depends without try/finally cleanup."""

from __future__ import annotations

from pathlib import Path

from diagnostic_engine.analysis.fastapi_routes import extract_fastapi_topology
from diagnostic_engine.analyzers.base import BaseAnalyzer, DiagnosticFinding


class DIRollbackAnalyzer(BaseAnalyzer):
    name = "di_rollback"

    def analyze(self, root: str | Path) -> list[DiagnosticFinding]:
        topology = extract_fastapi_topology(root)
        findings: list[DiagnosticFinding] = []
        for dep in topology.get("dependencies", []):
            if dep.get("is_generator") and not dep.get("has_try_finally"):
                findings.append(
                    DiagnosticFinding(
                        rule_id="FASTAPI-DI-001",
                        severity="HIGH",
                        summary=(
                            f"Dependency `{dep['name']}` yields a resource without "
                            "`try/finally` cleanup/rollback."
                        ),
                        location=f"{dep['file']}:{dep['line']}",
                        recommendation=(
                            "Wrap yield in try/finally and rollback/close the session "
                            "on exception before re-raising."
                        ),
                        details=dep,
                        analyzer=self.name,
                    )
                )
        return findings
