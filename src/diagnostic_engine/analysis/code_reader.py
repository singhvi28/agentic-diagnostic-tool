"""Sandboxed source file reading."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_under_root(file_path: str | Path, sandbox_root: Path) -> Path:
    """Resolve path and ensure it stays inside sandbox_root."""
    root = sandbox_root.resolve()
    path = Path(file_path)
    if not path.is_absolute():
        path = (root / path).resolve()
    else:
        path = path.resolve()
    if not path.is_relative_to(root):
        raise PermissionError(f"Path escapes sandbox root: {file_path}")
    return path


def read_source_file(
    file_path: str | Path,
    sandbox_root: Path,
    start_line: int = 1,
    end_line: int | None = None,
) -> dict[str, Any]:
    path = resolve_under_root(file_path, sandbox_root)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")

    lines = path.read_text(encoding="utf-8").splitlines()
    total = len(lines)
    start = max(1, start_line)
    end = total if end_line is None else min(end_line, total)
    if start > total:
        raise ValueError(f"start_line {start} beyond file length {total}")

    slice_lines = lines[start - 1 : end]
    numbered = "\n".join(f"{i:4d}| {line}" for i, line in enumerate(slice_lines, start=start))
    return {
        "file": str(path),
        "start_line": start,
        "end_line": end,
        "total_lines": total,
        "content": numbered,
    }
