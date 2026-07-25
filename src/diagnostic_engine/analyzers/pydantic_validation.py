"""FASTAPI-VAL-001: ValidationError / IndexError style 500 risk heuristics."""

from __future__ import annotations

import ast
from pathlib import Path

from diagnostic_engine.analyzers.base import BaseAnalyzer, DiagnosticFinding


class PydanticValidationAnalyzer(BaseAnalyzer):
    name = "pydantic_validation"

    def analyze(self, file_path: str | Path, function_name: str | None = None) -> list[DiagnosticFinding]:
        path = Path(file_path)
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        findings: list[DiagnosticFinding] = []

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if function_name and node.name != function_name:
                continue
            # Flag bare subscript/dict access without try in handlers that look like getters
            has_try = any(isinstance(n, ast.Try) for n in ast.walk(node))
            has_http_exc = "HTTPException" in ast.unparse(node)
            risky_subscripts = [
                n for n in ast.walk(node) if isinstance(n, ast.Subscript)
            ]
            if risky_subscripts and not has_try and not has_http_exc and node.name.startswith("get_"):
                findings.append(
                    DiagnosticFinding(
                        rule_id="FASTAPI-VAL-001",
                        severity="MEDIUM",
                        summary=(
                            f"`{node.name}` indexes data without try/HTTPException — "
                            "missing items may surface as 500 instead of 404/422."
                        ),
                        location=f"{path}:{node.lineno}",
                        recommendation=(
                            "Catch IndexError/KeyError/ValidationError and raise "
                            "HTTPException with the appropriate status code."
                        ),
                        details={"function": node.name},
                        analyzer=self.name,
                    )
                )
        return findings
