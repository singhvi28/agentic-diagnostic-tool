"""CLI entrypoint to run a diagnostic pass over an error log."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from diagnostic_engine.agent.graph import get_agent
from diagnostic_engine.agent.mcp_client import DiagnosticMcpClient
from diagnostic_engine.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the FastAPI diagnostic agent")
    parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help="Path to error log (defaults to ERROR_LOG_PATH)",
    )
    parser.add_argument(
        "--text",
        type=str,
        default=None,
        help="Inline traceback text instead of a log file",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "After tests pass, apply proposed unified diffs under the sandbox "
            "(prints each diff; prompts for confirmation unless --yes)"
        ),
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="With --apply, skip interactive confirmation (still prints diffs)",
    )
    args = parser.parse_args()

    apply_patches = bool(args.apply)
    apply_auto_yes = bool(args.yes) and apply_patches

    settings = get_settings()
    if args.text:
        raw = args.text
    else:
        log_path = args.log or settings.error_log_path
        raw = log_path.read_text(encoding="utf-8") if log_path.exists() else ""

    agent = get_agent()
    with DiagnosticMcpClient.from_settings(settings) as _mcp:
        final = agent.invoke(
            {
                "raw_log_entry": raw,
                "apply_patches": apply_patches,
                "apply_auto_yes": apply_auto_yes,
            }
        )

    out = dict(final)
    parsed = out.get("parsed_traceback")
    if parsed is not None and hasattr(parsed, "__dict__"):
        failing = parsed.failing_frame
        out["parsed_traceback"] = {
            "exception_type": parsed.exception_type,
            "exception_message": parsed.exception_message,
            "failing_frame": (
                {
                    "filename": failing.filename,
                    "line_number": failing.line_number,
                    "function_name": failing.function_name,
                    "code_context": failing.code_context,
                }
                if failing
                else None
            ),
            "frame_count": len(parsed.full_frames),
        }

    summary = {
        "session_id": out.get("session_id"),
        "retry_count": out.get("retry_count"),
        "test_passed": (out.get("test_result") or {}).get("passed"),
        "apply_result": out.get("apply_result"),
        "root_cause_analysis": out.get("root_cause_analysis"),
        "analyzer_findings": out.get("analyzer_findings"),
        "mcp_transport": settings.mcp_transport,
        "apply_patches": apply_patches,
        "apply_auto_yes": apply_auto_yes,
    }
    print(json.dumps({"summary": summary, "state": out}, indent=2, default=str))


if __name__ == "__main__":
    main()
