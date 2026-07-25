"""Plugin-style diagnostic findings (mcp-scanner inspired)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class DiagnosticFinding:
    rule_id: str
    severity: str  # HIGH | MEDIUM | LOW | INFO
    summary: str
    location: str
    recommendation: str
    details: dict[str, Any] = field(default_factory=dict)
    analyzer: str = "static"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def findings_to_dicts(findings: list[DiagnosticFinding]) -> list[dict[str, Any]]:
    return [f.to_dict() for f in findings]


class BaseAnalyzer:
    name: str = "base"

    def analyze(self, *args: Any, **kwargs: Any) -> list[DiagnosticFinding]:
        raise NotImplementedError

    def safe_analyze(self, *args: Any, **kwargs: Any) -> list[DiagnosticFinding]:
        try:
            return self.analyze(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            return [
                DiagnosticFinding(
                    rule_id="ANALYZER-ERROR",
                    severity="INFO",
                    summary=f"{self.name} failed: {exc}",
                    location="",
                    recommendation="Fix analyzer input or report the bug.",
                    details={"error": str(exc)},
                    analyzer=self.name,
                )
            ]
