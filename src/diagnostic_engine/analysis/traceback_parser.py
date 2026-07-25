"""Traceback parsing for diagnostic state."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedFrame:
    filename: str
    line_number: int
    function_name: str
    code_context: str = ""


@dataclass
class ParsedTraceback:
    exception_type: str
    exception_message: str
    failing_frame: Optional[ParsedFrame]
    full_frames: list[ParsedFrame] = field(default_factory=list)


_FRAME_RE = re.compile(
    r'File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>\S+)\n'
    r"(?:(?P<code>[^\n]+)\n)?",
)
_EXC_RE = re.compile(
    r"^(?P<type>[A-Za-z_][\w.]*(?:\.[A-Za-z_]\w*)*):?\s*(?P<msg>.*)$",
    re.M,
)


def parse_traceback(raw: str) -> ParsedTraceback:
    """Parse a Python traceback string into structured frames."""
    frames: list[ParsedFrame] = []
    for match in _FRAME_RE.finditer(raw):
        frames.append(
            ParsedFrame(
                filename=match.group("file"),
                line_number=int(match.group("line")),
                function_name=match.group("func"),
                code_context=(match.group("code") or "").strip(),
            )
        )

    exception_type = "Exception"
    exception_message = ""
    for line in reversed(raw.strip().splitlines()):
        line = line.strip()
        if not line or line.startswith("File ") or line.startswith("^"):
            continue
        m = _EXC_RE.match(line)
        if m:
            exception_type = m.group("type")
            exception_message = m.group("msg").strip()
            break

    failing = frames[-1] if frames else None
    return ParsedTraceback(
        exception_type=exception_type,
        exception_message=exception_message,
        failing_frame=failing,
        full_frames=frames,
    )
