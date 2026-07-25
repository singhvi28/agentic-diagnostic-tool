"""ASGI in-process reproduction helpers + pytest tempfile fallback."""

from __future__ import annotations

import importlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx

from diagnostic_engine.config import get_settings


def load_target_app():
    """Import the sample/target FastAPI app module path from settings."""
    # Default: examples.target_app.main:app
    module = importlib.import_module("examples.target_app.main")
    return getattr(module, "app")


async def run_asgi_smoke(path: str = "/health", method: str = "GET") -> dict[str, Any]:
    """Hit the target app in-process via ASGITransport."""
    app = load_target_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(method, path)
        return {
            "passed": response.status_code < 500,
            "status_code": response.status_code,
            "body": response.text[:2000],
        }


def run_reproduction_test(test_code: str, timeout_seconds: int = 30) -> dict[str, Any]:
    """Execute a pytest snippet; prefer sandbox cwd for imports."""
    settings = get_settings()
    sandbox = settings.sandbox_root.resolve()
    project_root = Path(__file__).resolve().parents[3]

    with tempfile.TemporaryDirectory(prefix="diag_test_") as tmp:
        test_path = Path(tmp) / "test_reproduction.py"
        test_path.write_text(test_code, encoding="utf-8")
        env_pythonpath = str(project_root)
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", str(test_path), "-q", "--tb=short"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env={**dict(**{k: v for k, v in __import__("os").environ.items()}), "PYTHONPATH": env_pythonpath},
            )
            return {
                "passed": result.returncode == 0,
                "returncode": result.returncode,
                "stdout": result.stdout[-4000:],
                "stderr": result.stderr[-2000:],
                "sandbox": str(sandbox),
            }
        except subprocess.TimeoutExpired:
            return {"passed": False, "error": f"Timed out after {timeout_seconds}s"}
        except FileNotFoundError:
            return {"passed": False, "error": "pytest not installed in this environment"}
