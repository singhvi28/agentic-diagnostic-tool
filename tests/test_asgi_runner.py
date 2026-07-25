"""In-process ASGI reproduction runner tests (no pytest subprocess)."""

from __future__ import annotations

import pytest

from diagnostic_engine.analysis.asgi_runner import (
    load_target_app,
    run_reproduction_test,
)


def test_load_target_app():
    app = load_target_app("examples.target_app.main:app")
    assert app is not None
    assert getattr(app, "title", None) == "Buggy Shop"


def test_json_suite_health_passes():
    result = run_reproduction_test(
        '[{"method": "GET", "path": "/health", "expect_status": 200}]'
    )
    assert result["runner"] == "asgi"
    assert result["mode"] == "json_suite"
    assert result["passed"] is True
    assert result["results"][0]["status_code"] == 200


def test_json_suite_expect_status_mismatch_fails():
    result = run_reproduction_test(
        '[{"method": "GET", "path": "/health", "expect_status": 201}]'
    )
    assert result["passed"] is False
    assert "expected status 201" in (result["results"][0].get("error") or "")


def test_json_suite_missing_order_500():
    result = run_reproduction_test(
        '[{"method": "GET", "path": "/orders/999", "expect_status": 500}]'
    )
    assert result["passed"] is True
    assert result["results"][0]["status_code"] == 500


def test_python_async_health():
    code = '''
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json().get("ok") is True
'''
    result = run_reproduction_test(code)
    assert result["mode"] == "python"
    assert result["passed"] is True
    assert result["results"][0]["name"] == "test_health"


def test_python_failing_assert():
    code = '''
async def test_fail(client):
    response = await client.get("/health")
    assert response.status_code == 999
'''
    result = run_reproduction_test(code)
    assert result["passed"] is False
    assert "AssertionError" in (result["results"][0].get("error") or "")


def test_empty_module_fails():
    result = run_reproduction_test("# no tests here\nx = 1\n")
    assert result["passed"] is False
    assert "No test_*" in (result.get("error") or "")


def test_empty_code_fails():
    result = run_reproduction_test("   ")
    assert result["passed"] is False


def test_no_subprocess_used(monkeypatch):
    import subprocess

    def _boom(*_a, **_k):
        raise AssertionError("subprocess.run must not be called")

    monkeypatch.setattr(subprocess, "run", _boom)
    result = run_reproduction_test(
        '[{"method": "GET", "path": "/health", "expect_status": 200}]'
    )
    assert result["passed"] is True
    assert result["runner"] == "asgi"
