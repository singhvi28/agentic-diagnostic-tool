"""In-process ASGI reproduction runner (httpx.ASGITransport — no pytest subprocess)."""

from __future__ import annotations

import asyncio
import inspect
import json
import traceback
from typing import Any, Callable

import httpx

from diagnostic_engine.config import get_settings


def load_target_app(import_path: str | None = None) -> Any:
    """Import the target FastAPI app from `module.sub:attr` (TARGET_APP_IMPORT)."""
    import importlib
    import sys
    from pathlib import Path

    # Ensure repo root is importable (examples.target_app.*)
    project_root = Path(__file__).resolve().parents[3]
    root_str = str(project_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    path = import_path or get_settings().target_app_import
    if ":" not in path:
        raise ValueError(f"Invalid target_app_import {path!r}; expected 'module:attr'")
    module_name, attr = path.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, attr)


async def _make_client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


def _looks_like_json_suite(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if not (stripped.startswith("[") or stripped.startswith("{")):
        return False
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    if isinstance(data, list):
        return bool(data) and all(isinstance(s, dict) and ("path" in s or "method" in s) for s in data)
    if isinstance(data, dict):
        if "steps" in data and isinstance(data["steps"], list):
            return True
        return "path" in data or "method" in data
    return False


def _normalize_steps(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "steps" in data and isinstance(data["steps"], list):
            return data["steps"]
        return [data]
    return []


async def run_request_suite(
    steps: list[dict[str, Any]],
    *,
    app: Any | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Hit the ASGI app with a list of HTTP steps."""
    app = app or load_target_app()
    results: list[dict[str, Any]] = []

    async with app.router.lifespan_context(app):
        async with await _make_client(app) as client:
            for i, step in enumerate(steps):
                method = str(step.get("method", "GET")).upper()
                path = str(step.get("path", "/"))
                expect = step.get("expect_status")
                body = step.get("json")
                name = step.get("name") or f"step_{i}_{method}_{path}"
                try:
                    response = await asyncio.wait_for(
                        client.request(method, path, json=body),
                        timeout=timeout,
                    )
                    status = response.status_code
                    ok = status == expect if expect is not None else status < 500
                    entry: dict[str, Any] = {
                        "name": name,
                        "passed": ok,
                        "status_code": status,
                        "body": response.text[:2000],
                    }
                    if expect is not None and not ok:
                        entry["error"] = f"expected status {expect}, got {status}"
                    results.append(entry)
                except Exception as exc:  # noqa: BLE001
                    results.append({"name": name, "passed": False, "error": str(exc)})

    passed = bool(results) and all(r.get("passed") for r in results)
    return {
        "passed": passed,
        "mode": "json_suite",
        "results": results,
        "runner": "asgi",
    }


async def run_python_tests(
    test_code: str,
    *,
    app: Any | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Exec Python defining test_* and run them against an ASGI client."""
    app = app or load_target_app()
    namespace: dict[str, Any] = {
        "__name__": "reproduction_tests",
        "__builtins__": __builtins__,
        "app": app,
        "httpx": httpx,
    }
    try:
        import pytest  # noqa: F401

        namespace["pytest"] = pytest
    except ImportError:
        pass

    try:
        compiled = compile(test_code, "<reproduction_test>", "exec")
        exec(compiled, namespace, namespace)  # noqa: S102 — intentional for LLM test snippets
    except Exception as exc:  # noqa: BLE001
        return {
            "passed": False,
            "mode": "python",
            "error": f"Failed to compile/exec test code: {exc}",
            "traceback": traceback.format_exc()[-2000:],
            "runner": "asgi",
        }

    tests: list[tuple[str, Callable[..., Any]]] = [
        (name, obj)
        for name, obj in namespace.items()
        if name.startswith("test_") and callable(obj)
    ]
    if not tests:
        return {
            "passed": False,
            "mode": "python",
            "error": "No test_* functions found in reproduction_test_code",
            "runner": "asgi",
        }

    results: list[dict[str, Any]] = []
    async with app.router.lifespan_context(app):
        async with await _make_client(app) as client:
            namespace["client"] = client
            for name, fn in tests:
                try:
                    sig = inspect.signature(fn)
                    kwargs: dict[str, Any] = {}
                    if "client" in sig.parameters:
                        kwargs["client"] = client
                    if "app" in sig.parameters:
                        kwargs["app"] = app

                    if inspect.iscoroutinefunction(fn):
                        await asyncio.wait_for(fn(**kwargs), timeout=timeout)
                    else:
                        def _call() -> Any:
                            return fn(**kwargs)

                        await asyncio.wait_for(asyncio.to_thread(_call), timeout=timeout)
                    results.append({"name": name, "passed": True})
                except AssertionError as exc:
                    results.append({
                        "name": name,
                        "passed": False,
                        "error": f"AssertionError: {exc}",
                        "traceback": traceback.format_exc()[-2000:],
                    })
                except Exception as exc:  # noqa: BLE001
                    results.append({
                        "name": name,
                        "passed": False,
                        "error": str(exc),
                        "traceback": traceback.format_exc()[-2000:],
                    })

    passed = bool(results) and all(r.get("passed") for r in results)
    return {
        "passed": passed,
        "mode": "python",
        "results": results,
        "runner": "asgi",
    }


def run_reproduction_test(test_code: str, timeout_seconds: int = 30) -> dict[str, Any]:
    """Run reproduction code in-process via ASGITransport (JSON suite or Python test_*)."""
    code = (test_code or "").strip()
    if not code:
        return {"passed": False, "error": "Empty reproduction_test_code", "runner": "asgi"}

    async def _run() -> dict[str, Any]:
        if _looks_like_json_suite(code):
            data = json.loads(code)
            steps = _normalize_steps(data)
            return await run_request_suite(steps, timeout=float(timeout_seconds))
        return await run_python_tests(code, timeout=float(timeout_seconds))

    try:
        return asyncio.run(asyncio.wait_for(_run(), timeout=float(timeout_seconds) + 5))
    except asyncio.TimeoutError:
        return {
            "passed": False,
            "error": f"Timed out after {timeout_seconds}s",
            "runner": "asgi",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "passed": False,
            "error": str(exc),
            "traceback": traceback.format_exc()[-2000:],
            "runner": "asgi",
        }


async def run_asgi_smoke(path: str = "/health", method: str = "GET") -> dict[str, Any]:
    """One-step ASGI smoke check."""
    return await run_request_suite(
        [{"method": method, "path": path, "expect_status": 200}],
    )
