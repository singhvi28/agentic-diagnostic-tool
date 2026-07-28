"""Traceback parsing for diagnostic state (single- and multi-stack logs)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
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
    raw_chunk: str = ""


_FRAME_RE = re.compile(
    r'File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>\S+)\n'
    r"(?:(?P<code>[^\n]+)\n)?",
)
_EXC_RE = re.compile(
    r"^(?P<type>[A-Za-z_][\w.]*(?:\.[A-Za-z_]\w*)*):?\s*(?P<msg>.*)$",
    re.M,
)
_TRACEBACK_HEADER = "Traceback (most recent call last):"

_BLOCKING_PATTERNS = (
    "sleep",
    "requests.",
    "urllib",
    "open(",
    "subprocess",
    "time.sleep",
    "httpx.get",
    "httpx.post",
)


def split_traceback_chunks(raw: str) -> list[str]:
    """Split a log into individual traceback stacks.

    Prefers ``Traceback (most recent call last):`` headers; falls back to
    blank-line separation when headers are absent.
    """
    text = (raw or "").strip()
    if not text:
        return []

    if _TRACEBACK_HEADER in text:
        parts = text.split(_TRACEBACK_HEADER)
        chunks: list[str] = []
        for part in parts:
            body = part.strip()
            if not body:
                continue
            chunks.append(f"{_TRACEBACK_HEADER}\n{body}")
        if chunks:
            return chunks

    return [c.strip() for c in re.split(r"\n\s*\n", text) if c.strip()]


def parse_traceback_chunk(raw: str) -> ParsedTraceback:
    """Parse a single Python traceback stack into structured frames."""
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
        if line.startswith(_TRACEBACK_HEADER):
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
        raw_chunk=raw.strip(),
    )


def parse_all_tracebacks(raw: str) -> list[ParsedTraceback]:
    """Parse every traceback stack in a (possibly concatenated) log."""
    chunks = split_traceback_chunks(raw)
    parsed = [parse_traceback_chunk(c) for c in chunks]
    # Drop empty placeholders with no frames and generic Exception
    return [
        p
        for p in parsed
        if p.full_frames
        or (p.exception_type and p.exception_type != "Exception")
        or p.exception_message
    ]


def _score_traceback(tb: ParsedTraceback, index: int) -> int:
    """FastAPI-aware priority: async/blocking beats IndexError/KeyError."""
    score = 0
    ctx = ""
    if tb.failing_frame:
        ctx = (tb.failing_frame.code_context or "").lower()
    msg = (tb.exception_message or "").lower()
    blob = f"{ctx} {msg}"
    if any(pat in blob for pat in _BLOCKING_PATTERNS):
        score += 100

    exc = tb.exception_type or ""
    exc_base = exc.rsplit(".", 1)[-1]
    if exc_base in {"KeyboardInterrupt", "CancelledError"} or (
        "event loop" in msg or "blocked" in msg
    ):
        score += 80
    if exc_base in {"ValidationError", "ResponseValidationError"}:
        score += 40
    if exc_base in {"TimeoutError", "ConnectionError", "OSError"}:
        score += 20
    if exc_base in {"IndexError", "KeyError", "AttributeError"}:
        score += 5

    # Prefer earlier stacks on ties
    score += max(0, 10 - index)
    return score


def select_primary_traceback(parsed: list[ParsedTraceback]) -> ParsedTraceback:
    """Choose the highest-priority traceback from a multi-bug log."""
    if not parsed:
        return ParsedTraceback(
            exception_type="Exception",
            exception_message="",
            failing_frame=None,
            full_frames=[],
            raw_chunk="",
        )
    if len(parsed) == 1:
        return parsed[0]
    ranked = sorted(
        enumerate(parsed),
        key=lambda pair: _score_traceback(pair[1], pair[0]),
        reverse=True,
    )
    return ranked[0][1]


def parse_traceback(raw: str) -> ParsedTraceback:
    """Parse a log into the primary traceback (multi-stack aware)."""
    all_tb = parse_all_tracebacks(raw)
    if not all_tb:
        return parse_traceback_chunk(raw or "")
    if len(all_tb) == 1:
        return all_tb[0]
    return select_primary_traceback(all_tb)


def traceback_summary(tb: ParsedTraceback, *, is_primary: bool = False) -> dict:
    """Compact dict for LLM prompts / session events."""
    frame = tb.failing_frame
    return {
        "exception_type": tb.exception_type,
        "exception_message": tb.exception_message,
        "function_name": frame.function_name if frame else None,
        "filename": frame.filename if frame else None,
        "line_number": frame.line_number if frame else None,
        "code_context": frame.code_context if frame else None,
        "is_primary": is_primary,
    }


def tracebacks_as_dicts(parsed: list[ParsedTraceback], primary: ParsedTraceback) -> list[dict]:
    return [
        traceback_summary(tb, is_primary=(tb is primary or tb == primary))
        for tb in parsed
    ]


def parsed_traceback_to_dict(tb: ParsedTraceback) -> dict:
    data = asdict(tb)
    return data
