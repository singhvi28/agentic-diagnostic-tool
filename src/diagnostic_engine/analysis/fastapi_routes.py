"""Static AST extraction of FastAPI routes, dependencies, and handlers."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


@dataclass
class RouteInfo:
    path: str
    method: str
    function_name: str
    file: str
    line: int
    is_async: bool
    dependencies: list[str] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)


@dataclass
class DependencyInfo:
    name: str
    file: str
    line: int
    is_async: bool
    is_generator: bool  # yield-based Depends
    has_try_finally: bool


def extract_fastapi_topology(root: str | Path) -> dict[str, Any]:
    """Walk a project tree and extract Endpoint / Dependency candidates for Neo4j."""
    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(f"Root not found: {root}")

    routes: list[RouteInfo] = []
    dependencies: list[DependencyInfo] = []

    for py_file in root_path.rglob("*.py"):
        if any(part.startswith(".") or part in {"__pycache__", ".venv", "venv"} for part in py_file.parts):
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        routes.extend(_extract_routes(tree, str(py_file)))
        dependencies.extend(_extract_dependencies(tree, str(py_file)))

    return {
        "routes": [asdict(r) for r in routes],
        "dependencies": [asdict(d) for d in dependencies],
    }


def _extract_routes(tree: ast.AST, file_path: str) -> list[RouteInfo]:
    routes: list[RouteInfo] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            method, path = _route_decorator(dec)
            if method is None:
                continue
            deps = _depends_from_signature(node)
            routes.append(
                RouteInfo(
                    path=path or "/",
                    method=method.upper(),
                    function_name=node.name,
                    file=file_path,
                    line=node.lineno,
                    is_async=isinstance(node, ast.AsyncFunctionDef),
                    dependencies=deps,
                    decorators=[ast.unparse(d) for d in node.decorator_list],
                )
            )
    return routes


def _route_decorator(dec: ast.AST) -> tuple[str | None, str | None]:
    """Return (method, path) for @app.get("/x") / @router.post(...) style decorators."""
    if not isinstance(dec, ast.Call):
        return None, None
    func = dec.func
    method: str | None = None
    if isinstance(func, ast.Attribute) and func.attr.lower() in HTTP_METHODS:
        method = func.attr.lower()
    elif isinstance(func, ast.Attribute) and func.attr == "api_route":
        method = "API_ROUTE"
    else:
        return None, None

    path: str | None = None
    if dec.args:
        path = _const_str(dec.args[0])
    for kw in dec.keywords:
        if kw.arg == "path":
            path = _const_str(kw.value)
        if kw.arg == "methods" and method == "API_ROUTE":
            # Keep first method if list literal
            if isinstance(kw.value, (ast.List, ast.Tuple)) and kw.value.elts:
                first = _const_str(kw.value.elts[0])
                if first:
                    method = first.lower()
    return method, path


def _depends_from_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    deps: list[str] = []
    positional = list(node.args.args)
    defaults = node.args.defaults
    # AST defaults align to the end of positional args
    default_offset = len(positional) - len(defaults)
    for i in range(default_offset, len(positional)):
        default = defaults[i - default_offset]
        name = _depends_call_name(default)
        if name:
            deps.append(name)
    for default in node.args.kw_defaults:
        if default is None:
            continue
        name = _depends_call_name(default)
        if name:
            deps.append(name)
    return deps


def _depends_call_name(default: ast.AST) -> str | None:
    """Extract dependency callable name from `Depends(get_db)` or `= Depends(...)`."""
    if not isinstance(default, ast.Call):
        return None
    if isinstance(default.func, ast.Name) and default.func.id == "Depends":
        if default.args:
            return ast.unparse(default.args[0])
        return "Depends"
    if isinstance(default.func, ast.Attribute) and default.func.attr == "Depends":
        if default.args:
            return ast.unparse(default.args[0])
        return "Depends"
    return None


def _extract_dependencies(tree: ast.AST, file_path: str) -> list[DependencyInfo]:
    """Find FastAPI dependency callables: yield-based or referenced via Depends()."""
    depends_refs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _depends_call_name(node)
            if name and name != "Depends":
                depends_refs.add(name.split(".")[-1])

    results: list[DependencyInfo] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        is_gen = _contains_yield(node)
        if not is_gen and node.name not in depends_refs:
            continue
        results.append(
            DependencyInfo(
                name=node.name,
                file=file_path,
                line=node.lineno,
                is_async=isinstance(node, ast.AsyncFunctionDef),
                is_generator=is_gen,
                has_try_finally=_has_try_finally(node),
            )
        )
    return results


def _contains_yield(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, (ast.Yield, ast.YieldFrom)):
            return True
    return False


def _has_try_finally(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Try) and child.finalbody:
            return True
    return False


def _const_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None
