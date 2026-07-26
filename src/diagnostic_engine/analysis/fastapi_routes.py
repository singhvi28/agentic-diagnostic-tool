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
    router_var: str | None = None


@dataclass
class DependencyInfo:
    name: str
    file: str
    line: int
    is_async: bool
    is_generator: bool
    has_try_finally: bool


@dataclass
class RouterMount:
    router_var: str
    prefix: str
    file: str
    line: int


@dataclass
class FunctionInfo:
    name: str
    qname: str
    file: str
    line: int
    is_async: bool
    decorators: list[dict[str, Any]] = field(default_factory=list)
    params: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)


def function_qname(file_path: str, name: str, root: Path | None = None) -> str:
    """Stable Function identity: relative/file::name."""
    p = Path(file_path)
    if root is not None:
        try:
            rel = p.resolve().relative_to(Path(root).resolve())
            return f"{rel.as_posix()}::{name}"
        except ValueError:
            pass
    return f"{p.name}::{name}"


def extract_fastapi_topology(root: str | Path) -> dict[str, Any]:
    """Walk a project tree and extract Endpoint / Dependency / Function graph candidates."""
    root_path = Path(root)
    if not root_path.exists():
        raise FileNotFoundError(f"Root not found: {root}")

    routes: list[RouteInfo] = []
    dependencies: list[DependencyInfo] = []
    mounts: list[RouterMount] = []
    functions: list[FunctionInfo] = []
    router_prefixes: dict[str, str] = {}  # var -> prefix from APIRouter(prefix=...)

    for py_file in root_path.rglob("*.py"):
        if any(part.startswith(".") or part in {"__pycache__", ".venv", "venv"} for part in py_file.parts):
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        router_prefixes.update(_extract_router_prefixes(tree))
        mounts.extend(_extract_include_router(tree, str(py_file)))
        routes.extend(_extract_routes(tree, str(py_file)))
        dependencies.extend(_extract_dependencies(tree, str(py_file)))
        functions.extend(_extract_functions(tree, str(py_file), root_path))

    # Compose mount prefixes onto routes by router variable name
    mount_prefix_by_router: dict[str, str] = dict(router_prefixes)
    for mount in mounts:
        base = mount_prefix_by_router.get(mount.router_var, "")
        mount_prefix_by_router[mount.router_var] = _join_paths(mount.prefix, base)

    for route in routes:
        if route.router_var and route.router_var in mount_prefix_by_router:
            route.path = _join_paths(mount_prefix_by_router[route.router_var], route.path)

    # Attach composed route method/path onto matching function decorators
    route_by_fn: dict[str, RouteInfo] = {r.function_name: r for r in routes}
    for fn in functions:
        route = route_by_fn.get(fn.name)
        if route is None:
            continue
        for dec in fn.decorators:
            if dec.get("is_route"):
                dec["method"] = route.method
                dec["path"] = route.path

    return {
        "routes": [asdict(r) for r in routes],
        "dependencies": [asdict(d) for d in dependencies],
        "mounts": [asdict(m) for m in mounts],
        "functions": [asdict(f) for f in functions],
    }


def _extract_functions(
    tree: ast.AST,
    file_path: str,
    root: Path,
) -> list[FunctionInfo]:
    """Top-level functions with decorators, params, shallow CALLS, and Depends edges."""
    results: list[FunctionInfo] = []
    module_body = getattr(tree, "body", [])
    for node in module_body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators: list[dict[str, Any]] = []
        for dec in node.decorator_list:
            raw = ast.unparse(dec)
            method, path, _router = _route_decorator(dec)
            decorators.append(
                {
                    "raw": raw,
                    "name": raw.split("(")[0],
                    "is_route": method is not None,
                    "method": method.upper() if method else None,
                    "path": path,
                    "module": None,
                }
            )

        params: list[dict[str, Any]] = []
        positional = list(node.args.args)
        defaults = list(node.args.defaults)
        default_offset = len(positional) - len(defaults)
        for i, arg in enumerate(positional):
            if arg.arg in {"self", "cls"}:
                continue
            default_val = None
            if i >= default_offset:
                default_val = ast.unparse(defaults[i - default_offset])
            annotation = ast.unparse(arg.annotation) if arg.annotation else None
            params.append(
                {
                    "name": arg.arg,
                    "annotation": annotation,
                    "default": default_val,
                }
            )
        for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
            params.append(
                {
                    "name": arg.arg,
                    "annotation": ast.unparse(arg.annotation) if arg.annotation else None,
                    "default": ast.unparse(default) if default is not None else None,
                }
            )

        calls: list[dict[str, Any]] = []
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            callee: str | None = None
            if isinstance(child.func, ast.Name):
                callee = child.func.id
            elif isinstance(child.func, ast.Attribute) and isinstance(child.func.value, ast.Name):
                # skip module.attr like time.sleep; keep local-ish attr only if needed
                continue
            if callee and callee != node.name:
                calls.append({"to": callee, "line": child.lineno})

        results.append(
            FunctionInfo(
                name=node.name,
                qname=function_qname(file_path, node.name, root),
                file=file_path,
                line=node.lineno,
                is_async=isinstance(node, ast.AsyncFunctionDef),
                decorators=decorators,
                params=params,
                calls=calls,
                depends_on=_depends_from_signature(node),
            )
        )
    return results


