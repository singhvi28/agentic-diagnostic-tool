"""AST analysis for sync blocking I/O inside async FastAPI handlers."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


# Attr calls on known sync libraries that starve the event loop.
BLOCKING_ATTR_MODULES = {
    "time": {"sleep"},
    "requests": {"get", "post", "put", "delete", "patch", "request", "head", "options"},
    "urllib": {"urlopen"},
    "urllib.request": {"urlopen"},
    "subprocess": {"run", "call", "Popen", "check_call", "check_output"},
    "httpx": set(),  # sync Client methods handled via receiver name below
    "os": {"system", "popen", "wait"},
}

# Bare names (from `from time import sleep`, builtins, etc.)
BLOCKING_NAMES = {
    "sleep",
    "open",
    "input",
    "urlopen",
}

# Receiver object names that imply sync HTTP clients
BLOCKING_RECEIVERS = {
    "requests",
    "session",  # requests.Session
    "client",  # ambiguous; only flag with HTTP attrs
}

HTTP_ATTRS = {"get", "post", "put", "delete", "patch", "request", "head", "options"}


class AsyncBlockingVisitor(ast.NodeVisitor):
    """Walk an async function body and flag likely sync-blocking calls."""

    def __init__(self, function_name: str) -> None:
        self.function_name = function_name
        self.issues: list[dict[str, Any]] = []
        self._in_target = False
        self._await_depth = 0

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node.name != self.function_name:
            self.generic_visit(node)
            return
        prev = self._in_target
        self._in_target = True
        for child in node.body:
            self.visit(child)
        self._in_target = prev

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Nested sync defs inside async handler — skip inner bodies by default.
        if self._in_target:
            return
        self.generic_visit(node)

    def visit_Await(self, node: ast.Await) -> None:
        self._await_depth += 1
        self.generic_visit(node)
        self._await_depth -= 1

    def visit_Call(self, node: ast.Call) -> None:
        if not self._in_target or self._await_depth > 0:
            self.generic_visit(node)
            return

        if _is_blocking_call(node.func):
            self.issues.append(
                {
                    "line": node.lineno,
                    "call": ast.unparse(node.func),
                    "message": (
                        f"Likely blocking call `{ast.unparse(node.func)}()` "
                        f"inside async function `{self.function_name}` without await"
                    ),
                }
            )
        self.generic_visit(node)


def _is_blocking_call(func: ast.AST) -> bool:
    if isinstance(func, ast.Name):
        return func.id in BLOCKING_NAMES

    if isinstance(func, ast.Attribute):
        attr = func.attr
        # time.sleep / requests.get / subprocess.run
        root = _root_name(func.value)
        if root and attr in BLOCKING_ATTR_MODULES.get(root, set()):
            return True
        # Fully qualified module path as dotted attribute chain
        dotted = _dotted_name(func.value)
        if dotted and attr in BLOCKING_ATTR_MODULES.get(dotted, set()):
            return True
        # session.get / client.post heuristics (HTTP only)
        if root in BLOCKING_RECEIVERS and attr in HTTP_ATTRS:
            return True
        if attr == "sleep" and root == "time":
            return True
    return False


def _root_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _root_name(node.value)
    return None


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else None
    return None


def analyze_async_blocking(file_path: str | Path, function_name: str) -> dict[str, Any]:
    """Parse a file and report sync-blocking calls inside the named async function."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return {
            "file": str(path),
            "function": function_name,
            "issues": [],
            "error": f"SyntaxError: {exc}",
        }

    visitor = AsyncBlockingVisitor(function_name)
    visitor.visit(tree)

    # Also note if the named function is missing or not async.
    found_async = False
    found_sync = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name:
            found_async = True
        elif isinstance(node, ast.FunctionDef) and node.name == function_name:
            found_sync = True

    notes: list[str] = []
    if found_sync and not found_async:
        notes.append(
            f"`{function_name}` is a sync `def`; blocking I/O is expected but "
            "will block the thread if used as an ASGI endpoint incorrectly."
        )
    elif not found_async and not found_sync:
        notes.append(f"Function `{function_name}` not found in {path}")

    return {
        "file": str(path),
        "function": function_name,
        "issues": visitor.issues,
        "notes": notes,
    }