def _join_paths(prefix: str, path: str) -> str:
    prefix = prefix or ""
    path = path or "/"
    if not prefix:
        return path if path.startswith("/") else f"/{path}"
    if prefix.endswith("/") and path.startswith("/"):
        return prefix[:-1] + path
    if not prefix.endswith("/") and not path.startswith("/"):
        return f"{prefix}/{path}"
    return prefix + path


def _extract_router_prefixes(tree: ast.AST) -> dict[str, str]:
    """Map `router = APIRouter(prefix="/api")` variable names to prefixes."""
    result: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        if not _is_api_router_call(value):
            continue
        prefix = ""
        for kw in value.keywords:
            if kw.arg == "prefix":
                prefix = _const_str(kw.value) or ""
        for target in node.targets:
            if isinstance(target, ast.Name):
                result[target.id] = prefix
    return result


def _is_api_router_call(node: ast.Call) -> bool:
    if isinstance(node.func, ast.Name) and node.func.id == "APIRouter":
        return True
    if isinstance(node.func, ast.Attribute) and node.func.attr == "APIRouter":
        return True
    return False


def _extract_include_router(tree: ast.AST, file_path: str) -> list[RouterMount]:
    mounts: list[RouterMount] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "include_router":
            continue
        if not node.args:
            continue
        router_var = ast.unparse(node.args[0]) if not isinstance(node.args[0], ast.Name) else node.args[0].id
        prefix = ""
        for kw in node.keywords:
            if kw.arg == "prefix":
                prefix = _const_str(kw.value) or ""
        mounts.append(
            RouterMount(router_var=router_var, prefix=prefix, file=file_path, line=node.lineno)
        )
    return mounts


def _extract_routes(tree: ast.AST, file_path: str) -> list[RouteInfo]:
    routes: list[RouteInfo] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            method, path, router_var = _route_decorator(dec)
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
                    router_var=router_var,
                )
            )
    return routes


def _route_decorator(dec: ast.AST) -> tuple[str | None, str | None, str | None]:
    """Return (method, path, router_var) for @app.get / @router.post style decorators."""
    if not isinstance(dec, ast.Call):
        return None, None, None
    func = dec.func
    method: str | None = None
    router_var: str | None = None
    if isinstance(func, ast.Attribute) and func.attr.lower() in HTTP_METHODS:
        method = func.attr.lower()
        if isinstance(func.value, ast.Name):
            router_var = func.value.id
    elif isinstance(func, ast.Attribute) and func.attr == "api_route":
        method = "API_ROUTE"
        if isinstance(func.value, ast.Name):
            router_var = func.value.id
    else:
        return None, None, None

    path: str | None = None
    if dec.args:
        path = _const_str(dec.args[0])
    for kw in dec.keywords:
        if kw.arg == "path":
            path = _const_str(kw.value)
        if kw.arg == "methods" and method == "API_ROUTE":
            if isinstance(kw.value, (ast.List, ast.Tuple)) and kw.value.elts:
                first = _const_str(kw.value.elts[0])
                if first:
                    method = first.lower()
    return method, path, router_var


def _depends_from_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    deps: list[str] = []
    # Annotated[..., Depends(...)] on annotations (no defaults required)
    for arg in list(node.args.args) + list(node.args.kwonlyargs):
        if arg.annotation is not None:
            name = _depends_from_annotated(arg.annotation)
            if name:
                deps.append(name)

    positional = list(node.args.args)
    defaults = node.args.defaults
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
    # Dedupe preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for d in deps:
        if d not in seen:
            seen.add(d)
            ordered.append(d)
    return ordered


def _depends_from_annotated(annotation: ast.AST) -> str | None:
    """Parse Annotated[T, Depends(x)] / Annotated[T, Depends()]."""
    if not isinstance(annotation, ast.Subscript):
        return None
    base = annotation.value
    is_annotated = (isinstance(base, ast.Name) and base.id == "Annotated") or (
        isinstance(base, ast.Attribute) and base.attr == "Annotated"
    )
    if not is_annotated:
        return None
    slice_node = annotation.slice
    elts: list[ast.AST]
    if isinstance(slice_node, ast.Tuple):
        elts = list(slice_node.elts)
    else:
        elts = [slice_node]
    for elt in elts[1:]:
        name = _depends_call_name(elt)
        if name:
            return name
    return None


def _depends_call_name(default: ast.AST) -> str | None:
    """Extract dependency callable name from Depends(get_db)."""
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
    depends_refs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _depends_call_name(node)
            if name and name != "Depends":
                depends_refs.add(name.split(".")[-1])
        if isinstance(node, ast.Subscript):
            name = _depends_from_annotated(node)
            if name and name != "Depends":
                depends_refs.add(name.split(".")[-1])

    results: list[DependencyInfo] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if _has_contextmanager_decorator(node):
            continue
        is_gen = _contains_yield(node)
        if not is_gen and node.name not in depends_refs:
            continue
        # Yield-only functions that are never Depends()-referenced are still
        # likely DI generators when named get_* / *_session / *_db.
        if is_gen and node.name not in depends_refs:
            likely = node.name.startswith("get_") or node.name.endswith(("_db", "_session"))
            if not likely:
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


def _has_contextmanager_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in node.decorator_list:
        name = ast.unparse(dec)
        if "contextmanager" in name:
            return True
    return False


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
